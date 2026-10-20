import Foundation

/// Server-side ASR via audio upload. Selectable only with explicit user consent (default OFF).
/// Not implemented: no audio ever leaves the watch in this build.
public struct RemoteTranscriber: TranscriptionService {
    public init() {}

    public var engine: TranscriptionEngine { .remote }

    public func availability() async -> ASRAvailability {
        .blocked(reason: "Remote ASR not implemented")
    }

    public func transcribe(_ audio: CapturedAudio, language: String?, keywords: [String]) async throws -> Transcript {
        throw ASRError.notImplemented
    }
}
