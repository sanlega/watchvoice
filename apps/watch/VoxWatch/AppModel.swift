import SwiftUI
import WatchKit
import VoxWatchCore

/// Non-secret preferences (UserDefaults). The device token lives in the Keychain only.
enum SettingsKey {
    static let language = "language"                     // "auto" | "es" | "en"
    static let preferLocalASR = "preferLocalASR"
    static let remoteAudioConsent = "remoteAudioConsent" // default OFF
    static let serverURL = "serverURL"
    static let runnerHint = "runnerHint"                 // "" = backend decides
    static let useMockASR = "useMockASR"                 // DEBUG only
}

@MainActor
final class AppModel: ObservableObject {
    enum Connection: Equatable {
        case unknown, online, offline, authRequired, notConfigured
    }

    @Published private(set) var flow: FlowState = .idle
    @Published private(set) var dictating = false
    @Published private(set) var records: [OutboxRecord] = []
    @Published private(set) var approvals: [Approval] = []
    @Published private(set) var projects: [Project] = []
    @Published private(set) var connection: Connection = .unknown
    @Published private(set) var whistleStatus: ASRAvailability?

    var activity: [OutboxRecord] { Array(records.prefix(10)) }
    var pendingCount: Int { records.filter { $0.status == .pending || $0.status == .sending }.count }

    static let maxRecordingSeconds: TimeInterval = 30
    /// Foreground-only refresh cadence (drain outbox + poll instruction/run progress).
    static let refreshInterval: Duration = .seconds(10)

    private let repository: any InstructionRepository
    private let coordinator: DeliveryCoordinator
    private let auth: any AuthSession
    private let capture: any AudioCapture
    private let selector: TranscriberSelector
    private let appVersion: String
    private var activeService: (any TranscriptionService)?
    private var currentClientId: UUID?
    private var recordingTimeout: Task<Void, Never>?
    private var decisionKeys: [String: UUID] = [:]

    init() {
        let fileURL = try? FileInstructionRepository.defaultFileURL()
        if fileURL == nil { VoxLog.event("outbox.no_file_url") }
        repository = FileInstructionRepository(fileURL: fileURL)
        coordinator = DeliveryCoordinator(repository: repository)
        auth = KeychainAuthSession(service: "app.vox.watch")
        capture = AVAudioEngineCapture(maxDuration: Self.maxRecordingSeconds)
        #if DEBUG
        let mock: (any TranscriptionService)? = MockTranscriber()
        #else
        let mock: (any TranscriptionService)? = nil
        #endif
        selector = TranscriberSelector(whistle: WhistleTranscriber.shared, remote: RemoteTranscriber(), mock: mock)
        appVersion = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0.0.0"
        UserDefaults.standard.register(defaults: [
            SettingsKey.language: "auto",
            SettingsKey.preferLocalASR: true,
            SettingsKey.remoteAudioConsent: false,
            SettingsKey.serverURL: "",
            SettingsKey.runnerHint: "",
        ])
    }

    // MARK: Recording / transcription

    func speakTapped() async {
        guard flow == .idle, !dictating else { return }
        switch await selector.select(asrSettings) {
        case .systemDictation:
            dictating = true
        case .service(let service):
            do {
                try await capture.start()
            } catch {
                apply(.failed(.capture(Self.code(error))))
                return
            }
            activeService = service
            apply(.startTapped(Date()))
            haptic(.start)
            recordingTimeout = Task { [weak self] in
                try? await Task.sleep(for: .seconds(Self.maxRecordingSeconds))
                guard !Task.isCancelled else { return }
                await self?.stopTapped()
            }
        }
    }

    func stopTapped() async {
        guard case .recording = flow else { return }
        recordingTimeout?.cancel()
        apply(.stopTapped)
        haptic(.stop)
        do {
            let audio = try await capture.stop()
            guard let service = activeService else { throw ASRError.notImplemented }
            // Runs on the service's own executor (actor / global pool), never the main actor.
            // The audio buffer is dropped right after this call.
            let transcript = try await service.transcribe(audio, language: languageCode, keywords: projects.map(\.name))
            apply(.transcribed(transcript))
        } catch {
            apply(.failed(.transcription(Self.code(error))))
        }
        activeService = nil
    }

    func cancelTapped() async {
        recordingTimeout?.cancel()
        await capture.cancel()
        activeService = nil
        apply(.cancelTapped)
    }

    /// System dictation (TextField). We cannot know whether Apple processed it on device.
    func dictated(_ text: String) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        dictating = false
        apply(.transcribed(Transcript(text: trimmed, language: languageCode, engine: .systemDictation,
                                      modelVersion: nil, onDevice: false, durationMs: nil)))
    }

    func cancelDictation() { dictating = false }

    func retake() { apply(.retake) }

    // MARK: Delivery

    func send(projectHint: UUID?) async {
        guard case .reviewing(let transcript) = flow else { return }
        let instruction = NewInstruction(transcript: transcript, projectHint: projectHint,
                                         runnerHint: UserDefaults.standard.string(forKey: SettingsKey.runnerHint),
                                         appVersion: appVersion)
        do {
            _ = try await repository.enqueue(instruction, now: Date())
        } catch {
            // Not saved: stay on the transcript so the user can retry without losing it.
            VoxLog.event("outbox.write_failed", id: instruction.clientInstructionId)
            haptic(.failure)
            return
        }
        currentClientId = instruction.clientInstructionId
        apply(.saved(instruction.clientInstructionId))
        await refresh()
    }

    func finish() {
        currentClientId = nil
        apply(.reset)
    }

    func foregroundLoop() async {
        while !Task.isCancelled {
            await refresh()
            try? await Task.sleep(for: Self.refreshInterval)
        }
    }

    func refresh() async {
        guard let api = makeAPI() else {
            records = await repository.all()
            return
        }
        if case .savedLocally = flow { apply(.sendStarted) }
        let report = await coordinator.drain(using: api)
        if let error = report.haltedBy { note(error) } else if !report.delivered.isEmpty { connection = .online }
        records = await repository.all()
        await syncCurrentWithOutbox()
        await pollInstructions(api)
        records = await repository.all()
        if projects.isEmpty, let loaded = try? await api.projects() { projects = loaded }
    }

    private func syncCurrentWithOutbox() async {
        guard let id = currentClientId, let record = await repository.record(id) else { return }
        switch record.status {
        case .delivered:
            if let receipt = record.receipt { apply(.receiptReceived(receipt)) }
        case .failed:
            apply(.failed(.rejected(code: record.lastError ?? "rejected")))
        case .pending, .sending:
            apply(.deferred)
        }
    }

    /// Polls routing status and run progress while non-terminal (foreground only).
    private func pollInstructions(_ api: any WatchAPIClient) async {
        for record in activity where record.status == .delivered && (record.remote?.needsPolling ?? true) {
            guard let serverId = record.receipt?.instructionId else { continue }
            do {
                let instruction = try await api.instruction(id: serverId)
                try? await repository.setRemote(record.id, instruction: instruction)
                if record.id == currentClientId { apply(.instructionUpdated(instruction)) }
                connection = .online
            } catch {
                note(error)
                return
            }
        }
    }

    // MARK: Approvals

    func loadApprovals() async {
        guard let api = makeAPI() else { return }
        do {
            approvals = try await api.approvals().filter { $0.state == "pending" }
            connection = .online
        } catch {
            note(error)
        }
    }

    func decide(_ approval: Approval, approve: Bool) async {
        guard let api = makeAPI() else { return }
        let decision = ApprovalDecision(decision: approve ? .approved : .denied, challengeToken: approval.challengeToken)
        // Same key if the user retries the same decision.
        let keyName = "\(approval.id.uuidString)-\(decision.decision.rawValue)"
        let key = decisionKeys[keyName] ?? UUID()
        decisionKeys[keyName] = key
        do {
            _ = try await api.decide(approvalId: approval.id, decision: decision, idempotencyKey: key)
            approvals.removeAll { $0.id == approval.id }
            haptic(.success)
        } catch {
            note(error)
            haptic(.failure)
        }
    }

    // MARK: Settings

    func checkWhistle() async {
        whistleStatus = await WhistleTranscriber.shared.availability()
    }

    func saveToken(_ token: String) async {
        let trimmed = token.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        do {
            try await auth.store(trimmed)
            connection = .unknown
            await refresh()
        } catch {
            haptic(.failure)
        }
    }

    func logout() async {
        try? await auth.clear()
        connection = .authRequired
    }

    // MARK: Helpers

    private var asrSettings: TranscriberSelector.Settings {
        let defaults = UserDefaults.standard
        return TranscriberSelector.Settings(preferLocal: defaults.bool(forKey: SettingsKey.preferLocalASR),
                                            remoteAudioConsent: defaults.bool(forKey: SettingsKey.remoteAudioConsent),
                                            useMock: defaults.bool(forKey: SettingsKey.useMockASR))
    }

    private var languageCode: String? {
        let language = UserDefaults.standard.string(forKey: SettingsKey.language) ?? "auto"
        return language == "auto" ? nil : language
    }

    private func makeAPI() -> (any WatchAPIClient)? {
        guard let url = URL(string: UserDefaults.standard.string(forKey: SettingsKey.serverURL) ?? ""),
              let api = try? URLSessionWatchAPIClient(baseURL: url, auth: auth) else {
            connection = .notConfigured
            return nil
        }
        return api
    }

    private func note(_ error: Error) {
        switch error as? APIError {
        case .unauthorized?: connection = .authRequired
        case .offline?: connection = .offline
        default: break
        }
    }

    private func apply(_ event: FlowEvent) {
        let next = RecordingStateMachine.reduce(flow, event)
        guard next != flow else { return }
        flow = next
        switch next {
        case .received: haptic(.success)
        case .failed: haptic(.failure)
        default: break
        }
    }

    private func haptic(_ type: WKHapticType) {
        WKInterfaceDevice.current().play(type)
    }

    private static func code(_ error: Error) -> String {
        if let error = error as? APIError { return error.code }
        if let error = error as? ASRError { return error.code }
        if let error = error as? CaptureError { return "capture_\(error)" }
        return "error"
    }
}
