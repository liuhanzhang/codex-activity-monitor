import AppKit
import Foundation
import MonitorModel
import Observation
import ServiceManagement

struct BackendConfiguration: Decodable, Sendable {
    let python: String
    let source_root: String
    let runtime: String
    let data_root: String
    let build_id: String
}

@MainActor @Observable
final class DashboardStore {
    var snapshot: Snapshot?
    var readError: String?
    var menuTitle = "—"
    var menuMode: MenuMode = .unavailable
    var fresh = false
    var management: ManagementState?
    var operationError: String?
    var busy = false
    var loginEnabled = false
    var loginNeedsApproval = false
    private(set) var expanded: Set<String> = []
    private var expansionOverrides: [String: Bool] = [:]
    private(set) var presentationID = 0
    var showRecent = false
    var dismissPresentation = 0
    var panelMaxHeight: CGFloat = 520
    var panelVisible = false {
        didSet {
            guard panelVisible, !oldValue else { return }
            presentationID += 1
            expansionOverrides = [:]
            showRecent = false
            reconcileExpansion()
        }
    }
    private(set) var chatOrder: [String: Int] = [:]
    private var nextChatRank = 0
    let configuration: BackendConfiguration
    let readOnly: Bool
    let dataRoot: String
    let snapshotOverride: String?
    let legacyUnset: Bool
    let testProfile: String?
    var onPurgeComplete: (() -> Void)?
    private var lease: ObservationLease?
    private var runtimePath: String
    private var nextCleanup = Date.distantPast
    private var nextResume = Date.distantPast
    private var closing = false
    var onUpdate: (() -> Void)?
    var onActivitySelected: ((TimelineStore, [TimelinePhase]) -> Void)?
    @ObservationIgnored private var timelines: [String: TimelineStore] = [:]
    private var timelineFixture: String?
    private var polling: Task<Void, Never>?
    private let injectedClient: (any BackendManaging)?

    init(configuration: BackendConfiguration, arguments: [String], client: (any BackendManaging)? = nil) {
        self.configuration = configuration
        injectedClient = client
        readOnly = arguments.contains("--read-only") || arguments.contains("--snapshot")
        snapshotOverride = arguments.firstIndex(of: "--snapshot").flatMap { i in i + 1 < arguments.count ? arguments[i + 1] : nil }
        timelineFixture = arguments.firstIndex(of: "--timeline-fixture").flatMap { i in i + 1 < arguments.count ? arguments[i + 1] : nil }
        let profile = arguments.firstIndex(of: "--test-profile").flatMap { i in i + 1 < arguments.count ? arguments[i + 1] : nil }
        testProfile = profile
        dataRoot = profile.map { $0 + "/data" } ?? arguments.firstIndex(of: "--data-root").flatMap { i in i + 1 < arguments.count ? arguments[i + 1] : nil } ?? configuration.data_root
        runtimePath = configuration.runtime
        legacyUnset = arguments.contains("--legacy-unset")
        if readOnly && timelineFixture == nil && FileManager.default.fileExists(atPath: configuration.runtime + "/timeline.json") {
            timelineFixture = configuration.runtime + "/timeline.json"
        }
    }

    private var client: any BackendManaging { injectedClient ?? BackendClient(configuration: configuration, dataRoot: dataRoot, testProfile: testProfile) }

    func start() {
        polling = Task {
            if !readOnly { await bootstrap() }
            while !Task.isCancelled {
                if let lease { await client.heartbeat(lease) }
                if !readOnly { await readManagement() }
                if management?.waitingForCleanup == true && !busy && Date() >= nextCleanup {
                    nextCleanup = Date().addingTimeInterval(5)
                    await cleanup()
                }
                guard !Task.isCancelled else { break }
                let url = URL(fileURLWithPath: snapshotOverride ?? runtimePath + "/snapshot.json")
                let result = await Task.detached(priority: .utility) { () -> Result<Snapshot, Error> in
                    Result { try Snapshot.decode(Data(contentsOf: url)) }
                }.value
                switch result {
                case .success(let value): snapshot = value; readError = nil
                case .failure: readError = L("暂时无法读取采集数据")
                }
                updatePresentation()
                if !readOnly && management?.configured == true && !fresh && !busy && !closing && Date() >= nextResume {
                    nextResume = Date().addingTimeInterval(5)
                    busy = true
                    do { try await resume() }
                    catch { operationError = error.localizedDescription }
                    busy = false
                }
                onUpdate?()
                try? await Task.sleep(for: .seconds(1))
            }
        }
    }

    func updatePresentation(at date: Date = Date()) {
        fresh = readError == nil && snapshot?.isFresh(at: date) == true
        menuTitle = fresh ? (snapshot?.menuTitle(at: date) ?? "—") : "—"
        menuMode = fresh ? (snapshot?.menuMode(at: date) ?? .unavailable) : .unavailable
        let chats = snapshot?.threads ?? []
        let ids = Set(chats.map(\.id))
        chatOrder = chatOrder.filter { ids.contains($0.key) }
        reconcileExpansion()
        timelines = timelines.filter { ids.contains($0.key) }
        for chat in chats where chatOrder[chat.id] == nil {
            chatOrder[chat.id] = nextChatRank
            nextChatRank += 1
        }
    }

    func setExpanded(_ value: Bool, for chat: Chat) {
        expansionOverrides[chat.id] = value
        if value { expanded.insert(chat.id) }
        else { expanded.remove(chat.id) }
    }

    private func reconcileExpansion() {
        guard let snapshot else { expanded = []; return }
        let ids = Set(snapshot.threads.map(\.id))
        expansionOverrides = expansionOverrides.filter { ids.contains($0.key) }
        expanded = Set(snapshot.rootChats.filter { snapshot.hasActiveWork(in: $0) }.map(\.id))
        for (id, value) in expansionOverrides {
            if value { expanded.insert(id) }
            else { expanded.remove(id) }
        }
    }

    var activeRows: [ChatRow] {
        guard let snapshot else { return [] }
        let roots = snapshot.rootChats.filter { snapshot.hasActiveWork(in: $0) }.sorted {
            (chatOrder[$0.id] ?? Int.max) < (chatOrder[$1.id] ?? Int.max)
        }
        return snapshot.rows(roots: roots, expanded: [], order: chatOrder)
    }

    var recentRoots: [Chat] {
        guard let snapshot else { return [] }
        return snapshot.rootChats.filter { !snapshot.hasActiveWork(in: $0) }
    }

    var recentRows: [ChatRow] {
        guard showRecent, let snapshot else { return [] }
        return snapshot.rows(roots: recentRoots, expanded: [], order: chatOrder)
    }

    func timeline(for chat: Chat) -> TimelineStore {
        if let existing = timelines[chat.id] { return existing }
        let client: any TimelineReading = readOnly
            ? FixtureTimelineClient(path: timelineFixture)
            : SystemTimelineClient(configuration: configuration, runtime: runtimePath)
        let value = TimelineStore(scope: chat.scope, thread: chat.threadID, title: chat.title, client: client)
        value.onOpen = { [weak self] timeline, phase in self?.onActivitySelected?(timeline, phase) }
        timelines[chat.id] = value
        return value
    }

    var connectionLabel: String {
        if !readOnly, let management, !management.configured { return L(management.label) }
        if readError != nil { return readError! }
        if !fresh { return String(localized: "Stale", bundle: .module) }
        if snapshot?.overview?.coverage == "partial" { return L("部分观测") }
        if snapshot?.overview?.coverage == "complete" { return L("已连接") }
        if snapshot?.backends?.values.contains(where: { $0.connected == true }) == true {
            return L("已连接 · 总速度暂不可用")
        }
        return String(localized: "NoConnection", bundle: .module)
    }

    var bridgeNeedsRestart: Bool {
        snapshot?.backends?.values.contains { $0.connected == true && !$0.supportsObservationLease } == true
    }

    var compactConnectionLabel: String {
        if !readOnly, let management, !management.configured { return L("未接入") }
        if !fresh { return L("数据不可用") }
        if snapshot?.overview?.coverage == "partial" { return L("部分观测") }
        if snapshot?.backends?.values.contains(where: { $0.connected == true }) == true { return L("已连接") }
        return L("等待接入")
    }

    var connectionDetail: String? {
        if bridgeNeedsRestart { return L("旧接入仍在运行；任务结束后重开 Codex，以启用退出暂停采集。") }
        if !readOnly, let management, !management.configured { return L(management.label) }
        if !fresh { return connectionLabel }
        if let notice = snapshot?.observationNotice { return L(notice.rawValue) }
        return nil
    }

    private func bootstrap() async {
        do {
            management = try await client.command("status")
            if let runtime = management?.runtime { runtimePath = runtime }
            if management?.state == "new" { await configure() }
            // launchctl values are session-scoped; reapply integration on login
            // without replacing its original backup.
            else if management?.configured == true { await configure() }
            else if management?.waitingForCleanup == true { await cleanup() }
            loginEnabled = SMAppService.mainApp.status == .enabled
            loginNeedsApproval = SMAppService.mainApp.status == .requiresApproval
        } catch { operationError = error.localizedDescription }
    }

    func configure() async {
        guard !busy, !readOnly, !closing else { return }
        busy = true
        operationError = nil
        defer { busy = false }
        do {
            var extra = ["--legacy-runtime", configuration.runtime]
            if legacyUnset { extra.append("--legacy-unset") }
            management = try await client.command("configure", extra: extra)
            if let runtime = management?.runtime { runtimePath = runtime }
            try await resume()
            let preferences = try? Data(contentsOf: URL(fileURLWithPath: dataRoot + "/app-state.json"))
            let object = preferences.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }
            if testProfile == nil && object?["launch_at_login"] as? Bool != false {
                do { try await updateLogin(true) }
                catch { operationError = L("登录启动未完成：") + error.localizedDescription }
            }
        } catch { operationError = error.localizedDescription }
    }

    private func resume() async throws {
        guard !closing else { return }
        let result = try await client.command("resume", extra: ["--owner-pid", String(ProcessInfo.processInfo.processIdentifier)])
        management = result
        lease = result.session
        if let runtime = result.runtime { runtimePath = runtime }
    }

    func restore(purge: Bool) async {
        guard !busy, !readOnly, !closing else { return }
        busy = true
        defer { busy = false }
        do {
            lease = nil
            management = try await client.command("restore", extra: purge ? ["--purge"] : [])
            if management?.state == "purged" { finishPurge() }
            operationError = nil
        } catch { operationError = error.localizedDescription }
    }

    private func cleanup() async {
        guard !busy, !readOnly, !closing else { return }
        busy = true
        defer { busy = false }
        do {
            management = try await client.command("cleanup")
            if management?.state == "purged" { finishPurge() }
        } catch { operationError = error.localizedDescription }
    }

    private func finishPurge() {
        closing = true
        polling?.cancel()
        lease = nil
        onPurgeComplete?()
    }

    func prepareQuit() async -> Bool {
        guard !busy else { operationError = L("管理操作仍在完成，请稍后退出。"); return false }
        let ownedSession = lease != nil
        closing = true
        guard !readOnly, management?.state != "purged" else { return true }
        do {
            if ownedSession { _ = try await client.command("stop", extra: ["--owner-pid", String(ProcessInfo.processInfo.processIdentifier)]) }
            polling?.cancel()
            lease = nil
            return true
        }
        catch { closing = false; operationError = error.localizedDescription; return false }
    }

    func setLogin(_ enabled: Bool) async {
        guard !busy, !readOnly, !closing else { return }
        busy = true
        defer { busy = false }
        do {
            try await updateLogin(enabled)
        } catch { operationError = L("登录启动未完成：") + error.localizedDescription }
    }

    private func updateLogin(_ enabled: Bool) async throws {
        if enabled && SMAppService.mainApp.status != .enabled { try SMAppService.mainApp.register() }
        else if !enabled && SMAppService.mainApp.status != .notRegistered { try await SMAppService.mainApp.unregister() }
        loginEnabled = SMAppService.mainApp.status == .enabled
        loginNeedsApproval = SMAppService.mainApp.status == .requiresApproval
        _ = try await client.command("preferences", extra: ["--launch-at-login", enabled ? "on" : "off"])
    }

    private func readManagement() async {
        let path = dataRoot + "/integration.json"
        let result = await Task.detached(priority: .utility) { () -> ManagementState? in
            guard let data = try? Data(contentsOf: URL(fileURLWithPath: path)) else { return nil }
            guard let state = try? JSONDecoder().decode(ManagementState.self, from: data), state.version == 1 else { return nil }
            return state
        }.value
        if let result { management = result }
    }
}
