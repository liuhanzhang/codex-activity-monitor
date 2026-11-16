import SwiftUI
import MonitorModel

/// Shared phase categories; colors and labels do not encode activity identity.
struct TimelineLegend: LocalizedView {
    @Environment(\.monitorLanguage) var language

    var body: some View {
        HStack(spacing: 5) {
            ForEach([("reasoning", "思考"), ("text", "回复"), ("tool_input", "参数"), ("file_edit_input", "改动"),
                     ("contextCompaction", "压缩")], id: \.0) { kind, title in
                HStack(spacing: 3) {
                    RoundedRectangle(cornerRadius: 1.5)
                        .fill(Color(nsColor: TimelinePalette.color(for: kind))).frame(width: 12, height: 5)
                    Text(L(title)).lineLimit(1).fixedSize()
                }
            }
        }
    }
}
