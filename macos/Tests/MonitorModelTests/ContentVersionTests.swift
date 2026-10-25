import Foundation
import Testing
@testable import MonitorModel

private func detail(_ text: String, offset: Int = 0, next: Int? = nil, total: Int? = nil,
                    token: String = "opaque", state: String = "available", source: String = "codex_history",
                    version: Int = 2) throws -> ActivityDetail {
    let phase = try timelinePhase(["id": "a"])
    return ActivityDetail(version: version, state: state, phase: phase,
        sections: [ActivitySection(id: "input", title: "参数", format: "code", text: text, offset: offset,
            total_chars: total ?? offset + text.unicodeScalars.count, next_offset: next, notice: nil, page_token: token,
            title_message: DisplayMessage("detail.input"))], attachments: [], source: source)
}

@Test func contentVersionUsesSourceOffsetsAndPreservesExactUnicodeAndNewlines() throws {
    let first = try detail("e", next: 1, total: 6)
    let phase = try #require(first.phase)
    _ = try first.validated(for: phase)
    let cursor = try #require(first.pageCursor())
    let merged = try first.appendingPage(detail("\u{301}🙂\r\nZ", offset: 1, total: 6), cursor: cursor)
    #expect(merged.sections[0].text.utf8.elementsEqual("e\u{301}🙂\r\nZ".utf8))
    #expect(merged.sections[0].localizedTitle(.english) == "Input")
    #expect(merged.pageCursor() == nil)
}

@Test func legacyFirstPageIsReadableButCannotContinueWithoutAVersion() throws {
    let first = try detail("first", next: 5, total: 10, version: 1)
    _ = try first.validated(for: #require(first.phase))
    #expect(first.pageCursor() == nil && first.requiresReload)
}

@Test func invalidRangesAndNonProgressingPagesAreRejected() throws {
    let phase = try #require(detail("").phase)
    for value in [try detail("e\u{301}", next: 1, total: 3), try detail("", next: 0, total: 3),
                  try detail("too long", total: 2), try detail("x", next: 1, total: 1),
                  try detail("x", token: ""), try detail("x", offset: Int.max, total: Int.max)] {
        #expect(throws: ActivityContentError.self) { try value.validated(for: phase) }
    }
}

@Test func liveFollowingOnlyReplacesWholePrefixGrowthFromTheSameSource() throws {
    let old = try detail("a", state: "partial", source: "observed_stream")
    let grown = try detail("ab", token: "grown", state: "partial", source: "observed_stream")
    #expect(try grown.reconcilingRefresh(from: old, followsLive: true).sections[0].text == "ab")
    #expect(try grown.reconcilingRefresh(from: old).state == "content_changed")
    let rewrite = try detail("b", token: "rewrite", state: "partial", source: "observed_stream")
    #expect(try rewrite.reconcilingRefresh(from: old, followsLive: true).state == "content_changed")
    let history = try detail("a")
    #expect(try history.reconcilingRefresh(from: old, followsLive: true).state == "content_changed")
}

@Test func pythonDetailContractDecodesAndMergesWithoutTranslatingSourceText() throws {
    struct Fixture: Decodable { let pages: [ActivityDetail]; let text: String }
    let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
        .deletingLastPathComponent().deletingLastPathComponent()
    let fixture = try JSONDecoder().decode(Fixture.self, from: Data(contentsOf: root.appendingPathComponent("tests/fixtures/detail-protocol-v2.json")))
    var value = try #require(fixture.pages.first)
    _ = try value.validated(for: #require(value.phase))
    for page in fixture.pages.dropFirst() {
        value = try value.appendingPage(page, cursor: #require(value.pageCursor()))
    }
    #expect(value.sections[0].text.utf8.elementsEqual(fixture.text.utf8))
    #expect(value.sections[0].localizedText(.english).utf8.elementsEqual(fixture.text.utf8))
    #expect(value.pageCursor() == nil)
}
