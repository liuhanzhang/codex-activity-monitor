import Foundation
import SwiftUI
import MonitorModel

private struct MonitorLanguageKey: EnvironmentKey { static let defaultValue = MonitorLanguage.chinese }
extension EnvironmentValues {
    var monitorLanguage: MonitorLanguage {
        get { self[MonitorLanguageKey.self] }
        set { self[MonitorLanguageKey.self] = newValue }
    }
}

func L(_ key: String, language: MonitorLanguage = .chinese) -> String {
    guard let path = Bundle.module.path(forResource: language.resourceName, ofType: "lproj"),
          let bundle = Bundle(path: path) else { return key }
    return bundle.localizedString(forKey: key, value: key, table: nil)
}

@MainActor protocol LocalizedView: View { var language: MonitorLanguage { get } }
extension LocalizedView {
    func L(_ key: String) -> String { localizedText(key, language: language) }
    func plural(_ key: String, count: Int) -> String {
        String(format: L(language == .english && count == 1 ? key + ".one" : key), locale: language.locale, count)
    }
}

// A separate name avoids recursive lookup from a view's L convenience method.
func localizedText(_ key: String, language: MonitorLanguage) -> String { L(key, language: language) }

struct PresentationFailure {
    var key: String?
    var message: DisplayMessage?
    var diagnostic: String?
    var prefix: String?
    func rendered(_ language: MonitorLanguage) -> String {
        let explanation = message?.rendered(in: language) ?? key.map { L($0, language: language) }
            ?? L("管理操作失败，请重试。", language: language)
        return (prefix.map { L($0, language: language) } ?? "") + explanation
            + (diagnostic.map { "\n" + $0 } ?? "")
    }
    init(key: String) { self.key = key }
    init(error: any Error, prefix: String? = nil) {
        self.prefix = prefix
        if let failure = error as? BackendFailure {
            message = failure.displayMessage
            if message == nil { diagnostic = failure.message }
        } else { diagnostic = error.localizedDescription }
    }
}
