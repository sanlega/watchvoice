import SwiftUI
import VoxWatchCore

struct ApprovalsView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        List {
            if let notice = model.notice {
                Text(notice)
                    .font(.caption2)
                    .foregroundStyle(.orange)
            }
            if model.approvals.isEmpty {
                Text("Nada pendiente")
            }
            ForEach(model.approvals) { approval in
                ApprovalRow(approval: approval)
            }
        }
        .navigationTitle("Aprobaciones")
        .task { await model.loadApprovals() }
    }
}

/// One tap to approve, signed by the watch key (ADR-0001 §5). High risk also needs a watch passcode (checked in AppModel).
struct ApprovalRow: View {
    @EnvironmentObject private var model: AppModel
    let approval: Approval

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            VStack(alignment: .leading, spacing: 2) {
                if approval.agentProvided {
                    Text("Escrito por el agente")
                        .font(.caption2)
                        .foregroundStyle(.orange)
                }
                Text(approval.actionSummary)
                    .font(approval.kind == "policy_change" ? Font.title3.bold() : Font.headline)
                Text(approval.capability)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                if let runner = approval.runner {
                    Text("Máquina: \(runner)")
                        .font(.caption2)
                }
                if approval.runnerTrust == "cooperative" {
                    Text("PC: sin aislamiento")
                        .font(.caption2)
                        .foregroundStyle(.orange)
                }
                Text("Riesgo: \(riskLabel)")
                    .font(.caption)
                    .foregroundStyle(riskColor)
                Text(approval.expiresAt, style: .timer)
                    .font(.caption2)
                    .accessibilityLabel(Text("Caduca \(approval.expiresAt, style: .relative)"))
            }
            .accessibilityElement(children: .combine)

            HStack {
                Button("Rechazar", role: .destructive) {
                    Task { await model.decide(approval, approve: false) }
                }
                .buttonStyle(.bordered)
                Button("Aprobar") {
                    Task { await model.decide(approval, approve: true) }
                }
                .buttonStyle(.bordered)
                .tint(.green)
                .accessibilityHint(approval.risk == "high" ? "Requiere código de desbloqueo en el reloj" : "")
            }
            .disabled(approval.expiresAt < Date())
        }
    }

    private var riskLabel: String {
        switch approval.risk {
        case "low": return "bajo"
        case "medium": return "medio"
        case "high": return "alto"
        default: return "desconocido"
        }
    }

    private var riskColor: Color {
        switch approval.risk {
        case "low": return .green
        case "medium": return .yellow
        default: return .red
        }
    }
}
