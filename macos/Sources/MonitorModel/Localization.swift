import Foundation

public enum MonitorLanguage: String, CaseIterable, Codable, Sendable {
    case chinese = "zh-CN", english = "en"
    public var resourceName: String { self == .chinese ? "zh-Hans" : "en" }
    public var name: String { self == .chinese ? "简体中文" : "English" }
    public var locale: Locale { Locale(identifier: rawValue) }
}

public indirect enum MessageValue: Codable, Sendable, Equatable {
    case string(String), number(Double), object([String: MessageValue]), bool(Bool), null
    public init(from decoder: any Decoder) throws {
        let value = try decoder.singleValueContainer()
        if value.decodeNil() { self = .null }
        else if let v = try? value.decode(String.self) { self = .string(v) }
        else if let v = try? value.decode(Bool.self) { self = .bool(v) }
        else if let v = try? value.decode(Double.self) { self = .number(v) }
        else if let v = try? value.decode([String: MessageValue].self) { self = .object(v) }
        else { self = .null }
    }
    public func encode(to encoder: any Encoder) throws {
        var value = encoder.singleValueContainer()
        switch self {
        case .string(let v): try value.encode(v)
        case .number(let v): try value.encode(v)
        case .object(let v): try value.encode(v)
        case .bool(let v): try value.encode(v)
        case .null: try value.encodeNil()
        }
    }
}

/// Malformed optional display metadata cannot invalidate an otherwise usable snapshot.
public struct DisplayMessage: Codable, Sendable, Equatable {
    public let key: String
    public let params: [String: MessageValue]
    public init(_ key: String, params: [String: MessageValue] = [:]) { self.key = key; self.params = params }
    enum CodingKeys: String, CodingKey { case key, params }
    public init(from decoder: any Decoder) throws {
        let container = try? decoder.container(keyedBy: CodingKeys.self)
        let decodedKey = (try? container?.decode(String.self, forKey: .key)) ?? ""
        let decodedParams = try? container?.decode([String: MessageValue].self, forKey: .params)
        key = container?.contains(.params) == true && decodedParams == nil ? "" : decodedKey
        params = decodedParams ?? [:]
    }
    public func rendered(in language: MonitorLanguage) -> String { CoreLocalizer.render(self, language: language) }
}

public enum CoreLocalizer {
    public static func contains(_ key: String, language: MonitorLanguage) -> Bool { catalogs[language.rawValue]?[key] != nil }
    private static let catalogs: [String: [String: MessageValue]] = {
        guard let url = Bundle.module.url(forResource: "CoreMessages", withExtension: "json"),
              let data = try? Data(contentsOf: url),
              let result = try? JSONDecoder().decode([String: [String: MessageValue]].self, from: data) else { return [:] }
        return result
    }()
    public static func render(_ message: DisplayMessage, language: MonitorLanguage) -> String {
        formatted(message, language: language, depth: 0) ?? fallback(language)
    }
    private static func fallback(_ language: MonitorLanguage) -> String { language == .english ? "Processing" : "处理中" }
    private static func formatted(_ message: DisplayMessage, language: MonitorLanguage, depth: Int) -> String? {
        guard depth < 8, let entry = catalogs[language.rawValue]?[message.key] else { return nil }
        let template: String
        switch entry {
        case .string(let s): template = s
        case .object(let forms):
            let singular = message.params["count"] == .number(1)
            guard case .string(let s) = forms[singular ? "one" : "other"] else { return nil }
            template = s
        default: return nil
        }
        var values = ["target": MessageValue.string(""), "language_hint": .string("")]
        values.merge(message.params) { _, new in new }
        var result = "", cursor = template.startIndex
        while cursor < template.endIndex {
            if template[cursor] == "{" {
                guard let close = template[cursor...].firstIndex(of: "}") else { return nil }
                let token = template[template.index(after: cursor)..<close].split(separator: ":", omittingEmptySubsequences: false)
                guard let first = token.first, let value = values[String(first)] else { return nil }
                let text: String
                switch value {
                case .string(let s):
                    guard token.count == 1 else { return nil }
                    text = s
                case .number(let number):
                    guard number.isFinite else { return nil }
                    let numeric = String(format: number.rounded() == number ? "%.0f" : "%g", locale: Locale(identifier: "en_US_POSIX"), number)
                    text = token.count == 2 && token[1] == "02"
                        ? String(repeating: "0", count: max(0, 2 - numeric.count)) + numeric : numeric
                case .object(let object):
                    guard token.count == 1 else { return nil }
                    guard case .string(let key) = object["key"] else { return nil }
                    let params: [String: MessageValue]
                    if case .object(let p) = object["params"] { params = p }
                    else if object["params"] != nil { text = fallback(language); break }
                    else { params = [:] }
                    text = formatted(DisplayMessage(key, params: params), language: language, depth: depth + 1) ?? fallback(language)
                default: return nil
                }
                result += first == "target" && !text.isEmpty ? " · " + text : text
                cursor = template.index(after: close)
            } else {
                result.append(template[cursor]); cursor = template.index(after: cursor)
            }
        }
        return result
    }
}

/// Shared model presentation copy. View-specific resources remain in MonitorApp.
public enum ModelLocalizer {
    public static func text(_ key: String, _ language: MonitorLanguage = .chinese) -> String {
        // SwiftPM normalizes zh-Hans.lproj to zh-hans.lproj; resolve by locale.
        guard let path = Bundle.module.path(forResource: "Localizable", ofType: "strings",
                                            inDirectory: nil, forLocalization: language.resourceName),
              let bundle = Bundle(path: URL(fileURLWithPath: path).deletingLastPathComponent().path) else { return key }
        return bundle.localizedString(forKey: key, value: key, table: nil)
    }
}
