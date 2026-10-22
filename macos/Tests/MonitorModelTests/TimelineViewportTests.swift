import Foundation
import Testing
import MonitorModel

@Test(arguments: [30.0, 60, 120, 53.123])
func pagingRetainsAQuarterAndAdvancesOnlyBeyondTheBoundary(span: Double) throws {
    let points = 347 / span
    func page(_ time: Double, previous: TimelineViewport? = nil) throws -> TimelineViewport {
        try #require(TimelineViewport(origin: 1000, time: 1000 + time * 1000,
            viewport: 355, pointsPerSecond: points, previous: previous))
    }
    for time in [0.0, span - 0.001, span] { #expect(try page(time).index == 0) }
    let next = try page(span + 0.001)
    #expect(next.index == 1)
    #expect(abs(next.offset - 347 * 0.75) < 0.000001)
    #expect(try page(span * 1.75).index == 1)
    #expect(try page(span * 1.75 + 0.001).index == 2)
    #expect(try page(span + 0.001, previous: next) == next)
    #expect(try page(span - 0.001, previous: next) == next)
    #expect(try page(span * 10).index == 12)
}

@Test func zoomAndResizeChangeCapacityWithoutRescalingSourceTime() throws {
    #expect(TimelineZoom.allCases.map(\.rawValue) == [30, 60, 120])
    #expect(TimelineZoom.detail.zoomedOut == .overview && TimelineZoom.overview.zoomedOut == .wide)
    #expect(TimelineZoom.wide.zoomedIn == .overview && TimelineZoom.overview.zoomedIn == .detail)
    #expect(TimelineZoom.wide.zoomedOut == .wide && TimelineZoom.detail.zoomedIn == .detail)
    for zoom in TimelineZoom.allCases {
        #expect(abs(TimelineLayout.visibleSeconds(in: 355, zoom: zoom) - zoom.rawValue) < 0.000001)
        #expect(abs(TimelineLayout.visibleSeconds(in: 702, zoom: zoom) - 2 * zoom.rawValue) < 0.000001)
        let first = try #require(TimelineViewport(origin: 0, time: 200000, viewport: 355, pointsPerSecond: zoom.pointsPerSecond))
        let resized = try #require(TimelineViewport(origin: 0, time: 200000, viewport: 702,
            pointsPerSecond: zoom.pointsPerSecond, previous: first))
        #expect(resized.seconds == first.seconds * 2)
        #expect(resized.pointsPerSecond == first.pointsPerSecond)
    }
}

@Test func pagingRejectsUnusableGeometryAndNonfiniteSourceTimes() {
    for width in [0.0, 8, -.infinity, .infinity, .nan] {
        #expect(TimelineViewport(origin: 0, time: 1000, viewport: width, pointsPerSecond: 5) == nil)
    }
    for value in [Double.infinity, -.infinity, .nan] {
        #expect(TimelineViewport(origin: value, time: 1000, viewport: 355, pointsPerSecond: 5) == nil)
        #expect(TimelineViewport(origin: 0, time: value, viewport: 355, pointsPerSecond: 5) == nil)
        #expect(TimelineViewport(origin: 0, time: 1000, viewport: 355, pointsPerSecond: value) == nil)
    }
    #expect(TimelineViewport(origin: 0, time: 1000, viewport: 355, pointsPerSecond: 0) == nil)
}
