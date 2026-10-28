import AppKit
import SwiftUI
import MonitorModel
import ServiceManagement

enum SettingsPage: String, CaseIterable, Identifiable {
    case general, health, logs
    var id: Self { self }
    var title: String { switch self { case .general: "通用"; case .health: "运行状态"; case .logs: "日志" } }
    var symbol: String { switch self { case .general: "gearshape"; case .health: "waveform.path.ecg"; case .logs: "list.bullet.rectangle" } }
}

@MainActor final class SettingsWindowController: NSWindowController {
    private let store: DashboardStore
    init(store: DashboardStore) {
        self.store = store
        let window = ActivityWindow(contentRect: NSRect(x: 0, y: 0, width: 760, height: 560),
                                    styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.identifier = NSUserInterfaceItemIdentifier("monitor.settings.window")
        window.contentMinSize = NSSize(width: 680, height: 460)
        window.isReleasedWhenClosed = false
        super.init(window: window)
        let host = NSHostingController(rootView: SettingsView(store: store))
        host.sizingOptions = []
        window.contentViewController = host
        if !window.setFrameUsingName(store.readOnly ? "SettingsPreview" : "Settings") { window.center() }
        window.setFrameAutosaveName(store.readOnly ? "SettingsPreview" : "Settings")
        updateLanguage()
    }
    required init?(coder: NSCoder) { nil }
    func updateLanguage() { window?.title = localizedText("设置", language: store.language) }
    func show() { window?.makeKeyAndOrderFront(nil); NSApplication.shared.activate(ignoringOtherApps: true) }
}

struct SettingsView: LocalizedView {
    @Bindable var store: DashboardStore
    var language: MonitorLanguage { store.language }
    @State private var page: SettingsPage? = .general
    @State private var diagnostics = DiagnosticsStore()
    @State private var uninstall = false

    var body: some View {
        NavigationSplitView {
            List(SettingsPage.allCases, selection: $page) { item in
                Label(L(item.title), systemImage: item.symbol).tag(item)
                    .accessibilityIdentifier("monitor.settings.page." + item.rawValue)
            }.navigationSplitViewColumnWidth(min: 150, ideal: 170, max: 210)
                .toolbar(removing: .sidebarToggle)
        } detail: {
            Group {
                switch page ?? .general {
                case .general: general
                case .health: health
                case .logs: DiagnosticLogView(store: store, diagnostics: diagnostics)
                }
            }
            .navigationTitle(L("设置"))
        }
        .environment(\.monitorLanguage, language).environment(\.locale, language.locale)
        .sheet(isPresented: $uninstall) { UninstallSettingsSheet(store: store) }
    }

    private var general: some View {
        Form {
            Section(L("应用设置")) {
                Picker(L("语言"), selection: Binding(get: { store.language }, set: { store.setLanguage($0) })) {
                    ForEach(MonitorLanguage.allCases, id: \.self) { Text($0.name).tag($0) }
                }.accessibilityIdentifier("monitor.language").disabled(store.busy)
                Toggle(L("登录时启动"), isOn: Binding(get: { store.loginEnabled }, set: { value in Task { await store.setLogin(value) } }))
                    .disabled(store.readOnly || store.busy || store.testProfile != nil)
                    .accessibilityIdentifier("monitor.login")
                if store.loginNeedsApproval {
                    Button(L("在系统设置中允许登录启动")) { SMAppService.openSystemSettingsLoginItems() }
                }
            }
            Section(L("Agent 接入")) {
                Toggle("Codex", isOn: Binding(get: { store.integrationEnabled }, set: { enabled in
                    Task { if enabled { await store.configure() } else { await store.restore(purge: false) } }
                })).disabled(!store.integrationCanToggle).accessibilityIdentifier("monitor.integration.codex")
                Text(store.integrationStatusText).font(.callout).foregroundStyle(.secondary).textSelection(.enabled)
                    .accessibilityIdentifier("monitor.integration.status")
                if let status = store.management?.integration_status, status.restart == "required", !store.integrationEnabled {
                    Text(L("配置已恢复，重开 Codex 后使用恢复后的配置。"))
                        .font(.callout).foregroundStyle(.secondary)
                }
                if store.management?.state == "error" || store.management?.integration_status?.confirmedEnabled == nil && store.management != nil {
                    Button(L("继续恢复配置")) { Task { await store.restore(purge: false) } }.disabled(store.busy || store.readOnly)
                }
                if store.management?.state == "configured", store.management?.integration_status?.configuration == "partial" {
                    Button(L("重新配置接入")) { Task { await store.configure() } }.disabled(store.busy || store.readOnly)
                }
                if store.operationError != nil || store.managementCheckFailed || !store.fresh && store.integrationEnabled {
                    Button(L("重试检查或接入")) { Task { await store.retryIntegration() } }.disabled(store.busy || store.readOnly)
                }
            }
            if let error = store.operationError {
                Section(L("上次操作")) {
                    Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(.red).textSelection(.enabled)
                    Button(L("清除提示")) { store.operationError = nil }
                }
            }
            Section(L("维护")) {
                Button(L("清理本机数据并退出，用于卸载")) { uninstall = true }.disabled(store.busy || store.readOnly)
                Button(L("Quit")) { NSApplication.shared.terminate(nil) }.keyboardShortcut("q")
            }
        }.formStyle(.grouped)
    }

    private var health: some View {
        Form {
            Section {
                Text(L("显示已检测项目的状态；不代表已经排除所有程序错误。"))
                    .foregroundStyle(.secondary).font(.callout)
            }
            Section(L("组件状态")) {
                ForEach(store.diagnosticComponents) { component in
                    VStack(alignment: .leading, spacing: 6) {
                        HStack {
                            Text(componentName(component.component)).fontWeight(.medium)
                            Spacer()
                            Label(healthName(component.state), systemImage: symbol(component.state))
                                .foregroundStyle(color(component.state))
                        }
                        Text(component.summary(language)).foregroundStyle(.secondary)
                        if ["degraded", "unavailable"].contains(component.state) {
                            Button(L("查看相关日志")) { diagnostics.source = component.component; page = .logs }
                                .buttonStyle(.link)
                        }
                    }.padding(.vertical, 3)
                }
            }
            if let at = store.management?.integration_status?.checked_at {
                Section { LabeledContent(L("最近配置检查"), value: Date(timeIntervalSince1970: at).formatted(.dateTime.hour().minute().second().locale(language.locale))) }
            }
        }.formStyle(.grouped)
    }

    private func symbol(_ state: String) -> String {
        switch state { case "healthy": "checkmark.circle"; case "degraded": "exclamationmark.triangle";
        case "unavailable": "xmark.circle"; case "disabled": "pause.circle"; default: "questionmark.circle" }
    }
    private func componentName(_ code: String) -> String { localizedText("component." + code, language: language) }
    private func healthName(_ state: String) -> String { localizedText("health." + state, language: language) }
    private func color(_ state: String) -> Color {
        switch state { case "healthy": .green; case "degraded": .orange; case "unavailable": .red; default: .secondary }
    }
}

private struct UninstallSettingsSheet: LocalizedView {
    let store: DashboardStore
    var language: MonitorLanguage { store.language }
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(L("清理本机数据并退出，用于卸载")).font(.headline)
            Text(L("恢复接入配置并清理 Monitor 的设置、日志和派生数据。请结束任务并退出 Codex，等待清理完成后再删除 App。"))
                .fixedSize(horizontal: false, vertical: true)
            HStack {
                Spacer()
                Button(L("取消")) { dismiss() }.keyboardShortcut(.cancelAction)
                Button(L("恢复配置并清理")) { dismiss(); Task { await store.restore(purge: true) } }
                    .keyboardShortcut(.defaultAction)
            }
        }.padding(20).frame(width: 440)
    }
}
