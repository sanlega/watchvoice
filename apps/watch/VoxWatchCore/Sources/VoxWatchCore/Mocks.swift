import Foundation

// Test/preview doubles. In-memory outbox: `FileInstructionRepository.inMemory()` (same logic, no disk).

/// Returns one second of silence.
public struct MockAudioCapture: AudioCapture {
    public init() {}
    public func start() async throws {}
    public func stop() async throws -> CapturedAudio {
        CapturedAudio(samples: [Float](repeating: 0, count: Int(CapturedAudio.sampleRate)))
    }
    public func cancel() async {}
}

/// Scripted API. Each submit consumes the next result; when the script is empty it succeeds.
public actor MockWatchAPIClient: WatchAPIClient {
    public private(set) var submittedKeys: [UUID] = []
    public private(set) var decisions: [(approvalId: UUID, body: ApprovalDecision, key: UUID)] = []
    public private(set) var cancels: [(runId: UUID, body: SignedBody)] = []
    public private(set) var pauses: [PauseRequest] = []
    public private(set) var pushTokens: [PushTokenRequest] = []
    public private(set) var syncs = 0
    /// When set, the next decide/cancel/pause/sync throws it.
    private var nextControlError: APIError?
    private var submitResults: [Result<InstructionReceipt, APIError>]
    private var instructions: [UUID: Instruction]
    private var pendingApprovals: [Approval]

    public init(submitResults: [Result<InstructionReceipt, APIError>] = [], instructions: [Instruction] = [], approvals: [Approval] = []) {
        self.submitResults = submitResults
        self.instructions = Dictionary(instructions.map { ($0.id, $0) }, uniquingKeysWith: { _, last in last })
        self.pendingApprovals = approvals
    }

    public func setSubmitResults(_ results: [Result<InstructionReceipt, APIError>]) {
        submitResults = results
    }

    public func setInstruction(_ instruction: Instruction) {
        instructions[instruction.id] = instruction
    }

    public func submit(_ instruction: NewInstruction, idempotencyKey: UUID) async throws -> InstructionReceipt {
        submittedKeys.append(idempotencyKey)
        guard !submitResults.isEmpty else {
            let id = UUID()
            return InstructionReceipt(instructionId: id, status: .queued, createdAt: Date(),
                                      links: .init(selfPath: "/v1/instructions/\(id.uuidString.lowercased())"))
        }
        return try submitResults.removeFirst().get()
    }

    public func instruction(id: UUID) async throws -> Instruction {
        guard let instruction = instructions[id] else { throw APIError.notFound }
        return instruction
    }

    public func approvals() async throws -> [Approval] {
        pendingApprovals.filter { $0.state == "pending" }
    }

    public func decide(approvalId: UUID, decision: ApprovalDecision, idempotencyKey: UUID) async throws -> Approval {
        guard let index = pendingApprovals.firstIndex(where: { $0.id == approvalId }) else { throw APIError.notFound }
        try throwControlError()
        decisions.append((approvalId, decision, idempotencyKey))
        pendingApprovals[index].state = decision.decision.rawValue
        return pendingApprovals[index]
    }

    public func projects() async throws -> [Project] { [] }

    public func pair(_ request: PairRequest) async throws -> PairResponse {
        guard PairingCode.isValid(request.code) else { throw APIError.unauthorized }
        return PairResponse(deviceId: UUID(), token: "vox_mock")
    }

    public func registerPushToken(_ request: PushTokenRequest) async throws {
        pushTokens.append(request)
    }

    public func cancelRun(runId: UUID, body: SignedBody, idempotencyKey: UUID) async throws {
        try throwControlError()
        cancels.append((runId, body))
    }

    public func setPaused(_ request: PauseRequest) async throws -> PauseResult {
        try throwControlError()
        pauses.append(request)
        return PauseResult(paused: request.paused, cancelledRuns: 0)
    }

    public func policy() async throws -> [PolicyRule] { [] }

    public func syncProjects() async throws {
        try throwControlError()
        syncs += 1
    }

    public func setNextControlError(_ error: APIError?) {
        nextControlError = error
    }

    private func throwControlError() throws {
        if let error = nextControlError {
            nextControlError = nil
            throw error
        }
    }
}
