import Foundation

/// One immutable graph per decoded state. No clock, language, window or store
/// reference is retained here. A heartbeat reuses the same graph instance.
public final class SnapshotProjection: Sendable {
    public struct Selection: Sendable {
        public let members: [Chat]
        public let roots: [Chat]
        public let unassigned: [Chat]
        /// Seconds after accepting this snapshot when the selected recent list changes.
        public let nextExpiry: Double?
        public let unassignedIDs: Set<String>
    }

    public struct Cost: Sendable {
        public let nodes: Int
        public let edges: Int
        public let parentCandidates: Int
        public let ancestorSteps: Int
        public let cycleSteps: Int
    }

    private struct Group: Sendable {
        let chat: Chat
        var active = false
        var age = Double.infinity
        var terminal = false
        var nonIdle = false
    }

    public let cost: Cost
    private let chats: [Chat]
    private let byID: [String: Chat]
    private let childrenByID: [String: [Chat]]
    private let rootByID: [String: String]
    private let subtreeActive: Set<String>
    private let activeRoots: [Chat]
    private let recentRoots: [Group]
    private let activeUnassigned: [Chat]
    private let recentUnassigned: [Group]
    private let selectedRoots: Set<String>
    private let memberIDs: Set<String>
    public let initial: Selection

    public init(threads: [Chat]) {
        let chats = threads.filter { $0.ephemeral != true }
        let byID = Dictionary(threads.map { ($0.id, $0) }, uniquingKeysWith: { first, _ in first })
        let nodes = Dictionary(chats.map { ($0.id, $0) }, uniquingKeysWith: { first, _ in first })
        let counts = Dictionary(grouping: chats, by: \.id).mapValues(\.count)
        var spawnParents: [String: Set<String>] = [:]
        let spawn: Set<String> = ["spawnAgent", "spawn_agent", "spawn"]
        for parent in chats {
            for (child, operation) in parent.subagents ?? [:] where spawn.contains(operation) && child != parent.threadID {
                spawnParents[parent.scope + "\u{0}" + child, default: []].insert(parent.id)
            }
        }
        var edges: [String: String] = [:]
        var parentCandidates = 0
        for child in chats where child.thread_source != "user" && !(child.parent_quality ?? []).contains("parent_identity_conflict") {
            if let parent = child.parent_thread_id {
                let id = child.scope + "\u{0}" + parent
                parentCandidates += 1
                if id != child.id, counts[id] == 1 { edges[child.id] = id }
            } else {
                let parents = spawnParents[child.id] ?? []
                parentCandidates += parents.count
                if parents.count == 1, let id = parents.first, counts[id] == 1 { edges[child.id] = id }
            }
        }

        // The previous contract quarantined both cycles and paths entering a
        // cycle. Finish each path once instead of walking every ancestor again.
        var finished: Set<String> = [], invalid: Set<String> = []
        var cycleSteps = 0
        for id in nodes.keys where !finished.contains(id) {
            var path: [String] = [], visiting: Set<String> = []
            var cursor: String? = id
            while let current = cursor, !finished.contains(current), visiting.insert(current).inserted {
                path.append(current); cycleSteps += 1; cursor = edges[current]
            }
            let blocked = cursor.map { visiting.contains($0) || invalid.contains($0) } ?? false
            finished.formUnion(path)
            if blocked { invalid.formUnion(path) }
        }
        for id in invalid { edges.removeValue(forKey: id) }

        var childIDs: [String: [String]] = [:]
        for chat in chats { if let parent = edges[chat.id] { childIDs[parent, default: []].append(chat.id) } }
        var roots: [String: String] = [:], traversal: [String] = []
        var stack = chats.filter { edges[$0.id] == nil }.map { ($0.id, $0.id) }
        while let (id, root) = stack.popLast() {
            guard roots[id] == nil else { continue }
            roots[id] = root; traversal.append(id)
            for child in childIDs[id] ?? [] { stack.append((child, root)) }
        }
        var eligible = Set(chats.filter { $0.visible != false }.map(\.id))
        var ancestorSteps = 0
        for id in Array(eligible) {
            var cursor = edges[id]
            while let parent = cursor, eligible.insert(parent).inserted {
                ancestorSteps += 1; cursor = edges[parent]
            }
        }
        var groups: [String: Group] = [:]
        for chat in chats where eligible.contains(chat.id) {
            guard let root = roots[chat.id], let parent = nodes[root] else { continue }
            var group = groups[root] ?? Group(chat: parent)
            group.active = group.active || chat.has_active_work == true
            group.age = min(group.age, chat.last_progress_seconds ?? .infinity)
            group.terminal = group.terminal || chat.isTerminal
            group.nonIdle = group.nonIdle || chat.status != "idle"
            groups[root] = group
        }
        var active = Set(chats.filter { $0.has_active_work == true }.map(\.id))
        for id in traversal.reversed() where active.contains(id) {
            if let parent = edges[id] { active.insert(parent) }
        }
        let candidates = chats.filter { eligible.contains($0.id) && edges[$0.id] == nil }.compactMap { groups[$0.id] }
        let main = candidates.filter { !$0.chat.isKnownSubagent }
        let uncertain = candidates.filter { $0.chat.isKnownSubagent }
        let activeRoots = main.filter(\.active).map(\.chat)
        let recentRoots = main.filter { !$0.active && $0.terminal }.sorted { $0.age < $1.age }
        let activeUnassigned = uncertain.filter(\.active).map(\.chat)
        let recentUnassigned = uncertain.filter { !$0.active && $0.nonIdle }.sorted { $0.age < $1.age }
        self.chats = chats; self.byID = byID; self.rootByID = roots; self.subtreeActive = active
        self.childrenByID = childIDs.mapValues { $0.compactMap { nodes[$0] } }
        self.activeRoots = activeRoots; self.recentRoots = recentRoots
        self.activeUnassigned = activeUnassigned; self.recentUnassigned = recentUnassigned
        let initial = Self.select(chats: chats, rootByID: roots, activeRoots: activeRoots, recentRoots: recentRoots,
            activeUnassigned: activeUnassigned, recentUnassigned: recentUnassigned, elapsed: 0)
        self.initial = initial
        self.selectedRoots = Set((initial.roots+initial.unassigned).map(\.id))
        self.memberIDs = Set(initial.members.map(\.id))
        self.cost = Cost(nodes: chats.count, edges: edges.count, parentCandidates: parentCandidates,
                         ancestorSteps: ancestorSteps, cycleSteps: cycleSteps)
    }

    private static func select(chats: [Chat], rootByID: [String: String], activeRoots: [Chat], recentRoots: [Group],
                               activeUnassigned: [Chat], recentUnassigned: [Group], elapsed: Double) -> Selection {
        let recent = Array(recentRoots.filter { $0.age+elapsed < 600 }.prefix(10))
        let uncertain = Array(recentUnassigned.filter { $0.age+elapsed < 600 }.prefix(10))
        let roots = activeRoots + recent.map(\.chat)
        let unassigned = activeUnassigned + uncertain.map(\.chat)
        let selected = Set((roots + unassigned).map(\.id))
        let members = chats.filter { rootByID[$0.id].map { selected.contains($0) } ?? false }
        return Selection(members: members, roots: roots, unassigned: unassigned,
            nextExpiry: (recent+uncertain).map { 600-$0.age }.min(), unassignedIDs: Set(unassigned.map(\.id)))
    }

    public func selection(elapsed: Double) -> Selection {
        if elapsed <= 0 || initial.nextExpiry.map({ elapsed < $0 }) != false { return initial }
        return Self.select(chats: chats, rootByID: rootByID, activeRoots: activeRoots, recentRoots: recentRoots,
            activeUnassigned: activeUnassigned, recentUnassigned: recentUnassigned, elapsed: elapsed)
    }

    public func chat(id: String) -> Chat? { byID[id] }
    public func children(of parent: Chat) -> [Chat] {
        guard memberIDs.contains(parent.id) else { return [] }
        return childrenByID[parent.id] ?? []
    }
    public func hasActiveWork(in root: Chat) -> Bool {
        subtreeActive.contains(root.id) && rootByID[root.id].map { selectedRoots.contains($0) } == true
    }

    public func rows(roots: [Chat], expanded: Set<String>, order: [String: Int] = [:]) -> [ChatRow] {
        var result: [ChatRow] = []
        var stack = roots.reversed().map { ($0, 0) }
        while let (chat, depth) = stack.popLast() {
            result.append(ChatRow(chat: chat, depth: depth))
            if expanded.contains(chat.id) {
                let children = children(of: chat).sorted { (order[$0.id] ?? Int.max) < (order[$1.id] ?? Int.max) }
                stack.append(contentsOf: children.reversed().map { ($0, depth+1) })
            }
        }
        return result
    }
}
