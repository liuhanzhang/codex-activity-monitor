import AppKit
import SwiftUI
import MonitorModel

@MainActor func copyDiagnosticEvent(_ event: DiagnosticEvent, to pasteboard: NSPasteboard = .general) -> Bool {
    pasteboard.clearContents()
    return pasteboard.setString(event.raw, forType: .string)
}

struct DiagnosticLogView: LocalizedView {
    let store: DashboardStore
    @Bindable var diagnostics: DiagnosticsStore
    @State private var copyFailed = false
    var language: MonitorLanguage { store.language }
    private var sources: [String] {
        Array(Set(diagnostics.batch.events.map(\.component) + ["gui", "management", "collector", "bridge", "metadata", "connection",
            "parsing", "tokenizer", "history", "timeline_query", "checkpoint", "logging"] +
                  (diagnostics.source.isEmpty ? [] : [diagnostics.source]))).sorted()
    }
    var body: some View {
        VStack(spacing: 0) {
            filters.padding(12)
            Divider()
            if diagnostics.batch.unavailable {
                Label(L("部分日志暂时无法读取。"), systemImage: "exclamationmark.triangle").foregroundStyle(.orange).padding(8)
            }
            if diagnostics.batch.events.isEmpty {
                ContentUnavailableView(L("没有匹配的日志"), systemImage: "text.alignleft",
                                       description: Text(L("日志在本地保留；关闭接入后仍可查看。")))
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                Table(diagnostics.batch.events, selection: $diagnostics.selection) {
                    TableColumn(L("时间")) { event in Text(event.timestamp?.formatted(.dateTime.month().day().hour().minute().second().locale(language.locale)) ?? event.at).font(.system(size: 11)).monospacedDigit().help(event.at) }.width(min: 110, ideal: 130, max: 160)
                    TableColumn(L("级别")) { event in Text(levelName(event.level)).font(.system(size: 11)) }.width(55)
                    TableColumn(L("来源")) { event in Text(componentName(event.component)).lineLimit(1) }.width(min: 65, ideal: 85, max: 110)
                    TableColumn(L("摘要")) { event in Text(event.summary(language) + ((event.count ?? 1) > 1 ? " · ×\(event.count!)" : "")).lineLimit(2).help(event.summary(language)) }
                }.accessibilityIdentifier("monitor.diagnostic.logs")
            }
            if let event = diagnostics.selectedEvent {
                Divider()
                ScrollView { Text(event.raw).font(.system(size: 11, design: .monospaced)).textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading).padding(12) }.frame(height: 135)
            }
            Divider()
            HStack {
                if diagnostics.batch.limited || diagnostics.batch.invalidLines > 0 {
                    Text(L("仅显示保留范围内的部分记录；不完整或损坏的记录已跳过。"))
                        .font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                if copyFailed { Text(L("复制失败，请重试。")).font(.caption).foregroundStyle(.red) }
                if diagnostics.batch.limited && diagnostics.limit < 1000 {
                    Button(L("加载更多")) { diagnostics.limit = min(1000, diagnostics.limit + 200) }
                }
                Button(L("复制")) {
                    guard let event = diagnostics.selectedEvent else { return }
                    copyFailed = !copyDiagnosticEvent(event)
                }.disabled(diagnostics.selectedEvent == nil)
                Button(L("打开日志目录")) {
                    if let path = store.diagnosticDirectory { NSWorkspace.shared.open(URL(fileURLWithPath: path)) }
                }.disabled(store.diagnosticDirectory == nil)
            }.padding(12)
        }
        .task(id: diagnostics.queryIdentity) {
            try? await Task.sleep(for: .milliseconds(150))
            while !Task.isCancelled {
                await diagnostics.refresh(directory: store.diagnosticDirectory)
                do { try await Task.sleep(for: .seconds(1)) } catch { return }
            }
        }
    }
    private var filters: some View {
        HStack(spacing: 8) {
            TextField(L("搜索日志"), text: $diagnostics.search).textFieldStyle(.roundedBorder)
                .accessibilityIdentifier("monitor.diagnostic.search")
            Picker(L("级别"), selection: $diagnostics.level) {
                Text(L("全部级别")).tag("")
                ForEach(["info", "warning", "error"], id: \.self) { Text(levelName($0)).tag($0) }
            }.labelsHidden().frame(width: 100)
            Picker(L("来源"), selection: $diagnostics.source) {
                Text(L("全部来源")).tag("")
                ForEach(sources, id: \.self) { Text(componentName($0)).tag($0) }
            }.labelsHidden().frame(width: 110)
        }
    }
    private func componentName(_ code: String) -> String { localizedText("component." + code, language: language) }
    private func levelName(_ level: String) -> String { localizedText("level." + level, language: language) }
}
