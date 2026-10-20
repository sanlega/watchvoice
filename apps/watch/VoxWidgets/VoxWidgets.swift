import SwiftUI
import WidgetKit

/// Watch face complication: one tap opens VOX straight into recording (`vox://record`, handled in VoxWatchApp).
@main
struct VoxWidgets: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "record", provider: Provider()) { _ in
            RecordComplication()
        }
        .configurationDisplayName("Dictar a VOX")
        .description("Empieza a grabar una instrucción con un toque.")
        .supportedFamilies([.accessoryCircular, .accessoryCorner, .accessoryRectangular])
    }
}

struct Provider: TimelineProvider {
    func placeholder(in context: Context) -> SimpleEntry { SimpleEntry(date: .now) }

    func getSnapshot(in context: Context, completion: @escaping (SimpleEntry) -> Void) {
        completion(SimpleEntry(date: .now))
    }

    // Static content: nothing to refresh.
    func getTimeline(in context: Context, completion: @escaping (Timeline<SimpleEntry>) -> Void) {
        completion(Timeline(entries: [SimpleEntry(date: .now)], policy: .never))
    }
}

struct SimpleEntry: TimelineEntry {
    let date: Date
}

struct RecordComplication: View {
    @Environment(\.widgetFamily) private var family

    var body: some View {
        content
            .containerBackground(.fill.tertiary, for: .widget)
            .widgetURL(URL(string: "vox://record"))
            .accessibilityElement(children: .ignore)
            .accessibilityLabel("Dictar a VOX")
            .accessibilityHint("Abre VOX y empieza a grabar una instrucción")
    }

    @ViewBuilder private var content: some View {
        switch family {
        case .accessoryRectangular:
            Label("Dictar a VOX", systemImage: "mic.fill")
                .font(.headline)
        case .accessoryCorner:
            Image(systemName: "mic.fill")
                .font(.title2)
                .widgetLabel { Text("VOX") }
        default:
            ZStack {
                AccessoryWidgetBackground()
                Image(systemName: "mic.fill")
                    .font(.title2)
            }
        }
    }
}
