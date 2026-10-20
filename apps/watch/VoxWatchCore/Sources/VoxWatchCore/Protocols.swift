import Foundation

/// 16 kHz mono Float32 PCM in [-1, 1].
public struct CapturedAudio: Sendable, Equatable {
    public static let sampleRate: Double = 16_000
    public var samples: [Float]
    public var duration: TimeInterval { Double(samples.count) / Self.sampleRate }

    public init(samples: [Float]) { self.samples = samples }
}

public enum ASRAvailability: Sendable, Equatable {
    case available
    case blocked(reason: String)
}

public enum ASRError: Error, Sendable, Equatable {
    case blocked(String)
    case notImplemented
    case emptyResult
    case engineFailure(String)

    public var code: String {
        switch self {
        case .blocked: return "asr_blocked"
        case .notImplemented: return "asr_not_implemented"
        case .emptyResult: return "asr_empty"
        case .engineFailure: return "asr_engine_failure"
        }
    }
}

public protocol AudioCapture: Sendable {
    func start() async throws
    func stop() async throws -> CapturedAudio
    func cancel() async
}

public protocol TranscriptionService: Sendable {
    var engine: TranscriptionEngine { get }
    func availability() async -> ASRAvailability
    func transcribe(_ audio: CapturedAudio, language: String?, keywords: [String]) async throws -> Transcript
}

/// Local outbox record. Stored on disk; never contains credentials.
public struct OutboxRecord: Codable, Sendable, Equatable, Identifiable {
    public enum DeliveryState: String, Codable, Sendable { case pending, sending, delivered, failed }

    public var id: UUID { instruction.clientInstructionId }
    public var instruction: NewInstruction
    public var createdAt: Date
    public var status: DeliveryState
    public var attemptCount: Int
    public var nextRetryAt: Date?
    public var lastError: String?
    public var receipt: InstructionReceipt?
    /// Last polled server view (GET /instructions/{id}), including run progress.
    public var remote: Instruction?
    public var remoteStatus: InstructionStatus? { remote?.status ?? receipt?.status }

    public init(instruction: NewInstruction, createdAt: Date) {
        self.instruction = instruction
        self.createdAt = createdAt
        status = .pending
        attemptCount = 0
    }
}

/// Persistent outbox. Implementations must write atomically.
public protocol InstructionRepository: Sendable {
    /// Idempotent: enqueuing an existing id returns the stored record.
    func enqueue(_ instruction: NewInstruction, now: Date) async throws -> OutboxRecord
    /// Pending or interrupted (`sending`) records whose retry time has come, oldest first.
    func pending(now: Date) async -> [OutboxRecord]
    func markSending(_ id: UUID) async throws
    func markDelivered(_ id: UUID, receipt: InstructionReceipt) async throws
    /// `nextRetryAt == nil` means permanent failure.
    func markFailed(_ id: UUID, error: String, nextRetryAt: Date?) async throws
    func setRemote(_ id: UUID, instruction: Instruction) async throws
    func record(_ id: UUID) async -> OutboxRecord?
    /// Newest first.
    func all() async -> [OutboxRecord]
}

public protocol WatchAPIClient: Sendable {
    func submit(_ instruction: NewInstruction, idempotencyKey: UUID) async throws -> InstructionReceipt
    func instruction(id: UUID) async throws -> Instruction
    func approvals() async throws -> [Approval]
    func decide(approvalId: UUID, decision: ApprovalDecision, idempotencyKey: UUID) async throws -> Approval
    func projects() async throws -> [Project]
}

public protocol AuthSession: Sendable {
    /// Throws `APIError.unauthorized` when no credential is stored.
    func token() async throws -> String
    func store(_ token: String) async throws
    func clear() async throws
}
