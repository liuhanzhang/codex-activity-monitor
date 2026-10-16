import Foundation

/// View copy lives in resources; activity labels are supplied by the core.
func L(_ key: String) -> String {
    NSLocalizedString(key, bundle: .module, comment: "")
}
