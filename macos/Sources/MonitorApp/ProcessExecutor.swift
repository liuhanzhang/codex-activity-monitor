import Darwin
import Foundation

struct ProcessRequest: Sendable {
    let executable: String
    var arguments: [String] = []
    var environment = ProcessInfo.processInfo.environment
    var directory: String?
    var timeout: TimeInterval = 30
    var terminationGrace: TimeInterval = 0.5
    var drainGrace: TimeInterval = 0.5
    var stdoutLimit = 1_048_576
    var stderrLimit = 262_144
}

struct ProcessResult: Sendable {
    let exitCode: Int32
    let stdout: Data
    let stderr: Data
    let stderrTruncated: Bool
}

enum ProcessExecutionError: Error, Sendable, Equatable {
    case invalidRequest
    case launchFailed(Int32)
    case ioFailed(Int32)
    case timedOut
    case outputLimit
    case terminationFailed
}

protocol ProcessExecuting: Sendable {
    func execute(_ request: ProcessRequest) async throws -> ProcessResult
}

struct ProcessExecutor: ProcessExecuting {
    func execute(_ request: ProcessRequest) async throws -> ProcessResult {
        let operation = ProcessOperation(request)
        let result = try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { continuation in
                DispatchQueue.global(qos: .utility).async {
                    do { continuation.resume(returning: try operation.run()) }
                    catch { continuation.resume(throwing: error) }
                }
            }
        } onCancel: { operation.cancel() }
        try Task.checkCancellation()
        return result
    }
}

/// The worker exclusively owns the child and FDs. Only the cancellation flag
/// crosses threads, under this lock; no signal handler or timer owns a PID.
private final class ProcessOperation: @unchecked Sendable {
    private let request: ProcessRequest
    private let deadline: TimeInterval
    private let lock = NSLock()
    private var cancelled = false

    init(_ request: ProcessRequest) {
        self.request = request
        deadline = ProcessInfo.processInfo.systemUptime + request.timeout
    }

    func cancel() { lock.withLock { cancelled = true } }
    private var isCancelled: Bool { lock.withLock { cancelled } }

    func run() throws -> ProcessResult {
        try validate()
        if isCancelled { throw CancellationError() }
        if ProcessInfo.processInfo.systemUptime >= deadline { throw ProcessExecutionError.timedOut }
        let output = try makePipe()
        var outputWriteOpen = true
        defer { close(output.0); if outputWriteOpen { close(output.1) } }
        let diagnostics = try makePipe()
        var diagnosticsWriteOpen = true
        defer { close(diagnostics.0); if diagnosticsWriteOpen { close(diagnostics.1) } }
        let pid = try spawn(stdout: output.1, stderr: diagnostics.1)
        close(output.1); outputWriteOpen = false
        close(diagnostics.1); diagnosticsWriteOpen = false
        return try collect(pid: pid, output: output, diagnostics: diagnostics)
    }

    private func validate() throws {
        let strings = [request.executable] + request.arguments + request.environment.flatMap { [$0.key, $0.value] }
        guard request.executable.hasPrefix("/"), !strings.contains(where: { $0.utf8.contains(0) }),
              !request.environment.keys.contains(where: { $0.contains("=") }),
              request.directory.map({ $0.hasPrefix("/") && !$0.utf8.contains(0) }) ?? true,
              request.timeout.isFinite, request.timeout > 0,
              request.terminationGrace.isFinite, request.terminationGrace >= 0,
              request.drainGrace.isFinite, request.drainGrace >= 0,
              request.stdoutLimit >= 0, request.stderrLimit >= 0 else {
            throw ProcessExecutionError.invalidRequest
        }
    }

    private func makePipe() throws -> (Int32, Int32) {
        var fds = [Int32](repeating: -1, count: 2)
        guard pipe(&fds) == 0 else { throw ProcessExecutionError.ioFailed(errno) }
        for index in fds.indices {
            if fds[index] < 3 {
                let replacement = fcntl(fds[index], F_DUPFD_CLOEXEC, 3)
                guard replacement >= 0 else {
                    let code = errno; fds.forEach { close($0) }; throw ProcessExecutionError.ioFailed(code)
                }
                close(fds[index]); fds[index] = replacement
            }
            guard fcntl(fds[index], F_SETFD, FD_CLOEXEC) == 0 else {
                let code = errno; fds.forEach { close($0) }; throw ProcessExecutionError.ioFailed(code)
            }
        }
        let flags = fcntl(fds[0], F_GETFL)
        guard flags >= 0, fcntl(fds[0], F_SETFL, flags | O_NONBLOCK) == 0 else {
            let code = errno; fds.forEach { close($0) }; throw ProcessExecutionError.ioFailed(code)
        }
        return (fds[0], fds[1])
    }

    private func spawn(stdout: Int32, stderr: Int32) throws -> pid_t {
        var actions: posix_spawn_file_actions_t?
        var attributes: posix_spawnattr_t?
        try check(posix_spawn_file_actions_init(&actions))
        defer { posix_spawn_file_actions_destroy(&actions) }
        try check(posix_spawnattr_init(&attributes))
        defer { posix_spawnattr_destroy(&attributes) }
        try check(posix_spawn_file_actions_addopen(&actions, STDIN_FILENO, "/dev/null", O_RDONLY, 0))
        try check(posix_spawn_file_actions_adddup2(&actions, stdout, STDOUT_FILENO))
        try check(posix_spawn_file_actions_adddup2(&actions, stderr, STDERR_FILENO))
        if let directory = request.directory {
            try check(posix_spawn_file_actions_addchdir(&actions, directory))
        }
        // Creation, not a racing setpgid() after exec, proves group ownership.
        let flags = POSIX_SPAWN_SETPGROUP | POSIX_SPAWN_CLOEXEC_DEFAULT | POSIX_SPAWN_SETSIGMASK | POSIX_SPAWN_SETSIGDEF
        try check(posix_spawnattr_setflags(&attributes, Int16(flags)))
        try check(posix_spawnattr_setpgroup(&attributes, 0))
        var mask = sigset_t(); sigemptyset(&mask)
        try check(posix_spawnattr_setsigmask(&attributes, &mask))
        var defaults = sigset_t(); sigemptyset(&defaults)
        for signal in [SIGTERM, SIGINT, SIGPIPE] { sigaddset(&defaults, signal) }
        try check(posix_spawnattr_setsigdefault(&attributes, &defaults))
        var argv = ([request.executable] + request.arguments).map { strdup($0) } + [nil]
        defer { argv.forEach { free($0) } }
        var env = request.environment.sorted { $0.key < $1.key }.map { strdup($0.key + "=" + $0.value) } + [nil]
        defer { env.forEach { free($0) } }
        guard argv.dropLast().allSatisfy({ $0 != nil }), env.dropLast().allSatisfy({ $0 != nil }) else {
            throw ProcessExecutionError.launchFailed(ENOMEM)
        }
        var pid: pid_t = 0
        try check(posix_spawn(&pid, request.executable, &actions, &attributes, &argv, &env))
        return pid
    }

    private func check(_ code: Int32) throws {
        if code != 0 { throw ProcessExecutionError.launchFailed(code) }
    }

    private func collect(pid: pid_t, output: (Int32, Int32), diagnostics: (Int32, Int32)) throws -> ProcessResult {
        // run() owns the read ends and has already closed the local writes.
        var readers = [pollfd(fd: output.0, events: Int16(POLLIN), revents: 0),
                       pollfd(fd: diagnostics.0, events: Int16(POLLIN), revents: 0)]
        var stdout = Data(), stderr = Data(), truncated = false
        var failure: (any Error)?
        var terminationAt: TimeInterval?, killedAt: TimeInterval?, exitedAt: TimeInterval?
        var bytes = [UInt8](repeating: 0, count: 65_536)
        while true {
            let now = ProcessInfo.processInfo.systemUptime
            if failure == nil {
                if isCancelled { failure = CancellationError() }
                else if now >= deadline { failure = ProcessExecutionError.timedOut }
            }
            if failure != nil && terminationAt == nil {
                kill(-pid, SIGTERM); terminationAt = now
            }
            if let terminationAt, killedAt == nil && now - terminationAt >= request.terminationGrace {
                kill(-pid, SIGKILL); killedAt = now
            }
            var info = siginfo_t()
            let waited = waitid(P_PID, id_t(pid), &info, WEXITED | WNOHANG | WNOWAIT)
            if waited == 0 && info.si_pid == pid { exitedAt = exitedAt ?? now }
            if waited != 0 && errno != EINTR {
                // In particular, ECHILD no longer proves PID ownership.
                throw ProcessExecutionError.ioFailed(errno)
            }
            if let exitedAt {
                if readers.allSatisfy({ $0.fd == -1 }) { break }
                if now - exitedAt >= request.drainGrace { break }
            } else if let killedAt, now - killedAt >= max(1, request.drainGrace) {
                // A kernel-stalled child cannot be synchronously reaped. Keep
                // its PID reserved and reap on exit without blocking this API.
                Self.reapWhenExited(pid)
                throw ProcessExecutionError.terminationFailed
            }
            let ready = poll(&readers, nfds_t(readers.count), 25)
            if ready < 0 && errno != EINTR { failure = failure ?? ProcessExecutionError.ioFailed(errno) }
            for index in readers.indices where readers[index].fd >= 0 && readers[index].revents != 0 {
                // Bounded batches give cancellation and the other pipe time.
                for _ in 0..<4 {
                    let count = read(readers[index].fd, &bytes, bytes.count)
                    if count == 0 { readers[index].fd = -1; break }
                    if count < 0 {
                        if errno != EAGAIN && errno != EINTR { failure = failure ?? ProcessExecutionError.ioFailed(errno) }
                        break
                    }
                    if index == 0 {
                        if count <= request.stdoutLimit - stdout.count { stdout.append(contentsOf: bytes.prefix(count)) }
                        else { failure = failure ?? ProcessExecutionError.outputLimit }
                    } else {
                        stderr.append(contentsOf: bytes.prefix(count))
                        if stderr.count > request.stderrLimit {
                            stderr.removeFirst(stderr.count - request.stderrLimit); truncated = true
                        }
                    }
                }
            }
        }
        // The child is intentionally not reaped until the last group signal:
        // its reserved PID prevents signalling a reused process/group ID.
        kill(-pid, SIGKILL)
        var status: Int32 = 0
        var reaped: pid_t
        repeat { reaped = waitpid(pid, &status, 0) } while reaped < 0 && errno == EINTR
        guard reaped == pid else { throw ProcessExecutionError.ioFailed(errno) }
        if let failure { throw failure }
        let signal = status & 0x7f
        return ProcessResult(exitCode: signal == 0 ? (status >> 8) & 0xff : 128 + signal,
                             stdout: stdout, stderr: stderr, stderrTruncated: truncated)
    }

    private static func reapWhenExited(_ pid: pid_t) {
        let source = DispatchSource.makeProcessSource(identifier: pid, eventMask: .exit, queue: .global(qos: .utility))
        source.setEventHandler {
            var status: Int32 = 0
            _ = waitpid(pid, &status, WNOHANG)
            source.setEventHandler(handler: nil)
            source.cancel()
        }
        source.resume()
    }
}
