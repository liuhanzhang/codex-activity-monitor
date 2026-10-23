import Darwin
@preconcurrency import Dispatch
import Foundation

/// Watch the directory: publishing a snapshot replaces the file atomically.
/// The regular refresh remains responsible for freshness and recovery.
func snapshotUpdates(at url: URL) -> AsyncStream<Void> {
    AsyncStream(bufferingPolicy: .bufferingNewest(1)) { continuation in
        let descriptor = open(url.deletingLastPathComponent().path, O_EVTONLY | O_CLOEXEC)
        guard descriptor >= 0 else { continuation.finish(); return }
        let source = DispatchSource.makeFileSystemObjectSource(
            fileDescriptor: descriptor, eventMask: [.write, .delete, .rename, .revoke],
            queue: .global(qos: .utility))
        source.setEventHandler {
            continuation.yield(())
            if !source.data.intersection([.delete, .rename, .revoke]).isEmpty { continuation.finish() }
        }
        source.setCancelHandler { close(descriptor) }
        continuation.onTermination = { _ in source.cancel() }
        source.resume()
    }
}
