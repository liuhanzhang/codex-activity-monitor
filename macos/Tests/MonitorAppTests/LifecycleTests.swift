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
                   arguments: ["--test-profile", "/unused"], client: backend, preferredLanguages: ["zh-Hans"])
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

private func pendingCleanup(blocked: Bool = false) throws -> ManagementState {
    let failure = blocked ? """
        ,"cleanup_error":{"code":"unowned_data","display_message":{"key":"control.unowned_data","params":{}}}
        """ : ""
    return try JSONDecoder().decode(ManagementState.self, from: Data("""
        {"version":1,"ok":true,"state":"pending_cleanup","purge":true,"can_cancel_cleanup":true,
         "pending":[{"pid":123,"reason":"codex_running"}]\(failure)}
        """.utf8))
}

@Test @MainActor func blockedCleanupShowsPersistentCauseAndStopsAutomaticRetries() throws {
    let value = store(FakeBackend([]))
    value.management = try pendingCleanup(blocked: true)
    #expect(value.management?.shouldAutomaticallyCleanup == false)
    #expect(value.management?.can_cancel_cleanup == true)
    #expect(value.integrationStatusText == "配置已恢复，清理受阻")
    #expect(value.cleanupErrorText == "数据目录包含未登记文件，未执行全部清理。")
    #expect(!value.integrationCanToggle)
}

@Test @MainActor func cancellingCleanupKeepsAppOpenAndAllowsReconfiguration() async throws {
    let backend = FakeBackend([.success(try pendingCleanup()), .success(try state("restored")),
                               .success(try state("configured")), .success(try state("configured", session: true))])
    let value = store(backend)
    var exited = false
    value.onPurgeComplete = { exited = true }
    await value.restore(purge: true)
    await value.cancelCleanup()
    #expect(!exited)
    #expect(value.management?.state == "restored")
    #expect(value.integrationCanToggle)
    #expect(value.operationError == nil)
    await value.configure()
    #expect(await backend.actions() == ["restore", "cancel-cleanup", "configure", "resume"])
}

@Test @MainActor func explicitCleanupRetryClearsTheFailureAndCompletesPurge() async throws {
    let backend = FakeBackend([.success(try state("purged"))])
    let value = store(backend)
    value.management = try pendingCleanup(blocked: true)
    value.operationError = "old error"
    var exited = false
    value.onPurgeComplete = { exited = true }
    await value.retryCleanup()
    #expect(exited)
    #expect(value.operationError == nil)
    #expect(await backend.actions() == ["cleanup"])
}

@Test @MainActor func cancellationLosingToPurgeClosesWithoutResuming() async throws {
    let backend = FakeBackend([.success(try state("purged"))])
    let value = store(backend)
    value.management = try pendingCleanup()
    var exited = false
    value.onPurgeComplete = { exited = true }
    await value.cancelCleanup()
    #expect(exited)
    await value.configure()
    #expect(await backend.actions() == ["cancel-cleanup"])
}

@Test @MainActor func failedCancellationRetainsCleanupStateForRetry() async throws {
    var failure = BackendFailure(message: "collector still stopping")
    failure.stateAfterFailure = try pendingCleanup()
    let backend = FakeBackend([.failure(failure)])
    let value = store(backend)
    value.management = try pendingCleanup()
    await value.cancelCleanup()
    #expect(value.management?.waitingForCleanup == true)
    #expect(value.management?.purge == true)
    #expect(value.operationError?.contains("collector still stopping") == true)
    #expect(!value.busy)
}
