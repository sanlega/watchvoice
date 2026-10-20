import Foundation

/// Outbox persisted as one JSON file, rewritten atomically on every change.
/// `fileURL == nil` keeps everything in memory (tests/previews, see `inMemory()`).
public actor FileInstructionRepository: InstructionRepository {
    public enum RepositoryError: Error { case notFound }

    private let fileURL: URL?
    private var records: [OutboxRecord]

    public init(fileURL: URL?) {
        self.fileURL = fileURL
        if let fileURL {
            records = Self.load(fileURL)
        } else {
            records = []
        }
    }

    public static func inMemory() -> FileInstructionRepository {
        FileInstructionRepository(fileURL: nil)
    }

    /// Application Support/outbox.json
    public static func defaultFileURL() throws -> URL {
        let dir = try FileManager.default.url(for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true)
        return dir.appendingPathComponent("outbox.json")
    }

    // shortcut: delivered/failed records are never pruned; ~1 KB each at ~20/day. Add a TTL policy when retention settings land.

    public func enqueue(_ instruction: NewInstruction, now: Date) throws -> OutboxRecord {
        if let existing = records.first(where: { $0.id == instruction.clientInstructionId }) { return existing }
        let record = OutboxRecord(instruction: instruction, createdAt: now)
        let previous = records
        records.append(record)
        do { try persist() } catch { records = previous; throw error }
        return record
    }

    public func pending(now: Date) -> [OutboxRecord] {
        records
            .filter { ($0.status == .pending || $0.status == .sending) && ($0.nextRetryAt ?? .distantPast) <= now }
            .sorted { $0.createdAt < $1.createdAt }
    }

    public func markSending(_ id: UUID) throws {
        try mutate(id) {
            $0.status = .sending
            $0.attemptCount += 1
        }
    }

    public func markDelivered(_ id: UUID, receipt: InstructionReceipt) throws {
        try mutate(id) {
            $0.status = .delivered
            $0.receipt = receipt
            $0.nextRetryAt = nil
            $0.lastError = nil
        }
    }

    public func markFailed(_ id: UUID, error: String, nextRetryAt: Date?) throws {
        try mutate(id) {
            $0.lastError = error
            $0.nextRetryAt = nextRetryAt
            $0.status = nextRetryAt == nil ? .failed : .pending
        }
    }

    public func setRemote(_ id: UUID, instruction: Instruction) throws {
        try mutate(id) { $0.remote = instruction }
    }

    public func record(_ id: UUID) -> OutboxRecord? {
        records.first { $0.id == id }
    }

    public func all() -> [OutboxRecord] {
        records.sorted { $0.createdAt > $1.createdAt }
    }

    /// Applies a change and persists it; restores the previous state if the write fails.
    private func mutate(_ id: UUID, _ change: (inout OutboxRecord) -> Void) throws {
        guard let index = records.firstIndex(where: { $0.id == id }) else { throw RepositoryError.notFound }
        let previous = records
        change(&records[index])
        do { try persist() } catch { records = previous; throw error }
    }

    private func persist() throws {
        guard let fileURL else { return }
        try FileManager.default.createDirectory(at: fileURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        let data = try VoxJSON.encoder().encode(records)
        #if os(watchOS) || os(iOS)
        try data.write(to: fileURL, options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
        #else
        try data.write(to: fileURL, options: .atomic)
        #endif
    }

    private static func load(_ url: URL) -> [OutboxRecord] {
        guard let data = try? Data(contentsOf: url) else { return [] }
        do {
            return try VoxJSON.decoder().decode([OutboxRecord].self, from: data)
        } catch {
            // Never silently drop queued instructions: keep the unreadable file aside for diagnosis.
            let backup = url.deletingPathExtension().appendingPathExtension("corrupt-\(Int(Date().timeIntervalSince1970)).json")
            try? FileManager.default.moveItem(at: url, to: backup)
            VoxLog.event("outbox.corrupt", code: "decode_failed")
            return []
        }
    }
}
