import Darwin
import Foundation
import MonitorModel

/// Only this queue touches disk. Enqueue is byte/item bounded on every caller.
final class DiagnosticRecorder: @unchecked Sendable {
    private let directory: URL?
    private let queue = DispatchQueue(label: "monitor.diagnostics", qos: .utility)
    private let lock = NSLock()
    private let instance = UUID().uuidString
    private var sequence = 0
    private var pending = 0
    private var pendingBytes = 0
    private var closed = false
    private var errorCode: Int32?
    private var discarded = 0
    private var lastDrop = -Double.infinity
    private var sessionStarted = false // writer queue only
    private var previousUnclean = false
    private struct Repeated {
        let code: String, level: String, component: String, started: Double, firstAt: String
        let params: [String: MessageValue]
        var count = 0
        var lastAt: String
    }
    private var repeats: [String: Repeated] = [:]
    private var repeatTimer: DispatchSourceTimer?
    private var nextMaintenance = 0.0 // writer queue only
    private var nextRecoveryProbe = 0.0 // writer queue only
    private var rawRuntime: URL? // writer queue only

    init(root: String?) {
        directory = root.map { URL(fileURLWithPath: $0).appendingPathComponent("diagnostics") }
        if root != nil {
            let timer = DispatchSource.makeTimerSource(queue: queue)
            timer.schedule(deadline: .now() + 1, repeating: 1)
            timer.setEventHandler { [weak self] in
                self?.flushRepeats()
                self?.maintainRetention()
                if let self, self.failure != nil, ProcessInfo.processInfo.systemUptime >= self.nextRecoveryProbe {
                    self.nextRecoveryProbe = ProcessInfo.processInfo.systemUptime + 5
                    self.record("logging_ready", component: "gui_logging")
                }
            }
            repeatTimer = timer; timer.resume()
        }
    }
    deinit { repeatTimer?.cancel() }

    var failure: Int32? { lock.withLock { errorCode } }
    var dropped: Int { lock.withLock { discarded } }
    var recentlyDropped: Bool { lock.withLock { ProcessInfo.processInfo.systemUptime - lastDrop < 5 } }
    var previousExitUnclean: Bool { lock.withLock { previousUnclean } }

    func record(_ code: String, level: String = "info", component: String = "gui", error: (any Error)? = nil) {
        guard directory != nil else { return }
        var params: [String: MessageValue] = [:]
        if let error {
            params["type"] = .string(String(describing: type(of: error)).prefix(128).description)
            if let key = (error as? BackendFailure)?.displayMessage?.key { params["reason"] = .string(key) }
            if (error as NSError).domain == NSPOSIXErrorDomain { params["errno"] = .number(Double((error as NSError).code)) }
        }
        let now = ProcessInfo.processInfo.systemUptime
        let at = ISO8601DateFormatter().string(from: Date())
        let encoder = JSONEncoder(); encoder.outputFormatting = .sortedKeys
        let signature = component + code + level + ((try? encoder.encode(params)).flatMap { String(data: $0, encoding: .utf8) } ?? "")
        let accepted = lock.withLock {
            guard !closed else { return false }
            if var value = repeats[signature], now - value.started < 5 {
                value.count += 1; value.lastAt = at; repeats[signature] = value; return false
            }
            return true
        }
        guard accepted else { return }
        flushRepeats()
        lock.withLock {
            if repeats.count >= 64, let key = repeats.keys.first {
                discarded += repeats.removeValue(forKey: key)?.count ?? 0
            }
            repeats[signature] = Repeated(code: code, level: level, component: component,
                                          started: now, firstAt: at, params: params, lastAt: at)
        }
        enqueue(code, level: level, component: component, params: params)
    }

    private func flushRepeats(force: Bool = false) {
        let now = ProcessInfo.processInfo.systemUptime
        let values = lock.withLock {
            let keys = repeats.keys.filter { force || now - repeats[$0]!.started >= 5 }
            return keys.compactMap { repeats.removeValue(forKey: $0) }
        }
        for value in values where value.count > 0 {
            enqueue(value.code, level: value.level, component: value.component, params: value.params,
                    count: value.count, firstAt: value.firstAt, at: value.lastAt)
        }
    }

    private func enqueue(_ code: String, level: String, component: String, params: [String: MessageValue],
                         count: Int = 1, firstAt: String? = nil, at: String? = nil) {
        let sequence = lock.withLock { self.sequence += 1; return self.sequence }
        let event = DiagnosticEvent(instance: instance, sequence: sequence, code: code, level: level,
                                    component: component, params: params, count: count, firstAt: firstAt, at: at)
        guard var frame = try? JSONEncoder().encode(event), frame.count < 8192 else { return }
        frame.append(10)
        let accepted = lock.withLock {
            guard !closed else { return false }
            guard pending < 256, pendingBytes + frame.count <= 256*1024 else {
                discarded += 1; lastDrop = ProcessInfo.processInfo.systemUptime; return false
            }
            pending += 1; pendingBytes += frame.count; return true
        }
        guard accepted else { return }
        let data = frame
        queue.async { [self] in
            defer { lock.withLock { pending -= 1; pendingBytes -= data.count } }
            let deadline = ProcessInfo.processInfo.systemUptime + 0.25
            while true {
                do { try append(data); lock.withLock { errorCode = nil }; return }
                catch let error as POSIXError where error.code == .EAGAIN && ProcessInfo.processInfo.systemUptime < deadline {
                    usleep(10_000)
                } catch {
                    lock.withLock {
                        errorCode = (error as NSError).code == NSFileNoSuchFileError ? ENOENT : EIO
                        discarded += 1; lastDrop = ProcessInfo.processInfo.systemUptime
                    }
                    return
                }
            }
        }
    }

    private func append(_ frame: Data) throws {
        guard let directory else { return }
        let fm = FileManager.default
        let owner = directory.deletingLastPathComponent().appendingPathComponent(".monitor-owned")
        guard try String(contentsOf: owner, encoding: .utf8) == "codex-activity-monitor:1\n",
              !isLink(directory) else { throw CocoaError(.fileReadNoPermission) }
        if !fm.fileExists(atPath: directory.path) {
            do { try fm.createDirectory(at: directory, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700]) }
            catch { if !fm.fileExists(atPath: directory.path) { throw error } }
        }
        let marker = directory.appendingPathComponent(".monitor-owned")
        if !fm.fileExists(atPath: marker.path) {
            let fd = Darwin.open(marker.path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0o600)
            if fd >= 0 {
                let bytes = Array("codex-activity-monitor-diagnostics:1\n".utf8)
                _ = bytes.withUnsafeBytes { Darwin.write(fd, $0.baseAddress, $0.count) }; Darwin.close(fd)
            } else if errno != EEXIST { throw POSIXError(.EIO) }
        }
        guard !isLink(marker), try String(contentsOf: marker, encoding: .utf8) == "codex-activity-monitor-diagnostics:1\n" else {
            throw CocoaError(.fileReadNoPermission)
        }
        let lockFD = Darwin.open(directory.appendingPathComponent("gui.lock").path, O_RDWR | O_CREAT | O_NOFOLLOW, 0o600)
        guard lockFD >= 0 else { throw POSIXError(.EIO) }
        defer { Darwin.close(lockFD) }
        guard flock(lockFD, LOCK_EX | LOCK_NB) == 0 else { throw POSIXError(.EAGAIN) }
        let path = directory.appendingPathComponent("gui.jsonl")
        guard !isLink(path) else { throw CocoaError(.fileReadNoPermission) }
        let attributes = try? fm.attributesOfItem(atPath: path.path)
        let expired = (attributes?[.creationDate] as? Date).map { Date().timeIntervalSince($0) > 7*86400 } ?? false
        if (attributes?[.size] as? Int ?? 0) + frame.count > 2*1024*1024 || expired {
            for i in stride(from: 3, through: 1, by: -1) {
                let source = i == 1 ? path : directory.appendingPathComponent("gui.jsonl.\(i-1)")
                let destination = directory.appendingPathComponent("gui.jsonl.\(i)")
                guard !isLink(source), !isLink(destination) else { throw CocoaError(.fileReadNoPermission) }
                if fm.fileExists(atPath: source.path) {
                    if fm.fileExists(atPath: destination.path) { try fm.removeItem(at: destination) }
                    try fm.moveItem(at: source, to: destination)
                }
            }
        }
        for i in 1...3 {
            let archive = directory.appendingPathComponent("gui.jsonl.\(i)")
            if let values = try? archive.resourceValues(forKeys: [.contentModificationDateKey]),
               let date = values.contentModificationDate, Date().timeIntervalSince(date) > 7*86400, !isLink(archive) {
                try fm.removeItem(at: archive)
            }
        }
        let fd = Darwin.open(path.path, O_APPEND | O_WRONLY | O_CREAT | O_NOFOLLOW, 0o600)
        guard fd >= 0 else { throw POSIXError(.EIO) }
        defer { Darwin.close(fd) }
        let written = frame.withUnsafeBytes { Darwin.write(fd, $0.baseAddress, $0.count) }
        guard written == frame.count else { throw POSIXError(.EIO) }
    }

    private func isLink(_ url: URL) -> Bool { (try? url.resourceValues(forKeys: [.isSymbolicLinkKey]).isSymbolicLink) == true }

    func setRuntime(_ path: String) { queue.async { [self] in rawRuntime = URL(fileURLWithPath: path) } }

    private func maintainRetention() {
        guard !lock.withLock({ closed }), ProcessInfo.processInfo.systemUptime >= nextMaintenance,
              let directory else { return }
        nextMaintenance = ProcessInfo.processInfo.systemUptime + 60
        let fm = FileManager.default
        guard !isLink(directory),
              (try? String(contentsOf: directory.appendingPathComponent(".monitor-owned"), encoding: .utf8)) == "codex-activity-monitor-diagnostics:1\n" else { return }
        func prune(_ root: URL, lockName: String, names: [String]) {
            let fd = Darwin.open(root.appendingPathComponent(lockName).path, O_CREAT | O_RDWR | O_NOFOLLOW, 0o600)
            guard fd >= 0 else { return }
            defer { Darwin.close(fd) }
            guard flock(fd, LOCK_EX | LOCK_NB) == 0 else { return }
            for name in names {
                let path = root.appendingPathComponent(name)
                if !isLink(path), let date = (try? path.resourceValues(forKeys: [.creationDateKey]))?.creationDate,
                   Date().timeIntervalSince(date) > 7*86400 { try? fm.removeItem(at: path) }
            }
        }
        for role in ["gui", "management", "collector", "bridge"] {
            prune(directory, lockName: role + ".lock", names: ["", ".1", ".2", ".3"].map { role + ".jsonl" + $0 })
        }
        if let runtime = rawRuntime, !isLink(runtime),
           let data = try? Data(contentsOf: runtime.appendingPathComponent("app-managed.json")), data.count < 65536,
           let marker = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           marker["session_file"] as? String == directory.deletingLastPathComponent().appendingPathComponent("observation.json").path {
            prune(runtime, lockName: "collector-output.lock", names: ["collector.log", "collector.previous.log"])
        }
    }

    func startSession() {
        guard let directory else { return }
        queue.async { [self] in
            guard !sessionStarted else { return }
            let parent = directory.deletingLastPathComponent()
            let marker = parent.appendingPathComponent("gui-session.json")
            guard !isLink(marker),
                  (try? String(contentsOf: parent.appendingPathComponent(".monitor-owned"), encoding: .utf8)) == "codex-activity-monitor:1\n" else { return }
            if let data = try? Data(contentsOf: marker), data.count < 4096,
               let old = try? JSONSerialization.jsonObject(with: data) as? [String: Any], old["active"] as? Bool == true,
               let pid = old["pid"] as? Int32, pid > 1 {
                if Darwin.kill(pid, 0) == 0 || errno == EPERM { return }
                if errno == ESRCH {
                    lock.withLock { previousUnclean = true }
                    record("previous_exit_unclean", level: "warning")
                }
            }
            do {
                let record: [String: Any] = ["version": 1, "instance_id": instance, "pid": ProcessInfo.processInfo.processIdentifier, "active": true]
                try JSONSerialization.data(withJSONObject: record).write(to: marker, options: .atomic)
                sessionStarted = true
                self.record("gui_started")
            } catch { lock.withLock { errorCode = EIO } }
        }
    }

    func finish() async {
        repeatTimer?.cancel()
        flushRepeats(force: true)
        record("gui_stopped")
        lock.withLock { closed = true }
        let group = DispatchGroup(); group.enter()
        queue.async { [self] in
            defer { group.leave() }
            guard sessionStarted, let directory else { return }
            let marker = directory.deletingLastPathComponent().appendingPathComponent("gui-session.json")
            guard !isLink(marker), let data = try? Data(contentsOf: marker),
                  var value = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  value["instance_id"] as? String == instance else { return }
            value["active"] = false
            try? JSONSerialization.data(withJSONObject: value).write(to: marker, options: .atomic)
        }
        _ = await Task.detached { self.waitForQueue(group) }.value
    }
    private func waitForQueue(_ group: DispatchGroup) -> Bool { group.wait(timeout: .now() + .milliseconds(500)) == .success }
}
