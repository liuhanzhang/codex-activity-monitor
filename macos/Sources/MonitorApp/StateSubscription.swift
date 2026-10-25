import Darwin
import Foundation
import MonitorModel

struct StatePersistence: Decodable, Sendable {
    let snapshot_write_failures: Int?
    let writing: Bool?
    let pending: Int?
}

struct StateObservation: Decodable, Sendable {
    let managed: Bool
    let owner_connected: Bool
    let session_id: String?
    let expires_in_ms: Double?
}

struct StateHealth: Sendable {
    let acknowledgedAt: ContinuousClock.Instant
    let tickAgeAtAcknowledgement: Duration
    let persistence: StatePersistence?
    let observation: StateObservation?

    init(acknowledgedAt: ContinuousClock.Instant, tickAgeAtAcknowledgement: Duration,
         persistence: StatePersistence?, observation: StateObservation? = nil) {
        self.acknowledgedAt = acknowledgedAt
        self.tickAgeAtAcknowledgement = tickAgeAtAcknowledgement
        self.persistence = persistence
        self.observation = observation
    }

    func isResponsive(at now: ContinuousClock.Instant = .now) -> Bool {
        let elapsed = acknowledgedAt.duration(to: now)
        return elapsed >= .zero && elapsed < .seconds(2) && tickAgeAtAcknowledgement+elapsed < .seconds(2)
    }
}

/// Each update includes the latest full value, so a paused UI can coalesce both
/// state and health updates without losing the only pending state frame.
struct StateUpdate: Sendable {
    let instanceID: String
    let sequence: UInt64
    let snapshot: Snapshot?
    let stateAvailable: Bool
    let health: StateHealth?

    var isFresh: Bool { stateAvailable && snapshot?.running == true && health?.isResponsive() == true }
}

protocol StateSubscribing: Sendable {
    func subscribe(at path: String) -> StateSubscription
}

struct StateSubscription: Sendable {
    let updates: AsyncThrowingStream<StateUpdate, any Error>
    let release: @Sendable () async -> Bool
    let cancel: @Sendable () -> Void
}

extension StateSubscribing {
    func updates(at path: String) -> AsyncThrowingStream<StateUpdate, any Error> { subscribe(at: path).updates }
}

enum StateSubscriptionError: Error, Equatable {
    case invalidPath, connectionFailed(Int32), ioFailed(Int32), unsupportedVersion
    case invalidFrame, frameTooLarge, timedOut, disconnected
    case leaseRejected(String)
}

struct StateOwnerCredential: Sendable {
    let sessionID: String
    let secret: String
}

struct StateSubscriptionOptions: Sendable {
    var heartbeatInterval: Duration = .seconds(1)
    var requestTimeout: Duration = .seconds(2)
    var maxFrameBytes = 8*1024*1024
    var owner: StateOwnerCredential?
}

struct UnixStateSubscriber: StateSubscribing {
    var options = StateSubscriptionOptions()

    func subscribe(at path: String) -> StateSubscription {
        let operation = StateSocketOperation(options: options)
        let updates = AsyncThrowingStream<StateUpdate, any Error>(bufferingPolicy: .bufferingNewest(1)) { continuation in
            continuation.onTermination = { _ in operation.cancel() }
            DispatchQueue.global(qos: .utility).async {
                do {
                    try operation.run(path: path, yield: { continuation.yield($0) })
                    operation.finish(); continuation.finish()
                } catch { operation.finish(); continuation.finish(throwing: error) }
            }
        }
        return StateSubscription(updates: updates, release: { await operation.release() }, cancel: { operation.cancel() })
    }
}

private struct StateEnvelope: Decodable {
    let protocol_version: Int
    let kind: String
    let instance_id: String
    let sequence: UInt64?
    let payload: Snapshot?
    let nonce: String?
    let tick_age_ms: Double?
    let last_state_sequence: UInt64?
    let persistence: StatePersistence?
    let observation: StateObservation?
    let code: String?
}

/// One worker owns every descriptor. Cancellation only changes a locked flag;
/// it cannot close a reused descriptor while an I/O call is in flight.
private final class StateSocketOperation: @unchecked Sendable {
    private let lock = NSLock()
    private var cancelled = false
    private var finished = false
    private var released = false
    private var releaseDeadline: ContinuousClock.Instant?
    private let options: StateSubscriptionOptions
    init(options: StateSubscriptionOptions) { self.options = options }
    func cancel() { lock.withLock { cancelled = true } }
    func finish() { lock.withLock { finished = true } }
    func release() async -> Bool {
        guard options.owner != nil else { cancel(); return false }
        let deadline = lock.withLock {
            if let releaseDeadline { return releaseDeadline }
            let deadline = ContinuousClock.now.advanced(by: .milliseconds(600))
            releaseDeadline = deadline
            return deadline
        }
        while !lock.withLock({ finished }), ContinuousClock.now < deadline {
            do { try await Task.sleep(for: .milliseconds(10)) }
            catch { cancel(); return false }
        }
        cancel()
        return lock.withLock { released }
    }
    private func checkCancellation() throws {
        if lock.withLock({ cancelled }) { throw CancellationError() }
    }

    private func connect(path: String) throws -> Int32 {
        try checkCancellation()
        var address = sockaddr_un()
        let bytes = Array(path.utf8)
        guard !bytes.isEmpty, !bytes.contains(0), bytes.count < MemoryLayout.size(ofValue: address.sun_path) else {
            throw StateSubscriptionError.invalidPath
        }
        address.sun_family = sa_family_t(AF_UNIX)
        address.sun_len = UInt8(MemoryLayout<sockaddr_un>.size)
        withUnsafeMutableBytes(of: &address.sun_path) { $0.copyBytes(from: bytes+[0]) }
        let descriptor = socket(AF_UNIX, SOCK_STREAM, 0)
        guard descriptor >= 0 else { throw StateSubscriptionError.connectionFailed(errno) }
        do {
            guard fcntl(descriptor, F_SETFL, O_NONBLOCK) == 0,
                  fcntl(descriptor, F_SETFD, FD_CLOEXEC) == 0 else { throw StateSubscriptionError.ioFailed(errno) }
            var one: Int32 = 1
            guard setsockopt(descriptor, SOL_SOCKET, SO_NOSIGPIPE, &one, socklen_t(MemoryLayout.size(ofValue: one))) == 0 else {
                throw StateSubscriptionError.ioFailed(errno)
            }
            let result = withUnsafePointer(to: &address) {
                $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                    Darwin.connect(descriptor, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
                }
            }
            if result != 0 {
                guard errno == EINPROGRESS else { throw StateSubscriptionError.connectionFailed(errno) }
                let deadline = ContinuousClock.now.advanced(by: options.requestTimeout)
                while true {
                    try checkCancellation()
                    guard ContinuousClock.now < deadline else { throw StateSubscriptionError.timedOut }
                    var event = pollfd(fd: descriptor, events: Int16(POLLOUT), revents: 0)
                    let count = Darwin.poll(&event, 1, 25)
                    if count < 0 && errno != EINTR { throw StateSubscriptionError.ioFailed(errno) }
                    if count > 0 {
                        var error: Int32 = 0
                        var size = socklen_t(MemoryLayout.size(ofValue: error))
                        guard getsockopt(descriptor, SOL_SOCKET, SO_ERROR, &error, &size) == 0 else {
                            throw StateSubscriptionError.ioFailed(errno)
                        }
                        guard error == 0 else { throw StateSubscriptionError.connectionFailed(error) }
                        break
                    }
                }
            }
            return descriptor
        } catch { Darwin.close(descriptor); throw error }
    }

    func run(path: String, yield: (StateUpdate) -> Void) throws {
        let descriptor = try connect(path: path)
        defer { Darwin.close(descriptor) }
        let decoder = JSONDecoder()
        var buffer = Data()
        var scanFrom = 0
        var outgoing = Data()
        var sent = 0
        var expected: (nonce: String, kind: String, sentAt: ContinuousClock.Instant)?
        var nextHeartbeat = ContinuousClock.now
        var instance: String?
        var sequence: UInt64 = 0
        var snapshot: Snapshot?
        var stateAvailable = false
        var health: StateHealth?

        func emit() {
            if let instance { yield(StateUpdate(instanceID: instance, sequence: sequence, snapshot: snapshot,
                                                 stateAvailable: stateAvailable, health: health)) }
        }
        func request(_ kind: String) throws {
            let nonce = UUID().uuidString
            var fields: [String: Any] = ["protocol_version": 1, "kind": kind, "nonce": nonce,
                                         "role": options.owner == nil ? "reader" : "owner"]
            if kind == "subscribe", let owner = options.owner {
                fields["session_id"] = owner.sessionID
                fields["session_secret"] = owner.secret
            }
            outgoing = try JSONSerialization.data(withJSONObject: fields)
            outgoing.append(10)
            sent = 0
            expected = (nonce, kind, .now)
        }
        try request("subscribe")
        while true {
            try checkCancellation()
            let now = ContinuousClock.now
            let releasing = lock.withLock { releaseDeadline }
            if let releasing, now >= releasing { throw StateSubscriptionError.timedOut }
            if let expected, expected.sentAt.duration(to: now) >= options.requestTimeout {
                throw StateSubscriptionError.timedOut
            }
            if expected == nil {
                if releasing != nil { try request("release") }
                else if now >= nextHeartbeat { try request("heartbeat") }
            }
            var event = pollfd(fd: descriptor, events: Int16(POLLIN | (sent < outgoing.count ? POLLOUT : 0)), revents: 0)
            let count = Darwin.poll(&event, 1, 25)
            if count < 0 {
                if errno == EINTR { continue }
                throw StateSubscriptionError.ioFailed(errno)
            }
            if event.revents & Int16(POLLOUT) != 0, sent < outgoing.count {
                let written = outgoing.withUnsafeBytes { Darwin.send(descriptor, $0.baseAddress!.advanced(by: sent), outgoing.count-sent, 0) }
                if written > 0 { sent += written }
                else if written < 0 && errno != EAGAIN && errno != EINTR { throw StateSubscriptionError.ioFailed(errno) }
            }
            if event.revents & Int16(POLLIN | POLLHUP | POLLERR) != 0 {
                // Bound each read batch to leave room for cancellation/deadlines.
                for _ in 0..<4 {
                    var chunk = [UInt8](repeating: 0, count: 65_536)
                    let received = Darwin.recv(descriptor, &chunk, chunk.count, 0)
                    if received == 0 { throw StateSubscriptionError.disconnected }
                    if received < 0 {
                        if errno == EAGAIN || errno == EINTR { break }
                        throw StateSubscriptionError.ioFailed(errno)
                    }
                    buffer.append(contentsOf: chunk.prefix(received))
                    var consumed = 0
                    while let end = buffer[scanFrom...].firstIndex(of: 10) {
                        guard end-consumed+1 <= options.maxFrameBytes else { throw StateSubscriptionError.frameTooLarge }
                        let envelope: StateEnvelope
                        do { envelope = try decoder.decode(StateEnvelope.self, from: buffer[consumed..<end]) }
                        catch { throw StateSubscriptionError.invalidFrame }
                        consumed = end+1; scanFrom = consumed
                        guard envelope.protocol_version == 1 else { throw StateSubscriptionError.unsupportedVersion }
                        guard !envelope.instance_id.isEmpty, envelope.instance_id.utf8.count <= 128 else {
                            throw StateSubscriptionError.invalidFrame
                        }
                        switch envelope.kind {
                        case "released":
                            guard let pending = expected, pending.kind == "release", envelope.nonce == pending.nonce,
                                  instance == envelope.instance_id else { throw StateSubscriptionError.invalidFrame }
                            lock.withLock { released = true }
                            return
                        case "hello", "heartbeat":
                            // Backlogged and foreign replies cannot renew health.
                            guard let pending = expected, envelope.nonce == pending.nonce else { continue }
                            let acknowledged = ContinuousClock.now
                            let roundTrip = pending.sentAt.duration(to: acknowledged)
                            guard roundTrip < options.requestTimeout else { throw StateSubscriptionError.timedOut }
                            guard envelope.kind == (pending.kind == "subscribe" ? "hello" : "heartbeat"),
                                  let age = envelope.tick_age_ms, age.isFinite, age >= 0,
                                  envelope.last_state_sequence != nil else { throw StateSubscriptionError.invalidFrame }
                            if instance == nil { instance = envelope.instance_id }
                            guard instance == envelope.instance_id else { throw StateSubscriptionError.invalidFrame }
                            if let owner = options.owner {
                                guard envelope.observation?.managed == true, envelope.observation?.owner_connected == true,
                                      envelope.observation?.session_id == owner.sessionID else {
                                    throw StateSubscriptionError.leaseRejected("invalid_owner")
                                }
                            }
                            health = StateHealth(acknowledgedAt: acknowledged,
                                tickAgeAtAcknowledgement: .milliseconds(min(age, 60_000))+roundTrip,
                                persistence: envelope.persistence, observation: envelope.observation)
                            expected = nil
                            nextHeartbeat = acknowledged.advanced(by: options.heartbeatInterval)
                            emit()
                        case "state", "error":
                            if envelope.kind == "error", let pending = expected, envelope.nonce == pending.nonce,
                               let code = envelope.code, ["invalid_lease", "lease_expired", "lease_superseded", "unmanaged_runtime"].contains(code) {
                                throw StateSubscriptionError.leaseRejected(code)
                            }
                            guard instance == envelope.instance_id, let next = envelope.sequence, next > 0 else {
                                throw StateSubscriptionError.invalidFrame
                            }
                            guard next > sequence else { continue }
                            sequence = next
                            if envelope.kind == "state" {
                                guard let value = envelope.payload else { throw StateSubscriptionError.invalidFrame }
                                guard value.schema_version == 1 else { throw StateSubscriptionError.unsupportedVersion }
                                snapshot = value; stateAvailable = true
                            } else {
                                guard envelope.code == "state_unavailable" else { throw StateSubscriptionError.invalidFrame }
                                stateAvailable = false
                            }
                            emit()
                        default: throw StateSubscriptionError.invalidFrame
                        }
                    }
                    if consumed > 0 { buffer = Data(buffer[consumed...]) }
                    scanFrom = buffer.count
                    guard buffer.count < options.maxFrameBytes else { throw StateSubscriptionError.frameTooLarge }
                }
            }
        }
    }
}
