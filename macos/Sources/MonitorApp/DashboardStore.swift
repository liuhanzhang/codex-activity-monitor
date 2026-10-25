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
    var snapshot: Snapshot? {
        didSet {
            if oldValue?.projection !== snapshot?.projection {
                projectionAcceptedAt = .now
                chatSelection = snapshot?.projection.initial
            }
        }
    }
    private(set) var chatSelection: SnapshotProjection.Selection?
    @ObservationIgnored private var projectionAcceptedAt = ContinuousClock.now
    private var projectionElapsed: Double {
        let duration = projectionAcceptedAt.duration(to: .now).components
        return Double(duration.seconds) + Double(duration.attoseconds)/1e18
    }
    private var selectionExpired: Bool { chatSelection?.nextExpiry.map { projectionElapsed >= $0 } == true }
    var readError: String?
    var menuTitle = "—"
    var menuMode: MenuMode = .unavailable
    var fresh = false
    var management: ManagementState?
    private(set) var language: MonitorLanguage = .chinese
    private var operationFailure: PresentationFailure?
    var operationError: String? {
        get { operationFailure?.rendered(language) }
        set { operationFailure = newValue.map { PresentationFailure(key: $0) } }
    }
    @ObservationIgnored private var languageSaveTask: Task<Void, Never>?
    private var pendingLanguage: MonitorLanguage?
    private func L(_ key: String) -> String { localizedText(key, language: language) }
    func setLanguage(_ value: MonitorLanguage) {
        guard !closing, !busy, management?.waitingForCleanup != true, management?.state != "purged" else { return }
        language = value
        onUpdate?()
        guard !readOnly else { return }
        pendingLanguage = value
        guard languageSaveTask == nil else { return }
        languageSaveTask = Task {
            while let next = pendingLanguage {
                pendingLanguage = nil
                do {
                    _ = try await client.command("preferences", extra: ["--ui-language", next.rawValue])
                    if pendingLanguage == nil && operationFailure?.key == "语言已切换，本次选择未能保存。" { operationFailure = nil }
                } catch {
                    if pendingLanguage == nil { operationFailure = PresentationFailure(key: "语言已切换，本次选择未能保存。") }
                }
            }
            languageSaveTask = nil
        }
    }
    func waitForLanguageSave() async { await languageSaveTask?.value }
    private func recordError(_ error: any Error, prefix: String? = nil) {
        if let state = (error as? BackendFailure)?.stateAfterFailure {
            management = state
            if let runtime = state.runtime { runtimePath = runtime }
            if state.configured != true { lease = nil }
        }
        operationFailure = PresentationFailure(error: error, prefix: prefix)
    }
    var busy = false
    var loginEnabled = false
    var loginNeedsApproval = false
    private(set) var selectedChatKey: String?
    private var retainedSelection: Chat?
    private var lastActiveSelectionKey: String?
    private(set) var presentationID = 0
    let auxiliary = PanelAuxiliaryState()
    var showRecent = false {
        didSet {
            guard oldValue != showRecent else { return }
            if !showRecent, let chat = selectedChat, !activeRows.contains(where: { $0.chat.id == chat.id }),
               !(showUnassigned && isUnassigned(chat)) {
                selectChat(activeRows.first?.chat)
            } else { reconcileSelection() }
        }
    }
    var showUnassigned = false {
        didSet {
            if oldValue && !showUnassigned, selectedChat?.isKnownSubagent == true { selectChat(activeRows.first?.chat) }
        }
    }
    var dismissPresentation = 0 { didSet { auxiliary.dismiss() } }
    var panelMaxHeight: CGFloat = 720
    var panelVisible = false {
        didSet {
            guard panelVisible != oldValue else { return }
            guard panelVisible else { dismissPresentation += 1; return }
            presentationID += 1
            let preferred = lastActiveSelectionKey
            selectedChatKey = nil; retainedSelection = nil
            showRecent = false
            showUnassigned = false
            let active = activeRows.map(\.chat)
            selectChat(active.first { $0.id == preferred } ?? active.first)
        }
    }
    private(set) var chatOrder: [String: Int] = [:]
    private var nextChatRank = 0
    let configuration: BackendConfiguration
    let readOnly: Bool
    let previewMaxHeight: CGFloat?
    let previewRestore: Bool
    let dataRoot: String
    let snapshotOverride: String?
    let legacyUnset: Bool
    let testProfile: String?
    var onPurgeComplete: (() -> Void)?
    private var lease: ObservationLease?
    private var runtimePath: String
    private var nextCleanup = Date.distantPast
    private var nextResume = Date.distantPast
    private var nextManagement = Date.distantPast
    private var closing = false
    var onUpdate: (() -> Void)?
    var onActivitySelected: ((TimelineStore, [TimelinePhase]) -> Void)?
    @ObservationIgnored private var timelines: [String: TimelineStore] = [:]
    private var timelineFixture: String?
    private var polling: Task<Void, Never>?
    @ObservationIgnored private var snapshotWatching: Task<Void, Never>?
    private var watchedSnapshotPath: String?
    private var snapshotReadGeneration = 0
    private let injectedClient: (any BackendManaging)?
    private let stateSubscriber: (any StateSubscribing)?
    @ObservationIgnored private var stateWatching: Task<Void, Never>?
    @ObservationIgnored private var stateConnection: StateSubscription?
    @ObservationIgnored private var stateSubscriptionKey: String?
    @ObservationIgnored private var stateSubscriptionGeneration = 0
    @ObservationIgnored private var healthClock: Task<Void, Never>?
    private var liveState: StateUpdate?
    var liveInstanceID: String? { liveState?.instanceID }
    var liveSequence: UInt64? { liveState?.sequence }
    private var usesLiveState: Bool { stateSubscriber != nil || !readOnly }

    init(configuration: BackendConfiguration, arguments: [String], client: (any BackendManaging)? = nil,
         stateSubscriber: (any StateSubscribing)? = nil) {
        self.configuration = configuration
        injectedClient = client
        self.stateSubscriber = stateSubscriber
        readOnly = arguments.contains("--read-only") || arguments.contains("--snapshot")
        previewRestore = readOnly && arguments.contains("--preview-restore")
        if readOnly, let index = arguments.firstIndex(of: "--preview-max-height"), index + 1 < arguments.count,
           let height = Double(arguments[index + 1]), height.isFinite, height >= 140 {
            previewMaxHeight = CGFloat(height)
        } else { previewMaxHeight = nil }
        snapshotOverride = arguments.firstIndex(of: "--snapshot").flatMap { i in i + 1 < arguments.count ? arguments[i + 1] : nil }
        timelineFixture = arguments.firstIndex(of: "--timeline-fixture").flatMap { i in i + 1 < arguments.count ? arguments[i + 1] : nil }
        let profile = arguments.firstIndex(of: "--test-profile").flatMap { i in i + 1 < arguments.count ? arguments[i + 1] : nil }
        testProfile = profile
        dataRoot = profile.map { $0 + "/data" } ?? arguments.firstIndex(of: "--data-root").flatMap { i in i + 1 < arguments.count ? arguments[i + 1] : nil } ?? configuration.data_root
        if readOnly, let index = arguments.firstIndex(of: "--lang"), index + 1 < arguments.count {
            language = MonitorLanguage(rawValue: arguments[index + 1]) ?? .chinese
        } else if !readOnly {
            language = Self.savedLanguage(at: dataRoot)
        }
        runtimePath = configuration.runtime
        legacyUnset = arguments.contains("--legacy-unset")
        if readOnly && timelineFixture == nil && FileManager.default.fileExists(atPath: configuration.runtime + "/timeline.json") {
            timelineFixture = configuration.runtime + "/timeline.json"
        }
    }

    static func savedLanguage(at root: String) -> MonitorLanguage {
        struct Preferences: Decodable { let version: Int; let language: MonitorLanguage }
        guard let data = try? Data(contentsOf: URL(fileURLWithPath: root + "/app-state.json")),
              let value = try? JSONDecoder().decode(Preferences.self, from: data),
              value.version == 1 else { return .chinese }
        return value.language
    }

    private var client: any BackendManaging { injectedClient ?? BackendClient(configuration: configuration, dataRoot: dataRoot, testProfile: testProfile) }

    func start() {
        watchState()
        healthClock = Task { [weak self] in
            while !Task.isCancelled {
                if let self, selectionExpired || (usesLiveState && fresh != (readError == nil && liveState?.isFresh == true)) {
                    updatePresentation(); onUpdate?()
                }
                try? await Task.sleep(for: .milliseconds(250))
            }
        }
        polling = Task {
            if !readOnly { await bootstrap() }
            while !Task.isCancelled {
                if !readOnly && Date() >= nextManagement {
                    nextManagement = Date().addingTimeInterval(5)
                    await readManagement()
                }
                if management?.waitingForCleanup == true && !busy && Date() >= nextCleanup {
                    nextCleanup = Date().addingTimeInterval(5)
                    await cleanup()
                }
                guard !Task.isCancelled else { break }
                if !usesLiveState {
                    watchSnapshot()
                    await readSnapshot()
                } else { watchState(); updatePresentation() }
                if !readOnly && management?.configured == true && !fresh && !busy && !closing && Date() >= nextResume {
                    nextResume = Date().addingTimeInterval(5)
                    busy = true
                    do { try await resume() }
                    catch { recordError(error) }
                    busy = false
                }
                onUpdate?()
                try? await Task.sleep(for: .seconds(1))
            }
        }
    }

    private func watchState() {
        guard usesLiveState, !closing else { return }
        let subscriber: any StateSubscribing
        let identity: String
        if let stateSubscriber { subscriber = stateSubscriber; identity = "injected" }
        else {
            guard let lease, lease.version == 2, let secret = lease.secret else { return }
            subscriber = UnixStateSubscriber(options: .init(owner: StateOwnerCredential(sessionID: lease.id, secret: secret)))
            identity = lease.id
        }
        let path = runtimePath + "/state.sock"
        let key = path + "\u{0}" + identity
        guard stateSubscriptionKey != key else { return }
        stateConnection?.cancel()
        stateWatching?.cancel()
        stateSubscriptionGeneration += 1
        let generation = stateSubscriptionGeneration
        stateSubscriptionKey = key
        liveState = nil
        stateWatching = Task { [weak self] in
            var delay = 0.1
            while !Task.isCancelled {
                let connection = subscriber.subscribe(at: path)
                self?.stateConnection = connection
                do {
                    for try await update in connection.updates {
                        guard !Task.isCancelled, let self, !closing, generation == stateSubscriptionGeneration else { break }
                        if liveState?.instanceID != update.instanceID || liveState?.sequence != update.sequence {
                            if let snapshot = update.snapshot { self.snapshot = snapshot; delay = 0.1 }
                        }
                        liveState = update; readError = nil
                        updatePresentation(); onUpdate?()
                    }
                } catch { /* Reconnect below; transport errors never become file health. */ }
                connection.cancel()
                guard !Task.isCancelled, let self, !closing, generation == stateSubscriptionGeneration else { return }
                stateConnection = nil; liveState = nil
                readError = "暂时无法读取采集数据"
                updatePresentation(); onUpdate?()
                do { try await Task.sleep(for: .seconds(delay * Double.random(in: 0.8...1.2))) }
                catch { return }
                delay = min(2, delay*2)
            }
        }
    }

    private func watchSnapshot() {
        let path = snapshotOverride ?? runtimePath + "/snapshot.json"
        guard watchedSnapshotPath != path else { return }
        snapshotWatching?.cancel()
        watchedSnapshotPath = path
        let updates = snapshotUpdates(at: URL(fileURLWithPath: path))
        snapshotWatching = Task { [weak self] in
            for await _ in updates {
                guard !Task.isCancelled, let self else { break }
                await self.readSnapshot()
            }
            if !Task.isCancelled { self?.watchedSnapshotPath = nil }
        }
    }

    private func readSnapshot() async {
        guard !closing else { return }
        snapshotReadGeneration += 1
        let generation = snapshotReadGeneration
        let url = URL(fileURLWithPath: snapshotOverride ?? runtimePath + "/snapshot.json")
        let result = await Task.detached(priority: .utility) { () -> Result<Snapshot, Error> in
            Result { try Snapshot.decode(Data(contentsOf: url)) }
        }.value
        guard generation == snapshotReadGeneration, !closing else { return }
        switch result {
        case .success(let value): snapshot = value; readError = nil
        case .failure: readError = "暂时无法读取采集数据"
        }
        updatePresentation()
        onUpdate?()
    }

    func updatePresentation(at date: Date = Date(), projectionElapsed elapsed: Double? = nil) {
        let elapsed = elapsed ?? projectionElapsed
        if chatSelection?.nextExpiry.map({ elapsed >= $0 }) == true {
            chatSelection = snapshot?.projection.selection(elapsed: elapsed)
        }
        let connectionFresh = usesLiveState ? liveState?.isFresh == true : nil
        fresh = readError == nil && (connectionFresh ?? (snapshot?.isFresh(at: date) == true))
        menuTitle = fresh ? (snapshot?.menuTitle(at: date, connectionFresh: connectionFresh) ?? "—") : "—"
        menuMode = fresh ? (snapshot?.menuMode(at: date, connectionFresh: connectionFresh) ?? .unavailable) : .unavailable
        let chats = snapshot?.threads ?? []
        let ids = Set(chats.map(\.id))
        chatOrder = chatOrder.filter { ids.contains($0.key) }
        for chat in chats where chatOrder[chat.id] == nil {
            chatOrder[chat.id] = nextChatRank
            nextChatRank += 1
        }
        reconcileSelection()
        timelines = timelines.filter { ids.contains($0.key) || $0.key == selectedChatKey }
    }

    var selectedChat: Chat? {
        let chat = selectedChatKey.flatMap { snapshot?.projection.chat(id: $0) } ?? retainedSelection
        return chat?.ephemeral == true ? nil : chat
    }

    func selectChat(_ chat: Chat?) {
        if selectedChatKey != chat?.id { dismissPresentation += 1 }
        selectedChatKey = chat?.id
        retainedSelection = chat
        if let chat, activeRows.contains(where: { $0.chat.id == chat.id }) { lastActiveSelectionKey = chat.id }
    }

    private func reconcileSelection() {
        guard panelVisible else { return }
        if let current = selectedChat {
            retainedSelection = current
            if activeRows.contains(where: { $0.chat.id == current.id }) { lastActiveSelectionKey = current.id }
            return
        }
        selectChat(activeRows.first?.chat ?? (showRecent ? recentRoots.first : nil))
    }

    var visibleChats: [Chat] {
        var rows = activeRows.map(\.chat)
        let recent = showRecent ? recentRoots : []
        let unassigned = unassignedRows.map(\.chat)
        if let selected = selectedChat, !rows.contains(where: { $0.id == selected.id }),
           !recent.contains(where: { $0.id == selected.id }), !unassigned.contains(where: { $0.id == selected.id }),
           !selected.isKnownSubagent || showUnassigned { rows.append(selected) }
        rows += recent + unassigned
        var seen: Set<String> = []
        return rows.filter { seen.insert($0.id).inserted }
    }

    func sourceAvailable(for chat: Chat) -> Bool {
        fresh && chat.connected != false && snapshot?.projection.chat(id: chat.id) != nil
    }

    var activeRows: [ChatRow] {
        guard let snapshot else { return [] }
        let roots = (chatSelection?.roots ?? []).filter { snapshot.hasActiveWork(in: $0) }.sorted {
            (chatOrder[$0.id] ?? Int.max) < (chatOrder[$1.id] ?? Int.max)
        }
        return snapshot.rows(roots: roots, expanded: [], order: chatOrder)
    }

    var recentRoots: [Chat] {
        guard let snapshot else { return [] }
        return (chatSelection?.roots ?? []).filter { !snapshot.hasActiveWork(in: $0) }
    }

    var recentRows: [ChatRow] {
        guard showRecent, let snapshot else { return [] }
        return snapshot.rows(roots: recentRoots, expanded: [], order: chatOrder)
    }

    var unassignedRows: [ChatRow] {
        guard showUnassigned, let snapshot else { return [] }
        return snapshot.rows(roots: chatSelection?.unassigned ?? [], expanded: [], order: chatOrder)
    }

    func isUnassigned(_ chat: Chat) -> Bool { chatSelection?.unassignedIDs.contains(chat.id) == true }

    func timeline(for chat: Chat) -> TimelineStore {
        if let existing = timelines[chat.id] { return existing }
        let client: any TimelineReading = readOnly
            ? FixtureTimelineClient(path: timelineFixture)
            : SystemTimelineClient(configuration: configuration, runtime: runtimePath)
        let value = TimelineStore(scope: chat.scope, thread: chat.threadID, title: chat.name ?? "", client: client)
        value.onOpen = { [weak self] timeline, phase in self?.onActivitySelected?(timeline, phase) }
        timelines[chat.id] = value
        return value
    }

    var connectionLabel: String {
        if !readOnly, let management, !management.configured { return management.localizedLabel(language) }
        if readError != nil { return L(readError!) }
        if !fresh { return L("Stale") }
        if snapshot?.overview?.coverage == "partial" { return L("部分观测") }
        if snapshot?.overview?.coverage == "complete" { return L("已连接") }
        if snapshot?.backends?.values.contains(where: { $0.connected == true }) == true {
            return L("已连接 · 总速度暂不可用")
        }
        return L("NoConnection")
    }

    var bridgeNeedsRestart: Bool {
        management?.bridge_restart_required == true ||
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
        if !readOnly, let management, !management.configured { return management.localizedLabel(language) }
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
        } catch { recordError(error) }
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
                catch { recordError(error, prefix: "登录启动未完成：") }
            }
        } catch { recordError(error) }
    }

    private func resume() async throws {
        guard !closing else { return }
        let result = try await client.command("resume", extra: ["--owner-pid", String(ProcessInfo.processInfo.processIdentifier)])
        management = result
        guard let session = result.session, session.version == 2, session.secret != nil else {
            throw BackendFailure(message: "", displayMessage: DisplayMessage("control.gui_upgrade_required"))
        }
        lease = session
        if let runtime = result.runtime { runtimePath = runtime }
        watchState()
    }

    func restore(purge: Bool) async {
        guard !busy, !readOnly, !closing else { return }
        busy = true
        defer { busy = false }
        do {
            await waitForLanguageSave()
            if let stateConnection { _ = await stateConnection.release() }
            stateConnection?.cancel(); stateWatching?.cancel()
            stateSubscriptionKey = nil; liveState = nil
            lease = nil
            management = try await client.command("restore", extra: purge ? ["--purge"] : [])
            if management?.state == "purged" { finishPurge() }
            operationError = nil
        } catch { recordError(error) }
    }

    private func cleanup() async {
        guard !busy, !readOnly, !closing else { return }
        busy = true
        defer { busy = false }
        do {
            await waitForLanguageSave()
            management = try await client.command("cleanup")
            if management?.state == "purged" { finishPurge() }
        } catch { recordError(error) }
    }

    private func finishPurge() {
        closing = true
        polling?.cancel()
        snapshotWatching?.cancel()
        stateWatching?.cancel()
        healthClock?.cancel()
        lease = nil
        onPurgeComplete?()
    }

    func prepareQuit() async -> Bool {
        guard !busy else { operationError = "管理操作仍在完成，请稍后退出。"; return false }
        let ownedSession = lease != nil
        closing = true
        await waitForLanguageSave()
        if let stateConnection { _ = await stateConnection.release() }
        guard !readOnly, management?.state != "purged" else {
            polling?.cancel()
            snapshotWatching?.cancel()
            stateWatching?.cancel()
            healthClock?.cancel()
            return true
        }
        do {
            if ownedSession { _ = try await client.command("stop", extra: ["--owner-pid", String(ProcessInfo.processInfo.processIdentifier)]) }
            polling?.cancel()
            snapshotWatching?.cancel()
            stateWatching?.cancel()
            healthClock?.cancel()
            lease = nil
            return true
        }
        catch { closing = false; stateSubscriptionKey = nil; watchState(); recordError(error); return false }
    }

    func setLogin(_ enabled: Bool) async {
        guard !busy, !readOnly, !closing else { return }
        busy = true
        defer { busy = false }
        do {
            try await updateLogin(enabled)
        } catch { recordError(error, prefix: "登录启动未完成：") }
    }

    private func updateLogin(_ enabled: Bool) async throws {
        if enabled && SMAppService.mainApp.status != .enabled { try SMAppService.mainApp.register() }
        else if !enabled && SMAppService.mainApp.status != .notRegistered { try await SMAppService.mainApp.unregister() }
        loginEnabled = SMAppService.mainApp.status == .enabled
        loginNeedsApproval = SMAppService.mainApp.status == .requiresApproval
        _ = try await client.command("preferences", extra: ["--launch-at-login", enabled ? "on" : "off"])
    }

    private func readManagement() async {
        // Finite management I/O runs independently of state subscription and its
        // health clock. A failed refresh preserves the last acknowledged config.
        let result = try? await client.command("status")
        if let result { management = result }
    }
}
