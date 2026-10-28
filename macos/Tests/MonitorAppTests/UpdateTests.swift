import Foundation
import Sparkle
import Testing
@testable import MonitorApp

@MainActor private final class UpdateFixture: UpdateServicing {
    var automatic = true
    var canCheck = true
    var checking = false
    var lastChecked: Date?
    var onChange: (() -> Void)?
    var onResult: ((UpdateResult) -> Void)?
    var starts = 0, checks = 0, stops = 0
    var failStart = false
    func start() throws {
        starts += 1
        if failStart { throw CocoaError(.fileReadCorruptFile) }
    }
    func check() { checks += 1; checking = true; canCheck = false; onChange?() }
    func stop() { stops += 1 }
    func complete(_ result: UpdateResult) {
        lastChecked = Date()
        onResult?(result)
        checking = false; canCheck = true; onChange?()
    }
}

private func sampleRelease() throws -> UpdateRelease {
    try #require(UpdateRelease(version: "0.1.0-alpha.3", pageURL: URL(string:
        "https://github.com/LiuHanzhang/codex-activity-monitor/releases/tag/v0.1.0-alpha.3")!))
}

@Test @MainActor func updateStoreDelegatesChecksAndShowsBackgroundAvailability() throws {
    let service = UpdateFixture(), store = UpdateStore(enabled: true, service: service)
    store.start(); store.start()
    #expect(service.starts == 1)
    service.complete(.available(try sampleRelease()))
    #expect(store.available?.version == "0.1.0-alpha.3")
    #expect(store.lastChecked != nil && store.canCheck)
    store.check(); store.check()
    #expect(service.checks == 1 && store.checking && !store.canCheck)
    service.complete(.noUpdate)
    #expect(store.available == nil && store.statusKey == "当前已是最新版本。")
    #expect(!store.checking && store.canCheck)
    service.complete(.available(try sampleRelease()))
    service.complete(.dismissed)
    #expect(store.available == nil && store.canCheck)
}

@Test @MainActor func manualChecksRemainAvailableWhenAutomaticChecksAreDisabled() {
    let service = UpdateFixture(), store = UpdateStore(enabled: true, service: service)
    store.start(); store.setAutomatic(false)
    #expect(!service.automatic && !store.automatic)
    store.check()
    #expect(service.checks == 1)
    service.complete(.failed)
    #expect(store.statusKey == "暂时无法检查更新，请检查网络后重试。")
}

@Test @MainActor func backgroundFailureIsQuietAndIncompatibilityDoesNotClaimLatest() {
    let service = UpdateFixture(), store = UpdateStore(enabled: true, service: service)
    store.start()
    service.complete(.failed)
    #expect(store.statusKey == nil)
    service.complete(.incompatible)
    #expect(store.statusKey == "当前没有适用于这台 Mac 的更新。")
}

@Test @MainActor func previewDoesNotStartServiceOrReadDailyPreferences() {
    let service = UpdateFixture(), store = UpdateStore(root: URL(fileURLWithPath: "/unused"), enabled: false, service: service)
    store.start(); store.check(); store.setAutomatic(false); store.stop()
    #expect(service.starts == 0 && service.checks == 0 && service.stops == 0)
    #expect(!store.canCheck && store.available == nil)
}

@Test @MainActor func stoppingAbortsServiceWithoutDisablingFutureAutomaticChecks() throws {
    let service = UpdateFixture(), store = UpdateStore(enabled: true, service: service)
    store.start(); store.check(); store.stop()
    #expect(service.stops == 1 && service.automatic)
    service.complete(.available(try sampleRelease()))
    #expect(store.available == nil && !store.canCheck && !store.checking)
    store.start()
    #expect(service.starts == 2 && store.automatic)
}

@Test @MainActor func failedStartupDoesNotPreventLaterRetry() {
    let service = UpdateFixture(), store = UpdateStore(enabled: true, service: service)
    service.failStart = true
    store.start()
    #expect(store.statusKey == "更新服务暂时不可用，请重新打开应用后重试。")
    service.failStart = false
    store.check()
    #expect(service.checks == 1 && store.checking)
}

@Test @MainActor func legacyAutomaticChoiceMigratesOnlyOnceAndDoesNotCopyOldTiming() throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: root) }
    let domain = "com.liuhanzhang.codexactivitymonitor.test." + UUID().uuidString
    let defaults = try #require(UserDefaults(suiteName: domain))
    defer { defaults.removePersistentDomain(forName: domain) }
    try Data("{\"automatic\":false,\"lastAttempt\":1,\"availableTag\":\"v99.0.0\"}".utf8)
        .write(to: root.appendingPathComponent("updates.json"))
    let service = UpdateFixture()
    let first = UpdateStore(root: root, enabled: true, service: service, defaults: defaults)
    first.start()
    #expect(!service.automatic && first.available == nil && first.lastChecked == nil)
    first.setAutomatic(true); first.stop()
    let second = UpdateStore(root: root, enabled: true, service: service, defaults: defaults)
    second.start()
    #expect(service.automatic)
}

@Test func updateLinksCannotOpenOtherSitesOrRepositoryPaths() throws {
    #expect(try sampleRelease().pageURL.host == "github.com")
    for address in ["http://github.com/LiuHanzhang/codex-activity-monitor/releases/tag/v1.0.0",
                    "https://evil.example/releases/tag/v1.0.0",
                    "https://github.com/other/repo/releases/tag/v1.0.0",
                    "https://github.com/LiuHanzhang/codex-activity-monitor/issues/1",
                    "https://user@github.com/LiuHanzhang/codex-activity-monitor/releases/tag/v1.0.0"] {
        #expect(UpdateRelease(version: "1.0.0", pageURL: URL(string: address)!) == nil)
    }
}

@Test func sparkleCurrentDisplayVersionKeepsThePrereleaseIdentifier() {
    let display: any SUVersionDisplay = MonitorUpdateVersionDisplay(currentVersion: "0.1.0-alpha.2")
    #expect(display.formatBundleDisplayVersion?("0.1.0", withBundleVersion: "2", matchingUpdate: nil) == "0.1.0-alpha.2")
    let fallback: any SUVersionDisplay = MonitorUpdateVersionDisplay(currentVersion: nil)
    #expect(fallback.formatBundleDisplayVersion?("1.2.3", withBundleVersion: "42", matchingUpdate: nil) == "1.2.3")
}

@Test @MainActor func onlyDisposableBundleCanEnableLocalUpdateAcceptance() {
    let arguments = ["--read-only", "--update-acceptance"]
    let identifier = "com.liuhanzhang.codexactivitymonitor.updatetest.fixture"
    let feed = URL(string: "http://127.0.0.1:12345/feed.xml")!
    #expect(DashboardStore.acceptsUpdateFixture(arguments: arguments, bundleID: identifier, distribution: "update-test", feedURL: feed))
    for id in ["com.liuhanzhang.codexactivitymonitor", "com.liuhanzhang.codexactivitymonitor.preview"] {
        #expect(!DashboardStore.acceptsUpdateFixture(arguments: arguments, bundleID: id, distribution: "update-test", feedURL: feed))
    }
    #expect(!DashboardStore.acceptsUpdateFixture(arguments: arguments, bundleID: identifier, distribution: "preview", feedURL: feed))
    #expect(!DashboardStore.acceptsUpdateFixture(arguments: arguments, bundleID: identifier, distribution: "update-test", feedURL: URL(string: "https://example.com/feed")))
    #expect(!DashboardStore.acceptsUpdateFixture(arguments: ["--update-acceptance"], bundleID: identifier, distribution: "update-test", feedURL: feed))
    let published = URL(string: "https://liuhanzhang.github.io/codex-activity-monitor/updates/appcast-prerelease.xml")!
    #expect(DashboardStore.acceptsUpdateFixture(arguments: arguments, bundleID: identifier, distribution: "update-test", feedURL: published))
    for address in ["https://liuhanzhang.github.io/other/appcast.xml", "https://liuhanzhang.github.io/codex-activity-monitor/updates/other.xml",
                    "https://liuhanzhang.github.io/codex-activity-monitor/updates/appcast.xml?token=fixture"] {
        #expect(!DashboardStore.acceptsUpdateFixture(arguments: arguments, bundleID: identifier, distribution: "update-test", feedURL: URL(string: address)))
    }
    #expect(!DashboardStore.acceptsUpdateFixture(arguments: arguments, bundleID: "com.liuhanzhang.codexactivitymonitor", distribution: "update-test", feedURL: published))
}

@Test @MainActor func uninstallRemovesOnlyTheBundlesOwnUpdatePreferences() throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    let contents = root.appendingPathComponent("Test.bundle/Contents")
    try FileManager.default.createDirectory(at: contents, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: root) }
    let identifier = "com.liuhanzhang.codexactivitymonitor.test." + UUID().uuidString
    let domain = identifier + ".updates"
    let foreign = identifier + ".foreign"
    let defaults = try #require(UserDefaults(suiteName: domain))
    let other = try #require(UserDefaults(suiteName: foreign))
    defer { defaults.removePersistentDomain(forName: domain); other.removePersistentDomain(forName: foreign) }
    defaults.set(false, forKey: "SUEnableAutomaticChecks")
    other.set("keep", forKey: "fixture")
    let info: [String: Any] = ["CFBundleIdentifier": identifier, "CFBundlePackageType": "BNDL", "SUDefaultsDomain": domain]
    try PropertyListSerialization.data(fromPropertyList: info, format: .xml, options: 0)
        .write(to: contents.appendingPathComponent("Info.plist"))
    let bundle = try #require(Bundle(url: contents.deletingLastPathComponent()))
    UpdateStore.removePreferences(bundle: bundle)
    #expect(defaults.object(forKey: "SUEnableAutomaticChecks") == nil)
    #expect(other.string(forKey: "fixture") == "keep")
}
