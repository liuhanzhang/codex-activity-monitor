import Foundation
import Testing
import MonitorModel
@testable import MonitorApp

private actor FakeBackend: BackendManaging {
    var replies: [Result<ManagementState, BackendFailure>]
    var calls: [String] = []
    init(_ replies: [Result<ManagementState, BackendFailure>]) { self.replies = replies }
    func command(_ action: String, extra: [String]) async throws -> ManagementState {
        calls.append(action)
        guard !replies.isEmpty else { throw BackendFailure(message: "Unexpected command") }
        return try replies.removeFirst().get()
    }
    func heartbeat(_ session: ObservationLease) async {}
    func actions() -> [String] { calls }
}

private func state(_ name: String, session: Bool = false) throws -> ManagementState {
    let lease = session ? ",\"session\":{\"version\":2,\"id\":\"test\",\"secret\":\"fixture-secret\",\"owner_pid\":123,\"created_at\":1,\"active\":true}" : ""
    return try JSONDecoder().decode(ManagementState.self, from: Data("{\"version\":1,\"ok\":true,\"state\":\"\(name)\"\(lease)}".utf8))
}

@MainActor private func store(_ backend: FakeBackend) -> DashboardStore {
    DashboardStore(configuration: BackendConfiguration(python: "/unused", source_root: "/unused", runtime: "/unused", data_root: "/unused", build_id: "test"),
                   arguments: ["--test-profile", "/unused"], client: backend)
}

@Test @MainActor func quittingWithoutOwnedSessionDoesNotStopAnotherGUI() async {
    let backend = FakeBackend([])
    let value = store(backend)
    #expect(await value.prepareQuit())
    #expect(await backend.actions().isEmpty)
}

@Test @MainActor func failedStopKeepsGUIAvailableForRetry() async throws {
    let backend = FakeBackend([.success(try state("configured")), .success(try state("configured", session: true)),
                               .failure(BackendFailure(message: "still stopping")), .success(try state("configured"))])
    let value = store(backend)
    await value.configure()
    #expect(!(await value.prepareQuit()))
    #expect(value.operationError == "管理操作失败，请重试。\nstill stopping")
    #expect(await value.prepareQuit())
    #expect(await backend.actions() == ["configure", "resume", "stop", "stop"])
}

@Test @MainActor func purgeClosesWithoutRecreatingOrResumingData() async throws {
    let backend = FakeBackend([.success(try state("purged"))])
    let value = store(backend)
    var exited = false
    value.onPurgeComplete = { exited = true }
    await value.restore(purge: true)
    #expect(exited)
    await value.configure()
    #expect(await value.prepareQuit())
    #expect(await backend.actions() == ["restore"])
}

@Test @MainActor func restoreFailureIsVisibleAndDoesNotReportSuccess() async {
    let backend = FakeBackend([.failure(BackendFailure(message: "restore failed"))])
    let value = store(backend)
    await value.restore(purge: false)
    #expect(value.operationError == "管理操作失败，请重试。\nrestore failed")
    #expect(value.management?.state != "restored")
    #expect(!value.busy)
}
