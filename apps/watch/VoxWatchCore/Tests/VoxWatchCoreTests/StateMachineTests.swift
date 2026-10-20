import XCTest
@testable import VoxWatchCore

final class StateMachineTests: XCTestCase {
    private typealias SM = RecordingStateMachine
    private let transcript = Transcript(text: "hola", language: "es", engine: .mock, modelVersion: nil, onDevice: true, durationMs: 1000)
    private let clientId = UUID()
    private let serverId = UUID()
    private let date = Date(timeIntervalSince1970: 1_800_000_000)

    private func receipt(_ status: InstructionStatus = .queued) -> InstructionReceipt {
        InstructionReceipt(instructionId: serverId, status: status, createdAt: date, links: .init(selfPath: "/v1/instructions/x"))
    }

    private func instruction(_ status: InstructionStatus, id: UUID? = nil, progress: RunProgress? = nil) -> Instruction {
        Instruction(id: id ?? serverId, clientInstructionId: clientId, status: status, errorCode: status == .failed ? "boom" : nil,
                    progress: progress, createdAt: date, updatedAt: date)
    }

    func testHappyPathUpToReceipt() {
        var s = SM.reduce(.idle, .startTapped(date))
        XCTAssertEqual(s, .recording(startedAt: date))
        s = SM.reduce(s, .stopTapped)
        XCTAssertEqual(s, .transcribing)
        s = SM.reduce(s, .transcribed(transcript))
        XCTAssertEqual(s, .reviewing(transcript))
        s = SM.reduce(s, .saved(clientId))
        XCTAssertEqual(s, .savedLocally(clientId))
        s = SM.reduce(s, .sendStarted)
        XCTAssertEqual(s, .sending(clientId))
        s = SM.reduce(s, .receiptReceived(receipt()))
        XCTAssertEqual(s, .received(receipt()))
    }

    func testReceiptNeverMeansTaskStarted() {
        // Even a 202 receipt claiming "dispatched" only means "received".
        let claimed = receipt(.dispatched)
        var s = SM.reduce(.sending(clientId), .receiptReceived(claimed))
        XCTAssertEqual(s, .received(claimed))

        s = SM.reduce(s, .instructionUpdated(instruction(.queued)))
        XCTAssertEqual(s, .received(claimed))
        s = SM.reduce(s, .instructionUpdated(instruction(.routing)))
        XCTAssertEqual(s, .received(claimed))

        let dispatched = instruction(.dispatched)
        XCTAssertEqual(SM.reduce(s, .instructionUpdated(dispatched)), .taskStarted(dispatched))
    }

    func testProgressUpdatesStayInTaskStarted() {
        let running = instruction(.dispatched, progress: RunProgress(runner: "mac", runState: .running))
        let done = instruction(.dispatched, progress: RunProgress(runner: "mac", runState: .succeeded, prUrl: "https://example.com/pr/1"))
        let s = SM.reduce(.taskStarted(running), .instructionUpdated(done))
        XCTAssertEqual(s, .taskStarted(done))
        XCTAssertFalse(done.needsPolling)
        XCTAssertTrue(running.needsPolling)
    }

    func testUpdatesForOtherInstructionsAreIgnored() {
        let s = FlowState.received(receipt())
        XCTAssertEqual(SM.reduce(s, .instructionUpdated(instruction(.dispatched, id: UUID()))), s)
    }

    func testStatusMapping() {
        let r = FlowState.received(receipt())
        let review = instruction(.needsClarification)
        XCTAssertEqual(SM.reduce(r, .instructionUpdated(review)), .needsReview(review))
        let gated = instruction(.awaitingApproval)
        XCTAssertEqual(SM.reduce(r, .instructionUpdated(gated)), .awaitingApproval(gated))
        let idea = instruction(.captured)
        XCTAssertEqual(SM.reduce(r, .instructionUpdated(idea)), .captured(idea))
        XCTAssertEqual(SM.reduce(r, .instructionUpdated(instruction(.failed))), .failed(.server(code: "boom")))
        // Approval granted later → task starts.
        let dispatched = instruction(.dispatched)
        XCTAssertEqual(SM.reduce(.awaitingApproval(gated), .instructionUpdated(dispatched)), .taskStarted(dispatched))
    }

    func testDeferredDeliveryReturnsToSavedLocally() {
        XCTAssertEqual(SM.reduce(.sending(clientId), .deferred), .savedLocally(clientId))
        XCTAssertEqual(SM.reduce(.sending(clientId), .failed(.rejected(code: "idempotency_conflict"))),
                       .failed(.rejected(code: "idempotency_conflict")))
    }

    func testCancelRetakeAndReset() {
        XCTAssertEqual(SM.reduce(.recording(startedAt: date), .cancelTapped), .idle)
        XCTAssertEqual(SM.reduce(.reviewing(transcript), .retake), .idle)
        XCTAssertEqual(SM.reduce(.failed(.capture("x")), .reset), .idle)
        XCTAssertEqual(SM.reduce(.idle, .transcribed(transcript)), .reviewing(transcript), "dictation path")
    }

    func testInvalidTransitionsKeepState() {
        XCTAssertEqual(SM.reduce(.idle, .stopTapped), .idle)
        XCTAssertEqual(SM.reduce(.idle, .sendStarted), .idle)
        XCTAssertEqual(SM.reduce(.idle, .receiptReceived(receipt())), .idle)
        XCTAssertEqual(SM.reduce(.reviewing(transcript), .startTapped(date)), .reviewing(transcript))
        XCTAssertEqual(SM.reduce(.recording(startedAt: date), .receiptReceived(receipt())), .recording(startedAt: date))
        XCTAssertEqual(SM.reduce(.sending(clientId), .instructionUpdated(instruction(.dispatched))), .sending(clientId))
        XCTAssertEqual(SM.reduce(.received(receipt()), .receiptReceived(receipt(.routing))), .received(receipt()))
    }
}
