import Foundation
import Sparkle

@MainActor final class SparkleUpdateService: NSObject, UpdateServicing, SPUUpdaterDelegate, SPUStandardUserDriverDelegate {
    var onChange: (() -> Void)?
    var onResult: ((UpdateResult) -> Void)?
    private var controller: SPUStandardUpdaterController?
    private var observations: [NSKeyValueObservation] = []
    private var stopped = false
    private nonisolated let versionDisplay = MonitorUpdateVersionDisplay(
        currentVersion: Bundle.main.object(forInfoDictionaryKey: "CAMReleaseVersion") as? String)

    override init() { super.init(); createController() }

    private func createController() {
        controller = SPUStandardUpdaterController(startingUpdater: false, updaterDelegate: self, userDriverDelegate: self)
        guard let updater = controller?.updater else { return }
        observations = [
            updater.observe(\.canCheckForUpdates) { [weak self] _, _ in MainActor.assumeIsolated { self?.onChange?() } },
            updater.observe(\.automaticallyChecksForUpdates) { [weak self] _, _ in MainActor.assumeIsolated { self?.onChange?() } },
            updater.observe(\.lastUpdateCheckDate) { [weak self] _, _ in MainActor.assumeIsolated { self?.onChange?() } }
        ]
    }

    var automatic: Bool {
        get { controller?.updater.automaticallyChecksForUpdates ?? true }
        set { controller?.updater.automaticallyChecksForUpdates = newValue }
    }
    var canCheck: Bool { !stopped && controller?.updater.canCheckForUpdates == true }
    var checking: Bool { !stopped && controller?.updater.sessionInProgress == true && !canCheck }
    var lastChecked: Date? { controller?.updater.lastUpdateCheckDate }

    func start() throws {
        stopped = false
        if controller == nil { createController() }
        // Capture configuration failures without opening a modal alert at launch.
        try controller?.updater.start()
        onChange?()
    }

    func check() { controller?.checkForUpdates(nil) }

    func stop() {
        stopped = true
        observations.removeAll()
        // SPUUpdater.dealloc cancels timers and aborts its current driver. Release
        // it instead of persisting automatic-checks = false on ordinary quit.
        controller = nil
    }

    func updater(_ updater: SPUUpdater, mayPerform updateCheck: SPUUpdateCheck) throws {
        if stopped { throw CancellationError() }
    }

    func updater(_ updater: SPUUpdater, shouldProceedWithUpdate updateItem: SUAppcastItem, updateCheck: SPUUpdateCheck) throws {
        // This release supports web downloads only, including if a future feed
        // accidentally contains an installable enclosure.
        guard updateItem.isInformationOnlyUpdate, release(updateItem) != nil else {
            throw NSError(domain: "CodexActivityMonitor.Updates", code: 1,
                          userInfo: [NSLocalizedDescriptionKey: "Unsupported update feed item."])
        }
    }

    func updater(_ updater: SPUUpdater, didFindValidUpdate item: SUAppcastItem) {
        guard !stopped, item.isInformationOnlyUpdate, let release = release(item) else { return }
        onResult?(.available(release))
    }

    func updaterDidNotFindUpdate(_ updater: SPUUpdater, error: Error) {
        guard !stopped else { return }
        let reason = ((error as NSError).userInfo[SPUNoUpdateFoundReasonKey] as? NSNumber)?.intValue ?? -1
        let latest = reason == Int(SPUNoUpdateFoundReason.onLatestVersion.rawValue)
            || reason == Int(SPUNoUpdateFoundReason.onNewerThanLatestVersion.rawValue)
        onResult?(latest ? .noUpdate : .incompatible)
    }

    func updater(_ updater: SPUUpdater, didAbortWithError error: Error) {
        guard !stopped, (error as NSError).code != SUError.noUpdateError.rawValue else { return }
        onResult?(.failed)
    }

    func updater(_ updater: SPUUpdater, userDidMake choice: SPUUserUpdateChoice, forUpdate item: SUAppcastItem,
                 state: SPUUserUpdateState) {
        guard !stopped else { return }
        if choice == .skip { onResult?(.skipped(item.versionString)) }
    }

    nonisolated var supportsGentleScheduledUpdateReminders: Bool { true }

    nonisolated func standardUserDriverRequestsVersionDisplayer() -> (any SUVersionDisplay)? { versionDisplay }

    nonisolated func standardUserDriverShouldHandleShowingScheduledUpdate(_ update: SUAppcastItem, andInImmediateFocus immediateFocus: Bool) -> Bool {
        false
    }

    nonisolated func standardUserDriverWillHandleShowingUpdate(_ handleShowingUpdate: Bool, forUpdate update: SUAppcastItem, state: SPUUserUpdateState) {
        // Sparkle's standard AppKit user driver calls its delegate on main.
        MainActor.assumeIsolated {
            guard !stopped, let release = release(update) else { return }
            onResult?(.available(release))
        }
    }

    nonisolated func standardUserDriverWillFinishUpdateSession() {
        MainActor.assumeIsolated {
            guard !stopped else { return }
            onResult?(.sessionEnded)
        }
    }

    private func release(_ item: SUAppcastItem) -> UpdateRelease? {
        guard let url = item.infoURL else { return nil }
        return UpdateRelease(version: item.displayVersionString, build: item.versionString, pageURL: url)
    }
}

/// Only presentation is customized; Sparkle continues comparing CFBundleVersion.
final class MonitorUpdateVersionDisplay: NSObject, SUVersionDisplay, @unchecked Sendable {
    private let currentVersion: String?
    init(currentVersion: String?) { self.currentVersion = currentVersion; super.init() }

    func formatUpdateVersion(fromUpdate update: SUAppcastItem,
                                    andBundleDisplayVersion bundleDisplayVersion: AutoreleasingUnsafeMutablePointer<NSString>,
                                    withBundleVersion bundleVersion: String) -> String {
        if let currentVersion { bundleDisplayVersion.pointee = currentVersion as NSString }
        return update.displayVersionString
    }

    func formatBundleDisplayVersion(_ bundleDisplayVersion: String, withBundleVersion bundleVersion: String,
                                    matchingUpdate: SUAppcastItem?) -> String {
        currentVersion ?? bundleDisplayVersion
    }
}
