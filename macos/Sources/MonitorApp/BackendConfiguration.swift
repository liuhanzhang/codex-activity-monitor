import Foundation

struct BackendConfiguration: Decodable, Sendable {
    let python: String
    let source_root: String
    let runtime: String
    let data_root: String
    let build_id: String
    var mode: String? = nil

    static func load(bundleURL: URL, userHome: URL = FileManager.default.homeDirectoryForCurrentUser) throws -> Self {
        let data = try Data(contentsOf: bundleURL.appendingPathComponent("Contents/Resources/backend.json"))
        struct Probe: Decodable { let mode: String? }
        guard try JSONDecoder().decode(Probe.self, from: data).mode == "bundled" else {
            return try JSONDecoder().decode(Self.self, from: data)
        }
        struct Manifest: Decodable { let backend: String; let build_id: String }
        let manifest = try JSONDecoder().decode(Manifest.self, from: data)
        let app = bundleURL.resolvingSymlinksInPath().standardizedFileURL
        let backend = app.appendingPathComponent(manifest.backend).resolvingSymlinksInPath().standardizedFileURL
        guard !manifest.backend.hasPrefix("/"), backend.path.hasPrefix(app.path + "/"),
              FileManager.default.isExecutableFile(atPath: backend.path) else {
            throw CocoaError(.fileReadCorruptFile)
        }
        let root = userHome.appendingPathComponent("Library/Application Support/CodexProgressMonitor").path
        return Self(python: backend.path, source_root: app.path, runtime: root + "/run",
                    data_root: root, build_id: manifest.build_id, mode: "bundled")
    }

    func request(arguments: [String]) -> ProcessRequest {
        let bundled = mode == "bundled"
        var request = ProcessRequest(executable: python,
            arguments: (bundled ? [] : ["-m", "codex_progress_monitor.cli"]) + arguments)
        request.directory = source_root
        if bundled {
            request.environment.removeValue(forKey: "PYTHONPATH")
            request.environment.removeValue(forKey: "PYTHONHOME")
            request.environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        } else {
            request.environment["PYTHONPATH"] = source_root + "/src"
            request.environment["TIKTOKEN_CACHE_DIR"] = source_root + "/.local/tokenizer-cache"
        }
        return request
    }
}
