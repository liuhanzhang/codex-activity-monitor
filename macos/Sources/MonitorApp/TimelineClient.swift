import Foundation
import MonitorModel

protocol TimelineReading: Sendable {
    func timeline(scope: String, thread: String, turn: String?) async throws -> TimelineBundle
    func detail(phase: TimelinePhase, section: String?, offset: Int) async throws -> ActivityDetail
}

private final class QueryProcess: @unchecked Sendable {
    private let lock = NSLock()
    private var process: Process?
    private var cancelled = false

    func run(configuration: BackendConfiguration, runtime: String, request: [String: Any]) throws -> Data {
        let child = Process()
        child.executableURL = URL(fileURLWithPath: configuration.python)
        let payload = String(decoding: try JSONSerialization.data(withJSONObject: request), as: UTF8.self)
        child.arguments = ["-m", "codex_progress_monitor.cli", "--runtime", runtime, "timeline-query", payload]
        child.currentDirectoryURL = URL(fileURLWithPath: configuration.source_root)
        var environment = ProcessInfo.processInfo.environment
        environment["PYTHONPATH"] = configuration.source_root + "/src"
        child.environment = environment
        let output = Pipe()
        child.standardOutput = output; child.standardError = FileHandle.nullDevice
        lock.lock()
        if cancelled { lock.unlock(); throw CancellationError() }
        process = child
        do { try child.run() } catch { process = nil; lock.unlock(); throw error }
        lock.unlock()
        DispatchQueue.global().asyncAfter(deadline: .now() + 12) { [weak self] in self?.cancel() }
        defer { lock.lock(); process = nil; lock.unlock() }
        let data = try output.fileHandleForReading.readToEnd() ?? Data()
        child.waitUntilExit()
        guard child.terminationStatus == 0 else { throw BackendFailure(message: L("暂时无法读取轨迹")) }
        return data
    }

    func cancel() {
        lock.lock(); defer { lock.unlock() }
        cancelled = true
        if process?.isRunning == true { process?.terminate() }
    }
}

struct SystemTimelineClient: TimelineReading {
    let configuration: BackendConfiguration
    let runtime: String

    private func request(_ value: [String: String]) async throws -> Data {
        let operation = QueryProcess()
        return try await withTaskCancellationHandler {
            let result = try await Task.detached(priority: .utility) {
                try operation.run(configuration: configuration, runtime: runtime, request: value)
            }.value
            try Task.checkCancellation()
            return result
        } onCancel: { operation.cancel() }
    }

    func timeline(scope: String, thread: String, turn: String?) async throws -> TimelineBundle {
        var value = ["action": "timeline", "scope_id": scope, "thread_id": thread]
        if let turn { value["turn_id"] = turn }
        return try TimelineBundle.decode(await request(value))
    }

    func detail(phase: TimelinePhase, section: String?, offset: Int) async throws -> ActivityDetail {
        var value = ["action": "detail", "scope_id": phase.scope_id, "thread_id": phase.thread_id,
                     "turn_id": phase.turn_id, "phase_id": phase.id, "offset": String(offset)]
        if let section { value["section_id"] = section }
        return try JSONDecoder().decode(ActivityDetail.self, from: await request(value))
    }
}

struct FixtureTimelineClient: TimelineReading {
    let path: String?
    private func fixture() async throws -> Data {
        guard let path else { throw BackendFailure(message: L("时间线暂不可用")) }
        return try await Task.detached(priority: .utility) { try Data(contentsOf: URL(fileURLWithPath: path)) }.value
    }
    func timeline(scope: String, thread: String, turn: String?) async throws -> TimelineBundle {
        let data = try await fixture()
        let object = try JSONSerialization.jsonObject(with: data) as? [String: Any] ?? [:]
        let bundles = object["bundles"] as? [String: Any] ?? [:]
        guard let value = bundles[turn ?? "current"] ?? object["timeline"] else {
            throw BackendFailure(message: L("时间线暂不可用"))
        }
        return try TimelineBundle.decode(JSONSerialization.data(withJSONObject: value))
    }
    func detail(phase: TimelinePhase, section: String?, offset: Int) async throws -> ActivityDetail {
        let data = try await fixture()
        let object = try JSONSerialization.jsonObject(with: data) as? [String: Any] ?? [:]
        let details = object["details"] as? [String: Any] ?? [:]
        guard let value = details[phase.id] else { throw BackendFailure(message: L("完整内容尚未写入历史")) }
        return try JSONDecoder().decode(ActivityDetail.self, from: JSONSerialization.data(withJSONObject: value))
    }
}
