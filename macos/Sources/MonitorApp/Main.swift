import AppKit
import SwiftUI
import ServiceManagement

@main
enum MonitorApplication {
    @MainActor static func main() {
        if CommandLine.arguments.contains("--login-remove") {
            do {
                if SMAppService.mainApp.status != .notRegistered { try SMAppService.mainApp.unregister() }
                print("{\"ok\":true}")
            } catch {
                let data = try? JSONSerialization.data(withJSONObject: ["ok": false, "error": String(describing: error)])
                print(data.flatMap { String(data: $0, encoding: .utf8) } ?? "{\"ok\":false}")
                exit(1)
            }
            return
        }
        if let index = CommandLine.arguments.firstIndex(of: "--login-probe"), index + 1 < CommandLine.arguments.count {
            let service = SMAppService.mainApp
            var result: [String: String] = [:]
            do {
                try service.register()
                result["registered_status"] = String(describing: service.status)
                try service.unregister()
                result["final_status"] = String(describing: service.status)
            } catch { result["error"] = String(describing: error); try? service.unregister() }
            try? JSONSerialization.data(withJSONObject: result).write(to: URL(fileURLWithPath: CommandLine.arguments[index + 1]))
            return
        }
        let app = NSApplication.shared
        let delegate = AppDelegate()
        app.delegate = delegate
        app.setActivationPolicy(.accessory)
        app.run()
        withExtendedLifetime(delegate) {}
    }
}

@MainActor final class AppDelegate: NSObject, NSApplicationDelegate {
    private var item: NSStatusItem!
    private var popover: NSPopover!
    private var store: DashboardStore!
    private var previewWindow: NSWindow?

    func applicationDidFinishLaunching(_ notification: Notification) {
        do {
            guard let url = Bundle.main.url(forResource: "backend", withExtension: "json") else {
                throw CocoaError(.fileNoSuchFile)
            }
            let config = try JSONDecoder().decode(BackendConfiguration.self, from: Data(contentsOf: url))
            store = DashboardStore(configuration: config, arguments: CommandLine.arguments)
            if isPreview && CommandLine.arguments.contains("--preview-dark") {
                NSApplication.shared.appearance = NSAppearance(named: .darkAqua)
            }
            item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
            if let button = item.button {
                button.image = NSImage(systemSymbolName: "waveform.path", accessibilityDescription: "Codex Monitor")
                button.imagePosition = .imageLeading
                button.font = .monospacedDigitSystemFont(ofSize: 12, weight: .regular)
                button.title = " —"
                button.toolTip = L("Codex 生成速度 · 点击查看实时活动")
                button.target = self
                button.action = #selector(togglePopover)
                button.setAccessibilityIdentifier("monitor.status")
            }
            popover = NSPopover()
            popover.behavior = .transient
            popover.contentViewController = NSHostingController(rootView: dashboard)
            store.onUpdate = { [weak self] in self?.item.button?.title = " " + (self?.store.menuTitle ?? "—") }
            store.onPurgeComplete = { NSApplication.shared.terminate(nil) }
            store.start()
            if CommandLine.arguments.contains("--window") {
                let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 720, height: 520),
                                      styleMask: [.titled, .closable], backing: .buffered, defer: false)
                window.title = store.testProfile != nil ? L("Codex Monitor · 隔离验证") : (store.readOnly ? L("Codex Monitor · 只读验证") : "Codex Monitor")
                window.contentViewController = NSHostingController(rootView: dashboard)
                window.center()
                window.makeKeyAndOrderFront(nil)
                NSApplication.shared.activate(ignoringOtherApps: true)
                previewWindow = window
            }
            if CommandLine.arguments.contains("--show") {
                Task { try? await Task.sleep(for: .milliseconds(400)); togglePopover() }
            }
        } catch {
            let alert = NSAlert()
            alert.messageText = L("无法启动 Codex Monitor")
            alert.informativeText = "请使用 bin/build-gui 重新构建本机 App。\n\(error.localizedDescription)"
            alert.runModal()
            NSApplication.shared.terminate(nil)
        }
    }

    private var isPreview: Bool { store.readOnly || store.testProfile != nil }

    @ViewBuilder private var dashboard: some View {
        DashboardView(store: store) { NSApplication.shared.terminate(nil) }
    }

    @objc private func togglePopover() {
        guard let button = item.button else { return }
        if popover.isShown { popover.performClose(nil) }
        else {
            NSApplication.shared.activate(ignoringOtherApps: true)
            popover.show(relativeTo: button.bounds, of: button, preferredEdge: .minY)
        }
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if !flag && popover != nil && !popover.isShown { togglePopover() }
        return true
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard let store, !store.readOnly, store.management?.state != "purged" else { return .terminateNow }
        if store.busy {
            store.operationError = L("管理操作仍在完成，请稍后退出。")
            return .terminateCancel
        }
        Task {
            let ready = await store.prepareQuit()
            sender.reply(toApplicationShouldTerminate: ready)
        }
        return .terminateLater
    }
}
