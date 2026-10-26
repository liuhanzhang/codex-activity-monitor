import Foundation
import MonitorModel

protocol TimelineReading: Sendable {
    func timeline(scope: String, thread: String, turn: String?) async throws -> TimelineBundle
    func detail(phase: TimelinePhase, page: ActivityPageCursor?) async throws -> ActivityDetail
}

func queryFailureKey(_ error: any Error, fallback: String) -> String {
    (error as? BackendFailure)?.presentationKey ?? fallback
}

private struct TimelineQueryRequest: Encodable, Sendable {
    enum Action: String, Encodable { case timeline, detail }
    let action: Action
    let scope_id: String
    let thread_id: String
    let turn_id: String?
    let phase_id: String?
    let section_id: String?
    let offset: Int?
    let page_token: String?

    init(action: Action, scope: String, thread: String, turn: String?, phase: String? = nil, page: ActivityPageCursor? = nil) {
        self.action = action; scope_id = scope; thread_id = thread; turn_id = turn; phase_id = phase
        section_id = page?.sectionID; offset = action == .detail ? page?.offset ?? 0 : nil; page_token = page?.token
    }
}

private struct TimelineQueryResponse<Value: Decodable>: Decodable {
    let value: Value
    private enum CodingKeys: String, CodingKey { case version, error }
    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        if let error = try container.decodeIfPresent(String.self, forKey: .error) {
            guard try container.decode(Int.self, forKey: .version) == 1 else { throw SnapshotError.unsupportedVersion }
            throw BackendFailure(message: "", presentationKey: error == "query_too_large"
                ? "所选内容过多，暂时无法一次载入。" : "时间线暂不可用")
        }
        value = try Value(from: decoder)
    }
}

struct SystemTimelineClient: TimelineReading {
    let configuration: BackendConfiguration
    let runtime: String
    var executor: any ProcessExecuting = ProcessExecutor()

    private func request(_ value: TimelineQueryRequest) async throws -> Data {
        let payload = String(decoding: try JSONEncoder().encode(value), as: UTF8.self)
        var request = configuration.request(arguments: ["--runtime", runtime, "timeline-query", payload])
        request.timeout = 12; request.stdoutLimit = 8 * 1_048_576
        let result = try await executor.execute(request)
        guard result.exitCode == 0 else { throw BackendFailure(message: "", presentationKey: "暂时无法读取轨迹") }
        return result.stdout
    }

    func timeline(scope: String, thread: String, turn: String?) async throws -> TimelineBundle {
        let value = TimelineQueryRequest(action: .timeline, scope: scope, thread: thread, turn: turn)
        let bundle = try JSONDecoder().decode(TimelineQueryResponse<TimelineBundle>.self, from: await request(value)).value
        guard bundle.version == 1 else { throw SnapshotError.unsupportedVersion }
        return bundle
    }

    func detail(phase: TimelinePhase, page: ActivityPageCursor?) async throws -> ActivityDetail {
        let value = TimelineQueryRequest(action: .detail, scope: phase.scope_id, thread: phase.thread_id,
                                         turn: phase.turn_id, phase: phase.id, page: page)
        return try JSONDecoder().decode(TimelineQueryResponse<ActivityDetail>.self, from: await request(value)).value
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
        let threads = object["threads"] as? [String: [String: Any]] ?? [:]
        let owner = threads[thread] ?? object
        let bundles = owner["bundles"] as? [String: Any] ?? [:]
        guard let value = bundles[turn ?? "current"] ?? owner["timeline"] else {
            throw BackendFailure(message: L("时间线暂不可用"))
        }
        return try TimelineBundle.decode(JSONSerialization.data(withJSONObject: value))
    }
    func detail(phase: TimelinePhase, page: ActivityPageCursor?) async throws -> ActivityDetail {
        guard page == nil else { throw ActivityContentError.invalidPage }
        let data = try await fixture()
        let object = try JSONSerialization.jsonObject(with: data) as? [String: Any] ?? [:]
        let details = object["details"] as? [String: Any] ?? [:]
        guard let value = details[phase.id] else { throw BackendFailure(message: L("完整内容尚未写入历史")) }
        return try JSONDecoder().decode(ActivityDetail.self, from: JSONSerialization.data(withJSONObject: value))
    }
}
