import Foundation

public enum APIError: Error, Sendable, Equatable {
    /// Transport failure (no network, timeout, TLS...). Detail is a short code, never user text.
    case offline(String)
    case unauthorized
    case forbidden
    case notFound
    /// 409: idempotency or client_instruction_id reused with a different body. Never retry.
    case conflict(code: String?)
    /// 400 / 413 / 422: the request itself is invalid. Never retry.
    case invalid(status: Int, code: String?)
    case rateLimited(retryAfter: TimeInterval?)
    case server(status: Int)
    case decoding(String)
    case unexpected(status: Int)
    case insecureBaseURL

    public var isRetriable: Bool {
        switch self {
        case .offline, .rateLimited, .server: return true
        case .unauthorized, .forbidden, .notFound, .conflict, .invalid, .decoding, .unexpected, .insecureBaseURL: return false
        }
    }

    /// Stable short code for logs and the outbox `last_error`.
    public var code: String {
        switch self {
        case .offline: return "offline"
        case .unauthorized: return "unauthorized"
        case .forbidden: return "forbidden"
        case .notFound: return "not_found"
        case .conflict(let code): return code ?? "conflict"
        case .invalid(let status, let code): return code ?? "invalid_\(status)"
        case .rateLimited: return "rate_limited"
        case .server(let status): return "server_\(status)"
        case .decoding: return "decoding"
        case .unexpected(let status): return "http_\(status)"
        case .insecureBaseURL: return "insecure_base_url"
        }
    }

    /// Maps an HTTP status to an error; nil for 2xx.
    public static func from(status: Int, retryAfter: String?, problemCode: String?) -> APIError? {
        switch status {
        case 200..<300: return nil
        case 401: return .unauthorized
        case 403: return .forbidden
        case 404: return .notFound
        case 409: return .conflict(code: problemCode)
        case 400, 413, 422: return .invalid(status: status, code: problemCode)
        // shortcut: only delta-seconds Retry-After is parsed; an HTTP-date falls back to exponential backoff.
        case 429: return .rateLimited(retryAfter: retryAfter.flatMap { TimeInterval($0.trimmingCharacters(in: .whitespaces)) })
        case 408, 500..<600: return .server(status: status)
        default: return .unexpected(status: status)
        }
    }
}
