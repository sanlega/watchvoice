import Foundation

// Wire models for contracts/openapi.yaml v0.4.0. Property names are the camelCase form of the
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
    /// A voice policy change took effect.
    case applied
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
        case .dispatched, .captured, .applied, .failed, .cancelled: return true
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
    /// v0.6: answer inside that run's T3 conversation instead of opening a new session.
    public var replyToRunId: UUID?
    public var transcription: Transcription
    public var client: Client

    /// Builds a contract-valid instruction. `id` is generated on device and reused as Idempotency-Key.
    public init(id: UUID = UUID(), transcript: Transcript, projectHint: UUID? = nil, runnerHint: String? = nil,
                replyToRunId: UUID? = nil, appVersion: String) {
        self.replyToRunId = replyToRunId
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
    /// e.g. ambiguous_route, unknown_runner, ambiguous_runner, policy_denied, policy_scope_missing
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
    /// v0.8: full agent answer (≤4000 chars). Agent-written, untrusted display text. Never log it.
    public var result: String?
    public var prUrl: String?
    public var updatedAt: Date?

    public init(runner: String?, runState: RunState, summary: String? = nil, result: String? = nil,
                prUrl: String? = nil, updatedAt: Date? = nil) {
        self.runner = runner
        self.runState = runState
        self.summary = summary
        self.result = result
        self.prUrl = prUrl
        self.updatedAt = updatedAt
    }
}

public struct Approval: Codable, Sendable, Equatable, Identifiable {
    public var id: UUID
    /// "action" | "policy_change" | "agent_request"; String for forward compatibility.
    public var kind: String
    public var runId: UUID?
    public var capability: String
    /// Untrusted when `agentProvided` is true. Never log it.
    public var actionSummary: String
    public var agentProvided: Bool
    public var state: String
    /// "low" | "medium" | "high"
    public var risk: String
    public var expiresAt: Date
    /// Signed back verbatim with the decision.
    public var actionFingerprint: String
    public var projectId: UUID?
    public var runner: String?
    /// "isolated" | "cooperative"
    public var runnerTrust: String?

    public init(id: UUID, kind: String = "action", runId: UUID? = nil, capability: String, actionSummary: String,
                agentProvided: Bool = false, state: String = "pending", risk: String = "low",
                expiresAt: Date = Date().addingTimeInterval(900), actionFingerprint: String = String(repeating: "0", count: 64),
                projectId: UUID? = nil, runner: String? = nil, runnerTrust: String? = nil) {
        self.id = id
        self.kind = kind
        self.runId = runId
        self.capability = capability
        self.actionSummary = actionSummary
        self.agentProvided = agentProvided
        self.state = state
        self.risk = risk
        self.expiresAt = expiresAt
        self.actionFingerprint = actionFingerprint
        self.projectId = projectId
        self.runner = runner
        self.runnerTrust = runnerTrust
    }
}

/// Device-signed bodies (ADR-0001 §5). Build them with `DeviceSigner`.
public struct ApprovalDecision: Codable, Sendable, Equatable {
    public enum Kind: String, Codable, Sendable { case approved, denied }
    public var ts: String
    public var signature: String
    public var decision: Kind
    public var actionFingerprint: String
}

public struct SignedBody: Codable, Sendable, Equatable {
    public var ts: String
    public var signature: String
}

public struct PauseRequest: Codable, Sendable, Equatable {
    public var ts: String
    public var signature: String
    public var paused: Bool
}

public struct PauseResult: Codable, Sendable, Equatable {
    public var paused: Bool
    public var cancelledRuns: Int?
}

public struct PairRequest: Codable, Sendable, Equatable {
    public var code: String
    /// base64 X9.63 P-256 public key
    public var publicKey: String
    public var name: String

    public init(code: String, publicKey: String, name: String) {
        self.code = code
        self.publicKey = publicKey
        self.name = name
    }
}

public struct PairResponse: Codable, Sendable, Equatable {
    public var deviceId: UUID
    public var token: String
}

public struct PushTokenRequest: Codable, Sendable, Equatable {
    public var token: String
    /// "development" | "production"
    public var environment: String

    public init(token: String, environment: String) {
        self.token = token
        self.environment = environment
    }
}

/// Read-only on the watch. Decoded with `VoxJSON.literalKeysDecoder()` so capability keys in
/// `effects` (e.g. "repo.merge_protected") are not camel-cased.
public struct PolicyRule: Codable, Sendable, Equatable, Identifiable {
    public var id: UUID
    public var runner: String?
    public var project: String?
    /// "bypass" | "granular"
    public var mode: String
    /// capability -> "allow" | "ask" | "deny"
    public var effects: [String: String]
    public var source: String
    public var expiresAt: Date?

    enum CodingKeys: String, CodingKey {
        case id, runner, project, mode, effects, source
        case expiresAt = "expires_at"
    }

    public init(id: UUID, runner: String?, project: String?, mode: String, effects: [String: String], source: String, expiresAt: Date?) {
        self.id = id
        self.runner = runner
        self.project = project
        self.mode = mode
        self.effects = effects
        self.source = source
        self.expiresAt = expiresAt
    }
}

/// Pairing codes from `python -m app.admin pair`: 8 chars, no 0/O/1/I/L.
public enum PairingCode {
    public static let alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"

    /// Uppercases, drops characters outside the alphabet and caps at 8.
    public static func normalize(_ input: String) -> String {
        String(input.uppercased().filter { alphabet.contains($0) }.prefix(8))
    }

    public static func isValid(_ code: String) -> Bool {
        code.count == 8 && normalize(code) == code
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

    /// Same dates as `decoder()` but keys are taken literally (types declare their own CodingKeys).
    public static func literalKeysDecoder() -> JSONDecoder {
        let decoder = VoxJSON.decoder()
        decoder.keyDecodingStrategy = .useDefaultKeys
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
