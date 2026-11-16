import AppKit
import SwiftUI
import Observation

/// Child popovers are owned windows, not modal exceptions to outside dismissal.
@MainActor @Observable final class PanelAuxiliaryState {
    private(set) var activeID: UUID?
    @ObservationIgnored private weak var window: NSWindow?
    @ObservationIgnored private var closeAction: (() -> Void)?

    func present(_ id: UUID, close: @escaping () -> Void) {
        if activeID != id { dismiss() }
        activeID = id; closeAction = close
    }
    func attach(_ window: NSWindow?, id: UUID) {
        guard activeID == id else { return }
        self.window = window
    }
    func owns(_ candidate: NSWindow?) -> Bool {
        guard let window else { return false }
        var current = candidate
        while let value = current {
            if value === window { return true }
            current = value.parent
        }
        return false
    }
    @discardableResult func dismiss() -> Bool {
        guard activeID != nil else { return false }
        let action = closeAction
        activeID = nil; window = nil; closeAction = nil
        action?()
        return true
    }
    func remove(_ id: UUID) {
        guard activeID == id else { return }
        activeID = nil; window = nil; closeAction = nil
    }
}

private struct AuxiliaryWindowProbe: NSViewRepresentable {
    let changed: (NSWindow?) -> Void
    final class Probe: NSView {
        var changed: ((NSWindow?) -> Void)?
        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            // SwiftUI's onAppear registers ownership in the same mounting pass.
            let attached = window
            DispatchQueue.main.async { [weak self] in self?.changed?(attached) }
        }
    }
    func makeNSView(context: Context) -> Probe {
        let view = Probe(); view.changed = changed
        return view
    }
    func updateNSView(_ view: Probe, context: Context) { view.changed = changed }
}

struct PanelAuxiliaryContent<Content: View>: View {
    @Environment(PanelAuxiliaryState.self) private var state
    let id: UUID
    let close: () -> Void
    @ViewBuilder var content: () -> Content

    var body: some View {
        content()
            .background(AuxiliaryWindowProbe { state.attach($0, id: id) })
            .onAppear { state.present(id, close: close) }
            .onDisappear { state.remove(id) }
    }
}
