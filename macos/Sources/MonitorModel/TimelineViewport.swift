import Foundation

/// Display-only pagination; never changes the source origin, intervals or history end.
public struct TimelineViewport: Sendable, Equatable {
    public static let retainedFraction = 0.25
    public let origin: Double
    public let seconds: Double
    public let pointsPerSecond: Double
    public private(set) var index: Double
    public var step: Double { seconds * (1 - Self.retainedFraction) }
    public var offset: Double { index * step * pointsPerSecond }

    public init?(origin: Double, time: Double, viewport: Double, pointsPerSecond: Double,
                 previous: Self? = nil) {
        guard origin.isFinite, time.isFinite, viewport.isFinite, viewport > 8,
              pointsPerSecond.isFinite, pointsPerSecond > 0 else { return nil }
        self.origin = origin; self.pointsPerSecond = pointsPerSecond
        seconds = (viewport - 8) / pointsPerSecond
        let elapsed = max(0, (time - origin) / 1000)
        // Sub-microsecond tolerance avoids a spurious page at an exact floating-point boundary.
        index = max(0, ceil((elapsed - seconds - 0.0000001) / (seconds * 0.75)))
        if let previous, hasSameGeometry(as: previous) { index = max(index, previous.index) }
        guard seconds.isFinite, seconds > 0, index.isFinite, offset.isFinite else { return nil }
    }

    public func hasSameGeometry(as other: Self) -> Bool {
        origin == other.origin && seconds == other.seconds && pointsPerSecond == other.pointsPerSecond
    }
}
