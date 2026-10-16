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
    var fresh = false
    var management: ManagementState?
    var operationError: String?
    var busy = false
    var loginEnabled = false
    var loginNeedsApproval = false
    var expanded: Set<String> = []
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
    private var polling: Task<Void, Never>?
    private let injectedClient: (any BackendManaging)?

    init(configuration: BackendConfiguration, arguments: [String], client: (any BackendManaging)? = nil) {
        self.configuration = configuration
        injectedClient = client
        readOnly = arguments.contains("--read-only") || arguments.contains("--snapshot")
        snapshotOverride = arguments.firstIndex(of: "--snapshot").flatMap { i in i + 1 < arguments.count ? arguments[i + 1] : nil }
        let profile = arguments.firstIndex(of: "--test-profile").flatMap { i in i + 1 < arguments.count ? arguments[i + 1] : nil }
        testProfile = profile
        dataRoot = profile.map { $0 + "/data" } ?? arguments.firstIndex(of: "--data-root").flatMap { i in i + 1 < arguments.count ? arguments[i + 1] : nil } ?? configuration.data_root
        runtimePath = configuration.runtime
        legacyUnset = arguments.contains("--legacy-unset")
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
                fresh = readError == nil && snapshot?.isFresh() == true
                menuTitle = fresh ? (snapshot?.menuTitle() ?? "—") : "—"
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
