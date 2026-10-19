import Foundation
import MonitorModel

/// Each number must identify the selected turn independently. The most recent
/// reasoning sample can belong to a different turn from the usage snapshot.
struct PanelStatistics {
    let outputTokens: Int?
    let reasoningTokens: Int?
    let reasoningRate: Double?
    let matchesUsageTurn: Bool

    init(usage: Usage?, selectedTurn: String?, switchingTurn: Bool = false) {
        let selected = selectedTurn.flatMap { $0.isEmpty ? nil : $0 }
        matchesUsageTurn = !switchingTurn && selected != nil && usage?.turn_id == selected
        outputTokens = matchesUsageTurn ? usage?.output_tokens.flatMap { $0 >= 0 ? $0 : nil } : nil
        reasoningTokens = matchesUsageTurn ? usage?.reasoning_tokens.flatMap { $0 >= 0 ? $0 : nil } : nil
        if !switchingTurn, let selected, usage?.latest_reasoning?.turn_id == selected,
           let rate = usage?.latest_reasoning?.tokens_per_second, rate.isFinite, rate >= 0 {
            reasoningRate = rate
        } else { reasoningRate = nil }
    }

    var isUnavailable: Bool { outputTokens == nil && reasoningTokens == nil && reasoningRate == nil }

    static func compactCount(_ value: Int?) -> String {
        guard let value, value >= 0 else { return "—" }
        if value >= 1_000_000 { return String(format: "%.1fM", locale: Locale(identifier: "en_US_POSIX"), Double(value) / 1_000_000) }
        if value >= 10_000 { return String(format: "%.1fk", locale: Locale(identifier: "en_US_POSIX"), Double(value) / 1_000) }
        return MetricFormat.count(value)
    }
}
