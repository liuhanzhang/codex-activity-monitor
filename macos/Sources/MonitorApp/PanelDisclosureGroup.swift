import SwiftUI

/// Keep the system disclosure control; also let its descriptive label toggle it.
struct PanelDisclosureGroup<Label: View, Content: View>: View {
    @Binding var isExpanded: Bool
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @ViewBuilder var content: () -> Content
    @ViewBuilder var label: () -> Label

    var body: some View {
        DisclosureGroup(isExpanded: $isExpanded) {
            if isExpanded { content() }
        } label: {
            label().contentShape(Rectangle())
                .onTapGesture { isExpanded.toggle() }
        }
        .disclosureGroupStyle(.automatic)
        .animation(reduceMotion ? nil : .easeInOut(duration: 0.16), value: isExpanded)
    }
}
