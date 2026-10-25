import Foundation

/// Server-provided Python code point offset and opaque section-prefix version.
public struct ActivityPageCursor: Sendable, Equatable {
    public let sectionID: String
    public let offset: Int
    public let token: String
}

public struct ActivitySection: Decodable, Identifiable, Sendable {
    public let id: String
    public let title: String
    public let format: String
    public var text: String
    public let offset: Int?
    public let total_chars: Int?
    public var next_offset: Int?
    public let notice: String?
    public var page_token: String? = nil
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

    func appendingPage(_ part: Self) -> Self {
        Self(id: id, title: title, format: format, text: text + part.text, offset: offset,
             total_chars: total_chars, next_offset: part.next_offset, notice: notice, page_token: page_token,
             title_message: title_message, notice_message: notice_message, text_message: text_message)
    }

    fileprivate func validatePage() throws {
        guard let offset, let total_chars, offset >= 0, total_chars >= offset,
              let page_token, !page_token.isEmpty, page_token.utf8.count <= 16_384,
              page_token.utf8.allSatisfy({ $0 < 128 }) else { throw ActivityContentError.invalidPage }
        // Validate the declared range; never derive a new cursor using String.count.
        let (end, overflow) = offset.addingReportingOverflow(text.unicodeScalars.count)
        guard !overflow, end <= total_chars,
              next_offset == nil ? end == total_chars : (next_offset == end && end > offset && end < total_chars) else {
            throw ActivityContentError.invalidPage
        }
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
    public func validated(for selected: TimelinePhase, page: ActivityPageCursor? = nil) throws -> Self {
        guard version == 1 || version == 2 else { throw SnapshotError.unsupportedVersion }
        if page != nil && version != 2 { throw SnapshotError.unsupportedVersion }
        let unavailable = ["content_unavailable", "history_unavailable", "missing", "unavailable", "not_retained", "content_changed", "unsupported"]
        if unavailable.contains(state), !sections.isEmpty || !attachments.isEmpty { throw ActivityContentError.invalidPage }
        if phase == nil, sections.isEmpty, attachments.isEmpty,
           unavailable.contains(state) {
            return self
        }
        guard let phase, phase.id == selected.id, phase.scope_id == selected.scope_id,
              phase.thread_id == selected.thread_id, phase.turn_id == selected.turn_id else {
            throw ActivityContentError.identityMismatch
        }
        guard Set(sections.map(\.id)).count == sections.count else { throw ActivityContentError.invalidPage }
        for section in sections {
            if version == 2 { try section.validatePage() }
            if page == nil, (section.offset ?? 0) != 0 { throw ActivityContentError.invalidPage }
        }
        if let page, !sections.isEmpty {
            guard sections.count == 1, sections[0].id == page.sectionID, sections[0].offset == page.offset else {
                throw ActivityContentError.invalidPage
            }
        }
        return self
    }

    public func pageCursor(for sectionID: String? = nil) -> ActivityPageCursor? {
        guard version == 2, ["available", "partial", "source_truncated"].contains(state),
              let section = sections.first(where: { (sectionID == nil || $0.id == sectionID) && $0.next_offset != nil }),
              let offset = section.next_offset, let token = section.page_token else { return nil }
        return ActivityPageCursor(sectionID: section.id, offset: offset, token: token)
    }

    public var requiresReload: Bool {
        state == "content_changed" || (version == 1 && sections.contains { $0.next_offset != nil })
    }

    /// Offsets are Python Unicode code points, not Swift grapheme counts.
    /// Validate the source cursor rather than deriving one from String.count.
    public func appendingPage(_ page: Self, cursor: ActivityPageCursor) throws -> Self {
        guard let selected = phase, let expected = pageCursor(for: cursor.sectionID), expected == cursor,
              let index = sections.firstIndex(where: { $0.id == cursor.sectionID }) else {
            throw ActivityContentError.invalidPage
        }
        _ = try page.validated(for: selected, page: cursor)
        guard let part = page.sections.first else {
            guard ["content_unavailable", "history_unavailable", "missing", "unavailable", "not_retained", "content_changed", "unsupported"].contains(page.state) else {
                throw ActivityContentError.invalidPage
            }
            return page.retainingContent(from: self)
        }
        if part.page_token != cursor.token || page.source != source || page.state != state || part.format != sections[index].format ||
           part.notice != sections[index].notice || part.notice_message != sections[index].notice_message ||
           part.text_message != sections[index].text_message {
            return page.requiringReload(from: self)
        }
        guard part.total_chars == sections[index].total_chars else {
            throw ActivityContentError.invalidPage
        }
        var result = page.retainingContent(from: self)
        result.sections[index] = sections[index].appendingPage(part)
        return result
    }

    public func reconcilingRefresh(from prior: Self?, followsLive: Bool = false, reload: Bool = false) throws -> Self {
        guard let prior, !prior.sections.isEmpty else { return self }
        if sections.isEmpty && state != "available" && state != "no_content" { return retainingContent(from: prior) }
        if reload || (version == 1 && prior.version == 1) { return self }
        let sameIDs = Set(sections.map(\.id)) == Set(prior.sections.map(\.id))
        guard version == 2, prior.version == 2, sameIDs, source == prior.source else { return requiringReload(from: prior) }
        var result = self
        var changed = false
        for index in sections.indices {
            let current = sections[index]
            guard let old = prior.sections.first(where: { $0.id == current.id }) else { throw ActivityContentError.invalidPage }
            if current.page_token == old.page_token {
                guard old.text.utf8.starts(with: current.text.utf8), current.total_chars == old.total_chars,
                      current.format == old.format else { throw ActivityContentError.invalidPage }
                result.sections[index] = old
            } else { changed = true }
        }
        if !changed { return result }
        // Following a small, unpaged live buffer may replace that whole buffer.
        // A paged reading snapshot or a source handoff always requires reload.
        if followsLive, source == "observed_stream", state == "partial", prior.state == state,
           sections.allSatisfy({ $0.next_offset == nil }), prior.sections.allSatisfy({ $0.next_offset == nil }),
           sections.allSatisfy({ current in
               prior.sections.contains { old in old.id == current.id && old.format == current.format &&
                   old.notice_message == current.notice_message && old.notice == current.notice &&
                   current.text.utf8.starts(with: old.text.utf8) }
           }) { return self }
        return requiringReload(from: prior)
    }

    private func requiringReload(from prior: Self) -> Self {
        Self(version: version, state: "content_changed", phase: phase, sections: prior.sections,
             attachments: prior.attachments, source: prior.source)
    }

    public func retainingContent(from prior: Self) -> Self {
        Self(version: version, state: state, phase: phase, sections: prior.sections,
             attachments: prior.attachments, source: prior.source)
    }
}

public enum ActivityContentError: Error { case identityMismatch, invalidPage }
