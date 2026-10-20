import AppIntents

/// "Dictar a VOX" for Siri / Atajos: opens the app and starts the normal record flow.
struct DictateIntent: AppIntent {
    static let title: LocalizedStringResource = "Dictar a VOX"
    static let description = IntentDescription("Abre VOX y empieza a grabar una instrucción para tus agentes.")
    static let openAppWhenRun = true

    @MainActor
    func perform() async throws -> some IntentResult {
        await AppModel.shared.speakTapped()
        return .result()
    }
}

struct VoxShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(
            intent: DictateIntent(),
            phrases: [
                "Dictar a \(.applicationName)",
                "Dicta a \(.applicationName)",
                "Habla con \(.applicationName)",
            ],
            shortTitle: "Dictar a VOX",
            systemImageName: "mic.fill"
        )
    }
}
