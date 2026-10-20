import SwiftUI
import VoxWatchCore

/// Each delivery stage has its own label: "Recibido" is never presented as execution.
struct DeliveryView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        let info = DeliveryInfo(model.flow)
        ScrollView {
            VStack(spacing: 8) {
                VStack(spacing: 4) {
                    Image(systemName: info.icon)
                        .font(.largeTitle)
                        .foregroundStyle(info.color)
                        .accessibilityHidden(true)
                    Text(info.title)
                        .font(.headline)
                    if !info.detail.isEmpty {
                        Text(info.detail)
                            .font(.footnote)
                            .multilineTextAlignment(.center)
                    }
                }
                .accessibilityElement(children: .combine)

                if case .taskStarted(let instruction) = model.flow, let progress = instruction.progress {
                    RunProgressView(progress: progress)
                }
                if model.connection == .authRequired {
                    Text("Sesión caducada: inicia sesión en Ajustes. La instrucción sigue guardada.")
                        .font(.caption2)
                        .foregroundStyle(.orange)
                }
                Button("Hecho") { model.finish() }
            }
        }
    }
}

struct DeliveryInfo {
    var title: String
    var detail: String
    var icon: String
    var color: Color

    init(_ state: FlowState) {
        switch state {
        case .savedLocally:
            self.init("Guardado local", "Se enviará cuando haya conexión.", "tray.and.arrow.down", .blue)
        case .sending:
            self.init("Enviando", "", "arrow.up.circle", .blue)
        case .received:
            self.init("Recibido", "El servidor lo guardó. Aún no hay tarea iniciada.", "checkmark.circle", .green)
        case .taskStarted:
            self.init("Tarea iniciada", "", "bolt.circle.fill", .green)
        case .needsReview(let instruction):
            self.init("Revisión requerida", Self.clarification(instruction), "questionmark.circle", .orange)
        case .awaitingApproval:
            self.init("Requiere aprobación", "Revísala en Aprobaciones.", "hand.raised", .orange)
        case .captured:
            self.init("Capturado", "Guardado como idea. No se ejecutó nada.", "lightbulb", .yellow)
        case .failed(let error):
            self.init("Error", Self.describe(error), "exclamationmark.triangle", .red)
        case .idle, .recording, .transcribing, .reviewing:
            self.init("", "", "circle", .secondary)
        }
    }

    private init(_ title: String, _ detail: String, _ icon: String, _ color: Color) {
        self.title = title
        self.detail = detail
        self.icon = icon
        self.color = color
    }

    static func clarification(_ instruction: Instruction) -> String {
        switch instruction.errorCode {
        case "unknown_runner": return "¿En qué máquina? No conozco esa máquina."
        case "ambiguous_runner": return "¿En qué máquina? Hay varias posibles."
        default:
            let names = (instruction.candidates ?? []).map(\.name).joined(separator: ", ")
            return names.isEmpty ? "¿En qué proyecto?" : "¿En qué proyecto? \(names)"
        }
    }

    private static func describe(_ error: FlowError) -> String {
        switch error {
        case .capture(let code):
            return code == "capture_permissionDenied" ? "Permite el micrófono en Ajustes del reloj." : "No se pudo grabar."
        case .transcription:
            return "No se pudo transcribir. Prueba a dictar."
        case .rejected(let code):
            return "El servidor rechazó la instrucción (\(code))."
        case .server(let code):
            return "La instrucción falló en el servidor (\(code ?? "sin código"))."
        }
    }
}

/// Run progress on the user's T3 Code machine. `summary` is agent-written, untrusted text: display only.
struct RunProgressView: View {
    let progress: RunProgress

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(Self.label(progress))
                .font(.footnote.bold())
            if let summary = progress.summary, !summary.isEmpty {
                Text(summary)
                    .font(.caption2)
                    .lineLimit(3)
            }
            if progress.prUrl != nil {
                Label("PR lista", systemImage: "arrow.triangle.pull")
                    .font(.caption2)
                    .foregroundStyle(.green)
            }
        }
        .accessibilityElement(children: .combine)
    }

    static func label(_ progress: RunProgress) -> String {
        let runner = progress.runner ?? "la máquina"
        switch progress.runState {
        case .queued: return "En cola"
        case .provisioning: return "Enviando a \(runner)"
        case .running: return "Trabajando en \(runner)"
        case .waitingApproval: return "Requiere aprobación"
        case .succeeded: return "Completado"
        case .failed, .timedOut: return "Falló"
        case .cancelled: return "Cancelado"
        case .unknown: return "Estado desconocido"
        }
    }
}
