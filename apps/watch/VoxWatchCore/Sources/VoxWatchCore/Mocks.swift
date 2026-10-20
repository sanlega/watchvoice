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
        pendingApprovals[index].state = decision.decision.rawValue
        pendingApprovals[index].challengeToken = nil
        return pendingApprovals[index]
    }

    public func projects() async throws -> [Project] { [] }
}
