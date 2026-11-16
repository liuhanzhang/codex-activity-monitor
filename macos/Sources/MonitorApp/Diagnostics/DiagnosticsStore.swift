import Foundation
import MonitorModel
import Observation

@MainActor @Observable final class DiagnosticsStore {
    var search = ""
    var level = ""
    var source = ""
    var limit = 200
    var batch = DiagnosticLogBatch()
    var selection: String?
    var reading = false
    @ObservationIgnored private let client = DiagnosticLogClient()
    private var generation = 0
    var queryIdentity: String { "\(search)|\(level)|\(source)|\(limit)" }
    var selectedEvent: DiagnosticEvent? { batch.events.first { $0.id == selection } }
    func refresh(directory: String?) async {
        generation += 1
        let token = generation
        reading = true
        let result = await client.read(directory: directory, search: search, level: level, source: source, limit: limit)
        guard !Task.isCancelled, token == generation else { return }
        batch = result; reading = false
    }
}
