import SwiftUI

enum AgentBrand: String {
    case codex = "Codex"
    case antigravity = "Antigravity"

    enum Artwork: String {
        case appIcon = "agent"
        case mark = "agent-mark"
    }

    @ViewBuilder func label(iconSize: CGFloat, artwork: Artwork = .appIcon) -> some View {
        if let image = Bundle.module.image(forResource: "\(artwork.rawValue)-\(rawValue).png") {
            Label {
                Text(verbatim: rawValue)
            } icon: {
                Image(nsImage: image).renderingMode(.original).resizable()
                    .frame(width: iconSize, height: iconSize)
                    .accessibilityHidden(true)
            }
        } else {
            Text(verbatim: rawValue)
        }
    }
}
