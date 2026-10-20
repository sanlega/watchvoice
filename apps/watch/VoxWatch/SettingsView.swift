import SwiftUI
import VoxWatchCore

struct SettingsView: View {
    @EnvironmentObject private var model: AppModel
    @AppStorage(SettingsKey.language) private var language = "auto"
    @AppStorage(SettingsKey.preferLocalASR) private var preferLocal = true
    @AppStorage(SettingsKey.remoteAudioConsent) private var remoteConsent = false
    @AppStorage(SettingsKey.serverURL) private var serverURL = ""
    @AppStorage(SettingsKey.runnerHint) private var runnerHint = ""
    #if DEBUG
    @AppStorage(SettingsKey.useMockASR) private var useMock = false
    #endif
    @State private var token = ""

    var body: some View {
        Form {
            Picker("Idioma", selection: $language) {
                Text("Automático").tag("auto")
                Text("Español").tag("es")
                Text("English").tag("en")
            }

            Section("Transcripción") {
                Toggle("Motor local (Whistle)", isOn: $preferLocal)
                Text(whistleLabel)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                Toggle("Enviar audio al servidor", isOn: $remoteConsent)
                    .accessibilityHint("Desactivado por defecto. El audio solo saldría del reloj si lo activas.")
                #if DEBUG
                Toggle("Motor mock (DEBUG)", isOn: $useMock)
                #endif
            }

            Section("Ejecución") {
                TextField("Máquina preferida", text: $runnerHint)
                    .accessibilityHint("Por ejemplo mac o vps. Vacío: decide el servidor.")
            }

            Section("Servidor") {
                TextField("https://servidor/v1", text: $serverURL)
                if !serverURL.isEmpty && !isServerURLValid {
                    Text("Usa una URL https")
                        .font(.caption2)
                        .foregroundStyle(.red)
                }
                SecureField("Token del dispositivo", text: $token)
                Button("Guardar token") {
                    let value = token
                    token = ""
                    Task { await model.saveToken(value) }
                }
                .disabled(token.isEmpty)
                Button("Cerrar sesión", role: .destructive) {
                    Task { await model.logout() }
                }
            }
        }
        .navigationTitle("Ajustes")
        .task { await model.checkWhistle() }
    }

    private var isServerURLValid: Bool {
        URL(string: serverURL).map(URLSessionWatchAPIClient.isAllowed) ?? false
    }

    private var whistleLabel: String {
        switch model.whistleStatus {
        case nil: return "Comprobando motor local…"
        case .available?: return "Whistle disponible (experimental)"
        case .blocked(let reason)?: return "No disponible: \(reason)"
        }
    }
}
