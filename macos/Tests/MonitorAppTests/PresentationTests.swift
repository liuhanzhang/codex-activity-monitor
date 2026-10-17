import AppKit
import Foundation
import Testing
import MonitorModel
@testable import MonitorApp

@Test @MainActor func refreshedMetricsDoNotReorderRowsOrResetExpansion() throws {
    let store = DashboardStore(configuration: BackendConfiguration(python: "/unused", source_root: "/unused", runtime: "/unused", data_root: "/unused", build_id: "test"), arguments: ["--read-only"])
    func snapshot(_ ids: [String]) throws -> Snapshot {
        let rows = ids.map { "{\"id\":\"\($0)\",\"has_active_work\":true}" }.joined(separator: ",")
        return try Snapshot.decode(Data("{\"schema_version\":1,\"threads\":[\(rows)]}".utf8))
    }
    store.snapshot = try snapshot(["a", "b"])
    store.updatePresentation()
    store.expanded = [store.snapshot!.threads[0].id]
    store.snapshot = try snapshot(["b", "c", "a"])
    store.updatePresentation()
    #expect(store.activeRows.map(\.chat.threadID) == ["a", "b", "c"])
    #expect(store.expanded.count == 1)
    store.snapshot = try snapshot(["c", "b"])
    store.updatePresentation()
    #expect(store.activeRows.map(\.chat.threadID) == ["b", "c"])
    #expect(store.expanded.isEmpty)
}

@Test @MainActor func menuAnimationStopsOnUnavailableAndReducedMotion() {
    let view = StatusContentView(frame: NSRect(x: 0, y: 0, width: StatusContentView.minimumItemWidth, height: 24))
    view.update(mode: .thinking, title: "0 T/s")
    #expect(view.animating == !view.reducedMotion)
    view.update(mode: .idle, title: "0 T/s")
    #expect(!view.animating)
    view.previewIdlePet = true
    view.update(mode: .idle, title: "0 T/s")
    #expect(view.animating == !view.reducedMotion)
    view.update(mode: .speed, title: "0 T/s")
    #expect(!view.animating)
    view.update(mode: .unavailable, title: "—")
    #expect(!view.animating)
    view.forceReducedMotion = true
    view.update(mode: .executing, title: "0 T/s")
    #expect(!view.animating)
    view.update(mode: .idle, title: "0 T/s")
    #expect(!view.animating)
    #expect(view.frame.width == StatusContentView.minimumItemWidth)
    view.stop()
    #expect(!view.animating)
}

@Test @MainActor func stoppedAnimationClockReleasesItsView() {
    weak var released: StatusContentView?
    autoreleasepool {
        let view = StatusContentView(frame: NSRect(x: 0, y: 0, width: StatusContentView.minimumItemWidth, height: 24))
        released = view
        view.update(mode: .thinking, title: "0 T/s")
        view.stop()
    }
    #expect(released == nil)
}

@Test @MainActor func menuWidthGrowsBeyondThreeDigitsAndBoundsExtremeRates() {
    let view = StatusContentView(frame: NSRect(x: 0, y: 0, width: StatusContentView.minimumItemWidth, height: 24))
    let font = NSFont.monospacedDigitSystemFont(ofSize: 12, weight: .regular)
    for title in ["0 T/s", "9 T/s", "48 T/s", "126 T/s", "999 T/s", "1000 T/s", "9999 T/s", "10000 T/s", "100000 T/s", "48 T/s"] {
        let width = StatusContentView.itemWidth(for: .speed, title: title)
        let availableWidth = width - StatusContentView.horizontalInset * 2
        #expect(view.boundedTitle(title, availableWidth: availableWidth) == title)
        #expect((title as NSString).size(withAttributes: [.font: font]).width <= availableWidth)
        if title.count <= "999 T/s".count { #expect(width == StatusContentView.minimumItemWidth) }
        else { #expect(width > StatusContentView.minimumItemWidth) }
    }
    for mode: MenuMode in [.idle, .thinking, .executing, .unavailable] {
        #expect(StatusContentView.itemWidth(for: mode, title: "10000 T/s") == StatusContentView.minimumItemWidth)
    }
    let title = MetricFormat.speed(1e100)
    let availableWidth = StatusContentView.itemWidth(for: .speed, title: title) - StatusContentView.horizontalInset * 2
    let bounded = view.boundedTitle(title, availableWidth: availableWidth)
    #expect(bounded == ">99999 T/s")
    #expect((bounded as NSString).size(withAttributes: [.font: font]).width <= availableWidth)
    view.stop()
}

@Test @MainActor func idlePetFramesRetainTransparencyWhiteFurAndLoopTiming() throws {
    #expect(IdlePetAnimation.frames.count == 8)
    for image in IdlePetAnimation.frames {
        let cgImage = try #require(image.cgImage(forProposedRect: nil, context: nil, hints: nil))
        let rep = NSBitmapImageRep(cgImage: cgImage)
        #expect(rep.hasAlpha)
        #expect(rep.colorAt(x: 0, y: 0)!.alphaComponent == 0)
        var white = 0, gray = 0
        for y in stride(from: 0, to: rep.pixelsHigh, by: 2) {
            for x in stride(from: 0, to: rep.pixelsWide, by: 2) {
                guard let color = rep.colorAt(x: x, y: y)?.usingColorSpace(.deviceRGB), color.alphaComponent > 0.9 else { continue }
                if color.redComponent > 0.95 { white += 1 }
                if color.redComponent > 0.3 && color.redComponent < 0.85 { gray += 1 }
            }
        }
        #expect(white > 20)
        #expect(gray > 100)
    }
    #expect(IdlePetAnimation.frameIndex(at: 0, reducedMotion: false) == 0)
    #expect(IdlePetAnimation.frameIndex(at: 0.21, reducedMotion: false) == 1)
    #expect(IdlePetAnimation.frameIndex(at: 1.41, reducedMotion: false) == 7)
    #expect(IdlePetAnimation.frameIndex(at: 1.6, reducedMotion: false) == 0)
    #expect(IdlePetAnimation.frameIndex(at: 0.8, reducedMotion: true) == 0)
}

@Test @MainActor func popupStaysBelowMenuBarAtBothScreenEdgesAndWithHiddenMenuBar() {
    let screens = [NSRect(x: 0, y: 0, width: 1728, height: 1084), NSRect(x: -1083, y: 1117, width: 1920, height: 1050)]
    for visible in screens {
        for width in [StatusContentView.minimumItemWidth, StatusContentView.itemWidth(for: .speed, title: "100000 T/s")] {
            for x in [visible.minX, visible.maxX - width] {
                for menuHeight in [0.0, 30.0] {
                    let anchor = NSRect(x: x, y: visible.maxY + menuHeight, width: width, height: 22)
                    let frame = PopoverController.constrainedFrame(NSRect(x: x-177, y: anchor.minY-100, width: 446, height: 400), anchor: anchor, visibleFrame: visible)
                    #expect(visible.contains(frame))
                    #expect(frame.maxY <= anchor.minY)
                }
            }
        }
    }
}
