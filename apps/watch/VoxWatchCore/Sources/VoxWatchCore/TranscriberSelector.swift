import Foundation

public enum ASRRoute: Sendable {
    /// Record audio and run this service (off the main actor).
    case service(any TranscriptionService)
    /// No audio capture by us: the UI shows a TextField and the system dictation sheet.
    case systemDictation

    public var engine: TranscriptionEngine {
        switch self {
        case .service(let service): return service.engine
        case .systemDictation: return .systemDictation
        }
    }
}

/// Order: [mock if DEBUG and enabled] → Whistle (if preferred and available)
/// → remote (only with explicit audio consent and available) → system dictation (always).
public struct TranscriberSelector: Sendable {
    public struct Settings: Sendable, Equatable {
        public var preferLocal: Bool
        public var remoteAudioConsent: Bool
        /// Honored only in DEBUG builds.
        public var useMock: Bool

        public init(preferLocal: Bool = true, remoteAudioConsent: Bool = false, useMock: Bool = false) {
            self.preferLocal = preferLocal
            self.remoteAudioConsent = remoteAudioConsent
            self.useMock = useMock
        }
    }

    private let whistle: any TranscriptionService
    private let remote: any TranscriptionService
    private let mock: (any TranscriptionService)?

    public init(whistle: any TranscriptionService, remote: any TranscriptionService, mock: (any TranscriptionService)?) {
        self.whistle = whistle
        self.remote = remote
        self.mock = mock
    }

    public func select(_ settings: Settings) async -> ASRRoute {
        #if DEBUG
        if settings.useMock, let mock { return .service(mock) }
        #endif
        if settings.preferLocal, await whistle.availability() == .available { return .service(whistle) }
        if settings.remoteAudioConsent, await remote.availability() == .available { return .service(remote) }
        return .systemDictation
    }
}
