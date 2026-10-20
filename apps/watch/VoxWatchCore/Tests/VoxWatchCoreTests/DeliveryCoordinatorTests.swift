import XCTest
@testable import VoxWatchCore

final class DeliveryCoordinatorTests: XCTestCase {
    private let t0 = Date(timeIntervalSince1970: 1_800_000_000)

    private func makeInstruction() -> NewInstruction {
        let transcript = Transcript(text: "hola", language: "es", engine: .mock, modelVersion: nil, onDevice: true, durationMs: 1000)
        return NewInstruction(transcript: transcript, appVersion: "test")
    }

    private func receipt() -> InstructionReceipt {
        InstructionReceipt(instructionId: UUID(), status: .queued, createdAt: t0, links: .init(selfPath: "/v1/instructions/x"))
    }

    private func makeCoordinator(_ repo: FileInstructionRepository) -> DeliveryCoordinator {
        DeliveryCoordinator(repository: repo, baseDelay: 2, maxDelay: 60, jitter: { 1 })
    }

    func testRetriesReuseTheSameIdempotencyKey() async throws {
        let repo = FileInstructionRepository.inMemory()
        let api = MockWatchAPIClient(submitResults: [.failure(.server(status: 503)), .failure(.offline("x")), .success(receipt())])
        let coordinator = makeCoordinator(repo)
        let instruction = makeInstruction()
        _ = try await repo.enqueue(instruction, now: t0)

        var now = t0
        for _ in 0..<3 {
            _ = await coordinator.drain(using: api, now: now)
            now = now.addingTimeInterval(3600)
        }

        let keys = await api.submittedKeys
        XCTAssertEqual(keys, Array(repeating: instruction.clientInstructionId, count: 3))
        let record = await repo.record(instruction.clientInstructionId)
        XCTAssertEqual(record?.status, .delivered)
        XCTAssertEqual(record?.attemptCount, 3)
        XCTAssertNotNil(record?.receipt)
    }

    func testExponentialBackoffSchedulesRetries() async throws {
        let repo = FileInstructionRepository.inMemory()
        let api = MockWatchAPIClient(submitResults: [.failure(.server(status: 500)), .failure(.server(status: 500))])
        let coordinator = makeCoordinator(repo)
        let id = try await repo.enqueue(makeInstruction(), now: t0).id

        _ = await coordinator.drain(using: api, now: t0)
        var nextRetry = await repo.record(id)?.nextRetryAt
        XCTAssertEqual(nextRetry, t0.addingTimeInterval(2))

        // Too early: nothing is sent.
        _ = await coordinator.drain(using: api, now: t0.addingTimeInterval(1))
        var count = await api.submittedKeys.count
        XCTAssertEqual(count, 1)

        _ = await coordinator.drain(using: api, now: t0.addingTimeInterval(2))
        count = await api.submittedKeys.count
        XCTAssertEqual(count, 2)
        nextRetry = await repo.record(id)?.nextRetryAt
        XCTAssertEqual(nextRetry, t0.addingTimeInterval(2 + 4))
    }

    func testConflictAndUnprocessableArePermanentWithoutRetry() async throws {
        for error in [APIError.conflict(code: "idempotency_conflict"), .invalid(status: 422, code: nil), .invalid(status: 413, code: nil)] {
            let repo = FileInstructionRepository.inMemory()
            let api = MockWatchAPIClient(submitResults: [.failure(error)])
            let coordinator = makeCoordinator(repo)
            let id = try await repo.enqueue(makeInstruction(), now: t0).id

            let report = await coordinator.drain(using: api, now: t0)
            XCTAssertEqual(report.permanentlyFailed, [id])
            XCTAssertNil(report.haltedBy)
            let record = await repo.record(id)
            XCTAssertEqual(record?.status, .failed)
            XCTAssertNil(record?.nextRetryAt)

            _ = await coordinator.drain(using: api, now: t0.addingTimeInterval(86_400))
            let count = await api.submittedKeys.count
            XCTAssertEqual(count, 1, "\(error) must not be retried")
        }
    }

    func testRateLimitHonorsRetryAfter() async throws {
        let repo = FileInstructionRepository.inMemory()
        let api = MockWatchAPIClient(submitResults: [.failure(.rateLimited(retryAfter: 120))])
        let coordinator = makeCoordinator(repo)
        let id = try await repo.enqueue(makeInstruction(), now: t0).id

        let report = await coordinator.drain(using: api, now: t0)
        XCTAssertEqual(report.haltedBy, .rateLimited(retryAfter: 120))
        let nextRetry = await repo.record(id)?.nextRetryAt
        XCTAssertEqual(nextRetry, t0.addingTimeInterval(120))

        _ = await coordinator.drain(using: api, now: t0.addingTimeInterval(119))
        var count = await api.submittedKeys.count
        XCTAssertEqual(count, 1)

        let later = await coordinator.drain(using: api, now: t0.addingTimeInterval(120))
        count = await api.submittedKeys.count
        XCTAssertEqual(count, 2)
        XCTAssertEqual(later.delivered, [id])
    }

    func testOfflineStaysPendingThenDeliversWhenOnline() async throws {
        let repo = FileInstructionRepository.inMemory()
        let api = MockWatchAPIClient(submitResults: [.failure(.offline("no_network"))])
        let coordinator = makeCoordinator(repo)
        let id = try await repo.enqueue(makeInstruction(), now: t0).id

        let offline = await coordinator.drain(using: api, now: t0)
        XCTAssertEqual(offline.haltedBy, .offline("no_network"))
        var status = await repo.record(id)?.status
        XCTAssertEqual(status, .pending)

        let online = await coordinator.drain(using: api, now: t0.addingTimeInterval(60))
        XCTAssertEqual(online.delivered, [id])
        status = await repo.record(id)?.status
        XCTAssertEqual(status, .delivered)
    }

    func testUnauthorizedHaltsDrainingAndKeepsEverythingQueued() async throws {
        let repo = FileInstructionRepository.inMemory()
        let api = MockWatchAPIClient(submitResults: [.failure(.unauthorized)])
        let coordinator = makeCoordinator(repo)
        let first = try await repo.enqueue(makeInstruction(), now: t0).id
        let second = try await repo.enqueue(makeInstruction(), now: t0.addingTimeInterval(1)).id

        let report = await coordinator.drain(using: api, now: t0.addingTimeInterval(2))
        XCTAssertTrue(report.authRequired)
        let count = await api.submittedKeys.count
        XCTAssertEqual(count, 1)
        let statuses = [await repo.record(first)?.status, await repo.record(second)?.status]
        XCTAssertEqual(statuses, [.pending, .pending])
    }

    func testOutboxSurvivesRelaunch() async throws {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString)
            .appendingPathComponent("outbox.json")
        defer { try? FileManager.default.removeItem(at: url.deletingLastPathComponent()) }

        let instruction = makeInstruction()
        _ = try await FileInstructionRepository(fileURL: url).enqueue(instruction, now: t0)

        let reopened = FileInstructionRepository(fileURL: url)
        let record = await reopened.record(instruction.clientInstructionId)
        XCTAssertEqual(record?.status, .pending)
        XCTAssertEqual(record?.instruction, instruction)
    }
}
