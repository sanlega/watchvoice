import SwiftUI
import VoxWatchCore

@main
struct VoxWatchApp: App {
    @StateObject private var model = AppModel()
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
    }
}
