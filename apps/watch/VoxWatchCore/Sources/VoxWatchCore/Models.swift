import Foundation

// Wire models for contracts/openapi.yaml v0.3.0. Property names are the camelCase form of the
// contract's snake_case keys; VoxJSON converts them, so no per-type CodingKeys are needed.

public enum TranscriptionEngine: String, Codable, Sendable, CaseIterable {
    case whistle
    case systemDictation = "system_dictation"
    case remote
    case manual
    case mock
}

public enum InstructionStatus: String, Codable, Sendable {
    case queued
    case routing
    case needsClarification = "needs_clarification"
    case captured
    case dispatched
    case awaitingApproval = "awaiting_approval"
    case failed
    case cancelled
    /// Forward compatibility: any status this build does not know.
    case unknown

    public init(from decoder: any Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        self = InstructionStatus(rawValue: raw) ?? .unknown
    }

    /// No further status change is expected for the instruction itself.
    public var isFinal: Bool {
        switch self {
        case .dispatched, .captured, .failed, .cancelled: return true
        case .queued, .routing, .needsClarification, .awaitingApproval, .unknown: return false
        }
    }
}

/// Output of any ASR path, before it becomes a NewInstruction.
public struct Transcript: Codable, Sendable, Equatable {
    public var text: String
    public var language: String?
    public var engine: TranscriptionEngine
    public var modelVersion: String?
    public var onDevice: Bool
    public var durationMs: Int?

    public init(text: String, language: String?, engine: TranscriptionEngine, modelVersion: String?, onDevice: Bool, durationMs: Int?) {
        self.text = text
        self.language = language
        self.engine = engine
        self.modelVersion = modelVersion
        self.onDevice = onDevice
        self.durationMs = durationMs
    }
}

public struct NewInstruction: Codable, Sendable, Equatable {
    public struct Transcription: Codable, Sendable, Equatable {
        public var engine: TranscriptionEngine
        public var modelVersion: String?
        public var onDevice: Bool
        public var durationMs: Int?
    }

    public struct Client: Codable, Sendable, Equatable {
        public var appVersion: String
        public var platform: String
    }

    public var clientInstructionId: UUID
    public var source: String
    public var text: String
    public var language: String?
    public var projectHint: UUID?
    /// Preferred T3 Code machine ("mac", "vps"); nil lets the backend decide.
    public var runnerHint: String?
    public var transcription: Transcription
    public var client: Client

    /// Builds a contract-valid instruction. `id` is generated on device and reused as Idempotency-Key.
    public init(id: UUID = UUID(), transcript: Transcript, projectHint: UUID? = nil, runnerHint: String? = nil, appVersion: String) {
        clientInstructionId = id
        source = "apple_watch"
        // shortcut: contract caps text at 4000 chars; a 30 s dictation is far below, so we truncate instead of failing.
        text = String(transcript.text.trimmingCharacters(in: .whitespacesAndNewlines).prefix(4000))
        language = transcript.language.flatMap { Self.isValidLanguage($0) ? $0 : nil }
        self.projectHint = projectHint
        let runner = runnerHint?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        self.runnerHint = runner.isEmpty ? nil : String(runner.prefix(64))
        transcription = Transcription(
            engine: transcript.engine,
            modelVersion: transcript.modelVersion.map { String($0.prefix(128)) },
            onDevice: transcript.onDevice,
            durationMs: transcript.durationMs.map { min(max($0, 0), 600_000) }
        )
        client = Client(appVersion: String(appVersion.prefix(32)), platform: "watchos")
    }

    static func isValidLanguage(_ code: String) -> Bool {
        code.range(of: "^[a-z]{2}(-[A-Z]{2})?$", options: .regularExpression) != nil
    }
}

public struct InstructionReceipt: Codable, Sendable, Equatable {
    public struct Links: Codable, Sendable, Equatable {
        public var selfPath: String
        enum CodingKeys: String, CodingKey { case selfPath = "self" }
        public init(selfPath: String) { self.selfPath = selfPath }
    }

    /// Server-side instruction id (differs from client_instruction_id).
    public var instructionId: UUID
    public var status: InstructionStatus
    public var createdAt: Date
    public var links: Links

    public init(instructionId: UUID, status: InstructionStatus, createdAt: Date, links: Links) {
        self.instructionId = instructionId
        self.status = status
        self.createdAt = createdAt
        self.links = links
    }
}

public struct Instruction: Codable, Sendable, Equatable, Identifiable {
    public struct Candidate: Codable, Sendable, Equatable {
        public var projectId: UUID
        public var name: String
    }

    public var id: UUID
    public var clientInstructionId: UUID
    public var status: InstructionStatus
    /// Kept as String so new intents never break decoding.
    public var intent: String?
    public var projectId: UUID?
    public var taskId: UUID?
    public var agentRunId: UUID?
    public var approvalId: UUID?
    public var candidates: [Candidate]?
    /// e.g. ambiguous_route, unknown_runner, ambiguous_runner
    public var errorCode: String?
    public var progress: RunProgress?
    public var createdAt: Date
    public var updatedAt: Date

    public init(id: UUID, clientInstructionId: UUID, status: InstructionStatus, intent: String? = nil,
                projectId: UUID? = nil, taskId: UUID? = nil, agentRunId: UUID? = nil, approvalId: UUID? = nil,
                candidates: [Candidate]? = nil, errorCode: String? = nil, progress: RunProgress? = nil,
                createdAt: Date = Date(), updatedAt: Date = Date()) {
        self.id = id
        self.clientInstructionId = clientInstructionId
        self.status = status
        self.intent = intent
        self.projectId = projectId
        self.taskId = taskId
        self.agentRunId = agentRunId
        self.approvalId = approvalId
        self.candidates = candidates
        self.errorCode = errorCode
        self.progress = progress
        self.createdAt = createdAt
        self.updatedAt = updatedAt
    }
}

extension Instruction {
    /// Worth polling again: routing not settled, or a dispatched run still in progress.
    public var needsPolling: Bool {
        if !status.isFinal { return true }
        guard status == .dispatched, let progress else { return false }
        return !progress.runState.isTerminal
    }
}

public enum RunState: String, Codable, Sendable {
    case queued
    case provisioning
    case running
    case waitingApproval = "waiting_approval"
    case succeeded
    case failed
    case cancelled
    case timedOut = "timed_out"
    case unknown

    public init(from decoder: any Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        self = RunState(rawValue: raw) ?? .unknown
    }

    public var isTerminal: Bool {
        switch self {
        case .succeeded, .failed, .cancelled, .timedOut: return true
        case .queued, .provisioning, .running, .waitingApproval, .unknown: return false
        }
    }
}

/// Progress of the T3 Code run executing a dispatched instruction.
public struct RunProgress: Codable, Sendable, Equatable {
    public var runner: String?
    public var runState: RunState
    /// Agent-written, untrusted display text. Never log it.
    public var summary: String?
    public var prUrl: String?
    public var updatedAt: Date?

    public init(runner: String?, runState: RunState, summary: String? = nil, prUrl: String? = nil, updatedAt: Date? = nil) {
        self.runner = runner
        self.runState = runState
        self.summary = summary
        self.prUrl = prUrl
        self.updatedAt = updatedAt
    }
}

public struct Approval: Codable, Sendable, Equatable, Identifiable {
    public var id: UUID
    public var runId: UUID
    public var capability: String
    public var actionSummary: String
    public var state: String
    public var expiresAt: Date?
    public var projectId: UUID?
    /// "low" | "medium" | "high"; String for forward compatibility.
    public var risk: String?
    public var challengeToken: String?

    public init(id: UUID, runId: UUID, capability: String, actionSummary: String, state: String,
                expiresAt: Date? = nil, projectId: UUID? = nil, risk: String? = nil, challengeToken: String? = nil) {
        self.id = id
        self.runId = runId
        self.capability = capability
        self.actionSummary = actionSummary
        self.state = state
        self.expiresAt = expiresAt
        self.projectId = projectId
        self.risk = risk
        self.challengeToken = challengeToken
    }
}

public struct ApprovalDecision: Codable, Sendable, Equatable {
    public enum Kind: String, Codable, Sendable { case approved, denied }
    public var decision: Kind
    public var challengeToken: String?

    public init(decision: Kind, challengeToken: String?) {
        self.decision = decision
        self.challengeToken = challengeToken
    }
}

public struct Project: Codable, Sendable, Equatable, Identifiable {
    public var id: UUID
    public var slug: String
    public var name: String
    public var description: String?
    public var repoUrl: String?
}

public struct Problem: Codable, Sendable, Equatable {
    public var code: String
    public var message: String
    public var traceId: String?
}

/// Shared JSON coding for the wire and the local outbox file.
public enum VoxJSON {
    public static func decoder() -> JSONDecoder {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        decoder.dateDecodingStrategy = .custom { decoder in
            let container = try decoder.singleValueContainer()
            let string = try container.decode(String.self)
            guard let date = parseDate(string) else {
                throw DecodingError.dataCorruptedError(in: container, debugDescription: "Invalid ISO-8601 date")
            }
            return date
        }
        return decoder
    }

    public static func encoder() -> JSONEncoder {
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        encoder.dateEncodingStrategy = .iso8601
        encoder.outputFormatting = [.sortedKeys]
        return encoder
    }

    /// Accepts RFC 3339 with or without fractional seconds.
    static func parseDate(_ string: String) -> Date? {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = formatter.date(from: string) { return date }
        formatter.formatOptions = [.withInternetDateTime]
        return formatter.date(from: string)
    }
}
