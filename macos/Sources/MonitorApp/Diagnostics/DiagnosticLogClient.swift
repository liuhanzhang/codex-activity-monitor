import Foundation
import MonitorModel

struct DiagnosticLogBatch: Sendable {
    var events: [DiagnosticEvent] = []
    var limited = false
    var invalidLines = 0
    var unavailable = false
}

/// File metadata changes invalidate a bounded tail. No collector RPC is needed.
actor DiagnosticLogClient {
    private var signature = ""
    private var cached = DiagnosticLogBatch()

    func read(directory: String?, search: String, level: String, source: String, limit: Int) -> DiagnosticLogBatch {
        guard let directory else { return .init() }
        let root = URL(fileURLWithPath: directory)
        let fm = FileManager.default
        guard (try? root.resourceValues(forKeys: [.isSymbolicLinkKey]).isSymbolicLink) != true else {
            return .init(unavailable: true)
        }
        if fm.fileExists(atPath: root.path),
           (!fm.isReadableFile(atPath: root.path) || (try? root.resourceValues(forKeys: [.isDirectoryKey]).isDirectory) != true) {
            return .init(unavailable: true)
        }
        var files: [(URL, Int, Date, String)] = []
        var unavailable = false
        for role in ["gui", "management", "collector", "bridge"] {
            for suffix in ["", ".1", ".2", ".3"] {
                let url = root.appendingPathComponent(role + ".jsonl" + suffix)
                guard fm.fileExists(atPath: url.path) else { continue }
                guard let values = try? url.resourceValues(forKeys: [.isSymbolicLinkKey, .isRegularFileKey, .fileSizeKey,
                                                                    .contentModificationDateKey, .fileResourceIdentifierKey]),
                      values.isSymbolicLink != true, values.isRegularFile == true else { unavailable = true; continue }
                files.append((url, values.fileSize ?? 0, values.contentModificationDate ?? .distantPast,
                              String(describing: values.fileResourceIdentifier)))
            }
        }
        files.sort { $0.2 > $1.2 }
        let next = "\(Int(Date().timeIntervalSince1970/60))|\(search)|\(level)|\(source)|\(limit)|" + files.map { "\($0.0.path):\($0.1):\($0.2.timeIntervalSince1970):\($0.3)" }.joined()
        if next == signature { return cached }
        var result = DiagnosticLogBatch(unavailable: unavailable)
        let decoder = JSONDecoder()
        let iso = ISO8601DateFormatter()
        let fractional = ISO8601DateFormatter(); fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        var seen = Set<String>(), weights: [String: Int] = [:], retained = 0, scanned = 0
        let deadline = ContinuousClock.now.advanced(by: .seconds(1))
        let searching = !search.isEmpty || !level.isEmpty || !source.isEmpty
        let budget = searching ? 64*1024*1024 : min(16*1024*1024, max(2*1024*1024, limit*2048))
        for (url, size, _, _) in files {
            if Task.isCancelled || ContinuousClock.now >= deadline || scanned >= budget { result.limited = true; break }
            do {
                let handle = try FileHandle(forReadingFrom: url)
                defer { try? handle.close() }
                let start = max(0, size - min(budget - scanned, 2*1024*1024))
                try handle.seek(toOffset: UInt64(start))
                if start > 0 { result.limited = true }
                var partial = Data(), skipFirst = start > 0, oversized = false, fileBytes = 0
                while !Task.isCancelled, ContinuousClock.now < deadline, scanned < budget {
                    let data = try handle.read(upToCount: min(65536, budget - scanned)) ?? Data()
                    if data.isEmpty { break }
                    scanned += data.count
                    fileBytes += data.count
                    for byte in data {
                        if byte != 10 {
                            if partial.count < 8192 { partial.append(byte) } else { oversized = true }
                            continue
                        }
                        defer { partial.removeAll(keepingCapacity: true); oversized = false; skipFirst = false }
                        if skipFirst { continue }
                        guard !oversized, var event = try? decoder.decode(DiagnosticEvent.self, from: partial), event.version == 1 else {
                            result.invalidLines += 1; continue
                        }
                        event.timestamp = fractional.date(from: event.at) ?? iso.date(from: event.at)
                        guard let timestamp = event.timestamp else { result.invalidLines += 1; continue }
                        if Date().timeIntervalSince(timestamp) > 7*86400 { result.limited = true; continue }
                        guard (level.isEmpty || event.level == level), (source.isEmpty || event.component == source || event.agent == source),
                              search.isEmpty || event.raw.localizedCaseInsensitiveContains(search) ||
                              event.summary(.chinese).localizedCaseInsensitiveContains(search) ||
                              event.summary(.english).localizedCaseInsensitiveContains(search) else { continue }
                        guard seen.insert(event.id).inserted else { continue }
                        result.events.append(event); retained += partial.count; weights[event.id] = partial.count
                        if result.events.count > limit || retained > 2*1024*1024 {
                            result.events.sort { ($0.timestamp ?? .distantPast) > ($1.timestamp ?? .distantPast) }
                            while result.events.count > limit || retained > 2*1024*1024 {
                                let removed = result.events.removeLast()
                                retained -= weights.removeValue(forKey: removed.id) ?? 0
                                seen.remove(removed.id)
                            }
                            result.limited = true
                        }
                    }
                }
                if !partial.isEmpty { result.limited = true } // append-in-progress or interrupted final record
                if fileBytes < size-start { result.limited = true }
            } catch { result.unavailable = true }
        }
        result.events.sort {
            if $0.timestamp == $1.timestamp { return $0.instance_id == $1.instance_id ? ($0.sequence ?? 0) > ($1.sequence ?? 0) : $0.id > $1.id }
            return ($0.timestamp ?? .distantPast) > ($1.timestamp ?? .distantPast)
        }
        signature = next; cached = result
        return result
    }
}
