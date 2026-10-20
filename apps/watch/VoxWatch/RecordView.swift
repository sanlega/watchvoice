import SwiftUI

struct RecordView: View {
    @EnvironmentObject private var model: AppModel
    let startedAt: Date

    var body: some View {
        VStack(spacing: 8) {
            Image(systemName: "mic.fill")
                .font(.largeTitle)
                .foregroundStyle(.red)
                .symbolEffect(.pulse)
                .accessibilityLabel("Micrófono activo")
            Text(startedAt, style: .timer)
                .font(.title3.monospacedDigit())
                .accessibilityLabel("Grabando. Máximo \(Int(AppModel.maxRecordingSeconds)) segundos")
            HStack {
                Button {
                    Task { await model.cancelTapped() }
                } label: {
                    Image(systemName: "xmark")
                }
                .accessibilityLabel("Cancelar")
                .accessibilityHint("Descarta la grabación")

                Button {
                    Task { await model.stopTapped() }
                } label: {
                    Image(systemName: "stop.fill")
                }
                .tint(.red)
                .accessibilityLabel("Detener")
                .accessibilityHint("Termina la grabación y la transcribe")
            }
        }
    }
}
