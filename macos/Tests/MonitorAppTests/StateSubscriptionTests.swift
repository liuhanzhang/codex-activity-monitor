import Darwin
import Foundation
import Testing
@testable import MonitorApp
import MonitorModel

private final class StateServerFixture: @unchecked Sendable {
    let directory: URL
    let process: Process
    var path: String { directory.appendingPathComponent("state.sock").path }

    init(mode: String) async throws {
        let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent().deletingLastPathComponent()
        // Short /tmp paths respect sockaddr_un's byte limit on macOS.
        directory = URL(fileURLWithPath: "/tmp/cam-s-"+UUID().uuidString.prefix(8))
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        process = Process()
        process.executableURL = root.appendingPathComponent(".local/venv/bin/python")
        process.arguments = ["-B", root.appendingPathComponent("tests/fixtures/state_subscription_server.py").path, directory.path, mode]
        process.environment = ProcessInfo.processInfo.environment.merging(["PYTHONPATH": root.appendingPathComponent("src").path]) { _, new in new }
        process.standardInput = FileHandle.nullDevice
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        try process.run()
        let deadline = ContinuousClock.now.advanced(by: .seconds(3))
        while !FileManager.default.fileExists(atPath: directory.appendingPathComponent("ready").path),
              process.isRunning, ContinuousClock.now < deadline { try await Task.sleep(for: .milliseconds(10)) }
        guard FileManager.default.fileExists(atPath: directory.appendingPathComponent("ready").path) else {
            await stop()
            throw StateSubscriptionError.disconnected
        }
    }

    func stop() async {
        try? Data().write(to: directory.appendingPathComponent("stop"))
        var deadline = ContinuousClock.now.advanced(by: .seconds(1))
        while process.isRunning, ContinuousClock.now < deadline { try? await Task.sleep(for: .milliseconds(10)) }
        if process.isRunning { process.terminate() }
        deadline = ContinuousClock.now.advanced(by: .seconds(1))
        while process.isRunning, ContinuousClock.now < deadline { try? await Task.sleep(for: .milliseconds(10)) }
        if process.isRunning { kill(process.processIdentifier, SIGKILL) }
        try? FileManager.default.removeItem(at: directory)
    }
}

private func withStateFixture(_ mode: String, isolation: isolated (any Actor)? = #isolation,
                              body: (StateServerFixture) async throws -> Void) async throws {
    let fixture = try await StateServerFixture(mode: mode)
    do { try await body(fixture); await fixture.stop() }
    catch { await fixture.stop(); throw error }
}

@Test func pythonStateServerIsHealthyWithAnOldArchiveTimestamp() async throws {
    try await withStateFixture("normal") { fixture in
        let subscriber = UnixStateSubscriber(options: .init(heartbeatInterval: .milliseconds(50)))
        var accepted = 0
        for try await value in subscriber.updates(at: fixture.path) where value.snapshot != nil {
            #expect(value.isFresh)
            #expect(value.snapshot?.updated_at == 0)
            #expect(value.snapshot?.isFresh() == false)
            #expect(value.snapshot?.menuTitle(connectionFresh: value.isFresh) == "13 T/s")
            #expect(value.snapshot?.threads.first?.name == "中文 café 👋")
            accepted += 1
            if accepted == 3 { break }
        }
        #expect(accepted == 3) // Health-only updates still retain the full state.
    }
}

@Test func fragmentedUTF8AndReorderedSequencesRemainValid() async throws {
    for mode in ["fragmented", "reordered"] {
        try await withStateFixture(mode) { fixture in
            var lastSequence: UInt64 = 0
            var latestUpdates = 0
            let subscriber = UnixStateSubscriber(options: .init(heartbeatInterval: .milliseconds(25)))
            for try await value in subscriber.updates(at: fixture.path) {
                #expect(value.sequence >= lastSequence)
                lastSequence = value.sequence
                if value.sequence == (mode == "reordered" ? 3 : 1) {
                    #expect(value.snapshot?.threads.first?.name == "中文 café 👋")
                    latestUpdates += 1
                    if latestUpdates == 2 { break }
                }
            }
            let requests = try String(contentsOf: fixture.directory.appendingPathComponent("requests.jsonl"), encoding: .utf8)
            #expect(requests.contains("\"role\": \"reader\""))
        }
    }
}

@Test func stateHealthExpiresUsingOnlyTheMonotonicClock() {
    let acknowledged = ContinuousClock.now
    let health = StateHealth(acknowledgedAt: acknowledged, tickAgeAtAcknowledgement: .milliseconds(500), persistence: nil)
    #expect(health.isResponsive(at: acknowledged.advanced(by: .seconds(1))))
    #expect(!health.isResponsive(at: acknowledged.advanced(by: .milliseconds(1500))))
    #expect(!health.isResponsive(at: acknowledged.advanced(by: .milliseconds(-1))))
}

@Test func ownedSubscriptionRenewsOnlyItsAuthenticatedConnection() async throws {
    try await withStateFixture("owner") { fixture in
        let subscriber = UnixStateSubscriber(options: .init(heartbeatInterval: .milliseconds(25),
            owner: StateOwnerCredential(sessionID: String(repeating: "i", count: 32), secret: String(repeating: "s", count: 64))))
        var updates = 0
        for try await value in subscriber.updates(at: fixture.path) where value.snapshot != nil {
            #expect(value.isFresh)
            #expect(value.health?.observation?.owner_connected == true)
            #expect((value.health?.observation?.expires_in_ms ?? 0) > 4000)
            updates += 1
            if updates == 3 { break }
        }
        #expect(updates == 3)
    }
}

@Test func ownedSubscriptionRejectsWrongCredentials() async throws {
    try await withStateFixture("owner") { fixture in
        let subscriber = UnixStateSubscriber(options: .init(owner: StateOwnerCredential(
            sessionID: String(repeating: "i", count: 32), secret: String(repeating: "n", count: 64))))
        await #expect(throws: StateSubscriptionError.leaseRejected("invalid_lease")) {
            for try await _ in subscriber.updates(at: fixture.path) {}
        }
    }
}

@Test func ownedSubscriptionHasExplicitBoundedReleaseAndReconnectGrace() async throws {
    try await withStateFixture("owner") { fixture in
        let subscriber = UnixStateSubscriber(options: .init(owner: StateOwnerCredential(
            sessionID: String(repeating: "i", count: 32), secret: String(repeating: "s", count: 64))))
        let first = subscriber.subscribe(at: fixture.path)
        var iterator = first.updates.makeAsyncIterator()
        while let value = try await iterator.next() { if value.snapshot != nil { break } }
        first.cancel() // A dropped connection preserves, but does not extend, its existing deadline.
        let second = subscriber.subscribe(at: fixture.path)
        var replacement = second.updates.makeAsyncIterator()
        while let value = try await replacement.next() { if value.snapshot != nil { break } }
        let before = ContinuousClock.now
        #expect(await second.release())
        #expect(before.duration(to: .now) < .seconds(1))
        #expect(await second.release()) // Repeated release does not create another request or deadline.
        // Explicit release revoked the grant, so reconnect now requires management.
        await #expect(throws: StateSubscriptionError.leaseRejected("lease_expired")) {
            for try await _ in subscriber.updates(at: fixture.path) {}
        }
    }
}

@Test func foreignOrBackloggedRepliesCannotRenewHealth() async throws {
    try await withStateFixture("wrong_nonce") { fixture in
        let subscriber = UnixStateSubscriber(options: .init(heartbeatInterval: .milliseconds(25), requestTimeout: .milliseconds(100)))
        await #expect(throws: StateSubscriptionError.timedOut) {
            for try await _ in subscriber.updates(at: fixture.path) {}
        }
    }
    try await withStateFixture("stale") { fixture in
        for try await value in UnixStateSubscriber().updates(at: fixture.path) where value.snapshot != nil {
            #expect(!value.isFresh)
            break
        }
    }
}

@Test func stateProtocolRejectsOversizeVersionAndForeignInstance() async throws {
    for (mode, failure) in [("oversized", StateSubscriptionError.frameTooLarge), ("unsupported", .unsupportedVersion),
                            ("foreign_instance", .invalidFrame)] {
        try await withStateFixture(mode) { fixture in
            let subscriber = UnixStateSubscriber(options: .init(maxFrameBytes: 4096))
            await #expect(throws: failure) { for try await _ in subscriber.updates(at: fixture.path) {} }
        }
    }
}

@Test func stateSubscriptionCancellationClosesItsOwnedSocket() async throws {
    try await withStateFixture("fragmented") { fixture in
        let task = Task {
            for try await _ in UnixStateSubscriber().updates(at: fixture.path) { try Task.checkCancellation() }
        }
        try await Task.sleep(for: .milliseconds(100))
        task.cancel()
        _ = await task.result
        let deadline = ContinuousClock.now.advanced(by: .seconds(1))
        while fixture.process.isRunning, ContinuousClock.now < deadline { try await Task.sleep(for: .milliseconds(10)) }
        #expect(!fixture.process.isRunning)
    }
}

private actor BlockedManagementClient: BackendManaging {
    private var entered = false
    func command(_ action: String, extra: [String]) async throws -> ManagementState {
        entered = true
        try await Task.sleep(for: .seconds(60))
        throw CancellationError()
    }
    func heartbeat(_ session: ObservationLease) async {}
    func hasStarted() -> Bool { entered }
}

@Test @MainActor func storeAppliesLiveStateWhileManagementIsBlocked() async throws {
    try await withStateFixture("normal") { fixture in
        let configuration = BackendConfiguration(python: "/unused", source_root: "/unused", runtime: fixture.directory.path,
                                                  data_root: "/unused", build_id: "fixture")
        let management = BlockedManagementClient()
        let store = DashboardStore(configuration: configuration, arguments: [], client: management,
                                   stateSubscriber: UnixStateSubscriber())
        store.start()
        let deadline = ContinuousClock.now.advanced(by: .seconds(2))
        while !store.fresh, ContinuousClock.now < deadline { try await Task.sleep(for: .milliseconds(10)) }
        #expect(store.fresh && store.snapshot?.threads.count == 1)
        #expect(await management.hasStarted())
        #expect(store.menuMode == .speed)
        #expect(!FileManager.default.fileExists(atPath: fixture.directory.appendingPathComponent("snapshot.json").path))
        #expect(await store.prepareQuit())
    }
}
