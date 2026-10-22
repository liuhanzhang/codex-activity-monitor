import SwiftUI
import MonitorModel

struct RestoreSheet: LocalizedView {
    var language: MonitorLanguage { store.language }
    let store: DashboardStore
    @Environment(\.dismiss) private var dismiss
    @State private var purge = false

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Label(L("恢复 Codex 配置"), systemImage: "arrow.uturn.backward").font(.headline)
            Text(L("恢复接入前的配置并停止采集。用户后来的设置会保留，Codex 任务不会被强制结束。"))
                .font(.callout).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            Toggle(L("清理本机数据并退出，用于卸载"), isOn: $purge)
            Text(purge ? L("等待 Codex 安全退出并完成清理后，Monitor 将退出。之后可手动删除 App；再次打开会按首次启动处理。") :
                 L("保留 App 设置与已恢复状态。以后打开仍保持未接入，需要时点击配置即可重新启用。"))
                .font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            HStack {
                Spacer()
                Button(L("取消")) { dismiss() }.keyboardShortcut(.cancelAction)
                Button(L("恢复配置")) {
                    let shouldPurge = purge
                    dismiss()
                    Task { await store.restore(purge: shouldPurge) }
                }.buttonStyle(.glassProminent).keyboardShortcut(.defaultAction)
            }
        }.padding(20).frame(width: 420).fixedSize(horizontal: false, vertical: true)
            .presentationSizing(.fitted)
    }
}
