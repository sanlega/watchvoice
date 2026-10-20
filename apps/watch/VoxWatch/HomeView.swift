import SwiftUI
import VoxWatchCore

struct HomeView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        ScrollView {
            VStack(spacing: 8) {
                Button {
                    Task { await model.speakTapped() }
                } label: {
                    Label("Hablar", systemImage: "mic.fill")
                        .font(.title3.bold())
                        .frame(maxWidth: .infinity, minHeight: 56)
                }
                .buttonStyle(.borderedProminent)
                .tint(.red)
                .accessibilityHint("Empieza a grabar una instrucción para tus agentes")

                Label(model.connection.label, systemImage: model.connection.icon)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .accessibilityLabel("Conexión: \(model.connection.label)")

                NavigationLink {
                    ActivityView()
                } label: {
                    HStack {
                        Label("Actividad", systemImage: "list.bullet")
                        Spacer()
                        if model.pendingCount > 0 {
                            Text("\(model.pendingCount)")
                                .font(.caption.bold())
                                .padding(.horizontal, 6)
                                .background(Capsule().fill(Color.orange))
                                .accessibilityLabel("\(model.pendingCount) pendientes de envío")
                        }
                    }
                }
                NavigationLink {
                    ApprovalsView()
                } label: {
                    Label("Aprobaciones", systemImage: "checkmark.shield")
                }
                NavigationLink {
                    SettingsView()
                } label: {
                    Label("Ajustes", systemImage: "gear")
                }
            }
        }
        .navigationTitle("VOX")
    }
}

extension AppModel.Connection {
    var label: String {
        switch self {
        case .unknown: return "Comprobando…"
        case .online: return "Conectado"
        case .offline: return "Sin conexión"
        case .authRequired: return "Vincula el reloj en Ajustes"
        case .notConfigured: return "Configura el servidor"
        }
    }

    var icon: String {
        switch self {
        case .unknown: return "questionmark.circle"
        case .online: return "checkmark.icloud"
        case .offline: return "icloud.slash"
        case .authRequired: return "person.crop.circle.badge.exclamationmark"
        case .notConfigured: return "gear.badge.questionmark"
        }
    }
}
