import AppKit

@MainActor enum IdlePetAnimation {
    static let frameDuration: TimeInterval = 0.2
    static let renderHeight: CGFloat = 18
    static let frames: [NSImage] = {
        let images = (0..<8).compactMap { index -> NSImage? in
            guard let url = Bundle.module.url(forResource: String(format: "idle-pet-%02d", index), withExtension: "png"),
                  let image = NSImage(contentsOf: url) else { return nil }
            return image
        }
        return images.count == 8 ? images : []
    }()

    static func frameIndex(at elapsed: TimeInterval, reducedMotion: Bool) -> Int {
        guard !reducedMotion, elapsed.isFinite, elapsed >= 0, !frames.isEmpty else { return 0 }
        let period = frameDuration * Double(frames.count)
        return min(frames.count - 1, Int(elapsed.truncatingRemainder(dividingBy: period) / frameDuration))
    }

    @discardableResult static func draw(in bounds: NSRect, elapsed: TimeInterval, reducedMotion: Bool) -> Bool {
        guard !frames.isEmpty else { return false }
        let image = frames[frameIndex(at: elapsed, reducedMotion: reducedMotion)]
        let height = min(renderHeight, bounds.height - 2)
        let width = height * image.size.width / image.size.height
        let rect = NSRect(x: bounds.midX - width / 2, y: bounds.midY - height / 2, width: width, height: height)
        NSGraphicsContext.current?.imageInterpolation = .high
        image.draw(in: rect, from: .zero, operation: .sourceOver, fraction: 1,
                   respectFlipped: true, hints: nil)
        return true
    }
}
