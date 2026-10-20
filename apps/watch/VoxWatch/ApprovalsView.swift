import SwiftUI
import VoxWatchCore

struct ApprovalsView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        List {
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

struct ApprovalRow: View {
    @EnvironmentObject private var model: AppModel
    let approval: Approval
    @State private var confirming = false

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            VStack(alignment: .leading, spacing: 2) {
                Text(approval.actionSummary)
                    .font(.headline)
                Text(approval.capability)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                Text("Riesgo: \(riskLabel)")
                    .font(.caption)
                    .foregroundStyle(riskColor)
                if let expiresAt = approval.expiresAt {
                    Text("Caduca \(expiresAt, style: .relative)")
                        .font(.caption2)
                }
            }
            .accessibilityElement(children: .combine)

            HStack {
                Button("Rechazar", role: .destructive) {
                    Task { await model.decide(approval, approve: false) }
                }
                .buttonStyle(.bordered)
                Button("Aprobar") {
                    if approval.risk == "high" {
                        confirming = true
                    } else {
                        Task { await model.decide(approval, approve: true) }
                    }
                }
                .buttonStyle(.bordered)
                .tint(.green)
                .accessibilityHint(approval.risk == "high" ? "Pide confirmación por ser de riesgo alto" : "")
            }
            .disabled(isExpired)
        }
        .confirmationDialog("¿Aprobar acción de riesgo alto?", isPresented: $confirming) {
            Button("Aprobar") { Task { await model.decide(approval, approve: true) } }
            Button("Cancelar", role: .cancel) {}
        }
    }

    private var isExpired: Bool {
        (approval.expiresAt ?? .distantFuture) < Date()
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
