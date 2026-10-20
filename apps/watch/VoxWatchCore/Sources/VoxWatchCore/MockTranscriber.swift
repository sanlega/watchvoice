import Foundation

/// MOCK ASR for tests, previews and DEBUG builds. Output is labeled engine "mock" end to end,
/// so the server always knows the text did not come from real speech recognition.
public struct MockTranscriber: TranscriptionService {
    public let text: String

    public init(text: String = "[MOCK] En Musiquillas añade modo multijugador") {
        self.text = text
    }

    public var engine: TranscriptionEngine { .mock }

    public func availability() async -> ASRAvailability { .available }

    public func transcribe(_ audio: CapturedAudio, language: String?, keywords: [String]) async throws -> Transcript {
        Transcript(text: text, language: language, engine: .mock, modelVersion: "mock",
                   onDevice: true, durationMs: Int(audio.duration * 1000))
    }
}
