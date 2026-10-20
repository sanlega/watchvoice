import Foundation

// Primary on-device ASR (Cactus "Whistle" model on the needle engine).
// The real engine is compiled only for watchOS devices, where project.yml links Vendor/needle/libneedle.a
// (watchOS 26.2+, arm64_32 + arm64, no simulator slice). Simulator and macOS (`swift test`) get the stub.
// Hardware validation is pending (issue #13, gate W-023).

#if os(watchOS) && !targetEnvironment(simulator)
import CNeedle

/// needle is process-global and NOT thread-safe, so all calls go through this single shared actor.
public actor WhistleTranscriber: TranscriptionService {
    public static let shared = WhistleTranscriber(modelURL: Bundle.main.url(forResource: "whistle", withExtension: "cact"))

    public nonisolated var engine: TranscriptionEngine { .whistle }

    private let modelURL: URL?
    /// Kept alive for the whole process: needle has no unload, and it is unverified whether
    /// needle_load copies the buffer or keeps pointing at it.
    private var model: Data?

    private init(modelURL: URL?) {
        self.modelURL = modelURL
    }

    public func availability() async -> ASRAvailability {
        do {
            try loadIfNeeded()
            return .available
        } catch let error as ASRError {
            return .blocked(reason: Self.describe(error))
        } catch {
            return .blocked(reason: "whistle.cact unreadable")
        }
    }

    public func transcribe(_ audio: CapturedAudio, language: String?, keywords: [String]) async throws -> Transcript {
        try loadIfNeeded()
        guard !audio.samples.isEmpty else { throw ASRError.emptyResult }
        let keywordList: String? = keywords.isEmpty ? nil : keywords.joined(separator: "\n")

        // needle.h: returns the transcript token count (<= 320), negative on failure. The JSON for a
        // 30 s clip fits easily in 16 KB; word timestamps are off.
        let capacity: Int32 = 16_384
        var out = [CChar](repeating: 0, count: Int(capacity))
        let rc: Int32 = audio.samples.withUnsafeBufferPointer { pcm -> Int32 in
            Self.withOptionalCString(language) { lang -> Int32 in
                Self.withOptionalCString(keywordList) { kw -> Int32 in
                    out.withUnsafeMutableBufferPointer { buffer -> Int32 in
                        needle_transcribe(pcm.baseAddress, Int32(pcm.count), lang, kw, 0, buffer.baseAddress, capacity)
                    }
                }
            }
        }
        if rc < 0 { throw ASRError.engineFailure(Self.lastError()) }
        let bytes = out.prefix { $0 != 0 }.map { UInt8(bitPattern: $0) }
        return try Self.parse(Data(bytes), duration: audio.duration)
    }

    private func loadIfNeeded() throws {
        if model != nil { return }
        guard let modelURL else { throw ASRError.blocked("whistle.cact is not bundled") }
        let data = try Data(contentsOf: modelURL, options: .alwaysMapped)
        let rc = data.withUnsafeBytes { raw -> Int32 in
            guard let base = raw.baseAddress?.assumingMemoryBound(to: UInt8.self) else { return -1 }
            return needle_load(base, UInt64(raw.count))
        }
        guard rc >= 0 else { throw ASRError.engineFailure(Self.lastError()) }
        model = data
    }

    private struct Output: Decodable {
        let text: String
        let language: String?
    }

    private static func parse(_ data: Data, duration: TimeInterval) throws -> Transcript {
        guard let output = try? JSONDecoder().decode(Output.self, from: data) else {
            throw ASRError.engineFailure("unparseable needle output")
        }
        let text = output.text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { throw ASRError.emptyResult }
        return Transcript(text: text, language: output.language, engine: .whistle, modelVersion: "whistle",
                          onDevice: true, durationMs: Int(duration * 1000))
    }

    private static func lastError() -> String {
        guard let message = needle_last_error() else { return "unknown needle error" }
        return String(cString: message)
    }

    private static func describe(_ error: ASRError) -> String {
        switch error {
        case .blocked(let reason), .engineFailure(let reason): return reason
        default: return error.code
        }
    }

    private static func withOptionalCString<R>(_ string: String?, _ body: (UnsafePointer<CChar>?) -> R) -> R {
        guard let string else { return body(nil) }
        return string.withCString { body($0) }
    }
}

#else

/// Stub used whenever needle is not linked. Never returns text.
public struct WhistleTranscriber: TranscriptionService {
    public static let blockedReason = "Whistle not linked: requires Vendor/needle/libneedle.a (watchOS 26.2+, device only); hardware validation pending"
    public static let shared = WhistleTranscriber()

    private init() {}

    public var engine: TranscriptionEngine { .whistle }

    public func availability() async -> ASRAvailability {
        .blocked(reason: Self.blockedReason)
    }

    public func transcribe(_ audio: CapturedAudio, language: String?, keywords: [String]) async throws -> Transcript {
        throw ASRError.blocked(Self.blockedReason)
    }
}

#endif
