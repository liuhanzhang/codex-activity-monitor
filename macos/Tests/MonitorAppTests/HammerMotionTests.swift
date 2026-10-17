import Testing
@testable import MonitorApp

@Test func hammerRestsAtTheSymbolsOriginalDiagonal() {
    for phase in [0.0, 0.04, 0.7, 0.9, 1.0] {
        #expect(HammerMotion.rotation(at: phase * HammerMotion.cycleDuration) == 0)
    }
    for frame in 0..<120 {
        #expect(HammerMotion.rotation(at: Double(frame) / 120, reducedMotion: true) == 0)
    }
}

@Test func hammerStrokesAccelerateTowardImpact() {
    for phase in [0.22, 0.46] {
        let start = phase * HammerMotion.cycleDuration
        let duration = 0.08 * HammerMotion.cycleDuration
        let a = HammerMotion.rotation(at: start)
        let b = HammerMotion.rotation(at: start + duration / 2)
        let c = HammerMotion.rotation(at: start + duration)
        #expect(abs(c - b) > 2 * abs(b - a))
    }
}

@Test func hammerMovesContinuouslyAcrossStagesAndLoopBoundary() {
    var previous = HammerMotion.rotation(at: 0)
    for frame in 1...800 {
        let current = HammerMotion.rotation(at: Double(frame) / 120)
        #expect(abs(current - previous) < 4)
        previous = current
    }
}
