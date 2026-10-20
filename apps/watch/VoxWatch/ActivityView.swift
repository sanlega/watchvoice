import SwiftUI
import VoxWatchCore

struct ActivityView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        List {
            if model.activity.isEmpty {
                Text("Sin actividad")
            }
            ForEach(model.activity) { record in
                VStack(alignment: .leading, spacing: 2) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(record.instruction.text)
                            .font(.footnote)
                            .lineLimit(2)
                        Text(record.statusLabel)
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                        if record.remote?.status == .dispatched, let progress = record.remote?.progress {
                            RunProgressView(progress: progress)
                        }
                    }
                    .accessibilityElement(children: .combine)
                    if let remote = record.remote, remote.status == .dispatched, let runId = remote.agentRunId,
                       let progress = remote.progress, !progress.runState.isTerminal {
                        StopRunButton(runId: runId)
                    }
                }
            }
        }
        .navigationTitle("Actividad")
    }
}

extension OutboxRecord {
    var statusLabel: String {
        switch status {
        case .pending: return attemptCount > 0 ? "Guardado local · reintentando" : "Guardado local"
        case .sending: return "Enviando"
        case .failed: return "Error (\(lastError ?? "rechazado"))"
        case .delivered:
            switch remoteStatus {
            case .dispatched?: return "Tarea iniciada"
            case .needsClarification?:
                if let remote { return "Revisión requerida · " + DeliveryInfo.clarification(remote) }
                return "Revisión requerida"
            case .awaitingApproval?: return "Requiere aprobación"
            case .captured?: return "Capturado"
            case .applied?: return "Regla aplicada"
            case .failed?: return remote?.errorCode == "policy_denied" ? "Bloqueado por política" : "Error"
            case .cancelled?: return "Error"
            default: return "Recibido"
            }
        }
    }
}
