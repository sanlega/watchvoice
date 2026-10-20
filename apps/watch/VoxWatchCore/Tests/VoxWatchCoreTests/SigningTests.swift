#if canImport(CryptoKit)
import CryptoKit
import XCTest
@testable import VoxWatchCore

final class SigningTests: XCTestCase {
    private let deviceId = "8C1D2E3F-4A5B-4C6D-8E7F-9A0B1C2D3E4F"
    private let t0 = Date(timeIntervalSince1970: 1_792_490_460) // 2026-10-20T10:01:00Z

    private func signer(_ key: P256.Signing.PrivateKey) -> DeviceSigner {
        let fixed = t0
        return DeviceSigner(key: DeviceKey(software: key), deviceId: deviceId, now: { fixed })
    }

    private func assertValid(_ signature: String, _ message: [String: String], _ key: P256.Signing.PrivateKey,
                             file: StaticString = #filePath, line: UInt = #line) throws {
        let der = try XCTUnwrap(Data(base64Encoded: signature), file: file, line: line)
        let ecdsa = try P256.Signing.ECDSASignature(derRepresentation: der)
        XCTAssertTrue(key.publicKey.isValidSignature(ecdsa, for: Data(CanonicalJSON.encode(message).utf8)), file: file, line: line)
    }

    /// Expected string produced by Python json.dumps(m, sort_keys=True, separators=(",", ":"), ensure_ascii=False).
    func testCanonicalJSONMatchesPython() {
        let message = ["ts": "2026-10-20T10:01:00Z", "decision": "approved", "note": "aprobación",
                       "q": "a\"b\\c\n\u{01}", "approval_id": "x"]
        let expected = #"{"approval_id":"x","decision":"approved","note":"aprobación","q":"a\"b\\c\n\u0001","ts":"2026-10-20T10:01:00Z"}"#
        XCTAssertEqual(CanonicalJSON.encode(message), expected)
        XCTAssertTrue(Array(CanonicalJSON.encode(message).utf8).contains(0xC3), "non-ASCII stays raw UTF-8")
    }

    func testTimestampIsUTC() {
        XCTAssertEqual(voxTimestamp(t0), "2026-10-20T10:01:00Z")
    }

    func testDecisionSignatureVerifiesWithPublicKey() throws {
        let key = P256.Signing.PrivateKey()
        let approvalId = UUID()
        let fingerprint = String(repeating: "a", count: 64)
        let body = try signer(key).decision(approvalId: approvalId, fingerprint: fingerprint, approve: true)
        XCTAssertEqual(body.decision, .approved)
        try assertValid(body.signature, ["approval_id": approvalId.uuidString.lowercased(), "action_fingerprint": fingerprint,
                                         "decision": "approved", "device_id": deviceId.lowercased(), "ts": body.ts], key)
        XCTAssertEqual(Data(base64Encoded: DeviceKey(software: key).publicKeyX963Base64)?.count, 65)
    }

    func testMockDecideCancelPauseFlows() async throws {
        let key = P256.Signing.PrivateKey()
        let s = signer(key)
        let approval = Approval(id: UUID(), capability: "deploy.production", actionSummary: "deploy", risk: "low")
        let api = MockWatchAPIClient(approvals: [approval])

        let decision = try s.decision(approvalId: approval.id, fingerprint: approval.actionFingerprint, approve: false)
        let updated = try await api.decide(approvalId: approval.id, decision: decision, idempotencyKey: UUID())
        XCTAssertEqual(updated.state, "denied")
        let pending = try await api.approvals()
        XCTAssertTrue(pending.isEmpty)

        let runId = UUID()
        try await api.cancelRun(runId: runId, body: try s.cancel(runId: runId), idempotencyKey: UUID())
        let cancels = await api.cancels
        let cancel = try XCTUnwrap(cancels.first)
        try assertValid(cancel.body.signature, ["action": "cancel_run", "run_id": runId.uuidString.lowercased(),
                                                "device_id": deviceId.lowercased(), "ts": cancel.body.ts], key)

        let result = try await api.setPaused(try s.pause(true))
        XCTAssertTrue(result.paused)
        let pauses = await api.pauses
        let pause = try XCTUnwrap(pauses.first)
        try assertValid(pause.signature, ["action": "pause", "device_id": deviceId.lowercased(), "ts": pause.ts], key)

        await api.setNextControlError(.offline("x"))
        do {
            _ = try await api.setPaused(try s.pause(false))
            XCTFail("expected offline")
        } catch {
            XCTAssertEqual(error as? APIError, .offline("x"))
        }
    }

    func testPairingCodeNormalization() {
        XCTAssertEqual(PairingCode.normalize("ab c2-d3ef9xyz"), "ABC2D3EF")
        XCTAssertEqual(PairingCode.normalize("O0I1L"), "")
        XCTAssertTrue(PairingCode.isValid("ABC2D3EF"))
        XCTAssertFalse(PairingCode.isValid("ABC2D3E"))
    }
}
#endif
