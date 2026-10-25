import Foundation
import Testing
@testable import MonitorApp
@testable import MonitorModel

private actor SideEffectExecutor: ProcessExecuting {
    var calls: [String] = []
    let failure: ProcessExecutionError
    init(failure: ProcessExecutionError = .timedOut) { self.failure = failure }
    func execute(_ request: ProcessRequest) async throws -> ProcessResult {
        let action = request.arguments[3]
        calls.append(action)
        if action != "status" { throw failure }
        return ProcessResult(exitCode: 0, stdout: Data("{\"version\":1,\"ok\":true,\"state\":\"configured\",\"runtime\":\"/fixture-runtime\"}".utf8),
                             stderr: Data(), stderrTruncated: false)
    }
    func recorded() -> [String] { calls }
}

private struct FailingStatusExecutor: ProcessExecuting {
    func execute(_ request: ProcessRequest) async throws -> ProcessResult { throw ProcessExecutionError.timedOut }
}

private struct QueryReplyExecutor: ProcessExecuting {
    let code: String
    func execute(_ request: ProcessRequest) async throws -> ProcessResult {
        ProcessResult(exitCode: 0, stdout: Data("{\"version\":1,\"error\":\"\(code)\"}\n".utf8),
                      stderr: Data(), stderrTruncated: false)
    }
}

@Test func timelineQueryErrorsHaveLocalizedPresentation() async throws {
    for code in ["query_too_large", "query_response_invalid", "query_unavailable"] {
        let client = SystemTimelineClient(configuration: executionConfiguration(), runtime: "/unused", executor: QueryReplyExecutor(code: code))
        do {
            _ = try await client.timeline(scope: "/synthetic", thread: "t", turn: "u")
            Issue.record("Expected structured query error")
        } catch let error as BackendFailure {
            let presentation = PresentationFailure(error: error)
            #expect(presentation.diagnostic == nil)
            #expect(presentation.key == (code == "query_too_large" ? "所选内容过多，暂时无法一次载入。" : "时间线暂不可用"))
            #expect(presentation.rendered(.english) != presentation.rendered(.chinese))
        }
    }
}

private actor CancelledMutationExecutor: ProcessExecuting {
    var ready = false
    var continuation: CheckedContinuation<Void, Never>?
    func execute(_ request: ProcessRequest) async throws -> ProcessResult {
        if request.arguments[3] != "status" {
            await withCheckedContinuation { continuation = $0; ready = true }
            try Task.checkCancellation()
        }
        return ProcessResult(exitCode: 0, stdout: Data("{\"version\":1,\"ok\":true,\"state\":\"configured\"}".utf8),
                             stderr: Data(), stderrTruncated: false)
    }
    func started() -> Bool { ready }
    func release() { continuation?.resume(); continuation = nil }
}

private func executionConfiguration() -> BackendConfiguration {
    BackendConfiguration(python: "/unused", source_root: "/unused", runtime: "/unused", data_root: "/unused", build_id: "fixture")
}

@Test func failedMutatingCommandReconcilesActualState() async throws {
    let executor = SideEffectExecutor()
    let client = BackendClient(configuration: executionConfiguration(), dataRoot: "/unused", testProfile: nil, executor: executor)
    do {
        _ = try await client.command("configure")
        Issue.record("Expected the original operation failure")
    } catch let error as BackendFailure {
        #expect(error.stateAfterFailure?.configured == true)
        #expect(error.stateAfterFailure?.runtime == "/fixture-runtime")
        #expect(PresentationFailure(error: error).rendered(.english) == "The background command timed out. Try again.")
    }
    #expect(await executor.recorded() == ["configure", "status"])
}

@Test @MainActor func storeAppliesReconciledManagementStateBeforeNextOperation() async {
    let executor = SideEffectExecutor()
    let client = BackendClient(configuration: executionConfiguration(), dataRoot: "/unused", testProfile: nil, executor: executor)
    let store = DashboardStore(configuration: executionConfiguration(), arguments: [], client: client)
    await store.configure()
    #expect(store.management?.configured == true && !store.busy)
    #expect(await executor.recorded() == ["configure", "status"])
}

@Test func statusFailureDoesNotRecursivelyReconcile() async {
    let client = BackendClient(configuration: executionConfiguration(), dataRoot: "/unused", testProfile: nil, executor: FailingStatusExecutor())
    await #expect(throws: ProcessExecutionError.timedOut) { try await client.command("status") }
}

@Test func processErrorsRenderInBothLanguages() {
    for failure in [ProcessExecutionError.invalidRequest, .launchFailed(2), .ioFailed(5), .timedOut, .outputLimit, .terminationFailed] {
        let presentation = PresentationFailure(error: failure)
        #expect(presentation.rendered(.chinese) == failure.presentationKey)
        #expect(presentation.rendered(.english) != failure.presentationKey)
    }
}

@Test func cancelledMutationStillReadsStateOutsideTheCancelledTask() async throws {
    let executor = CancelledMutationExecutor()
    let client = BackendClient(configuration: executionConfiguration(), dataRoot: "/unused", testProfile: nil, executor: executor)
    let task = Task { try await client.command("configure") }
    let deadline = ContinuousClock.now.advanced(by: .seconds(2))
    while !(await executor.started()) && ContinuousClock.now < deadline { await Task.yield() }
    #expect(await executor.started())
    task.cancel(); await executor.release()
    do { _ = try await task.value; Issue.record("Expected cancellation after mutation") }
    catch let error as BackendFailure {
        #expect(error.stateAfterFailure?.configured == true)
        #expect(PresentationFailure(error: error).rendered(.english) == "The background command was cancelled.")
    }
}

@Test func systemTimelineClientQueriesOwnedHistoryThroughTheRealExecutor() async throws {
    let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
        .deletingLastPathComponent().deletingLastPathComponent()
    let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    let sessions = directory.appendingPathComponent("sessions")
    try FileManager.default.createDirectory(at: sessions, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: directory) }
    let body = String(repeating: "x", count: 262_143) + "e\u{301}🙂\r\n原始内容"
    let rows: [[String: Any]] = [
        ["type": "session_meta", "payload": ["id": "synthetic", "history_mode": "paginated"]],
        ["type": "event_msg", "timestamp": "2026-01-01T00:00:00Z", "payload": ["type": "task_started", "turn_id": "turn"]],
        ["type": "event_msg", "payload": ["type": "item_completed", "thread_id": "synthetic", "turn_id": "turn",
             "item": ["id": "message", "type": "AgentMessage", "text": body, "status": "completed"]]]
    ]
    var jsonl = Data()
    for row in rows { jsonl.append(try JSONSerialization.data(withJSONObject: row)); jsonl.append(10) }
    try jsonl.write(to: sessions.appendingPathComponent("fixture-synthetic.jsonl"))
    let configuration = BackendConfiguration(python: root.appendingPathComponent(".local/venv/bin/python").path,
        source_root: root.path, runtime: directory.appendingPathComponent("runtime").path,
        data_root: directory.path, build_id: "fixture")
    let client = SystemTimelineClient(configuration: configuration, runtime: configuration.runtime)
    let bundle = try await client.timeline(scope: directory.path, thread: "synthetic", turn: "turn")
    #expect(bundle.thread_id == "synthetic" && bundle.selected_turn_id == "turn")
    let phase = try #require(bundle.allActivities.first { $0.item_id == "message" })
    let first = try await client.detail(phase: phase, page: nil).validated(for: phase)
    #expect(first.version == 2 && first.sections[0].next_offset == 262_144)
    let cursor = try #require(first.pageCursor())
    let page = try await client.detail(phase: phase, page: cursor)
    let complete = try first.appendingPage(page, cursor: cursor)
    #expect(complete.sections[0].text.utf8.elementsEqual(body.utf8))
    #expect(complete.pageCursor() == nil)
    #expect(!FileManager.default.fileExists(atPath: configuration.runtime + "/collector.json"))
}
