import Foundation
import MonitorModel
import Darwin

protocol BackendManaging: Sendable {
    func command(_ action: String, extra: [String]) async throws -> ManagementState
    func heartbeat(_ session: ObservationLease) async
}

extension BackendManaging {
    func command(_ action: String) async throws -> ManagementState { try await command(action, extra: []) }
}

struct BackendClient: BackendManaging {
    let configuration: BackendConfiguration
    let dataRoot: String
    let testProfile: String?

    func command(_ action: String, extra: [String] = []) async throws -> ManagementState {
        let config = configuration
        let root = dataRoot
        let app = Bundle.main.bundleURL.path
        let profile = testProfile
        return try await Task.detached(priority: .userInitiated) {
            let process = Process()
            process.executableURL = URL(fileURLWithPath: config.python)
            process.arguments = ["-m", "codex_progress_monitor.cli", "app", action,
                                 "--data-root", root, "--app", app] + extra + (profile.map { ["--test-profile", $0] } ?? [])
            process.currentDirectoryURL = URL(fileURLWithPath: config.source_root)
            var environment = ProcessInfo.processInfo.environment
            environment["PYTHONPATH"] = config.source_root + "/src"
            process.environment = environment
            let output = Pipe(), diagnostics = Pipe()
            process.standardOutput = output
            process.standardError = diagnostics
            try process.run()
            let data = try output.fileHandleForReading.readToEnd() ?? Data()
            _ = try diagnostics.fileHandleForReading.readToEnd()
            process.waitUntilExit()
            let result = try JSONDecoder().decode(ManagementState.self, from: data)
            if result.ok == false || process.terminationStatus != 0 {
                throw BackendFailure(message: result.error ?? L("管理操作失败，请重试。"))
            }
            return result
        }.value
    }

    func heartbeat(_ session: ObservationLease) async {
        let root = dataRoot
        await Task.detached(priority: .utility) {
            let descriptor = Darwin.open(root + "/management.lock", O_WRONLY | O_CLOEXEC)
            guard descriptor >= 0 else { return }
            defer { Darwin.close(descriptor) }
            guard flock(descriptor, LOCK_EX | LOCK_NB) == 0 else { return }
            defer { flock(descriptor, LOCK_UN) }
            let url = URL(fileURLWithPath: root + "/observation.json")
            guard let data = try? Data(contentsOf: url),
                  let current = try? JSONDecoder().decode(ObservationLease.self, from: data),
                  current.id == session.id, current.active else { return }
            var next = session
            next.heartbeat_at = Date().timeIntervalSince1970
            try? JSONEncoder().encode(next).write(to: url, options: .atomic)
        }.value
    }
}

struct BackendFailure: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}
