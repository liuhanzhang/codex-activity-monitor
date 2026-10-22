import SwiftUI
import MonitorModel
import ServiceManagement

/// Only changes that affect menu contents may rebuild its native submenus.
/// Snapshot polling and timeline updates must not reset an open language picker.
struct SettingsMenuState: Equatable {
    let language: MonitorLanguage
    let languageEnabled: Bool
    let configureEnabled: Bool
    let restoreEnabled: Bool
    let loginSelected: Bool
    let loginEditable: Bool
    let loginNeedsApproval: Bool

    @MainActor init(store: DashboardStore) {
        language = store.language
        languageEnabled = !store.busy && store.management?.waitingForCleanup != true
        configureEnabled = !store.readOnly && languageEnabled
        restoreEnabled = !store.readOnly && !store.busy && store.management != nil
        loginSelected = store.loginEnabled
        loginEditable = !store.readOnly && !store.busy && store.testProfile == nil
        loginNeedsApproval = store.loginNeedsApproval
    }
}

struct DashboardSettingsMenu: LocalizedView, Equatable {
    let state: SettingsMenuState
    let store: DashboardStore
    let restore: () -> Void
    let quit: () -> Void
    var language: MonitorLanguage { state.language }

    nonisolated static func == (lhs: Self, rhs: Self) -> Bool {
        lhs.state == rhs.state && lhs.store === rhs.store
    }

    var body: some View {
        Menu {
            Picker(L("语言"), selection: Binding(get: { state.language }, set: { store.setLanguage($0) })) {
                ForEach(MonitorLanguage.allCases, id: \.self) { language in Text(language.name).tag(language) }
            }.accessibilityIdentifier("monitor.language")
                .disabled(!state.languageEnabled)
            Divider()
            Button(L("Configure")) { Task { await store.configure() } }
                .disabled(!state.configureEnabled)
            Button(L("Restore"), action: restore)
                .disabled(!state.restoreEnabled)
            Divider()
            Toggle(L("登录时启动"), isOn: Binding(get: { state.loginSelected }, set: { enabled in
                Task { await store.setLogin(enabled) }
            })).disabled(!state.loginEditable)
            if state.loginNeedsApproval {
                Button(L("在系统设置中允许登录启动")) { SMAppService.openSystemSettingsLoginItems() }
            }
            Divider()
            Button(L("Quit"), action: quit).keyboardShortcut("q")
        } label: { Image(systemName: "gearshape").font(.system(size: 13)).accessibilityLabel(L("设置")) }
        .menuStyle(.borderlessButton).fixedSize().accessibilityLabel(L("设置")).help(L("设置"))
        .menuIndicator(.hidden)
        .accessibilityIdentifier("monitor.settings")
    }
}
