import Foundation

/// Byte-for-byte equal to Python `json.dumps(m, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`,
/// which is what backend/app/devicesig.py verifies. Written by hand: JSONEncoder ordering/escaping differ.
public enum CanonicalJSON {
    public static func encode(_ message: [String: String]) -> String {
        // Python sorts str keys by code point.
        let keys = message.keys.sorted { $0.unicodeScalars.lexicographicallyPrecedes($1.unicodeScalars) }
        return "{" + keys.map { quote($0) + ":" + quote(message[$0] ?? "") }.joined(separator: ",") + "}"
    }

    static func quote(_ string: String) -> String {
        var out = "\""
        for scalar in string.unicodeScalars {
            switch scalar {
            case "\"": out += "\\\""
            case "\\": out += "\\\\"
            case "\n": out += "\\n"
            case "\r": out += "\\r"
            case "\t": out += "\\t"
            case "\u{08}": out += "\\b"
            case "\u{0C}": out += "\\f"
            case _ where scalar.value < 0x20: out += String(format: "\\u%04x", scalar.value)
            default: out.unicodeScalars.append(scalar)
            }
        }
        return out + "\""
    }
}

/// ISO-8601 UTC ("...Z"); the server accepts ±2 min of skew.
public func voxTimestamp(_ date: Date = Date()) -> String {
    ISO8601DateFormatter().string(from: date)
}

#if canImport(CryptoKit) && canImport(Security)
import CryptoKit
import Security

/// The watch's P-256 signing key: Secure Enclave when available, else a software key (simulator).
/// Both are stored in the Keychain as WhenPasscodeSetThisDeviceOnly: no passcode, no key.
public struct DeviceKey {
    public struct KeychainError: Error, Equatable {
        public let status: OSStatus
    }

    private enum Backing {
        case enclave(SecureEnclave.P256.Signing.PrivateKey)
        case software(P256.Signing.PrivateKey)
    }

    private let backing: Backing

    /// Tests and previews.
    public init(software key: P256.Signing.PrivateKey) {
        backing = .software(key)
    }

    /// Loads the stored key or creates and stores a new one.
    public static func loadOrCreate(service: String) throws -> DeviceKey {
        if SecureEnclave.isAvailable {
            let account = "signing-key-se"
            // The enclave blob is only usable on this device's Secure Enclave.
            if let data = try read(service: service, account: account) {
                return DeviceKey(backing: .enclave(try SecureEnclave.P256.Signing.PrivateKey(dataRepresentation: data)))
            }
            let key = try SecureEnclave.P256.Signing.PrivateKey()
            try write(key.dataRepresentation, service: service, account: account)
            return DeviceKey(backing: .enclave(key))
        }
        let account = "signing-key-sw"
        if let data = try read(service: service, account: account) {
            return DeviceKey(backing: .software(try P256.Signing.PrivateKey(rawRepresentation: data)))
        }
        let key = P256.Signing.PrivateKey()
        try write(key.rawRepresentation, service: service, account: account)
        return DeviceKey(backing: .software(key))
    }

    private init(backing: Backing) {
        self.backing = backing
    }

    public var publicKey: P256.Signing.PublicKey {
        switch backing {
        case .enclave(let key): return key.publicKey
        case .software(let key): return key.publicKey
        }
    }

    /// Sent as `public_key` when pairing.
    public var publicKeyX963Base64: String {
        publicKey.x963Representation.base64EncodedString()
    }

    /// base64 DER ECDSA P-256/SHA-256 over the canonical JSON of `message`.
    public func sign(_ message: [String: String]) throws -> String {
        let data = Data(CanonicalJSON.encode(message).utf8)
        switch backing {
        case .enclave(let key): return try key.signature(for: data).derRepresentation.base64EncodedString()
        case .software(let key): return try key.signature(for: data).derRepresentation.base64EncodedString()
        }
    }

    private static func query(service: String, account: String) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service,
         kSecAttrAccount as String: account]
    }

    private static func read(service: String, account: String) throws -> Data? {
        var query = Self.query(service: service, account: account)
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess else { throw KeychainError(status: status) }
        return item as? Data
    }

    private static func write(_ data: Data, service: String, account: String) throws {
        var attributes = Self.query(service: service, account: account)
        SecItemDelete(attributes as CFDictionary)
        attributes[kSecValueData as String] = data
        attributes[kSecAttrAccessible as String] = kSecAttrAccessibleWhenPasscodeSetThisDeviceOnly
        let status = SecItemAdd(attributes as CFDictionary, nil)
        guard status == errSecSuccess else { throw KeychainError(status: status) }
    }
}

/// Builds the device-signed request bodies. Message keys must match backend/app/control.py exactly.
public struct DeviceSigner {
    public let key: DeviceKey
    /// From PairResponse; lowercased like Python `str(uuid)`.
    public let deviceId: String
    public var now: () -> Date

    public init(key: DeviceKey, deviceId: String, now: @escaping () -> Date = { Date() }) {
        self.key = key
        self.deviceId = deviceId.lowercased()
        self.now = now
    }

    public func decision(approvalId: UUID, fingerprint: String, approve: Bool) throws -> ApprovalDecision {
        let ts = voxTimestamp(now())
        let decision: ApprovalDecision.Kind = approve ? .approved : .denied
        let signature = try key.sign(["approval_id": approvalId.uuidString.lowercased(), "action_fingerprint": fingerprint,
                                      "decision": decision.rawValue, "device_id": deviceId, "ts": ts])
        return ApprovalDecision(ts: ts, signature: signature, decision: decision, actionFingerprint: fingerprint)
    }

    public func cancel(runId: UUID) throws -> SignedBody {
        let ts = voxTimestamp(now())
        let signature = try key.sign(["action": "cancel_run", "run_id": runId.uuidString.lowercased(), "device_id": deviceId, "ts": ts])
        return SignedBody(ts: ts, signature: signature)
    }

    public func pause(_ paused: Bool) throws -> PauseRequest {
        let ts = voxTimestamp(now())
        let signature = try key.sign(["action": paused ? "pause" : "resume", "device_id": deviceId, "ts": ts])
        return PauseRequest(ts: ts, signature: signature, paused: paused)
    }
}
#endif
