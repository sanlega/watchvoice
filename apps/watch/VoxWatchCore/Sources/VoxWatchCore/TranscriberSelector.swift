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

/// Order: [mock if DEBUG and enabled] → Whistle (primary: if preferred and available)
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

    public static let whistleUnavailableNotice = "Whistle no disponible: usa el dictado del sistema"
    public static let whistleFailedNotice = "Whistle falló: usa el dictado del sistema"

    /// Whistle is the primary engine: non-nil when the user wants it but `route` is a fallback.
    public static func fallbackNotice(for route: ASRRoute, settings: Settings) -> String? {
        guard settings.preferLocal, route.engine == .systemDictation || route.engine == .remote else { return nil }
        return whistleUnavailableNotice
    }

    /// A transcription that threw after recording. Whistle falls back to system dictation (the user
    /// dictates again; no text is ever made up); any other engine just reports the failure.
    public static func fallbackNotice(afterFailureOf engine: TranscriptionEngine) -> String? {
        engine == .whistle ? whistleFailedNotice : nil
    }
}
