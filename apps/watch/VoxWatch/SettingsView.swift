import SwiftUI
import VoxWatchCore

struct SettingsView: View {
    @EnvironmentObject private var model: AppModel
    @AppStorage(SettingsKey.language) private var language = "auto"
    @AppStorage(SettingsKey.preferLocalASR) private var preferLocal = true
    @AppStorage(SettingsKey.remoteAudioConsent) private var remoteConsent = false
    @AppStorage(SettingsKey.runnerHint) private var runnerHint = ""
    @AppStorage(SettingsKey.speakResult) private var speakResult = false
    #if DEBUG
    @AppStorage(SettingsKey.useMockASR) private var useMock = false
    #endif
    @State private var pairingCode = ""

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
                Toggle("Leer en voz alta", isOn: $speakResult)
                    .accessibilityHint("Desactivado por defecto. Lee el resultado completo al terminar, con la app abierta.")
                Button("Sincronizar") {
                    Task { await model.syncProjects() }
                }
                .accessibilityHint("Pide a cada máquina su lista de proyectos")
            }

            Section("Vincular") {
                TextField("Código de 8 letras", text: $pairingCode)
                    .onChange(of: pairingCode) { _, value in
                        let normalized = PairingCode.normalize(value)
                        if normalized != value { pairingCode = normalized }
                    }
                    .accessibilityHint("Genéralo en el servidor con python -m app.admin pair")
                Button("Vincular") {
                    let code = pairingCode
                    pairingCode = ""
                    Task { await model.pair(code: code) }
                }
                .disabled(!PairingCode.isValid(pairingCode))
                NavigationLink {
                    AdvancedServerView()
                } label: {
                    Text("Avanzado")
                }
                Button("Cerrar sesión", role: .destructive) {
                    Task { await model.logout() }
                }
            }

            if let notice = model.notice {
                Text(notice)
                    .font(.caption2)
                    .foregroundStyle(.orange)
            }

            Section("Control") {
                Toggle("Pausar todo", isOn: Binding(get: { model.paused }, set: { value in
                    Task { await model.setPaused(value) }
                }))
                Text("Para las ejecuciones activas y bloquea nuevas. Parada best-effort.")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }

            Section("Reglas activas") {
                if model.rules.isEmpty {
                    Text("Sin reglas")
                        .font(.caption2)
                }
                ForEach(model.rules) { rule in
                    VStack(alignment: .leading, spacing: 2) {
                        Text(Self.title(rule))
                            .font(.footnote)
                        if !rule.effects.isEmpty {
                            Text(Self.effects(rule))
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                        }
                        if let expiresAt = rule.expiresAt {
                            Text("Caduca \(expiresAt, style: .relative)")
                                .font(.caption2)
                        }
                    }
                    .accessibilityElement(children: .combine)
                }
            }
        }
        .navigationTitle("Ajustes")
        .task {
            await model.checkWhistle()
            await model.loadPolicy()
        }
    }

    private static func title(_ rule: PolicyRule) -> String {
        let mode = rule.mode == "bypass" ? "Bypass" : "Granular"
        return "\(mode) · \(rule.runner ?? "todas") · \(rule.project ?? "todos")"
    }

    private static func effects(_ rule: PolicyRule) -> String {
        rule.effects.keys.sorted().map { "\($0): \(rule.effects[$0] ?? "")" }.joined(separator: "\n")
    }

    private var whistleLabel: String {
        switch model.whistleStatus {
        case nil: return "Comprobando motor local…"
        case .available?: return "Whistle disponible"
        case .blocked(let reason)?: return "No disponible: \(reason)"
        }
    }
}

/// Server override; the default is VoxBackend.defaultServerURL.
private struct AdvancedServerView: View {
    @AppStorage(SettingsKey.serverURL) private var serverURL = VoxBackend.defaultServerURL

    var body: some View {
        Form {
            TextField("https://servidor/v1", text: $serverURL)
            if !serverURL.isEmpty && !isServerURLValid {
                Text("Usa una URL https")
                    .font(.caption2)
                    .foregroundStyle(.red)
            }
            Button("Restablecer") { serverURL = VoxBackend.defaultServerURL }
        }
        .navigationTitle("Avanzado")
    }

    private var isServerURLValid: Bool {
        URL(string: serverURL).map(URLSessionWatchAPIClient.isAllowed) ?? false
    }
}
