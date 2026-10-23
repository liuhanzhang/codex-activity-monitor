import Foundation

public struct ActivitySection: Decodable, Identifiable, Sendable {
    public let id: String
    public let title: String
    public let format: String
    public var text: String
    public let offset: Int?
    public let total_chars: Int?
    public var next_offset: Int?
    public let notice: String?
    public var title_message: DisplayMessage? = nil
    public var notice_message: DisplayMessage? = nil
    public var text_message: DisplayMessage? = nil

    public func localizedTitle(_ language: MonitorLanguage) -> String {
        if let title_message { return title_message.rendered(in: language) }
        let key = ["input": "detail.input", "output": "detail.output", "live": "detail.live", "attachments": "detail.attachments"][id]
        return key.map { DisplayMessage($0).rendered(in: language) } ?? (language == .chinese ? title : ModelLocalizer.text("活动", language))
    }
    public func localizedNotice(_ language: MonitorLanguage) -> String? {
        notice_message.map { $0.rendered(in: language) } ?? notice.map { language == .chinese ? $0 : ModelLocalizer.text("来源未提供可查看内容，活动状态与时间见上方。", language) }
    }
    public func localizedText(_ language: MonitorLanguage) -> String { text_message?.rendered(in: language) ?? text }

    public func appendingPage(_ part: Self) -> Self {
        Self(id: id, title: title, format: format, text: text + part.text, offset: offset,
             total_chars: part.total_chars ?? total_chars, next_offset: part.next_offset, notice: part.notice ?? notice,
             title_message: part.title_message ?? title_message, notice_message: part.notice_message ?? notice_message,
             text_message: part.text_message ?? text_message)
    }
}

public struct ActivityDetail: Decodable, Sendable {
    public let version: Int
    public let state: String
    public let phase: TimelinePhase?
    public var sections: [ActivitySection]
    public let attachments: [String]
    public let source: String?

    /// Content always belongs to the full clicked identity. An unavailable
    /// response may omit that identity only when it carries no content at all.
    public func validated(for selected: TimelinePhase) throws -> Self {
        guard version == 1 else { throw SnapshotError.unsupportedVersion }
        if phase == nil, sections.isEmpty, attachments.isEmpty,
           ["content_unavailable", "history_unavailable", "missing", "unavailable", "not_retained"].contains(state) {
            return self
        }
        guard let phase, phase.id == selected.id, phase.scope_id == selected.scope_id,
              phase.thread_id == selected.thread_id, phase.turn_id == selected.turn_id else {
            throw ActivityContentError.identityMismatch
        }
        return self
    }

    /// Offsets are Python Unicode code points, not Swift grapheme counts.
    /// Validate the source cursor rather than deriving one from String.count.
    public func appendingPage(_ page: Self, sectionID: String, offset: Int) throws -> Self {
        guard let index = sections.firstIndex(where: { $0.id == sectionID }),
              sections[index].next_offset == offset else { throw ActivityContentError.invalidPage }
        guard let part = page.sections.first(where: { $0.id == sectionID }) else {
            guard page.state != "available" else { throw ActivityContentError.invalidPage }
            return page.retainingContent(from: self)
        }
        guard part.offset == offset, part.next_offset == nil || part.next_offset! > offset else {
            throw ActivityContentError.invalidPage
        }
        var result = page.retainingContent(from: self)
        result.sections[index] = sections[index].appendingPage(part)
        return result
    }

    public func retainingContent(from prior: Self) -> Self {
        Self(version: version, state: state, phase: phase, sections: prior.sections,
             attachments: prior.attachments, source: prior.source)
    }
}

public enum ActivityContentError: Error { case identityMismatch, invalidPage }
