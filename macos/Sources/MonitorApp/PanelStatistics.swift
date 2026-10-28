import Foundation
import MonitorModel

/// Official usage must identify the selected turn explicitly.
struct PanelStatistics {
    let outputTokens: Int?
    let reasoningTokens: Int?
    let matchesUsageTurn: Bool
    let averageSpeed: Double?
    let averageIsPartial: Bool

    init(usage: Usage?, selectedTurn: String?, switchingTurn: Bool = false,
         average: TimelineAverageSpeed? = nil, thread: String? = nil) {
        let selected = selectedTurn.flatMap { $0.isEmpty ? nil : $0 }
        matchesUsageTurn = !switchingTurn && selected != nil && usage?.turn_id == selected
        outputTokens = matchesUsageTurn ? usage?.output_tokens.flatMap { $0 >= 0 ? $0 : nil } : nil
        reasoningTokens = matchesUsageTurn ? usage?.reasoning_tokens.flatMap { $0 >= 0 ? $0 : nil } : nil
        averageSpeed = switchingTurn ? nil : average?.value(thread: thread, turn: selected)
        averageIsPartial = averageSpeed != nil && average?.coverage == "partial"
    }

    var isUnavailable: Bool { outputTokens == nil && reasoningTokens == nil }

    var averageText: String {
        guard let averageSpeed else { return "—" }
        return String(format: "%.1f T/s", locale: Locale(identifier: "en_US_POSIX"), averageSpeed)
    }

    static func compactCount(_ value: Int?) -> String {
        guard let value, value >= 0 else { return "—" }
        if value >= 1_000_000 { return String(format: "%.1fM", locale: Locale(identifier: "en_US_POSIX"), Double(value) / 1_000_000) }
        if value >= 10_000 { return String(format: "%.1fk", locale: Locale(identifier: "en_US_POSIX"), Double(value) / 1_000) }
        return MetricFormat.count(value)
    }
}
