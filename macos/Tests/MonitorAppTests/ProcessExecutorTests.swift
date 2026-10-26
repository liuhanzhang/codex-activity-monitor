import Darwin
import Foundation
import Testing
@testable import MonitorApp

@Suite(.serialized) struct ProcessExecutorTests {
    private var python: String {
        let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent().deletingLastPathComponent()
        let local = root.appendingPathComponent(".local/venv/bin/python").path
        return FileManager.default.isExecutableFile(atPath: local) ? local : "/usr/bin/python3"
    }

    private func request(_ code: String, marker: URL? = nil) -> ProcessRequest {
        var value = ProcessRequest(executable: python, arguments: ["-c", code] + (marker.map { [$0.path] } ?? []))
        value.timeout = 5; value.terminationGrace = 0.1; value.drainGrace = 0.1
        return value
    }

    private func waitFor(_ condition: () -> Bool, seconds: Double = 3) async -> Bool {
        let deadline = ContinuousClock.now.advanced(by: .seconds(seconds))
        while ContinuousClock.now < deadline {
            if condition() { return true }
            try? await Task.sleep(for: .milliseconds(10))
        }
        return condition()
    }

    private func marker() -> URL { FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString) }
    private func pid(_ url: URL) throws -> pid_t { try #require(Int32(String(contentsOf: url, encoding: .utf8))) }
    private func gone(_ pid: pid_t) -> Bool { Darwin.kill(pid, 0) == -1 && errno == ESRCH }

    @Test func drainsBothPipesAndKeepsOnlyBoundedStderrTail() async throws {
        var value = request("""
        import sys, threading
        t = threading.Thread(target=lambda: sys.stderr.buffer.write(b'e'*1048576))
        t.start()
        sys.stdout.buffer.write(b'o'*262144)
        t.join()
        """)
        value.stdoutLimit = 300_000; value.stderrLimit = 4096
        let result = try await ProcessExecutor().execute(value)
        #expect(result.exitCode == 0)
        #expect(result.stdout == Data(repeating: 111, count: 262_144))
        #expect(result.stderr == Data(repeating: 101, count: 4096) && result.stderrTruncated)
    }

    @Test func outputOverflowTerminatesAndReapsOwnedChild() async throws {
        let file = marker(); defer { try? FileManager.default.removeItem(at: file) }
        var value = request("""
        import os, sys
        with open(sys.argv[1], 'w') as f: f.write(str(os.getpid()))
        while True: os.write(1, b'x'*65536)
        """, marker: file)
        value.stdoutLimit = 32
        await #expect(throws: ProcessExecutionError.outputLimit) { try await ProcessExecutor().execute(value) }
        #expect(gone(try pid(file)))
    }

    @Test func timeoutEscalatesWhenTerminationIsIgnored() async throws {
        let file = marker(); defer { try? FileManager.default.removeItem(at: file) }
        var value = request("""
        import os, signal, sys, time
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        with open(sys.argv[1], 'w') as f: f.write(str(os.getpid()))
        time.sleep(30)
        """, marker: file)
        value.timeout = 0.4
        let started = ContinuousClock.now
        await #expect(throws: ProcessExecutionError.timedOut) { try await ProcessExecutor().execute(value) }
        #expect(started.duration(to: .now) < .seconds(2))
        #expect(gone(try pid(file)))
    }

    @Test func cancellationStopsARealRunningChild() async throws {
        let file = marker(); defer { try? FileManager.default.removeItem(at: file) }
        let value = request("""
        import os, signal, sys, time
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        with open(sys.argv[1], 'w') as f: f.write(str(os.getpid()))
        time.sleep(30)
        """, marker: file)
        let task = Task { try await ProcessExecutor().execute(value) }
        let ready = await waitFor { FileManager.default.fileExists(atPath: file.path) }
        #expect(ready)
        task.cancel()
        await #expect(throws: CancellationError.self) { try await task.value }
        #expect(gone(try pid(file)))
    }

    @Test func inheritedPipeCannotHoldCallOpenAfterParentExit() async throws {
        let file = marker(); defer { try? FileManager.default.removeItem(at: file) }
        let value = request("""
        import os, sys, time
        child = os.fork()
        if child == 0: time.sleep(30)
        else:
            with open(sys.argv[1], 'w') as f: f.write(str(child))
            print('done')
        """, marker: file)
        let started = ContinuousClock.now
        let result = try await ProcessExecutor().execute(value)
        #expect(started.duration(to: .now) < .seconds(2))
        #expect(result.exitCode == 0 && String(decoding: result.stdout, as: UTF8.self) == "done\n")
        let child = try pid(file)
        let cleaned = await waitFor { gone(child) }
        #expect(cleaned)
    }

    @Test func anAlreadyCancelledTaskNeverLaunches() async throws {
        let file = marker(); defer { try? FileManager.default.removeItem(at: file) }
        let value = request("import sys; open(sys.argv[1], 'w').write('launched')", marker: file)
        let task = Task {
            withUnsafeCurrentTask { $0?.cancel() }
            return try await ProcessExecutor().execute(value)
        }
        await #expect(throws: CancellationError.self) { try await task.value }
        #expect(!FileManager.default.fileExists(atPath: file.path))
    }

    @Test func launchFailureAndExitStatusStayDistinct() async throws {
        await #expect(throws: ProcessExecutionError.launchFailed(ENOENT)) {
            try await ProcessExecutor().execute(ProcessRequest(executable: "/nonexistent-cam-test"))
        }
        let result = try await ProcessExecutor().execute(request("import sys; print('payload'); sys.exit(9)"))
        #expect(result.exitCode == 9 && String(decoding: result.stdout, as: UTF8.self) == "payload\n")
    }

    @Test func detachedServiceIsNotOwnedByTheShortCommandGroup() async throws {
        let file = marker(); defer { try? FileManager.default.removeItem(at: file) }
        let value = request("""
        import os, sys, time
        child = os.fork()
        if child == 0:
            os.setsid()
            fd = os.open('/dev/null', os.O_WRONLY)
            os.dup2(fd, 1); os.dup2(fd, 2); os.close(fd)
            with open(sys.argv[1], 'w') as f: f.write(str(os.getpid()))
            until = time.monotonic()+10
            while os.path.exists(sys.argv[1]) and time.monotonic()<until: time.sleep(.01)
        else:
            while not os.path.exists(sys.argv[1]): time.sleep(.005)
            print('service started')
        """, marker: file)
        let result = try await ProcessExecutor().execute(value)
        let service = try pid(file)
        #expect(result.exitCode == 0 && !gone(service))
        try FileManager.default.removeItem(at: file) // The fixture's own shutdown protocol.
        let stopped = await waitFor { gone(service) }
        #expect(stopped)
    }

    @Test func environmentDirectoryAndArgumentsRemainLiteral() async throws {
        let directory = marker()
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        var value = request("import os,sys,json; print(json.dumps([os.getcwd(),os.environ['CAM_PROCESS_TEST'],sys.argv[1],sys.stdin.read()]))")
        let literal = "literal $(command) `value`; spaces"
        value.arguments.append(literal); value.environment["CAM_PROCESS_TEST"] = "line\nvalue"
        value.directory = directory.path
        let result = try await ProcessExecutor().execute(value)
        let values = try JSONDecoder().decode([String].self, from: result.stdout)
        let canonical = try #require(realpath(directory.path, nil))
        defer { free(canonical) }
        #expect(values == [String(cString: canonical), "line\nvalue", literal, ""])
    }

    @Test func cancellationRacingExitDoesNotAffectTheNextOperation() async throws {
        for _ in 0..<8 {
            let file = marker()
            defer { try? FileManager.default.removeItem(at: file) }
            let value = request("import os,sys; open(sys.argv[1],'w').write(str(os.getpid()))", marker: file)
            let task = Task { try await ProcessExecutor().execute(value) }
            let ready = await waitFor { FileManager.default.fileExists(atPath: file.path) }
            #expect(ready)
            task.cancel()
            do { #expect(try await task.value.exitCode == 0) }
            catch is CancellationError { }
            #expect(gone(try pid(file)))
        }
        let next = try await ProcessExecutor().execute(request("import sys; sys.exit(7)"))
        #expect(next.exitCode == 7)
    }
}
