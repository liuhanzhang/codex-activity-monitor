import Foundation
import Observation

struct UpdateRelease: Equatable, Sendable {
    let version: String
    let build: String
    let pageURL: URL
    static let projectURL = URL(string: "https://github.com/LiuHanzhang/codex-activity-monitor")!

    init?(version: String, build: String, pageURL: URL) {
        guard !version.isEmpty, UInt64(build).map({ $0 > 0 }) == true,
              pageURL.scheme == "https", pageURL.host?.lowercased() == "github.com",
              pageURL.user == nil, pageURL.password == nil, pageURL.port == nil,
              pageURL.path.hasPrefix(Self.projectURL.path + "/releases/tag/") else { return nil }
        self.version = version
        self.build = build
        self.pageURL = pageURL
    }
}

enum UpdateResult {
    case available(UpdateRelease), noUpdate, incompatible, failed, skipped(String), sessionEnded
}

/// A narrow boundary for synthetic tests; Sparkle owns scheduling, comparison and preferences.
@MainActor protocol UpdateServicing: AnyObject {
    var automatic: Bool { get set }
    var canCheck: Bool { get }
    var checking: Bool { get }
    var lastChecked: Date? { get }
    var onChange: (() -> Void)? { get set }
    var onResult: ((UpdateResult) -> Void)? { get set }
    func start() throws
    func check()
    func stop()
}

@MainActor @Observable final class UpdateStore {
    private(set) var automatic = true
    private(set) var checking = false
    private(set) var canCheck = false
    private(set) var available: UpdateRelease?
    private(set) var lastChecked: Date?
    private(set) var statusKey: String?
    let enabled: Bool
    private let root: URL?
    private let defaults: UserDefaults?
    @ObservationIgnored private var service: (any UpdateServicing)?
    private var started = false
    private var manual = false
    private let currentBuild: String?
    private let feedURL: String?
    private static let availableKey = "CAMAvailableUpdate"
    private struct SavedRelease: Codable {
        let version: String
        let build: String
        let pageURL: URL
        let feedURL: String
    }

    init(root: URL? = nil, enabled: Bool = false, service: (any UpdateServicing)? = nil,
         defaults: UserDefaults? = nil,
         currentBuild: String? = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String,
         feedURL: String? = Bundle.main.object(forInfoDictionaryKey: "SUFeedURL") as? String) {
        self.enabled = enabled
        self.root = root
        self.service = enabled ? service : nil
        self.defaults = enabled ? defaults ?? Self.preferenceDomain.flatMap(UserDefaults.init(suiteName:)) : nil
        self.currentBuild = currentBuild
        self.feedURL = feedURL
    }

    static var preferenceDomain: String? { Bundle.main.object(forInfoDictionaryKey: "SUDefaultsDomain") as? String }

    static func removePreferences(bundle: Bundle = .main) {
        guard let domain = bundle.object(forInfoDictionaryKey: "SUDefaultsDomain") as? String,
              domain == (bundle.bundleIdentifier ?? "") + ".updates" else { return }
        UserDefaults(suiteName: domain)?.removePersistentDomain(forName: domain)
    }

    func start() {
        guard enabled, !started else { return }
        restoreAvailableUpdate()
        let service = service ?? SparkleUpdateService()
        self.service = service
        service.onChange = { [weak self] in self?.synchronize() }
        service.onResult = { [weak self] result in self?.receive(result) }
        do {
            // Migrate only an explicit old user choice, before Sparkle schedules a check.
            if defaults?.bool(forKey: "CAMMigratedLegacyPreferences") != true {
                if let url = root?.appendingPathComponent("updates.json"),
                   let data = try? Data(contentsOf: url),
                   let old = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                   let automatic = old["automatic"] as? Bool {
                    service.automatic = automatic
                }
                defaults?.set(true, forKey: "CAMMigratedLegacyPreferences")
            }
            try service.start()
            started = true
            synchronize()
        } catch { statusKey = "更新服务暂时不可用，请重新打开应用后重试。" }
    }

    func stop() {
        service?.onChange = nil; service?.onResult = nil
        service?.stop()
        started = false
        checking = false; canCheck = false
    }

    func setAutomatic(_ value: Bool) {
        guard enabled, started, let service else { return }
        service.automatic = value
        synchronize()
    }

    func check() {
        guard enabled else { return }
        if !started { start() }
        guard started, service?.canCheck == true else { return }
        statusKey = nil
        manual = true
        service?.check()
        synchronize()
    }

    private func synchronize() {
        guard let service else { return }
        automatic = service.automatic
        canCheck = service.canCheck
        checking = service.checking
        lastChecked = service.lastChecked
    }

    private func saveAvailableUpdate() {
        guard let release = available, let feedURL,
              let data = try? JSONEncoder().encode(SavedRelease(version: release.version, build: release.build,
                                                              pageURL: release.pageURL, feedURL: feedURL)) else {
            defaults?.removeObject(forKey: Self.availableKey)
            return
        }
        defaults?.set(data, forKey: Self.availableKey)
    }

    private func restoreAvailableUpdate() {
        guard let data = defaults?.data(forKey: Self.availableKey) else { return }
        guard let saved = try? JSONDecoder().decode(SavedRelease.self, from: data), saved.feedURL == feedURL,
              let current = currentBuild.flatMap(UInt64.init), let candidate = UInt64(saved.build), candidate > current,
              let release = UpdateRelease(version: saved.version, build: saved.build, pageURL: saved.pageURL) else {
            defaults?.removeObject(forKey: Self.availableKey)
            available = nil
            return
        }
        available = release
    }

    private func receive(_ result: UpdateResult) {
        switch result {
        case .available(let release):
            available = release; statusKey = nil; saveAvailableUpdate()
        case .noUpdate:
            available = nil; saveAvailableUpdate(); statusKey = "当前已是最新版本。"; manual = false
        case .incompatible:
            available = nil; saveAvailableUpdate(); statusKey = "当前没有适用于这台 Mac 的更新。"; manual = false
        case .skipped(let build):
            if available?.build == build { available = nil; saveAvailableUpdate() }
        case .sessionEnded:
            // Closing the SDK window or opening its web link does not install
            // the update. Keep the indicator until upgraded or explicitly skipped.
            manual = false
        case .failed:
            if manual { statusKey = "暂时无法检查更新，请检查网络后重试。" }
            manual = false
        }
        synchronize()
    }
}
