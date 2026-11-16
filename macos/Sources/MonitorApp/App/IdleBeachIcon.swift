import AppKit

/// A compact, appearance-aware interpretation of the beach-with-umbrella emoji.
@MainActor enum IdleBeachIcon {
    static let renderHeight: CGFloat = 18

    static func draw(in bounds: NSRect) {
        guard let context = NSGraphicsContext.current?.cgContext else { return }
        let scale = renderHeight / 24
        context.saveGState()
        defer { context.restoreGState() }
        context.translateBy(x: bounds.midX - 14 * scale, y: bounds.midY + renderHeight / 2)
        context.scaleBy(x: scale, y: -scale)
        context.setLineCap(.round)
        context.setLineJoin(.round)
        let ink = NSColor.labelColor

        let sand = CGMutablePath()
        sand.move(to: CGPoint(x: 4.7, y: 19.9))
        sand.addCurve(to: CGPoint(x: 9.2, y: 17.1), control1: CGPoint(x: 4.4, y: 18.7), control2: CGPoint(x: 6.2, y: 17.8))
        sand.addCurve(to: CGPoint(x: 20, y: 17), control1: CGPoint(x: 13, y: 16.2), control2: CGPoint(x: 16.9, y: 16.3))
        sand.addCurve(to: CGPoint(x: 25, y: 19.8), control1: CGPoint(x: 23, y: 17.6), control2: CGPoint(x: 25, y: 18.5))
        sand.addCurve(to: CGPoint(x: 21.4, y: 20.4), control1: CGPoint(x: 25, y: 20.7), control2: CGPoint(x: 23, y: 20))
        sand.addCurve(to: CGPoint(x: 16.6, y: 21), control1: CGPoint(x: 19.8, y: 21.4), control2: CGPoint(x: 18.8, y: 21.5))
        sand.addCurve(to: CGPoint(x: 4.7, y: 19.9), control1: CGPoint(x: 12, y: 20.2), control2: CGPoint(x: 8, y: 20.9))
        sand.closeSubpath()
        context.setFillColor(ink.withAlphaComponent(0.48).cgColor)
        context.addPath(sand)
        context.fillPath()

        let water = CGMutablePath()
        water.move(to: CGPoint(x: 3, y: 20.1))
        water.addCurve(to: CGPoint(x: 14.8, y: 23.1), control1: CGPoint(x: 0, y: 22.4), control2: CGPoint(x: 5.3, y: 23.5))
        water.addCurve(to: CGPoint(x: 26, y: 20.5), control1: CGPoint(x: 23, y: 23.1), control2: CGPoint(x: 28.4, y: 22.2))
        context.setStrokeColor(ink.cgColor)
        context.setLineWidth(1.4)
        context.addPath(water)
        context.strokePath()

        let pole = CGMutablePath()
        pole.move(to: CGPoint(x: 13.5, y: 10))
        pole.addLine(to: CGPoint(x: 15.2, y: 18))
        context.setLineWidth(1.6)
        context.addPath(pole)
        context.strokePath()

        let canopy = CGMutablePath()
        canopy.move(to: CGPoint(x: 12, y: 1.5))
        canopy.addCurve(to: CGPoint(x: 25.1, y: 5.1), control1: CGPoint(x: 17, y: -0.2), control2: CGPoint(x: 23, y: 1.6))
        canopy.addCurve(to: CGPoint(x: 21.3, y: 8.8), control1: CGPoint(x: 22.9, y: 6), control2: CGPoint(x: 22.1, y: 7.4))
        canopy.addCurve(to: CGPoint(x: 14, y: 11.3), control1: CGPoint(x: 18.4, y: 8.6), control2: CGPoint(x: 16, y: 9.7))
        canopy.addCurve(to: CGPoint(x: 6.3, y: 11.5), control1: CGPoint(x: 11.3, y: 10.6), control2: CGPoint(x: 8.6, y: 10.7))
        canopy.addCurve(to: CGPoint(x: 2.4, y: 9.7), control1: CGPoint(x: 5, y: 10.5), control2: CGPoint(x: 3.8, y: 9.8))
        canopy.addCurve(to: CGPoint(x: 12, y: 1.5), control1: CGPoint(x: 3, y: 6), control2: CGPoint(x: 5, y: 3.4))
        canopy.closeSubpath()
        // Open panels use transparency so the menu bar shows through in either appearance.
        canopy.move(to: CGPoint(x: 11.5, y: 2.2))
        canopy.addCurve(to: CGPoint(x: 6.9, y: 10.6), control1: CGPoint(x: 9.3, y: 4), control2: CGPoint(x: 7.6, y: 7.1))
        canopy.addCurve(to: CGPoint(x: 13.3, y: 10.4), control1: CGPoint(x: 8.5, y: 10.2), control2: CGPoint(x: 10.8, y: 10.1))
        canopy.addCurve(to: CGPoint(x: 11.5, y: 2.2), control1: CGPoint(x: 13.4, y: 8.1), control2: CGPoint(x: 12.9, y: 4.5))
        canopy.closeSubpath()
        canopy.move(to: CGPoint(x: 12.8, y: 1.9))
        canopy.addCurve(to: CGPoint(x: 24.1, y: 5), control1: CGPoint(x: 16, y: 1.5), control2: CGPoint(x: 21, y: 3))
        canopy.addCurve(to: CGPoint(x: 21, y: 7.9), control1: CGPoint(x: 22.6, y: 5.7), control2: CGPoint(x: 21.6, y: 6.7))
        canopy.addCurve(to: CGPoint(x: 12.8, y: 1.9), control1: CGPoint(x: 19.1, y: 5.4), control2: CGPoint(x: 16.8, y: 3.4))
        canopy.closeSubpath()
        context.setFillColor(ink.cgColor)
        context.addPath(canopy)
        context.fillPath(using: .evenOdd)
    }
}
