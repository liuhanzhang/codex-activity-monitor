import AppKit
import SwiftUI

@MainActor final class PopoverController: NSObject, NSPopoverDelegate {
    let popover = NSPopover()
    private let button: NSStatusBarButton
    private let store: DashboardStore
    private var host: NSHostingController<DashboardView>!
    private var localMonitor: Any?
    private var globalMonitor: Any?
    private var observers: [NSObjectProtocol] = []
    private var modalShown = false
    private var pendingClose: String?
    private var menuTracking = false
    private var measuredSize = NSSize(width: 420, height: 170)
    var onTrace: ((String) -> Void)?

    init(button: NSStatusBarButton, store: DashboardStore, quit: @escaping () -> Void) {
        self.button = button
        self.store = store
        super.init()
        popover.behavior = .transient
        popover.animates = false
        popover.delegate = self
        host = NSHostingController(rootView: DashboardView(store: store, quit: quit,
            onSizeChange: { [weak self] in self?.resize($0) },
            onModalChange: { [weak self] shown in
                guard let self else { return }
                self.modalShown = shown
                if !shown, let reason = self.pendingClose { self.pendingClose = nil; self.close(reason: reason) }
            }))
        // Explicit content sizing prevents the hosting view's automatic window
        // minimum from competing with our content-measured popover dimensions.
        host.sizingOptions = []
        host.view.setFrameSize(measuredSize)
        popover.contentViewController = host
        popover.contentSize = measuredSize
        if let appearance = NSApplication.shared.appearance {
            popover.appearance = appearance
            host.view.appearance = appearance
        }
    }

    func toggle() { popover.isShown ? close(reason: "toggle") : show() }

    func show() {
        guard !popover.isShown, button.window != nil else { return }
        updateScreenLimit()
        store.panelVisible = true
        popover.contentSize = measuredSize
        popover.show(relativeTo: button.bounds, of: button, preferredEdge: .minY)
        popover.contentViewController?.view.window?.makeKey()
        NSApplication.shared.activate(ignoringOtherApps: true)
        installMonitors()
        onTrace?("show-native")
        DispatchQueue.main.async { [weak self] in self?.constrainPosition(); self?.onTrace?("show-positioned") }
    }

    func close(reason: String) {
        guard popover.isShown else { return }
        store.auxiliary.dismiss()
        if modalShown {
            pendingClose = reason
            store.dismissPresentation += 1
            return
        }
        popover.performClose(nil)
        onTrace?("close-" + reason)
    }

    func stop() { store.auxiliary.dismiss(); removeMonitors(); popover.close() }

    func statusItemDidResize() {
        guard popover.isShown else { return }
        DispatchQueue.main.async { [weak self] in
            guard let self, self.popover.isShown else { return }
            self.popover.positioningRect = self.button.bounds
            self.constrainPosition()
        }
    }

    private func resize(_ size: CGSize) {
        guard size.width > 0, size.height > 0, abs(size.height - measuredSize.height) > 0.5 else { return }
        measuredSize = NSSize(width: 420, height: ceil(size.height))
        popover.contentSize = measuredSize
        if popover.isShown {
            DispatchQueue.main.async { [weak self] in self?.constrainPosition(); self?.onTrace?("resize") }
        }
    }

    private var anchor: NSRect? {
        button.window?.convertToScreen(button.convert(button.bounds, to: nil))
    }

    private func updateScreenLimit() {
        guard let screen = button.window?.screen, let anchor else { return }
        let available = Self.availableContentHeight(anchor: anchor, visibleFrame: screen.visibleFrame)
        store.panelMaxHeight = min(available, store.previewMaxHeight ?? available)
    }

    static func availableContentHeight(anchor: NSRect, visibleFrame: NSRect) -> CGFloat {
        // Account for native popover arrow/frame in addition to the screen margin.
        max(0, min(anchor.minY - 2, visibleFrame.maxY - 8) - (visibleFrame.minY + 8) - 26)
    }

    private func constrainPosition() {
        guard popover.isShown, !modalShown, let window = host.view.window,
              let anchor, let screen = button.window?.screen else { return }
        updateScreenLimit()
        let frame = Self.constrainedFrame(window.frame, anchor: anchor, visibleFrame: screen.visibleFrame)
        if window.frame.origin != frame.origin { window.setFrameOrigin(frame.origin) }
    }

    static func constrainedFrame(_ original: NSRect, anchor: NSRect, visibleFrame: NSRect) -> NSRect {
        let bounds = visibleFrame.insetBy(dx: 8, dy: 8)
        var frame = original
        // In screen coordinates Y grows upward. Keep the entire native window
        // below the button, including the popover's arrow and shadow frame.
        frame.origin.y = min(anchor.minY - 2, bounds.maxY) - frame.height
        frame.origin.y = max(bounds.minY, frame.origin.y)
        frame.origin.x = max(bounds.minX, min(frame.minX, bounds.maxX - frame.width))
        return frame
    }

    private func installMonitors() {
        guard localMonitor == nil else { return }
        let mouse: NSEvent.EventTypeMask = [.leftMouseDown, .rightMouseDown, .otherMouseDown]
        globalMonitor = NSEvent.addGlobalMonitorForEvents(matching: mouse) { [weak self] _ in
            MainActor.assumeIsolated { self?.close(reason: "outside") }
        }
        localMonitor = NSEvent.addLocalMonitorForEvents(matching: mouse.union(.keyDown)) { [weak self] event in
            let consumed = MainActor.assumeIsolated {
                guard let self else { return false }
                if event.type == .keyDown {
                    if event.keyCode == 53 && !self.modalShown && !self.menuTracking {
                        if self.store.auxiliary.dismiss() { return true }
                        self.close(reason: "escape"); return true
                    }
                    return false
                }
                if self.menuTracking || self.modalShown { return false }
                if event.window === self.host.view.window { return false }
                if self.store.auxiliary.owns(event.window) { return false }
                if let anchor = self.anchor, anchor.contains(NSEvent.mouseLocation) { return false }
                self.close(reason: "local-outside")
                return false
            }
            return consumed ? nil : event
        }
        let center = NotificationCenter.default
        observers.append(center.addObserver(forName: NSApplication.didResignActiveNotification, object: nil, queue: .main) {
            [weak self] _ in MainActor.assumeIsolated { self?.close(reason: "deactivate") }
        })
        observers.append(center.addObserver(forName: NSMenu.didBeginTrackingNotification, object: nil, queue: .main) {
            [weak self] _ in MainActor.assumeIsolated { self?.menuTracking = true }
        })
        observers.append(center.addObserver(forName: NSMenu.didEndTrackingNotification, object: nil, queue: .main) {
            [weak self] _ in MainActor.assumeIsolated { self?.menuTracking = false }
        })
        observers.append(center.addObserver(forName: NSApplication.didChangeScreenParametersNotification, object: nil, queue: .main) {
            [weak self] _ in MainActor.assumeIsolated { self?.updateScreenLimit(); self?.constrainPosition() }
        })
    }

    private func removeMonitors() {
        if let localMonitor { NSEvent.removeMonitor(localMonitor) }
        if let globalMonitor { NSEvent.removeMonitor(globalMonitor) }
        localMonitor = nil; globalMonitor = nil
        observers.forEach { NotificationCenter.default.removeObserver($0) }; observers.removeAll()
        menuTracking = false
    }

    func popoverShouldClose(_ popover: NSPopover) -> Bool { !modalShown && store.auxiliary.activeID == nil }
    func popoverDidClose(_ notification: Notification) { store.panelVisible = false; removeMonitors(); onTrace?("did-close") }

    var diagnostics: [String: Any] {
        func rect(_ value: NSRect?) -> [CGFloat]? { value.map { [$0.minX, $0.minY, $0.width, $0.height] } }
        return ["shown": popover.isShown, "content_size": [measuredSize.width, measuredSize.height],
                "button_frame": rect(anchor) ?? [], "window_frame": rect(host.view.window?.frame) ?? [],
                "screen_frame": rect(button.window?.screen?.frame) ?? [],
                "visible_frame": rect(button.window?.screen?.visibleFrame) ?? [],
                "button_flipped": button.isFlipped, "key": host.view.window?.isKeyWindow ?? false,
                "appearance": host.view.effectiveAppearance.name.rawValue,
                "presentation_options": NSApplication.shared.presentationOptions.rawValue,
                "modal": modalShown, "monitor_count": (localMonitor == nil ? 0 : 1) + (globalMonitor == nil ? 0 : 1)]
    }
}
