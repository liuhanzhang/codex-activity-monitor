import AppKit
import MonitorModel
import QuartzCore
import SwiftUI

/// A drawing surface inside the status button; the button owns input.
@MainActor final class StatusContentView: NSView {
    private static let font = NSFont.monospacedDigitSystemFont(ofSize: 12, weight: .regular)
    static let horizontalInset: CGFloat = 2
    static let minimumItemWidth: CGFloat = ceil(("999 T/s" as NSString).size(withAttributes: [.font: font]).width)
        + horizontalInset * 2
    private static let maximumItemWidth: CGFloat = ceil((">99999 T/s" as NSString).size(withAttributes: [.font: font]).width)
        + horizontalInset * 2

    static func itemWidth(for mode: MenuMode, title: String) -> CGFloat {
        guard mode == .speed else { return minimumItemWidth }
        let textWidth = ceil((title as NSString).size(withAttributes: [.font: font]).width)
        return min(maximumItemWidth, max(minimumItemWidth, textWidth + horizontalInset * 2))
    }
    private(set) var mode: MenuMode = .unavailable
    private(set) var fullTitle = "—"
    private var epoch = CACurrentMediaTime()
    private var timer: Timer?
    private var refreshLink: CADisplayLink?
    private lazy var frameTarget = FrameTarget(view: self)
    private var presentationTime: CFTimeInterval?
    private var frameSampleStart = CACurrentMediaTime()
    private var callbackCount = 0
    private var drawCount = 0
    private var drawCalls = 0
    private var lastDrawTime: CFTimeInterval?
    private var motionObserver: NSObjectProtocol?
    var forceReducedMotion = false
    var recordsFrameCadence = false
    // Developer-only archive export; there is no pet selector in the product UI.
    var previewIdlePet = false

    override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        setAccessibilityElement(false)
        motionObserver = NSWorkspace.shared.notificationCenter.addObserver(
            forName: NSWorkspace.accessibilityDisplayOptionsDidChangeNotification, object: nil, queue: .main
        ) { [weak self] _ in MainActor.assumeIsolated { self?.updateClock() } }
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }
    override func hitTest(_ point: NSPoint) -> NSView? { nil }

    isolated deinit { stop() }

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        if window == nil { stopClocks() }
        else { updateClock() }
    }

    var reducedMotion: Bool { forceReducedMotion || NSWorkspace.shared.accessibilityDisplayShouldReduceMotion }
    var animating: Bool { refreshLink != nil || timer != nil }

    var frameDiagnostics: [String: Any] {
        let elapsed = max(0.001, CACurrentMediaTime() - frameSampleStart)
        return ["driver": refreshLink != nil ? "display-link" : (timer != nil ? "pet-timer" : "none"),
                "preferred_fps": refreshLink?.preferredFrameRateRange.preferred ?? 0,
                "screen_max_fps": window?.screen?.maximumFramesPerSecond ?? 0,
                "sample_seconds": elapsed, "callbacks": callbackCount, "draws": drawCount, "draw_calls": drawCalls,
                "callback_fps": Double(callbackCount) / elapsed, "draw_fps": Double(drawCount) / elapsed]
    }

    func update(mode: MenuMode, title: String) {
        if self.mode != mode {
            epoch = CACurrentMediaTime()
            presentationTime = nil
            frameSampleStart = epoch; callbackCount = 0; drawCount = 0; drawCalls = 0; lastDrawTime = nil
        }
        self.mode = mode
        fullTitle = title
        updateClock()
        needsDisplay = true
    }

    func stop() {
        stopClocks()
        if let motionObserver { NSWorkspace.shared.notificationCenter.removeObserver(motionObserver) }
        motionObserver = nil
    }

    private func stopClocks() {
        refreshLink?.invalidate(); refreshLink = nil
        timer?.invalidate(); timer = nil
        presentationTime = nil
    }

    private func updateClock() {
        if (mode == .thinking || mode == .executing) && !reducedMotion {
            timer?.invalidate(); timer = nil
            if refreshLink == nil {
                let link = displayLink(target: frameTarget, selector: #selector(FrameTarget.step(_:)))
                link.preferredFrameRateRange = CAFrameRateRange(minimum: 60, maximum: 120, preferred: 120)
                link.add(to: .main, forMode: .common)
                refreshLink = link
            }
        } else if mode == .idle && previewIdlePet && IdlePetAnimation.frames.count > 1 && !reducedMotion {
            refreshLink?.invalidate(); refreshLink = nil
            presentationTime = nil
            if timer == nil {
                let timer = Timer(timeInterval: IdlePetAnimation.frameDuration, repeats: true) { [weak self] timer in
                    guard self != nil else { timer.invalidate(); return }
                    MainActor.assumeIsolated { self?.needsDisplay = true }
                }
                self.timer = timer
                RunLoop.main.add(timer, forMode: .common)
            }
        } else { stopClocks() }
        needsDisplay = true
    }

    private func displayFrame(_ link: CADisplayLink) {
        guard link === refreshLink else { return }
        if recordsFrameCadence { callbackCount += 1 }
        presentationTime = link.targetTimestamp
        needsDisplay = true
    }

    @MainActor private final class FrameTarget: NSObject {
        weak var view: StatusContentView?
        init(view: StatusContentView) { self.view = view }
        @objc func step(_ link: CADisplayLink) {
            guard let view else { link.invalidate(); return }
            view.displayFrame(link)
        }
    }

    func boundedTitle(_ title: String, availableWidth: CGFloat) -> String {
        let attributes: [NSAttributedString.Key: Any] = [.font: Self.font]
        guard (title as NSString).size(withAttributes: attributes).width > availableWidth else { return title }
        var limit = "9"
        while ((">" + limit + "9 T/s") as NSString).size(withAttributes: attributes).width <= availableWidth {
            limit += "9"
        }
        return ">" + limit + " T/s"
    }

    override func draw(_ dirtyRect: NSRect) {
        super.draw(dirtyRect)
        let frameTime = presentationTime ?? CACurrentMediaTime()
        if recordsFrameCadence {
            drawCalls += 1
            // AppKit may draw the same presentation multiple times for distinct dirty regions.
            if lastDrawTime != frameTime { drawCount += 1; lastDrawTime = frameTime }
        }
        drawContent(at: max(0, frameTime - epoch))
    }

    func drawContent(at elapsed: TimeInterval) {
        let center = NSPoint(x: bounds.midX, y: bounds.midY)
        NSColor.labelColor.setFill()
        switch mode {
        case .idle:
            if previewIdlePet {
                if !IdlePetAnimation.draw(in: bounds, elapsed: elapsed, reducedMotion: reducedMotion) {
                    drawTitle("0 T/s", center: center)
                }
            } else {
                IdleBeachIcon.draw(in: bounds)
            }
        case .thinking:
            for index in 0..<3 {
                let phase = (elapsed / 1.35 - Double(index) * 0.18 / 1.35).truncatingRemainder(dividingBy: 1)
                let wrapped = phase < 0 ? phase + 1 : phase
                let pulse = reducedMotion ? 0 : max(0, sin(min(wrapped / 0.48, 1) * .pi))
                NSColor.labelColor.withAlphaComponent(0.65 + 0.35 * pulse).setFill()
                NSBezierPath(ovalIn: NSRect(x: center.x - 9 + CGFloat(index) * 7, y: center.y - 2, width: 4, height: 4)).fill()
            }
        case .executing:
            NSGraphicsContext.saveGraphicsState()
            let transform = AffineTransform(translationByX: center.x - 5, byY: center.y - 6)
            (transform as NSAffineTransform).concat()
            let rotation = NSAffineTransform()
            // Mirror the whole pose back to the right, including its pivot and swing.
            rotation.rotate(byDegrees: -CGFloat(HammerMotion.rotation(at: elapsed, reducedMotion: reducedMotion)))
            rotation.concat()
            let image = NSImage(systemSymbolName: "hammer.fill", accessibilityDescription: nil)?
                .withSymbolConfiguration(.init(paletteColors: [.labelColor]))
            image?.draw(in: NSRect(x: -4, y: -3, width: 18, height: 18))
            NSGraphicsContext.restoreGraphicsState()
        case .speed, .unavailable:
            let title = mode == .unavailable ? "—" : boundedTitle(fullTitle, availableWidth: bounds.width - Self.horizontalInset * 2)
            drawTitle(title, center: center)
        }
    }

    private func drawTitle(_ title: String, center: NSPoint) {
        let attributes: [NSAttributedString.Key: Any] = [.font: Self.font, .foregroundColor: NSColor.labelColor]
        let size = (title as NSString).size(withAttributes: attributes)
        (title as NSString).draw(at: NSPoint(x: center.x - size.width / 2, y: center.y - size.height / 2), withAttributes: attributes)
    }
}

/// Read-only, opt-in native fixture: the same renderer as the real status item.
struct NativeStatusPreview: NSViewRepresentable {
    let mode: MenuMode
    let title: String
    func makeNSView(context: Context) -> StatusContentView {
        let view = StatusContentView(frame: NSRect(x: 0, y: 0, width: StatusContentView.itemWidth(for: mode, title: title), height: 24))
        view.forceReducedMotion = CommandLine.arguments.contains("--preview-reduce-motion")
        return view
    }
    func updateNSView(_ nsView: StatusContentView, context: Context) { nsView.update(mode: mode, title: title) }
    static func dismantleNSView(_ nsView: StatusContentView, coordinator: ()) { nsView.stop() }
}
