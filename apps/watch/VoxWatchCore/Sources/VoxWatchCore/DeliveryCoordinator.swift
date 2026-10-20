import Foundation

/// Drains the outbox. Every attempt for a record uses its client_instruction_id as Idempotency-Key.
public actor DeliveryCoordinator {
    public struct Report: Sendable, Equatable {
        public var delivered: [UUID] = []
        public var permanentlyFailed: [UUID] = []
        /// Why draining stopped early (offline, 429, 5xx, 401). nil when the queue was fully processed.
        public var haltedBy: APIError?
        /// Another drain was already running.
        public var skipped = false
        public var authRequired: Bool { haltedBy == .unauthorized }
    }

    private let repository: any InstructionRepository
    private let baseDelay: TimeInterval
    private let maxDelay: TimeInterval
    private let jitter: @Sendable () -> Double
    private var isDraining = false

    /// `jitter` returns a multiplier applied to the backoff (default 0.5...1.0).
    public init(repository: any InstructionRepository, baseDelay: TimeInterval = 2, maxDelay: TimeInterval = 600,
                jitter: @escaping @Sendable () -> Double = { Double.random(in: 0.5...1.0) }) {
        self.repository = repository
        self.baseDelay = baseDelay
        self.maxDelay = maxDelay
        self.jitter = jitter
    }

    public func drain(using api: any WatchAPIClient, now: Date = Date()) async -> Report {
        var report = Report()
        guard !isDraining else {
            report.skipped = true
            return report
        }
        isDraining = true
        defer { isDraining = false }

        for record in await repository.pending(now: now) {
            let id = record.id
            do {
                try await repository.markSending(id)
                let receipt = try await api.submit(record.instruction, idempotencyKey: id)
                try await repository.markDelivered(id, receipt: receipt)
                report.delivered.append(id)
                VoxLog.event("delivery.ok", id: id, status: receipt.status.rawValue)
            } catch {
                // Non-API errors (local I/O, cancellation) are treated as transient.
                let apiError = (error as? APIError) ?? .offline("local")
                VoxLog.event("delivery.error", id: id, code: apiError.code)

                if apiError == .unauthorized {
                    // Keep it queued; nothing else can succeed until the user signs in again.
                    try? await repository.markFailed(id, error: apiError.code, nextRetryAt: now)
                    report.haltedBy = apiError
                    return report
                }
                if apiError.isRetriable {
                    let delay = retryDelay(for: apiError, attempt: record.attemptCount + 1)
                    try? await repository.markFailed(id, error: apiError.code, nextRetryAt: now.addingTimeInterval(delay))
                    report.haltedBy = apiError
                    return report
                }
                try? await repository.markFailed(id, error: apiError.code, nextRetryAt: nil)
                report.permanentlyFailed.append(id)
            }
        }
        return report
    }

    /// Exponential backoff with jitter, capped; 429 Retry-After wins when present.
    func retryDelay(for error: APIError, attempt: Int) -> TimeInterval {
        if case .rateLimited(let retryAfter?) = error { return retryAfter }
        let exponential = baseDelay * Double(1 << min(max(attempt - 1, 0), 16))
        return min(maxDelay, exponential) * jitter()
    }
}
