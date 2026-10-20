import Foundation
#if canImport(os)
import os
#endif

/// Privacy rule: log ids, status values and error codes only. Never transcript text or tokens,
/// which is why this API has no free-form message parameter.
public enum VoxLog {
    public static func event(_ name: String, id: UUID? = nil, status: String? = nil, code: String? = nil) {
        #if canImport(os)
        let logger = Logger(subsystem: "app.vox.watch", category: "core")
        logger.info("\(name, privacy: .public) id=\(id?.uuidString ?? "-", privacy: .public) status=\(status ?? "-", privacy: .public) code=\(code ?? "-", privacy: .public)")
        #endif
    }
}
