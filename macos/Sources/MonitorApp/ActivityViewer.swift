import AppKit
import SwiftUI
import MonitorModel

@MainActor final class ActivityWindow: NSWindow {
    override func performKeyEquivalent(with event: NSEvent) -> Bool {
        if event.modifierFlags.intersection(.deviceIndependentFlagsMask) == .command {
            if event.charactersIgnoringModifiers == "w" { performClose(nil); return true }
            if event.charactersIgnoringModifiers == "q" { NSApplication.shared.terminate(nil); return true }
        }
        return super.performKeyEquivalent(with: event)
    }
}

@MainActor final class ActivityViewerWindowController: NSWindowController, NSWindowDelegate {
    private(set) var viewer: ActivityViewerGroupStore?
    var onTrace: ((String) -> Void)?

    private let dashboard: DashboardStore?
    func updateLanguage() { window?.title = localizedText("活动查看器", language: dashboard?.language ?? .chinese) }
    init(preview: Bool, dashboard: DashboardStore? = nil) {
        self.dashboard = dashboard
        let window = ActivityWindow(contentRect: NSRect(x: 0, y: 0, width: 800, height: 600),
                                    styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = localizedText("活动查看器", language: dashboard?.language ?? .chinese)
        window.identifier = NSUserInterfaceItemIdentifier("monitor.activity.viewer")
        window.contentMinSize = NSSize(width: 640, height: 420)
        window.isReleasedWhenClosed = false
        super.init(window: window)
        window.delegate = self
        if !window.setFrameUsingName(preview ? "ActivityViewerPreview" : "ActivityViewer") { window.center() }
        window.setFrameAutosaveName(preview ? "ActivityViewerPreview" : "ActivityViewer")
    }
    required init?(coder: NSCoder) { nil }

    func show(timeline: TimelineStore, phase: TimelinePhase) { show(timeline: timeline, phases: [phase]) }

    func show(timeline: TimelineStore, phases: [TimelinePhase]) {
        guard !phases.isEmpty else { return }
        if let viewer, viewer.timeline === timeline { viewer.select(phases) }
        else {
            let preservedFrame = window?.frame
            let group = ActivityViewerGroupStore(timeline: timeline, phases: phases)
            viewer = group
            let host = NSHostingController(rootView: ViewerLanguageScope(dashboard: dashboard) { ActivityViewerView(store: group) })
            host.sizingOptions = []
            window?.contentViewController = host
            if let preservedFrame { window?.setFrame(preservedFrame, display: false) }
        }
        if let window, let screen = window.screen ?? NSScreen.main {
            window.setFrame(Self.constrained(window.frame, to: screen.visibleFrame), display: false)
        }
        window?.makeKeyAndOrderFront(nil)
        NSApplication.shared.activate(ignoringOtherApps: true)
        onTrace?("viewer-open")
    }
    static func constrained(_ frame: NSRect, to bounds: NSRect) -> NSRect {
        var result = frame
        result.size.width = min(frame.width, bounds.width)
        result.size.height = min(frame.height, bounds.height)
        result.origin.x = min(max(bounds.minX, frame.minX), bounds.maxX - result.width)
        result.origin.y = min(max(bounds.minY, frame.minY), bounds.maxY - result.height)
        return result
    }
    func windowWillClose(_ notification: Notification) {
        window?.contentViewController = nil
        viewer = nil
        onTrace?("viewer-close")
    }
}

struct ActivityViewerView: LocalizedView {
    @Environment(\.monitorLanguage) var language
    @Bindable var store: ActivityViewerGroupStore

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text(store.timeline.title.isEmpty ? L("未命名聊天") : store.timeline.title).lineLimit(1).help(store.timeline.title)
                Spacer()
                if store.entries.count > 1 {
                    Text(plural("%d 项工具", count: store.entries.count)).foregroundStyle(.secondary)
                }
            }.font(.system(size: 12, weight: .medium)).padding(.horizontal, 20).padding(.vertical, 14)
            Divider()
            ScrollView(.vertical) {
                TimelinePanelView(store: store.timeline, presentation: .activityViewer, onSelect: { store.select($0) })
                    .padding(.horizontal, 20).padding(.vertical, 10)
            }.frame(maxHeight: 190)
            Divider()
            if store.entries.count == 1, let entry = store.entries.first {
                ActivityDetailView(store: entry, onSelect: { store.select([$0]) })
            } else {
                ScrollView(.vertical) {
                    LazyVStack(spacing: 16) {
                        ForEach(store.entries, id: \.phase.id) { entry in
                            ActivityDetailView(store: entry, onSelect: { store.select([$0]) })
                                .frame(height: 360)
                                .overlay(RoundedRectangle(cornerRadius: 6).stroke(.separator, lineWidth: 1))
                                .accessibilityElement(children: .contain)
                                .accessibilityIdentifier("monitor.activity.tool." + entry.phase.id)
                        }
                    }.padding(16)
                }.id(store.selectionID).accessibilityIdentifier("monitor.activity.multiple")
            }
        }
        .frame(minWidth: 640, idealWidth: 800, maxWidth: .infinity, minHeight: 420, idealHeight: 600, maxHeight: .infinity)
        .background(.background)
        .task(id: store.selectionID) {
            while !Task.isCancelled {
                await store.refresh()
                do { try await Task.sleep(for: .seconds(1)) } catch { break }
            }
        }
    }
}

private struct ActivityDetailView: LocalizedView {
    @Environment(\.monitorLanguage) var language
    @Bindable var store: ActivityViewerStore
    let onSelect: (TimelinePhase) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            context.padding(.horizontal, 20).padding(.vertical, 14)
            Divider()
            content
            Divider()
            statusBar.padding(.horizontal, 18).padding(.vertical, 9)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(.background)
    }

    private var context: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack(spacing: 6) {
                Text(ownerName).foregroundStyle(.secondary).lineLimit(1)
                Spacer()
            }.font(.system(size: 12))
            HStack(spacing: 8) {
                if store.phase.isCoordination {
                    Image(systemName: "person.2").foregroundStyle(.secondary)
                } else {
                    Circle().fill(Color(nsColor: TimelinePalette.color(store.phase))).frame(width: 7, height: 7)
                }
                Text(L(store.phase.kindLabel(in: language))).fontWeight(.medium)
                if store.phase.isCoordination && store.phase.coordination_target_scope != "global" {
                    Text(store.phase.coordinationTargets(in: store.timeline.bundle?.agents ?? [], language: language))
                        .lineLimit(1).foregroundStyle(.secondary)
                        .help(store.phase.coordinationTargetDetails(in: store.timeline.bundle?.agents ?? [], language: language))
                } else if !store.phase.isCoordination && store.phase.sourceLabel(in: language) != store.phase.kindLabel(in: language) {
                    Text(store.phase.sourceLabel(in: language)).lineLimit(1).foregroundStyle(.secondary).help(store.phase.sourceLabel(in: language))
                }
                Spacer(minLength: 8)
                HStack(spacing: 4) {
                    if let symbol = TimelineDecoration.statusSymbol(for: store.phase) {
                        Image(systemName: symbol)
                            .foregroundStyle(Color(nsColor: TimelineDecoration.statusColor(for: store.phase)))
                            .accessibilityHidden(true)
                    }
                    Text(L(store.phase.statusLabel(in: language))).foregroundStyle(.secondary)
                }.accessibilityElement(children: .combine)
                Text(durationText)
                    .monospacedDigit().foregroundStyle(.secondary)
                    .help(store.phase.start_source == "receipt" || store.phase.end_source == "receipt" ? L("接收事件窗口估算，并非服务端纯计算耗时。") : L("Codex 记录的活动生命周期。"))
            }.font(.system(size: 12))
            HStack(spacing: 7) {
                if let time = store.phase.anchor {
                    Text(Date(timeIntervalSince1970: time / 1000).formatted(.dateTime.month(.abbreviated).day().hour().minute().second().locale(language.locale)))
                }
                Text("·")
                Text(L(store.phase.thread_id != store.timeline.thread ? "子智能体活动" :
                       store.phase.turn_id == store.timeline.bundle?.current_turn_id ? "当前轮" : "历史轮次"))
                if store.phase.start_ms == nil || !(store.phase.quality ?? []).isEmpty {
                    Text("·")
                    Text(L("部分观测")).help(L("部分时间边界未被完整观测，未知耗时不会按短线宽度计算。"))
                }
            }.font(.system(size: 10)).foregroundStyle(.tertiary)
        }
    }

    @ViewBuilder private var content: some View {
        if isEmptyReasoning {
            Color.clear.frame(maxWidth: .infinity, maxHeight: .infinity)
        } else if let detail = store.detail, !detail.sections.isEmpty {
            HStack(spacing: 18) {
                ForEach(detail.sections) { section in
                    Button { store.selectedSection = section.id } label: {
                        Text(section.localizedTitle(language)).fontWeight(store.selectedSection == section.id ? .semibold : .regular)
                            .foregroundStyle(store.selectedSection == section.id ? .primary : .secondary)
                            .padding(.vertical, 11)
                    }.buttonStyle(.plain).accessibilityIdentifier("monitor.activity.tab.\(section.id)")
                }
                Spacer()
                if let selected = selectedSection, selected.notice == nil {
                    Button {
                        NSPasteboard.general.clearContents()
                        NSPasteboard.general.setString(selected.text, forType: .string)
                    } label: { Label(L("复制"), systemImage: "doc.on.doc") }
                        .buttonStyle(.plain).help(selected.next_offset == nil ? L("复制当前内容") : L("复制当前已加载内容"))
                }
            }.font(.system(size: 12)).padding(.horizontal, 20)
            Divider()
            if let section = selectedSection {
                if let notice = section.localizedNotice(language) {
                    Text(notice).font(.system(size: 12)).foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if section.text.isEmpty {
                    Text(L("来源记录为空字符串。")).font(.system(size: 12)).foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else {
                    ActivityTextView(text: section.localizedText(language), format: section.format, followsContent: $store.followsContent)
                        .id(store.phase.id + "-" + section.id)
                        .accessibilityIdentifier("monitor.activity.content")
                }
                if detail.pageCursor(for: section.id) != nil {
                    Button(L("继续加载完整内容")) { Task { await store.loadMore() } }
                        .disabled(store.loadingMore).padding(.horizontal, 20).padding(.vertical, 8)
                }
            }
            if !detail.attachments.isEmpty {
                ScrollView(.horizontal) {
                    HStack(spacing: 12) { ForEach(detail.attachments, id: \.self) { path in AttachmentPreview(path: path) } }
                }.frame(maxHeight: 160).padding(10)
            }
        } else {
            VStack(spacing: 12) {
                if store.detail == nil && store.error == nil { ProgressView().controlSize(.small) }
                Text(store.error.map { L($0) } ?? detailNotice).font(.system(size: 12)).foregroundStyle(.secondary)
                if store.detail?.state != "no_content" {
                    Button(L("重新读取")) { Task { await store.refresh(reload: true) } }.buttonStyle(.bordered)
                }
            }.frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }

    private var statusBar: some View {
        HStack(spacing: 8) {
            if let error = store.error { Text(L(error)).foregroundStyle(.red) }
            else if store.detail?.requiresReload == true || (!isEmptyReasoning && store.detail?.state != "available" && store.detail?.state != "no_content") {
                Image(systemName: "info.circle"); Text(detailNotice)
            }
            else {
                Text(L(store.detail?.source == "synthetic" ? "合成预览记录" :
                       store.detail?.source == "observed_metadata" ? "本地观测的活动元数据" : "Codex 保留的活动记录"))
            }
            Spacer(minLength: 8)
            if store.detail?.requiresReload == true || store.error != nil ||
               (store.detail != nil && !["available", "no_content", "partial", "source_truncated"].contains(store.detail!.state)) {
                Button(L("重新读取")) { Task { await store.refresh(reload: true) } }.buttonStyle(.plain)
            }
            if let related = store.phase.related_phase_ids?.first,
               let phase = store.timeline.bundle?.allActivities.first(where: { $0.id == related }) {
                Button(L("查看执行")) { onSelect(phase) }.buttonStyle(.plain)
            }
            if !store.followsContent && store.phase.isActive {
                Button(L("跟随内容")) { store.followsContent = true }.buttonStyle(.plain)
            }
        }.font(.system(size: 10)).foregroundStyle(.secondary)
    }
    private var durationText: String {
        let seconds = store.phase.duration(at: store.timeline.bundle?.now_ms ?? Date().timeIntervalSince1970 * 1000)
        if let seconds, seconds < 1 { return String(format: L("%d 毫秒"), Int((seconds * 1000).rounded())) }
        return MetricFormat.duration(seconds)
    }
    private var selectedSection: ActivitySection? { store.detail?.sections.first { $0.id == store.selectedSection } }
    private var isEmptyReasoning: Bool {
        guard store.phase.kind == "reasoning", store.error == nil, let detail = store.detail else { return false }
        return detail.sections.isEmpty && detail.attachments.isEmpty &&
            ["available", "no_content", "content_unavailable", "pending"].contains(detail.state)
    }
    private var ownerName: String {
        if store.phase.thread_id == store.timeline.thread { return L("主智能体") }
        return store.timeline.bundle?.agents.first { $0.thread_id == store.phase.thread_id }?.displayName ?? L("所属智能体") }
    private var detailNotice: String {
        if store.detail?.version == 1 && store.detail?.requiresReload == true {
            return L("当前来源不支持继续加载，请更新 Codex Activity Monitor 后重新读取。")
        }
        return switch store.detail?.state {
        case "content_changed": L("源内容已变化。保留已加载内容，请重新读取。")
        case "source_truncated": L("来源已截断，以下是 Codex 保留的全部可用内容。")
        case "partial": L("当前内容为部分观测，完成后将从历史补齐。")
        case "pending": L("完整内容尚未写入历史。")
        case "missing": L("源历史已移除或当前内容不可用。")
        case "history_unavailable": L("当前找不到可读取的源历史。")
        case "content_unavailable": L("源历史中尚无这项活动的可用内容，可稍后重新读取。")
        case "no_content": L("来源未提供可查看内容，活动状态与时间见上方。")
        case "not_retained": L("这段未完成的内容未被 Codex 历史保留。")
        case "unavailable": L("暂时无法读取源历史，可稍后重试。")
        case "unsupported": L("当前来源不支持读取此类内容。")
        case "available": ""
        default: L("正在读取活动内容…")
        }
    }
}

private struct AttachmentPreview: LocalizedView {
    @Environment(\.monitorLanguage) var language
    let path: String
    @State private var image: NSImage?
    var body: some View {
        Group {
            if let image {
                Image(nsImage: image).resizable().scaledToFit().frame(maxWidth: 360, maxHeight: 140).accessibilityLabel(L("活动图片"))
            } else { Text(path).font(.system(size: 11)).textSelection(.enabled) }
        }.task(id: path) {
            guard path.hasPrefix("/"), ["png", "jpg", "jpeg", "webp", "heic", "gif"].contains(URL(fileURLWithPath: path).pathExtension.lowercased()) else { return }
            let data = try? await Task.detached(priority: .utility) { try Data(contentsOf: URL(fileURLWithPath: path)) }.value
            guard !Task.isCancelled else { return }
            image = data.flatMap { NSImage(data: $0) }
        }
    }
}

struct ActivityTextView: NSViewRepresentable {
    let text: String
    let format: String
    @Binding var followsContent: Bool

    func makeCoordinator() -> Coordinator { Coordinator() }
    func makeNSView(context: Context) -> NSScrollView {
        let scroll = NSScrollView()
        scroll.hasVerticalScroller = true; scroll.autohidesScrollers = true; scroll.borderType = .noBorder
        let view = NSTextView()
        view.isEditable = false; view.isSelectable = true; view.isRichText = false
        view.drawsBackground = false; scroll.drawsBackground = false
        view.textContainerInset = NSSize(width: 18, height: 14)
        view.isVerticallyResizable = true; view.isHorizontallyResizable = false
        view.autoresizingMask = [.width]
        view.textContainer?.widthTracksTextView = true
        scroll.documentView = view
        scroll.contentView.postsBoundsChangedNotifications = true
        context.coordinator.observation = NotificationCenter.default.addObserver(forName: NSView.boundsDidChangeNotification,
            object: scroll.contentView, queue: .main) { [weak scroll, weak coordinator = context.coordinator] _ in
                MainActor.assumeIsolated {
                    guard let scroll, let coordinator, !coordinator.updating else { return }
                    let bottom = max(0, (scroll.documentView?.frame.height ?? 0) - scroll.contentSize.height)
                    coordinator.onFollow?(scroll.contentView.bounds.minY >= bottom - 4)
                }
            }
        return scroll
    }
    func updateNSView(_ scroll: NSScrollView, context: Context) {
        guard let view = scroll.documentView as? NSTextView else { return }
        let coordinator = context.coordinator
        coordinator.updating = true; defer { coordinator.updating = false }
        coordinator.onFollow = { followsContent = $0 }
        let oldOrigin = scroll.contentView.bounds.origin
        if view.string != text || coordinator.format != format {
            let selection = view.selectedRanges
            view.font = format == "text" ? .systemFont(ofSize: 13) : .monospacedSystemFont(ofSize: 12, weight: .regular)
            view.textColor = .labelColor
            view.string = text
            coordinator.format = format
            if format == "diff" {
                let full = text as NSString
                view.textStorage?.addAttribute(.foregroundColor, value: NSColor.labelColor, range: NSRange(location: 0, length: full.length))
                var location = 0
                for line in text.components(separatedBy: "\n") {
                    let length = (line as NSString).length
                    let color: NSColor? = line.hasPrefix("+") ? .systemGreen : line.hasPrefix("-") ? .systemRed : nil
                    if let color, length > 0 { view.textStorage?.addAttribute(.foregroundColor, value: color, range: NSRange(location: location, length: length)) }
                    location += length + 1
                }
            }
            let maxLength = (text as NSString).length
            let validSelection = selection.filter { $0.rangeValue.upperBound <= maxLength }
            view.selectedRanges = validSelection.isEmpty ? [NSValue(range: NSRange(location: 0, length: 0))] : validSelection
            view.layoutManager?.ensureLayout(for: view.textContainer!)
        }
        if followsContent { view.scrollToEndOfDocument(nil) }
        else { scroll.contentView.scroll(to: oldOrigin); scroll.reflectScrolledClipView(scroll.contentView) }
    }
    static func dismantleNSView(_ nsView: NSScrollView, coordinator: Coordinator) {
        if let observation = coordinator.observation { NotificationCenter.default.removeObserver(observation) }
        coordinator.onFollow = nil
    }
    @MainActor final class Coordinator {
        var observation: NSObjectProtocol?
        var updating = false
        var format = ""
        var onFollow: ((Bool) -> Void)?
    }
}

private struct ViewerLanguageScope<Content: View>: View {
    let dashboard: DashboardStore?
    @ViewBuilder var content: () -> Content
    var body: some View { content().environment(\.monitorLanguage, dashboard?.language ?? .chinese).environment(\.locale, (dashboard?.language ?? .chinese).locale) }
}
