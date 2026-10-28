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
        localizedText(key, language: store?.language ?? DashboardStore.savedLanguage(at: NSHomeDirectory() + "/Library/Application Support/CodexActivityMonitor"))
    }
    private var item: NSStatusItem!
    private var panel: PopoverController!
    private var statusContent: StatusContentView!
    private var store: DashboardStore!
    private var previewWindow: NSWindow?
    private let diagnosticsStartedAt = Date()
    private var fullscreenObserver: NSObjectProtocol?
    private var activityViewer: ActivityViewerWindowController?
    private var settingsWindow: SettingsWindowController?
    private var settingsMenuItem: NSMenuItem?
    private var quitMenuItem: NSMenuItem?
    private var editMenu: NSMenu?
    private var editMenuItem: NSMenuItem?
    private var statusCaptureIndex = 0

    func applicationDidFinishLaunching(_ notification: Notification) {
        do {
            let config = try BackendConfiguration.load(bundleURL: Bundle.main.bundleURL)
            var arguments = CommandLine.arguments
            if Bundle.main.bundleIdentifier?.hasSuffix(".preview") == true && !arguments.contains("--test-profile") {
                arguments.append("--read-only")
            }
            store = DashboardStore(configuration: config, arguments: arguments)
            store.onSettingsRequested = { [weak self] in
                self?.showSettings(nil)
            }
            installApplicationMenu()
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
                self.capturePreviewStatus()
                let state = switch self.store.menuMode {
                case .idle: self.L("空闲 · 暂无活动聊天")
                case .thinking: self.L("思考中")
                case .executing: self.L("执行中")
                case .unavailable: self.store.connectionDetail ?? self.store.connectionLabel
                case .speed: self.store.menuTitle
                }
                self.item.button?.toolTip = state + " · " + self.L("点击查看实时活动")
                self.item.button?.setAccessibilityLabel("Codex Activity Monitor · " + state)
                self.activityViewer?.updateLanguage()
                self.settingsWindow?.updateLanguage()
                self.settingsMenuItem?.title = self.L("设置…")
                self.quitMenuItem?.title = self.L("Quit")
                self.editMenu?.title = self.L("编辑")
                self.editMenuItem?.title = self.L("编辑")
                let editorKeys = ["撤销", "重做", "剪切", "复制", "粘贴", "全选"]
                for (item, key) in zip(self.editMenu?.items.filter { !$0.isSeparatorItem } ?? [], editorKeys) { item.title = self.L(key) }
                self.previewWindow?.title = self.store.testProfile != nil ? self.L("Codex Activity Monitor · 隔离验证") : (self.store.readOnly ? self.L("Codex Activity Monitor · 只读验证") : "Codex Activity Monitor")
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
            if arguments.contains("--settings") { store.onSettingsRequested?() }
            if CommandLine.arguments.contains("--window") {
                store.panelVisible = true
                let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 420, height: 300),
                                      styleMask: [.titled, .closable, .resizable], backing: .buffered, defer: false)
                window.title = store.testProfile != nil ? L("Codex Activity Monitor · 隔离验证") : (store.readOnly ? L("Codex Activity Monitor · 只读验证") : "Codex Activity Monitor")
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
            alert.messageText = L("无法启动 Codex Activity Monitor")
            alert.informativeText = L("请使用 bin/build-gui 重新构建本机 App。") + "\n" + error.localizedDescription
            alert.addButton(withTitle: L("关闭提示"))
            alert.runModal()
            NSApplication.shared.terminate(nil)
        }
    }

    private var isPreview: Bool { store.readOnly || store.testProfile != nil }

    /// Export the actual status button for isolated documentation captures.
    /// Never enabled for a daily runtime or a preview without explicit fixtures.
    private func capturePreviewStatus() {
        let args = CommandLine.arguments
        guard store.readOnly, store.snapshotOverride != nil, args.contains("--timeline-fixture"),
              let index = args.firstIndex(of: "--capture-status-to"), index + 1 < args.count,
              let button = item.button,
              let bitmap = button.bitmapImageRepForCachingDisplay(in: button.bounds) else { return }
        button.cacheDisplay(in: button.bounds, to: bitmap)
        guard let png = bitmap.representation(using: .png, properties: [:]) else { return }
        let directory = URL(fileURLWithPath: args[index + 1], isDirectory: true)
        do {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            let name = String(format: "status-%06d.png", statusCaptureIndex)
            try png.write(to: directory.appendingPathComponent(name), options: .atomic)
            let record: [String: Any] = ["file": name, "at": Date().timeIntervalSince1970 * 1000,
                                         "title": store.menuTitle]
            try JSONSerialization.data(withJSONObject: record)
                .write(to: directory.appendingPathComponent("latest.json"), options: .atomic)
            statusCaptureIndex += 1
        } catch { fputs("Status capture: \(error)\n", stderr) }
    }

    @objc private func togglePopover() {
        panel?.toggle()
    }

    @objc private func showSettings(_ sender: Any?) {
        guard let store else { return }
        panel?.close(reason: "settings")
        if settingsWindow == nil { settingsWindow = SettingsWindowController(store: store) }
        settingsWindow?.show()
    }

    private func installApplicationMenu() {
        let menu = NSMenu()
        let application = NSMenuItem()
        let submenu = NSMenu(title: "Codex Activity Monitor")
        let settings = NSMenuItem(title: L("设置…"), action: #selector(showSettings(_:)), keyEquivalent: ",")
        settings.target = self
        submenu.addItem(settings); submenu.addItem(.separator())
        let quit = NSMenuItem(title: L("Quit"), action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        quit.target = NSApplication.shared
        submenu.addItem(quit); application.submenu = submenu; menu.addItem(application)
        let editor = NSMenuItem(title: L("编辑"), action: nil, keyEquivalent: "")
        let edit = makeEditMenu(language: store.language)
        editor.submenu = edit; menu.addItem(editor); editMenu = edit; editMenuItem = editor
        NSApplication.shared.mainMenu = menu
        settingsMenuItem = settings; quitMenuItem = quit
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if settingsWindow?.window?.isVisible == true { settingsWindow?.show(); return true }
        if activityViewer?.window?.isVisible == true { activityViewer?.window?.makeKeyAndOrderFront(nil); return true }
        if panel != nil && !panel.popover.isShown { panel.show() }
        return true
    }

    func applicationWillTerminate(_ notification: Notification) {
        activityViewer?.close()
        settingsWindow?.close()
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
        value["chat_speed_observations"] = store.snapshot?.displayedChats.map { chat in
            var observation: [String: Any] = ["id": chat.threadID]
            observation["rate"] = chat.generation_rate
            observation["coverage"] = chat.generation_rate_coverage
            observation["notice"] = chat.speedObservationNotice?.rawValue
            return observation
        }
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

@MainActor func makeEditMenu(language: MonitorLanguage) -> NSMenu {
    let menu = NSMenu(title: localizedText("编辑", language: language))
    for (title, selector, key) in [("撤销", Selector(("undo:")), "z"), ("重做", Selector(("redo:")), "Z"),
                                  ("剪切", #selector(NSText.cut(_:)), "x"), ("复制", #selector(NSText.copy(_:)), "c"),
                                  ("粘贴", #selector(NSText.paste(_:)), "v"), ("全选", #selector(NSText.selectAll(_:)), "a")] {
        let item = NSMenuItem(title: localizedText(title, language: language), action: selector, keyEquivalent: key.lowercased())
        if key == "Z" { item.keyEquivalentModifierMask = [.command, .shift] }
        menu.addItem(item)
    }
    return menu
}
