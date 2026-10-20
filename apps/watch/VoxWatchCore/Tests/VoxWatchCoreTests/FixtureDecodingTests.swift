import XCTest
@testable import VoxWatchCore

/// Every file in contracts/fixtures (copied into Fixtures/) must decode with the app models.
final class FixtureDecodingTests: XCTestCase {
    private func fixture(_ name: String) throws -> Data {
        let url = try XCTUnwrap(Bundle.module.url(forResource: name, withExtension: "json", subdirectory: "Fixtures"), name)
        return try Data(contentsOf: url)
    }

    private func decode<T: Decodable>(_ type: T.Type, _ name: String) throws -> T {
        try VoxJSON.decoder().decode(type, from: fixture(name))
    }

    func testNewInstructionRequestRoundTripsWithSnakeCaseKeys() throws {
        let value = try decode(NewInstruction.self, "new_instruction.request")
        XCTAssertEqual(value.clientInstructionId, UUID(uuidString: "c1fd810c-5b24-47a9-acb5-81702daf8126"))
        XCTAssertEqual(value.source, "apple_watch")
        XCTAssertEqual(value.language, "es")
        XCTAssertNil(value.projectHint)
        XCTAssertEqual(value.transcription.engine, .mock)
        XCTAssertEqual(value.transcription.durationMs, 7400)
        XCTAssertTrue(value.transcription.onDevice)
        XCTAssertEqual(value.client.platform, "watchos")

        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: VoxJSON.encoder().encode(value)) as? [String: Any])
        XCTAssertNotNil(json["client_instruction_id"])
        let transcription = try XCTUnwrap(json["transcription"] as? [String: Any])
        XCTAssertEqual(transcription["on_device"] as? Bool, true)
        XCTAssertEqual(transcription["duration_ms"] as? Int, 7400)
        XCTAssertEqual(transcription["engine"] as? String, "mock")
        XCTAssertEqual((json["client"] as? [String: Any])?["app_version"] as? String, "0.1.0")
    }

    func testRunnerHintEncodesAsSnakeCase() throws {
        let transcript = Transcript(text: "hola", language: nil, engine: .systemDictation, modelVersion: nil, onDevice: false, durationMs: nil)
        let instruction = NewInstruction(transcript: transcript, runnerHint: " mac ", appVersion: "0.1.0")
        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: VoxJSON.encoder().encode(instruction)) as? [String: Any])
        XCTAssertEqual(json["runner_hint"] as? String, "mac")
        XCTAssertEqual((json["transcription"] as? [String: Any])?["engine"] as? String, "system_dictation")

        let blank = NewInstruction(transcript: transcript, runnerHint: "  ", appVersion: "0.1.0")
        XCTAssertNil(blank.runnerHint)
    }

    func testInstructionReceipt() throws {
        let value = try decode(InstructionReceipt.self, "instruction_receipt.response")
        XCTAssertEqual(value.instructionId, UUID(uuidString: "0b8f3c2e-6d0a-4b8e-9c1f-2a7d5e4b3c21"))
        XCTAssertEqual(value.status, .queued)
        XCTAssertEqual(value.links.selfPath, "/v1/instructions/0b8f3c2e-6d0a-4b8e-9c1f-2a7d5e4b3c21")
    }

    func testDispatchedInstructionWithProgress() throws {
        let value = try decode(Instruction.self, "instruction.dispatched.response")
        XCTAssertEqual(value.status, .dispatched)
        XCTAssertEqual(value.intent, "implement_feature")
        XCTAssertNotNil(value.taskId)
        XCTAssertNil(value.approvalId)
        XCTAssertEqual(value.candidates, [])
        let progress = try XCTUnwrap(value.progress)
        XCTAssertEqual(progress.runner, "mac")
        XCTAssertEqual(progress.runState, .running)
        XCTAssertNotNil(progress.summary)
        XCTAssertEqual(progress.result?.hasPrefix("Causa:"), true)
        XCTAssertNil(progress.prUrl)
        XCTAssertNotNil(progress.updatedAt)
        XCTAssertTrue(value.needsPolling, "a running run keeps being polled")
    }

    func testNeedsClarificationInstruction() throws {
        let value = try decode(Instruction.self, "instruction.needs_clarification.response")
        XCTAssertEqual(value.status, .needsClarification)
        XCTAssertEqual(value.errorCode, "ambiguous_route")
        XCTAssertEqual(value.candidates?.map(\.name), ["Musiquillas", "Hardware VOX"])
        XCTAssertNil(value.progress)
    }

    func testPendingApproval() throws {
        let value = try decode(Approval.self, "approval.pending")
        XCTAssertEqual(value.kind, "action")
        XCTAssertEqual(value.state, "pending")
        XCTAssertEqual(value.risk, "high")
        XCTAssertFalse(value.agentProvided)
        XCTAssertEqual(value.actionFingerprint.count, 64)
        XCTAssertEqual(value.runner, "vps")
        XCTAssertEqual(value.runnerTrust, "isolated")
        XCTAssertNotNil(value.runId)
    }

    func testPolicyChangeApproval() throws {
        let value = try decode(Approval.self, "approval.policy_change")
        XCTAssertEqual(value.kind, "policy_change")
        XCTAssertNil(value.runId)
        XCTAssertNil(value.projectId)
        XCTAssertEqual(value.actionSummary, "Bypass · vps · 2 h")
    }

    func testDecisionRequestDecodes() throws {
        let value = try decode(ApprovalDecision.self, "decision.request")
        XCTAssertEqual(value.decision, .approved)
        XCTAssertEqual(value.ts, "2026-10-20T10:01:00+00:00")
        XCTAssertEqual(value.actionFingerprint.count, 64)
    }

    func testPolicyRuleKeepsCapabilityKeysLiteral() throws {
        let json = #"[{"id":"5b6c7d8e-9f0a-4b1c-8d2e-4f5a6b7c8d9e","runner":"vps","project":null,"mode":"granular","effects":{"repo.merge_protected":"ask"},"source":"voice","expires_at":"2026-10-20T12:00:00Z"}]"#
        let rules = try VoxJSON.literalKeysDecoder().decode([PolicyRule].self, from: Data(json.utf8))
        XCTAssertEqual(rules.first?.effects, ["repo.merge_protected": "ask"])
        XCTAssertNotNil(rules.first?.expiresAt)
    }

    func testAppliedStatus() throws {
        XCTAssertEqual(try VoxJSON.decoder().decode(InstructionStatus.self, from: Data("\"applied\"".utf8)), .applied)
        XCTAssertTrue(InstructionStatus.applied.isFinal)
    }

    func testProblem409() throws {
        let value = try decode(Problem.self, "problem.409")
        XCTAssertEqual(value.code, "idempotency_conflict")
    }

    func testUnknownEnumValuesDecodeSafely() throws {
        let decoder = VoxJSON.decoder()
        XCTAssertEqual(try decoder.decode(InstructionStatus.self, from: Data("\"teleported\"".utf8)), .unknown)
        XCTAssertEqual(try decoder.decode(RunState.self, from: Data("\"levitating\"".utf8)), .unknown)
        XCTAssertEqual(try decoder.decode(RunState.self, from: Data("\"timed_out\"".utf8)), .timedOut)
    }

    func testFractionalSecondDates() {
        XCTAssertNotNil(VoxJSON.parseDate("2026-10-20T10:00:00.123Z"))
        XCTAssertNotNil(VoxJSON.parseDate("2026-10-20T10:00:00Z"))
        XCTAssertNil(VoxJSON.parseDate("yesterday"))
    }
}
