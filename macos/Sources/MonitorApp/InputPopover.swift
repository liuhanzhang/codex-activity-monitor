import AppKit
import SwiftUI
import MonitorModel
import Observation

struct InputPopoverRequest: Identifiable {
    let id = UUID()
    let phase: TimelinePhase
}

/// Scoped to one presented input. The phase's entire identity is immutable.
@MainActor @Observable final class InputContentStore {
    let phase: TimelinePhase
    let client: any TimelineReading
    private(set) var detail: ActivityDetail?
    private(set) var error: String?
    private(set) var loading = false
    private var serial = 0

    init(phase: TimelinePhase, client: any TimelineReading) { self.phase = phase; self.client = client }
    func invalidate() { serial += 1; loading = false }

    func load() async {
        serial += 1
        let token = serial
        loading = true; error = nil
        defer { if token == serial { loading = false } }
        do {
            let value = try await client.detail(phase: phase, page: nil).validated(for: phase)
            guard !Task.isCancelled, token == serial else { return }
            detail = try value.reconcilingRefresh(from: detail, reload: true)
        } catch {
            guard !Task.isCancelled, token == serial else { return }
            self.error = queryFailureKey(error, fallback: "暂时无法读取活动内容")
        }
    }

    func loadMore() async {
        guard !loading, let cursor = detail?.pageCursor() else { return }
        serial += 1
        let token = serial
        loading = true; error = nil
        defer { if token == serial { loading = false } }
        do {
            let value = try await client.detail(phase: phase, page: cursor)
            guard !Task.isCancelled, token == serial, let prior = detail else { return }
            detail = try prior.appendingPage(value, cursor: cursor)
        } catch {
            guard !Task.isCancelled, token == serial else { return }
            self.error = queryFailureKey(error, fallback: "暂时无法读取更多内容")
        }
    }

    var notice: String? {
        guard let detail else { return nil }
        if detail.version == 1 && detail.requiresReload { return "当前来源不支持继续加载，请更新 Monitor 后重新读取。" }
        switch detail.state {
        case "content_changed": return "源内容已变化。保留已加载内容，请重新读取。"
        case "available": return nil
        case "source_truncated": return "来源已截断，以下是 Codex 保留的全部可用内容。"
        case "not_retained": return "这段未完成的内容未被 Codex 历史保留。"
        case "partial": return "当前内容为部分观测，完成后将从历史补齐。"
        case "pending": return "完整内容尚未写入历史。"
        case "missing", "no_content": return "源历史已移除或当前内容不可用。"
        default: return "暂时无法读取源历史，可稍后重试。"
        }
    }
}

private enum InputLoadAction: Hashable { case refresh(Int), next(Int) }

struct InputPopoverView: LocalizedView {
    @Environment(\.monitorLanguage) var language
    @State private var store: InputContentStore
    @State private var action = InputLoadAction.refresh(0)
    private let close: () -> Void

    init(phase: TimelinePhase, client: any TimelineReading, close: @escaping () -> Void) {
        _store = State(initialValue: InputContentStore(phase: phase, client: client))
        self.close = close
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(L("本轮用户输入")).fontWeight(.medium)
                Spacer()
                if let detail = store.detail, detail.sections.contains(where: { $0.notice == nil }) {
                    Button {
                        NSPasteboard.general.clearContents()
                        NSPasteboard.general.setString(detail.sections.filter { $0.notice == nil }.map(\.text).joined(separator: "\n"), forType: .string)
                    } label: { Image(systemName: "doc.on.doc") }
                        .help(L("复制当前已加载内容")).accessibilityLabel(L("复制当前已加载内容"))
                        .accessibilityIdentifier("monitor.input.copy")
                }
                Button(action: close) { Image(systemName: "xmark") }
                    .accessibilityLabel(L("关闭输入浮层")).accessibilityIdentifier("monitor.input.close")
            }.buttonStyle(.borderless)
            if store.loading { ProgressView().controlSize(.small) }
            if let detail = store.detail, !detail.sections.isEmpty {
                ScrollView(.vertical) {
                    VStack(alignment: .leading, spacing: 8) {
                        ForEach(detail.sections) { section in
                            if let notice = section.notice { Text(section.localizedNotice(language) ?? L(notice)).foregroundStyle(.secondary) }
                            if section.notice == nil || !section.text.isEmpty {
                                Text(section.text.isEmpty ? L("来源记录为空字符串。") : section.localizedText(language))
                                    .frame(maxWidth: .infinity, alignment: .leading).textSelection(.enabled)
                            }
                        }
                    }.frame(maxWidth: .infinity, alignment: .leading)
                }.frame(maxHeight: 220).fixedSize(horizontal: false, vertical: true)
                    .accessibilityIdentifier("monitor.input.content")
                if detail.pageCursor() != nil {
                    Button(L("继续加载完整内容")) { action = .next(version + 1) }.disabled(store.loading)
                }
            }
            if let notice = store.notice { Text(L(notice)).font(.system(size: 10)).foregroundStyle(.secondary) }
            if let error = store.error { Text(L(error)).foregroundStyle(.secondary) }
            if store.error != nil || store.detail?.requiresReload == true || (store.detail != nil && store.detail?.state != "available") {
                Button(L("重新读取")) { action = .refresh(version + 1) }.disabled(store.loading)
            }
        }
        .font(.system(size: 12)).padding(14).frame(width: 350)
        .task(id: action) {
            switch action { case .refresh: await store.load(); case .next: await store.loadMore() }
        }
        .onDisappear { store.invalidate() }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("monitor.input.popover")
    }

    private var version: Int { switch action { case .refresh(let n), .next(let n): n } }
}
