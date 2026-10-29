// Render the Finder installation guide at 1x and 2x without a GUI session.
import AppKit

let destination = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
let size = NSSize(width: 660, height: 430)

func centered(_ text: String, y: CGFloat, font: NSFont, color: NSColor) {
    let attributes: [NSAttributedString.Key: Any] = [.font: font, .foregroundColor: color]
    let width = (text as NSString).size(withAttributes: attributes).width
    (text as NSString).draw(at: NSPoint(x: (size.width - width) / 2, y: y), withAttributes: attributes)
}

for scale in [1, 2] {
    guard let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil,
        pixelsWide: Int(size.width) * scale, pixelsHigh: Int(size.height) * scale,
        bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
        colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0),
        let context = NSGraphicsContext(bitmapImageRep: bitmap) else {
        fatalError("Cannot create DMG background")
    }
    bitmap.size = size
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = context
    context.cgContext.scaleBy(x: CGFloat(scale), y: CGFloat(scale))
    NSColor(calibratedWhite: 0.98, alpha: 1).setFill()
    NSRect(origin: .zero, size: size).fill()

    let ink = NSColor(calibratedWhite: 0.16, alpha: 1)
    let secondary = NSColor(calibratedWhite: 0.43, alpha: 1)
    centered("Codex Activity Monitor", y: 365, font: .systemFont(ofSize: 25, weight: .semibold), color: ink)
    centered("拖入“应用程序”以安装", y: 330, font: .systemFont(ofSize: 16), color: ink)
    centered("Drag the app into Applications to install", y: 308, font: .systemFont(ofSize: 12), color: secondary)

    let arrow = NSBezierPath()
    arrow.lineWidth = 3
    arrow.lineCapStyle = .round
    arrow.lineJoinStyle = .round
    arrow.move(to: NSPoint(x: 306, y: 215))
    arrow.line(to: NSPoint(x: 354, y: 215))
    arrow.move(to: NSPoint(x: 341, y: 228))
    arrow.line(to: NSPoint(x: 354, y: 215))
    arrow.line(to: NSPoint(x: 341, y: 202))
    NSColor(calibratedWhite: 0.64, alpha: 1).setStroke()
    arrow.stroke()

    centered("更新前，请先结束任务并退出 Codex 和旧版 Monitor", y: 89,
             font: .systemFont(ofSize: 11), color: secondary)
    centered("Updating? Finish tasks, then quit Codex and the old Monitor.", y: 70,
             font: .systemFont(ofSize: 10), color: secondary)
    centered("安装后，从“应用程序”打开  ·  Open from Applications after installing", y: 42,
             font: .systemFont(ofSize: 10), color: secondary)
    NSGraphicsContext.restoreGraphicsState()

    guard let data = bitmap.representation(using: .png, properties: [:]) else {
        fatalError("Cannot encode DMG background")
    }
    try data.write(to: destination.appendingPathComponent(scale == 1 ? "background.png" : "background@2x.png"))
}
