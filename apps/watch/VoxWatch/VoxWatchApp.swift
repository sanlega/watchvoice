import SwiftUI
import WatchKit
import VoxWatchCore

@main
struct VoxWatchApp: App {
    @WKApplicationDelegateAdaptor(VoxAppDelegate.self) private var appDelegate
    @StateObject private var model = AppModel.shared
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(model)
                // Polling/draining only while in foreground; no background promises.
                .task(id: scenePhase) {
                    guard scenePhase == .active else { return }
                    await model.foregroundLoop()
                }
                // Complication tap (VoxWidgets): straight into recording.
                .onOpenURL { url in
                    guard url.scheme == "vox", url.host == "record" else { return }
                    Task { await model.speakTapped() }
                }
        }
    }
}

struct RootView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        NavigationStack {
            switch model.flow {
            case .idle:
                if model.dictating { DictationView() } else { HomeView() }
            case .recording(let startedAt):
                RecordView(startedAt: startedAt)
            case .transcribing:
                ProgressView("Transcribiendo…")
            case .reviewing(let transcript):
                TranscriptView(transcript: transcript)
            default:
                DeliveryView()
            }
        }
        // Opened from an approval notification ("Revisar" or tap).
        .sheet(isPresented: Binding(get: { model.openApprovalId != nil }, set: { if !$0 { model.openApprovalId = nil } })) {
            NavigationStack { ApprovalsView() }
                .environmentObject(model)
        }
    }
}
