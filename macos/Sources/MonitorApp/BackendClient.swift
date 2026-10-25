import Foundation
import MonitorModel

protocol BackendManaging: Sendable {
    func command(_ action: String, extra: [String]) async throws -> ManagementState
}

extension BackendManaging {
    func command(_ action: String) async throws -> ManagementState { try await command(action, extra: []) }
}

struct BackendClient: BackendManaging {
    let configuration: BackendConfiguration
    let dataRoot: String
    let testProfile: String?
    var executor: any ProcessExecuting = ProcessExecutor()

    func command(_ action: String, extra: [String] = []) async throws -> ManagementState {
        do { return try await perform(action, extra: extra, timeout: action == "status" ? 5 : 30) }
        catch {
            guard action != "status" else { throw error }
            // A cancelled CLI may already have changed configuration or started
            // a separately owned collector. Query, never guess or undo it.
            let state = await Task.detached { try? await perform("status", extra: [], timeout: 5) }.value
            if var failure = error as? BackendFailure {
                failure.stateAfterFailure = state
                throw failure
            }
            throw BackendFailure(message: "", presentationKey: error is CancellationError ? "后台命令已取消。" :
                (error as? ProcessExecutionError)?.presentationKey ?? "后台命令返回的数据无效。", stateAfterFailure: state)
        }
    }

    private func perform(_ action: String, extra: [String], timeout: TimeInterval) async throws -> ManagementState {
        var request = ProcessRequest(executable: configuration.python,
            arguments: ["-m", "codex_progress_monitor.cli", "app", action,
                        "--data-root", dataRoot, "--app", Bundle.main.bundleURL.path]
                + extra + (["configure", "resume"].contains(action) ? ["--state-protocol", "1"] : [])
                + (testProfile.map { ["--test-profile", $0] } ?? []))
        request.directory = configuration.source_root
        request.environment["PYTHONPATH"] = configuration.source_root + "/src"
        request.timeout = timeout
        let output = try await executor.execute(request)
        let result = try JSONDecoder().decode(ManagementState.self, from: output.stdout)
        if result.ok == false || output.exitCode != 0 {
            throw BackendFailure(message: result.error ?? "", displayMessage: result.display_message)
        }
        return result
    }

}

struct BackendFailure: LocalizedError {
    let message: String
    var displayMessage: DisplayMessage? = nil
    var presentationKey: String? = nil
    var stateAfterFailure: ManagementState? = nil
    var errorDescription: String? { message }
}
