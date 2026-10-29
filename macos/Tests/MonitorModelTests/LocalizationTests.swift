import Foundation
import Testing
@testable import MonitorModel

@Test func initialLanguageUsesOnlyThePrimarySystemLanguage() {
    for identifier in ["zh", "zh-CN", "zh-Hans-CN", "zh-Hant-TW", "zh-HK", "ZH_cn"] {
        #expect(MonitorLanguage.systemDefault(preferredLanguages: [identifier, "en"]) == .chinese)
    }
    for languages in [["en-US", "zh-Hans"], ["ja-JP", "zh-Hans"], ["fr-FR"], ["de-DE"], ["zho"], [], [""]] {
        #expect(MonitorLanguage.systemDefault(preferredLanguages: languages) == .english)
    }
}

@Test func localizationMatchesEveryExportedPythonTemplate() throws {
    struct Example: Decodable { let language: MonitorLanguage; let message: DisplayMessage; let expected: String }
    let url = try #require(Bundle.module.url(forResource: "LocalizationExamples", withExtension: "json"))
    let examples = try JSONDecoder().decode([Example].self, from: Data(contentsOf: url))
    #expect(examples.count > 300)
    for sample in examples {
        #expect(sample.message.rendered(in: sample.language) == sample.expected,
                "\(sample.language.rawValue) / \(sample.message.key)")
    }
}

@Test func localizationMalformedMessagesAndLegacyDataAreConservative() throws {
    for json in ["null", "[]", "42", "{\"key\":\"missing.key\"}",
                 "{\"key\":\"time.minutes\",\"params\":{\"minutes\":2,\"seconds\":\"bad\"}}",
                 "{\"key\":\"activity.reasoning\",\"params\":null}",
                 "{\"key\":\"activity.named\",\"params\":{\"name\":[]}}"] {
        let message = try JSONDecoder().decode(DisplayMessage.self, from: Data(json.utf8))
        #expect(message.rendered(in: .english) == "Processing")
    }
    let snapshot = try Snapshot.decode(Data("""
        {"schema_version":1,"threads":[{"id":"a","display_label":"旧中文状态","status":"inProgress"},
         {"id":"b","name":"保留中文标题","display_message":{"key":"activity.running","params":{"target":"测试.py"}}}]}
        """.utf8))
    #expect(snapshot.threads[0].localizedTitle(.english) == "Untitled chat")
    #expect(snapshot.threads[0].activityLabel(.english) == "Status unavailable")
    #expect(snapshot.threads[0].activityLabel(.chinese) == "旧中文状态")
    #expect(snapshot.threads[1].localizedTitle(.english) == "保留中文标题")
    #expect(snapshot.threads[1].activityLabel(.english) == "Running · 测试.py")
    let phase = try timelinePhase(["kind": "tool_input", "namespace": "input", "label_message": ["key": "activity.named", "params": ["name": "服务.工具"]]])
    #expect(phase.sourceLabel(in: .english) == "服务.工具")
    #expect(phase.kindLabel(in: .english) == "Tool params")
    #expect(phase.accessibleLabel(at: 3000, language: .english).contains("Completed"))
    #expect(phase.duration(at: 3000) == 2)
    let legacyText = try timelinePhase(["label": "正文输出"])
    #expect(legacyText.presentationLabel(language: .chinese) == "回复")
    #expect(legacyText.label == "正文输出")
    let legacyCompaction = try timelinePhase(["kind": "contextCompaction", "namespace": "canonical", "label": "整理上下文"])
    #expect(legacyCompaction.presentationLabel(language: .chinese) == "压缩")
    #expect(legacyCompaction.label == "整理上下文")
}

@Test func localizationSectionsRetainMessagesAcrossPaginationAndRawContent() throws {
    func section(_ text: String, offset: Int, next: Int?) throws -> ActivitySection {
        let value: [String: Any] = ["id": "input", "title": "参数", "format": "code", "text": text,
            "offset": offset, "next_offset": next.map { $0 as Any } ?? NSNull(),
            "title_message": ["key": "detail.input", "params": [:]]]
        return try JSONDecoder().decode(ActivitySection.self, from: JSONSerialization.data(withJSONObject: value))
    }
    let first = try section("原始中文{内容}", offset: 0, next: 8)
    let second = try section("\n保留原文", offset: 8, next: nil)
    let merged = first.appendingPage(second)
    #expect(merged.localizedTitle(.english) == "Input")
    #expect(merged.localizedText(.english) == "原始中文{内容}\n保留原文")
    #expect(merged.next_offset == nil)
}
