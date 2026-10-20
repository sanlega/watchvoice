import SwiftUI
import VoxWatchCore

struct TranscriptView: View {
    @EnvironmentObject private var model: AppModel
    let transcript: Transcript
    @State private var projectId: UUID?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 8) {
                Text(transcript.text)
                    .font(.body)
                    .accessibilityLabel("Transcripción: \(transcript.text)")
                Text(engineLabel)
                    .font(.caption2)
                    .foregroundStyle(.secondary)

                if !model.projects.isEmpty {
                    Picker("Proyecto", selection: $projectId) {
                        Text("Automático").tag(UUID?.none)
                        ForEach(model.projects) { project in
                            Text(project.name).tag(Optional(project.id))
                        }
                    }
                }

                Button {
                    Task { await model.send(projectHint: projectId) }
                } label: {
                    Label("Enviar", systemImage: "paperplane.fill")
                }
                .buttonStyle(.borderedProminent)
                .accessibilityHint("Guarda la instrucción y la envía al servidor")

                Button {
                    model.retake()
                } label: {
                    Label("Volver a grabar", systemImage: "arrow.counterclockwise")
                }

                DictationField(prompt: "Dictar de nuevo")
            }
        }
    }

    private var engineLabel: String {
        switch transcript.engine {
        case .whistle: return "Whistle (en el reloj, experimental)"
        case .systemDictation: return "Dictado del sistema"
        case .remote: return "Servidor"
        case .manual: return "Manual"
        case .mock: return "MOCK — no es transcripción real"
        }
    }
}

/// System text input (dictation / scribble / keyboard) → engine "system_dictation".
struct DictationField: View {
    @EnvironmentObject private var model: AppModel
    var prompt = "Dictar"
    @State private var text = ""

    var body: some View {
        TextField(prompt, text: $text)
            .onSubmit {
                model.dictated(text)
                text = ""
            }
            .accessibilityHint("Abre el dictado del sistema")
    }
}

/// System dictation: chosen in Settings, or the fallback when Whistle is unavailable or fails.
struct DictationView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        ScrollView {
            VStack(spacing: 8) {
                Text("Dicta tu instrucción")
                    .font(.headline)
                if let notice = model.dictationNotice {
                    Text(notice)
                        .font(.caption2)
                        .foregroundStyle(.orange)
                }
                DictationField()
                Button("Cancelar", role: .cancel) { model.cancelDictation() }
            }
        }
    }
}
