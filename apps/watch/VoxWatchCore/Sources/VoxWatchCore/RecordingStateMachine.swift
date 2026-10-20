import Foundation

public enum FlowError: Sendable, Equatable {
    /// Mic/permission problem. Associated values are short codes, never user text.
    case capture(String)
    case transcription(String)
    /// Server permanently rejected the instruction (409/413/422/403...).
    case rejected(code: String)
    /// Instruction failed or was cancelled server-side.
    case server(code: String?)
}

public enum FlowState: Sendable, Equatable {
    case idle
    case recording(startedAt: Date)
    case transcribing
    case reviewing(Transcript)
    case savedLocally(UUID)
    case sending(UUID)
    /// 202: durably stored by the server. Says nothing about execution.
    case received(InstructionReceipt)
    case taskStarted(Instruction)
    case needsReview(Instruction)
    case awaitingApproval(Instruction)
    case captured(Instruction)
    /// A voice policy change took effect.
    case applied(Instruction)
    case failed(FlowError)
}

public enum FlowEvent: Sendable {
    case startTapped(Date)
    case stopTapped
    case cancelTapped
    case transcribed(Transcript)
    case failed(FlowError)
    case retake
    case saved(UUID)
    case sendStarted
    /// Delivery postponed (offline, 429, 5xx, 401); the instruction stays in the outbox.
    case deferred
    case receiptReceived(InstructionReceipt)
    case instructionUpdated(Instruction)
    case reset
}

/// Pure UI flow. Invalid transitions return the current state unchanged.
public enum RecordingStateMachine {
    public static func reduce(_ state: FlowState, _ event: FlowEvent) -> FlowState {
        switch (state, event) {
        case (_, .reset):
            return .idle
        case (.idle, .startTapped(let at)):
            return .recording(startedAt: at)
        case (.recording, .stopTapped):
            return .transcribing
        case (.recording, .cancelTapped):
            return .idle
        // Dictation can produce a transcript from idle or replace the one under review.
        case (.idle, .transcribed(let t)), (.transcribing, .transcribed(let t)), (.reviewing, .transcribed(let t)):
            return .reviewing(t)
        case (.idle, .failed(let e)), (.recording, .failed(let e)), (.transcribing, .failed(let e)),
             (.savedLocally, .failed(let e)), (.sending, .failed(let e)):
            return .failed(e)
        case (.reviewing, .retake):
            return .idle
        case (.reviewing, .saved(let id)):
            return .savedLocally(id)
        case (.savedLocally(let id), .sendStarted):
            return .sending(id)
        case (.sending(let id), .deferred):
            return .savedLocally(id)
        case (.savedLocally, .receiptReceived(let r)), (.sending, .receiptReceived(let r)):
            return .received(r)
        case (_, .instructionUpdated(let instruction)):
            guard trackedInstructionId(state) == instruction.id else { return state }
            return progress(state, instruction)
        default:
            return state
        }
    }

    private static func trackedInstructionId(_ state: FlowState) -> UUID? {
        switch state {
        case .received(let receipt): return receipt.instructionId
        case .taskStarted(let i), .needsReview(let i), .awaitingApproval(let i), .captured(let i), .applied(let i): return i.id
        default: return nil
        }
    }

    /// The only way to reach `taskStarted` is a polled Instruction with status `dispatched`.
    private static func progress(_ state: FlowState, _ instruction: Instruction) -> FlowState {
        switch instruction.status {
        case .dispatched: return .taskStarted(instruction)
        case .needsClarification: return .needsReview(instruction)
        case .awaitingApproval: return .awaitingApproval(instruction)
        case .captured: return .captured(instruction)
        case .applied: return .applied(instruction)
        case .failed, .cancelled: return .failed(.server(code: instruction.errorCode))
        case .queued, .routing, .unknown: return state
        }
    }
}
