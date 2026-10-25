import AppKit
import SwiftUI
import ServiceManagement
import MonitorModel

@main
enum MonitorApplication {
    @MainActor static func main() {
        if let index = CommandLine.arguments.firstIndex(of: "--probe-status-cadence"), index + 1 < CommandLine.arguments.count {
            StatusPreviewRenderer.probeCadence(to: URL(fileURLWithPath: CommandLine.arguments[index + 1]))
            return
        }
        for (flag, pet, mode) in [("--render-idle-pet-previews", true, MenuMode.idle),
                                  ("--render-idle-beach-previews", false, .idle),
                                  ("--render-hammer-previews", false, .executing)] {
            if let index = CommandLine.arguments.firstIndex(of: flag), index + 1 < CommandLine.arguments.count {
                _ = NSApplication.shared
                do {
                    try StatusPreviewRenderer.render(to: URL(fileURLWithPath: CommandLine.arguments[index + 1]), pet: pet, mode: mode)
                } catch { fputs("\(error)\n", stderr); exit(1) }
                return
            }
        }
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
    private func L(_ key: String) -> String {
        localizedText(key, language: store?.language ?? DashboardStore.savedLanguage(at: NSHomeDirectory() + "/Library/Application Support/CodexProgressMonitor"))
    }
    private var item: NSStatusItem!
    private var panel: PopoverController!
    private var statusContent: StatusContentView!
    private var store: DashboardStore!
    private var previewWindow: NSWindow?
    private let diagnosticsStartedAt = Date()
    private var fullscreenObserver: NSObjectProtocol?
    private var activityViewer: ActivityViewerWindowController?

    func applicationDidFinishLaunching(_ notification: Notification) {
        do {
            guard let url = Bundle.main.url(forResource: "backend", withExtension: "json") else {
                throw CocoaError(.fileNoSuchFile)
            }
            let config = try JSONDecoder().decode(BackendConfiguration.self, from: Data(contentsOf: url))
            var arguments = CommandLine.arguments
            if Bundle.main.bundleIdentifier?.hasSuffix(".preview") == true && !arguments.contains("--test-profile") {
                arguments.append("--read-only")
            }
            store = DashboardStore(configuration: config, arguments: arguments)
            if isPreview {
                store.showRecent = arguments.contains("--preview-recent")
            }
            if isPreview && CommandLine.arguments.contains("--preview-dark") {
                NSApplication.shared.appearance = NSAppearance(named: .darkAqua)
            } else if isPreview && CommandLine.arguments.contains("--preview-light") {
                NSApplication.shared.appearance = NSAppearance(named: .aqua)
            }
            if isPreview && CommandLine.arguments.contains("--preview-autohide") {
                // Per-application presentation only; never changes OS preferences.
                NSApplication.shared.presentationOptions = [.autoHideMenuBar, .autoHideDock]
            }
            item = NSStatusBar.system.statusItem(withLength: StatusContentView.minimumItemWidth)
            if let button = item.button {
                button.title = ""
                statusContent = StatusContentView(frame: button.bounds)
                statusContent.autoresizingMask = [.width, .height]
                statusContent.forceReducedMotion = isPreview && arguments.contains("--preview-reduce-motion")
                statusContent.recordsFrameCadence = arguments.contains("--ui-diagnostics")
                statusContent.onModeDrawn = { [weak self] in self?.writeDiagnostics("draw") }
                button.addSubview(statusContent)
                button.toolTip = L("Codex 生成速度 · 点击查看实时活动")
                button.target = self
                button.action = #selector(togglePopover)
                button.setAccessibilityIdentifier("monitor.status")
                panel = PopoverController(button: button, store: store) { NSApplication.shared.terminate(nil) }
                panel.onTrace = { [weak self] in self?.writeDiagnostics($0) }
            }
            store.onUpdate = { [weak self] in
                guard let self else { return }
                let width = StatusContentView.itemWidth(for: self.store.menuMode, title: self.store.menuTitle)
                if self.item.length != width {
                    self.item.length = width
                    self.panel.statusItemDidResize()
                }
                self.statusContent.update(mode: self.store.menuMode, title: self.store.menuTitle)
                let state = switch self.store.menuMode {
                case .idle: self.L("空闲 · 暂无活动聊天")
                case .thinking: self.L("思考中")
                case .executing: self.L("执行中")
                case .unavailable: self.store.connectionDetail ?? self.store.connectionLabel
                case .speed: self.store.menuTitle
                }
                self.item.button?.toolTip = state + " · " + self.L("点击查看实时活动")
                self.item.button?.setAccessibilityLabel("Codex Monitor · " + state)
                self.activityViewer?.updateLanguage()
                self.previewWindow?.title = self.store.testProfile != nil ? self.L("Codex Monitor · 隔离验证") : (self.store.readOnly ? self.L("Codex Monitor · 只读验证") : "Codex Monitor")
                self.writeDiagnostics("tick")
            }
            store.onPurgeComplete = { NSApplication.shared.terminate(nil) }
            store.onActivitySelected = { [weak self] timeline, phases in
                guard let self else { return }
                self.panel.close(reason: "activity-viewer")
                if self.activityViewer == nil {
                    self.activityViewer = ActivityViewerWindowController(preview: self.isPreview, dashboard: self.store)
                    self.activityViewer?.onTrace = { [weak self] in self?.writeDiagnostics($0) }
                }
                self.activityViewer?.show(timeline: timeline, phases: phases)
            }
            store.start()
            if CommandLine.arguments.contains("--window") {
                store.panelVisible = true
                let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 420, height: 300),
                                      styleMask: [.titled, .closable, .resizable], backing: .buffered, defer: false)
                window.title = store.testProfile != nil ? L("Codex Monitor · 隔离验证") : (store.readOnly ? L("Codex Monitor · 只读验证") : "Codex Monitor")
                let host = NSHostingController(rootView: DashboardView(store: store, quit: { NSApplication.shared.terminate(nil) }))
                host.sizingOptions = [.preferredContentSize]
                window.contentViewController = host
                window.center()
                window.makeKeyAndOrderFront(nil)
                NSApplication.shared.activate(ignoringOtherApps: true)
                previewWindow = window
                if isPreview && CommandLine.arguments.contains("--preview-fullscreen") {
                    window.collectionBehavior = [.fullScreenPrimary]
                    fullscreenObserver = NotificationCenter.default.addObserver(forName: NSWindow.didEnterFullScreenNotification, object: window, queue: .main) { [weak self] _ in
                        MainActor.assumeIsolated { self?.panel.show(); self?.writeDiagnostics("fullscreen") }
                    }
                    window.toggleFullScreen(nil)
                }
            }
            if CommandLine.arguments.contains("--show") && !CommandLine.arguments.contains("--preview-fullscreen") {
                Task { try? await Task.sleep(for: .milliseconds(400)); togglePopover() }
            }
        } catch {
            let alert = NSAlert()
            alert.messageText = L("无法启动 Codex Monitor")
            alert.informativeText = L("请使用 bin/build-gui 重新构建本机 App。") + "\n" + error.localizedDescription
            alert.addButton(withTitle: L("关闭提示"))
            alert.runModal()
            NSApplication.shared.terminate(nil)
        }
    }

    private var isPreview: Bool { store.readOnly || store.testProfile != nil }

    @objc private func togglePopover() {
        panel?.toggle()
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if activityViewer?.window?.isVisible == true { activityViewer?.window?.makeKeyAndOrderFront(nil); return true }
        if panel != nil && !panel.popover.isShown { panel.show() }
        return true
    }

    func applicationWillTerminate(_ notification: Notification) {
        activityViewer?.close()
        panel?.stop(); statusContent?.stop()
        if let fullscreenObserver { NotificationCenter.default.removeObserver(fullscreenObserver) }
    }

    private func writeDiagnostics(_ event: String) {
        guard let i = CommandLine.arguments.firstIndex(of: "--ui-diagnostics"), i + 1 < CommandLine.arguments.count,
              let panel, let statusContent else { return }
        if let limit = CommandLine.arguments.firstIndex(of: "--ui-diagnostics-seconds"),
           limit + 1 < CommandLine.arguments.count,
           let seconds = Double(CommandLine.arguments[limit + 1]), seconds.isFinite, seconds > 0,
           Date().timeIntervalSince(diagnosticsStartedAt) >= seconds { return }
        var value = panel.diagnostics
        value["event"] = event; value["at"] = Date().timeIntervalSince1970
        value["snapshot_updated_at"] = store.snapshot?.updated_at
        value["state_instance_id"] = store.liveInstanceID
        value["state_sequence"] = store.liveSequence
        value["transport_fresh"] = store.fresh
        value["management_state"] = store.management?.state
        value["mode"] = store.menuMode.rawValue; value["title"] = store.menuTitle
        value["status_tooltip"] = item.button?.toolTip
        value["connection_label"] = store.connectionLabel
        value["observation_notice"] = store.snapshot?.observationNotice?.rawValue
        value["animating"] = statusContent.animating; value["reduced_motion"] = statusContent.reducedMotion
        value["animation_frames"] = statusContent.frameDiagnostics
        value["build_id"] = store.configuration.build_id
        value["pid"] = ProcessInfo.processInfo.processIdentifier
        value["fullscreen"] = previewWindow?.styleMask.contains(.fullScreen) ?? false
        func viewports(_ view: NSView) -> [[String: Any]] {
            if let scroll = view as? TimelineScrollView { return [scroll.viewportDiagnostics?() ?? [:]] }
            return view.subviews.flatMap { viewports($0) }
        }
        value["timeline_viewports"] = NSApplication.shared.windows.filter(\.isVisible).flatMap {
            $0.contentView.map { viewports($0) } ?? []
        }
        if let data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]),
           let line = (String(data: data, encoding: .utf8).map { $0 + "\n" })?.data(using: .utf8) {
            let url = URL(fileURLWithPath: CommandLine.arguments[i + 1])
            if !FileManager.default.fileExists(atPath: url.path) { FileManager.default.createFile(atPath: url.path, contents: nil) }
            if let handle = try? FileHandle(forWritingTo: url) { _ = try? handle.seekToEnd(); try? handle.write(contentsOf: line); try? handle.close() }
        }
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard let store, !store.readOnly, store.management?.state != "purged" else { return .terminateNow }
        if store.busy {
            store.operationError = "管理操作仍在完成，请稍后退出。"
            return .terminateCancel
        }
        Task {
            let ready = await store.prepareQuit()
            sender.reply(toApplicationShouldTerminate: ready)
        }
        return .terminateLater
    }
}
