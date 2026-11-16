import Foundation

/// One height budget for both scroll regions; no reserved empty chat rows.
struct PanelLayoutBudget {
    static let rowHeight: CGFloat = 56
    let listHeight: CGFloat
    let detailHeight: CGFloat
    let timelineHeight: CGFloat

    init(maximum: CGFloat, chrome: CGFloat, chatCount: Int, hasSelection: Bool) {
        let available = max(0, maximum - chrome)
        let detailReserve = hasSelection ? min(180, available * 0.6) : 0
        let desiredList = chatCount == 0 ? 48 : CGFloat(min(5, chatCount)) * Self.rowHeight
        listHeight = min(desiredList, max(0, available - detailReserve))
        detailHeight = hasSelection ? max(0, available - listHeight) : 0
        timelineHeight = min(250, max(40, detailHeight - 112))
    }
}
