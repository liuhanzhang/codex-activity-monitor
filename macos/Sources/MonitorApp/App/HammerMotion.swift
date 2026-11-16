import Foundation

/// Time-based hammer motion relative to its diagonal resting pose.
enum HammerMotion {
    static let cycleDuration: TimeInterval = 3.3
    static let restingRotation = 0.0

    private enum Curve: Sendable {
        case smooth, accelerate, decelerate

        func progress(_ t: Double) -> Double {
            switch self {
            case .smooth: t * t * (3 - 2 * t)
            case .accelerate: t * t
            case .decelerate: 1 - pow(1 - t, 3)
            }
        }
    }

    private struct Keyframe: Sendable {
        let phase: Double
        let rotation: Double
        let curve: Curve
        init(_ phase: Double, _ rotation: Double, _ curve: Curve) {
            self.phase = phase; self.rotation = rotation; self.curve = curve
        }
    }

    // Each destination names the curve used to approach it.
    private static let keyframes: [Keyframe] = [
        .init(0, 0, .smooth), .init(0.08, 0, .smooth),
        .init(0.18, -18, .smooth), .init(0.22, -18, .smooth),
        .init(0.30, 16, .accelerate), .init(0.34, 7, .decelerate),
        .init(0.42, -18, .smooth), .init(0.46, -18, .smooth),
        .init(0.54, 16, .accelerate), .init(0.58, 7, .decelerate),
        .init(0.66, 0, .smooth), .init(1, 0, .smooth)
    ]

    static func rotation(at elapsed: TimeInterval, reducedMotion: Bool = false) -> Double {
        guard !reducedMotion, elapsed.isFinite, elapsed >= 0 else { return restingRotation }
        let phase = elapsed.truncatingRemainder(dividingBy: cycleDuration) / cycleDuration
        guard let (start, end) = zip(keyframes, keyframes.dropFirst()).first(where: {
            phase >= $0.0.phase && phase <= $0.1.phase
        }) else { return restingRotation }
        let t = (phase - start.phase) / (end.phase - start.phase)
        return start.rotation + (end.rotation - start.rotation) * end.curve.progress(t)
    }
}
