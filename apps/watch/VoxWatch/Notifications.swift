import UserNotifications
import WatchKit
import VoxWatchCore

/// Categories and action ids. Must match the `category` the backend puts in `aps` (backend/app/notify.py).
enum VoxNotifications {
    static let approve = "VOX_APPROVE"
    static let reject = "VOX_REJECT"
    static let review = "VOX_REVIEW"

    static var categories: Set<UNNotificationCategory> {
        // Aprobar first and non-destructive: Double Tap triggers it. No .authenticationRequired (ADR-0001 §6).
        let approveAction = UNNotificationAction(identifier: approve, title: "Aprobar", options: [])
        let rejectAction = UNNotificationAction(identifier: reject, title: "Rechazar", options: [.destructive])
        let reviewAction = UNNotificationAction(identifier: review, title: "Revisar", options: [.foreground])
        return [
            UNNotificationCategory(identifier: "VOX_APPROVAL_LOW", actions: [approveAction, rejectAction], intentIdentifiers: [], options: []),
            UNNotificationCategory(identifier: "VOX_APPROVAL_HIGH", actions: [reviewAction], intentIdentifiers: [], options: []),
        ]
    }

    /// Tapping it opens the approvals card for the same approval.
    static func scheduleRetry(approvalId: UUID, fingerprint: String, approve: Bool) {
        let content = UNMutableNotificationContent()
        content.title = "VOX"
        content.body = approve ? "No se pudo aprobar, toca para reintentar" : "No se pudo rechazar, toca para reintentar"
        content.sound = .default
        content.userInfo = ["vox": ["approval_id": approvalId.uuidString.lowercased(), "action_fingerprint": fingerprint]]
        let request = UNNotificationRequest(identifier: "retry-\(approvalId.uuidString)", content: content, trigger: nil)
        UNUserNotificationCenter.current().add(request, withCompletionHandler: nil)
    }
}

final class VoxAppDelegate: NSObject, WKApplicationDelegate {
    // UNUserNotificationCenter.delegate is weak.
    private let notifications = NotificationHandler()

    func applicationDidFinishLaunching() {
        let center = UNUserNotificationCenter.current()
        center.delegate = notifications
        center.setNotificationCategories(VoxNotifications.categories)
        center.requestAuthorization(options: [.alert, .sound]) { _, _ in }
        // Every launch: the token can change.
        WKApplication.shared().registerForRemoteNotifications()
    }

    func didRegisterForRemoteNotifications(withDeviceToken deviceToken: Data) {
        UserDefaults.standard.set(deviceToken.map { String(format: "%02x", $0) }.joined(), forKey: SettingsKey.pushToken)
        Task { @MainActor in await AppModel.shared.registerPushToken() }
    }

    func didFailToRegisterForRemoteNotificationsWithError(_ error: Error) {
        VoxLog.event("push.register_failed")
    }
}

final class NotificationHandler: NSObject, UNUserNotificationCenterDelegate {
    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification,
                                withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        completionHandler([.banner, .sound])
    }

    /// Runs in the background for Aprobar/Rechazar: one signed call, then a local retry notification on failure.
    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse,
                                withCompletionHandler completionHandler: @escaping () -> Void) {
        let vox = response.notification.request.content.userInfo["vox"] as? [String: Any] ?? [:]
        let approvalId = (vox["approval_id"] as? String).flatMap { UUID(uuidString: $0) }
        let action = response.actionIdentifier
        switch action {
        case VoxNotifications.approve, VoxNotifications.reject:
            guard let approvalId, let fingerprint = vox["action_fingerprint"] as? String else {
                completionHandler()
                return
            }
            let approve = action == VoxNotifications.approve
            Task {
                do {
                    try await VoxBackend.decide(approvalId: approvalId, fingerprint: fingerprint, approve: approve)
                } catch {
                    VoxLog.event("push.decide_failed", id: approvalId, code: (error as? APIError)?.code)
                    VoxNotifications.scheduleRetry(approvalId: approvalId, fingerprint: fingerprint, approve: approve)
                }
                completionHandler()
            }
        case UNNotificationDefaultActionIdentifier, VoxNotifications.review:
            if let approvalId {
                Task { @MainActor in AppModel.shared.openApprovalId = approvalId }
            }
            completionHandler()
        default:
            completionHandler()
        }
    }
}
