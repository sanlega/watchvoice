import Foundation

public struct URLSessionWatchAPIClient: WatchAPIClient {
    private let baseURL: URL
    private let auth: any AuthSession
    private let session: URLSession

    /// `baseURL` includes the version prefix, e.g. https://vox.example.com/v1
    public init(baseURL: URL, auth: any AuthSession, session: URLSession = .shared) throws {
        guard Self.isAllowed(baseURL) else { throw APIError.insecureBaseURL }
        self.baseURL = baseURL
        self.auth = auth
        self.session = session
    }

    /// HTTPS only; plain HTTP is accepted for localhost development.
    public static func isAllowed(_ url: URL) -> Bool {
        guard let host = url.host, !host.isEmpty else { return false }
        switch url.scheme?.lowercased() {
        case "https": return true
        case "http": return ["localhost", "127.0.0.1", "::1"].contains(host.lowercased())
        default: return false
        }
    }

    public func submit(_ instruction: NewInstruction, idempotencyKey: UUID) async throws -> InstructionReceipt {
        let body = try VoxJSON.encoder().encode(instruction)
        return try await send("POST", baseURL.appendingPathComponent("instructions"), body: body, idempotencyKey: idempotencyKey)
    }

    public func instruction(id: UUID) async throws -> Instruction {
        try await send("GET", baseURL.appendingPathComponent("instructions/\(id.uuidString.lowercased())"))
    }

    public func approvals() async throws -> [Approval] {
        var components = URLComponents(url: baseURL.appendingPathComponent("approvals"), resolvingAgainstBaseURL: false)
        components?.queryItems = [URLQueryItem(name: "status", value: "pending")]
        guard let url = components?.url else { throw APIError.insecureBaseURL }
        return try await send("GET", url)
    }

    public func decide(approvalId: UUID, decision: ApprovalDecision, idempotencyKey: UUID) async throws -> Approval {
        let body = try VoxJSON.encoder().encode(decision)
        let url = baseURL.appendingPathComponent("approvals/\(approvalId.uuidString.lowercased())/decision")
        return try await send("POST", url, body: body, idempotencyKey: idempotencyKey)
    }

    public func projects() async throws -> [Project] {
        try await send("GET", baseURL.appendingPathComponent("projects"))
    }

    public func pair(_ request: PairRequest) async throws -> PairResponse {
        try await send("POST", baseURL.appendingPathComponent("devices/pair"), body: VoxJSON.encoder().encode(request), authorized: false)
    }

    public func registerPushToken(_ request: PushTokenRequest) async throws {
        _ = try await data("PUT", baseURL.appendingPathComponent("devices/me/push-token"), body: VoxJSON.encoder().encode(request))
    }

    public func cancelRun(runId: UUID, body: SignedBody, idempotencyKey: UUID) async throws {
        let url = baseURL.appendingPathComponent("runs/\(runId.uuidString.lowercased())/cancel")
        _ = try await data("POST", url, body: VoxJSON.encoder().encode(body), idempotencyKey: idempotencyKey)
    }

    public func setPaused(_ request: PauseRequest) async throws -> PauseResult {
        try await send("POST", baseURL.appendingPathComponent("pause"), body: VoxJSON.encoder().encode(request))
    }

    public func policy() async throws -> [PolicyRule] {
        let body = try await data("GET", baseURL.appendingPathComponent("policy"))
        do {
            return try VoxJSON.literalKeysDecoder().decode([PolicyRule].self, from: body)
        } catch {
            throw APIError.decoding("PolicyRule")
        }
    }

    private func send<T: Decodable>(_ method: String, _ url: URL, body: Data? = nil, idempotencyKey: UUID? = nil,
                                    authorized: Bool = true) async throws -> T {
        let raw = try await data(method, url, body: body, idempotencyKey: idempotencyKey, authorized: authorized)
        do {
            return try VoxJSON.decoder().decode(T.self, from: raw)
        } catch {
            throw APIError.decoding(String(describing: T.self))
        }
    }

    /// Performs the request and maps non-2xx to APIError; returns the raw body.
    private func data(_ method: String, _ url: URL, body: Data? = nil, idempotencyKey: UUID? = nil,
                      authorized: Bool = true) async throws -> Data {
        var request = URLRequest(url: url, timeoutInterval: 20)
        request.httpMethod = method
        if authorized {
            let token = try await auth.token()
            request.setValue("Bearer " + token, forHTTPHeaderField: "Authorization")
        }
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if let body {
            request.httpBody = body
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        if let idempotencyKey {
            request.setValue(idempotencyKey.uuidString.lowercased(), forHTTPHeaderField: "Idempotency-Key")
        }

        let (data, response) = try await perform(request)
        guard let http = response as? HTTPURLResponse else { throw APIError.offline("no_http_response") }
        if !(200..<300).contains(http.statusCode) {
            let problemCode = (try? VoxJSON.decoder().decode(Problem.self, from: data))?.code
            throw APIError.from(status: http.statusCode, retryAfter: http.value(forHTTPHeaderField: "Retry-After"), problemCode: problemCode)
                ?? .unexpected(status: http.statusCode)
        }
        return data
    }

    private func perform(_ request: URLRequest) async throws -> (Data, URLResponse) {
        do {
            return try await session.data(for: request)
        } catch let error as URLError {
            throw APIError.offline("urlerror_\(error.code.rawValue)")
        } catch {
            throw APIError.offline("transport")
        }
    }
}
