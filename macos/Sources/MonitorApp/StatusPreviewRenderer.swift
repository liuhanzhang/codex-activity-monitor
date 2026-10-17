import AppKit
import MonitorModel

/// Explicit developer export of the same renderer used by the actual status item.
@MainActor enum StatusPreviewRenderer {
    /// A short isolated probe on the built-in display, without management or live chat data.
    static func probeCadence(to output: URL) {
        let app = NSApplication.shared
        app.setActivationPolicy(.accessory)
        let screen = NSScreen.screens.first { screen in
            let id = (screen.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber)?.uint32Value ?? 0
            return CGDisplayIsBuiltin(id) != 0
        } ?? NSScreen.main
        guard let screen else { fputs("No display available\n", stderr); exit(1) }
        let window = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 200, height: 60),
            styleMask: [.titled, .nonactivatingPanel], backing: .buffered, defer: false)
        window.title = "Monitor · 动画刷新验证"
        window.isReleasedWhenClosed = false
        window.level = .floating
        let container = NSView(frame: NSRect(x: 0, y: 0, width: 200, height: 60))
        let view = StatusContentView(frame: NSRect(x: 76, y: 19, width: StatusContentView.minimumItemWidth, height: 22))
        view.recordsFrameCadence = true
        container.addSubview(view)
        window.contentView = container
        window.setFrameOrigin(NSPoint(x: screen.visibleFrame.minX + 24, y: screen.visibleFrame.minY + 24))
        window.orderFrontRegardless()
        Task { @MainActor in
            var result: [String: Any] = ["screen": screen.localizedName, "screen_max_fps": screen.maximumFramesPerSecond]
            for (name, mode) in [("thinking", MenuMode.thinking), ("executing", .executing)] {
                view.update(mode: mode, title: "0 T/s")
                try? await Task.sleep(for: .seconds(4))
                result[name] = view.frameDiagnostics
            }
            view.forceReducedMotion = true
            view.update(mode: .executing, title: "0 T/s")
            let before = view.frameDiagnostics["callbacks"] as! Int
            try? await Task.sleep(for: .seconds(1))
            result["reduced_motion_callback_delta"] = (view.frameDiagnostics["callbacks"] as! Int) - before
            result["reduced_motion_animating"] = view.animating
            view.forceReducedMotion = false
            view.update(mode: .idle, title: "0 T/s")
            try? await Task.sleep(for: .seconds(1))
            result["idle"] = view.frameDiagnostics
            result["idle_animating"] = view.animating
            view.stop()
            window.close()
            do {
                try FileManager.default.createDirectory(at: output.deletingLastPathComponent(), withIntermediateDirectories: true)
                try JSONSerialization.data(withJSONObject: result, options: [.prettyPrinted, .sortedKeys]).write(to: output)
                exit(0)
            } catch { fputs("\(error)\n", stderr); exit(1) }
        }
        app.run()
    }

    static func render(to directory: URL, pet: Bool, mode: MenuMode = .idle) throws {
        let size = NSSize(width: StatusContentView.minimumItemWidth, height: 22)
        let scale: CGFloat = 2
        let frameTimes: [TimeInterval] = mode == .executing
            ? [0, 0.18, 0.30, 0.34, 0.42, 0.54, 0.8].map { $0 * HammerMotion.cycleDuration }
            : (pet ? (0..<8).map { Double($0) * IdlePetAnimation.frameDuration } : [0])
        for (name, appearanceName, reducedMotion) in [
            ("light", NSAppearance.Name.aqua, false),
            ("dark", NSAppearance.Name.darkAqua, false),
            ("reduced-motion", NSAppearance.Name.aqua, true)
        ] {
            let output = directory.appendingPathComponent(name)
            try FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)
            let view = StatusContentView(frame: NSRect(origin: .zero, size: size))
            let appearance = NSAppearance(named: appearanceName)!
            view.appearance = appearance
            view.forceReducedMotion = reducedMotion
            view.previewIdlePet = pet
            view.update(mode: mode, title: "0 T/s")
            defer { view.stop() }
            for (frame, elapsed) in frameTimes.enumerated() {
                let rep = NSBitmapImageRep(bitmapDataPlanes: nil,
                    pixelsWide: Int(size.width * scale), pixelsHigh: Int(size.height * scale),
                    bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                    colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
                appearance.performAsCurrentDrawingAppearance {
                    NSGraphicsContext.saveGraphicsState()
                    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
                    NSGraphicsContext.current!.cgContext.scaleBy(x: scale, y: scale)
                    NSColor.windowBackgroundColor.setFill()
                    NSRect(origin: .zero, size: size).fill()
                    view.drawContent(at: elapsed)
                    NSGraphicsContext.restoreGraphicsState()
                }
                try rep.representation(using: .png, properties: [:])!
                    .write(to: output.appendingPathComponent(String(format: "%02d.png", frame)))
            }
        }
        let summary: [String: Any] = ["status_item_width_pt": size.width, "status_item_height_pt": size.height,
            "artwork": mode == .executing ? "hammer" : (pet ? "pet" : "beach"), "icon_height_pt": pet ? IdlePetAnimation.renderHeight : IdleBeachIcon.renderHeight, "scale": scale,
            "frames": frameTimes.count, "frame_times_seconds": frameTimes,
            "frame_duration_seconds": pet ? IdlePetAnimation.frameDuration : 0,
            "system_reduce_motion": NSWorkspace.shared.accessibilityDisplayShouldReduceMotion]
        try JSONSerialization.data(withJSONObject: summary, options: [.prettyPrinted, .sortedKeys])
            .write(to: directory.appendingPathComponent("render-summary.json"))
    }
}
