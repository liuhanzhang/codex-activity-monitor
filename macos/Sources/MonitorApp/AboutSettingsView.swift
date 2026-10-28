import AppKit
import SwiftUI
import MonitorModel

struct AboutSettingsView: LocalizedView {
    let language: MonitorLanguage
    @Bindable var updates: UpdateStore
    private let info = Bundle.main.infoDictionary ?? [:]
    private let resources = Bundle.main.resourceURL
    private let project = URL(string: "https://github.com/LiuHanzhang/codex-activity-monitor")!

    private var version: String {
        (info["CAMReleaseVersion"] as? String) ?? (info["CFBundleShortVersionString"] as? String) ?? "—"
    }

    var body: some View {
        Form {
            Section {
                HStack(spacing: 16) {
                    Image(nsImage: NSApplication.shared.applicationIconImage)
                        .resizable().scaledToFit().frame(width: 64, height: 64).accessibilityHidden(true)
                    VStack(alignment: .leading, spacing: 5) {
                        Text("Codex Activity Monitor").font(.title2).fontWeight(.semibold)
                        Text(L("在菜单栏查看 Codex 的实时活动与生成速度。"))
                            .foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                    }
                }.padding(.vertical, 8)
                LabeledContent(L("版本"), value: version)
                    .accessibilityIdentifier("monitor.about.version")
                LabeledContent(L("构建号"), value: info["CFBundleVersion"] as? String ?? "—")
                if let distribution = info["CAMDistribution"] as? String, distribution != "standalone" {
                    Text(L(distribution == "preview" ? "这是只读预览构建。" : "这是本地源码构建。"))
                        .font(.callout).foregroundStyle(.secondary)
                }
            }
            Section(L("软件更新")) {
                if let channel = info["CAMUpdateChannel"] as? String {
                    LabeledContent(L("更新范围"), value: L(channel == "prerelease" ? "正式发布与预发布" : "仅正式发布"))
                }
                Toggle(L("自动检查更新"), isOn: Binding(get: { updates.automatic }, set: { updates.setAutomatic($0) }))
                    .disabled(!updates.enabled).accessibilityIdentifier("monitor.updates.automatic")
                Text(L("每天检查更新，有新版本时提示下载。不上传聊天、日志或使用记录。"))
                    .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                HStack {
                    Button(L("检查更新")) { updates.check() }
                        .disabled(!updates.enabled || !updates.canCheck).accessibilityIdentifier("monitor.updates.check")
                    if updates.checking { ProgressView().controlSize(.small).accessibilityLabel(L("正在检查更新…")) }
                }
                if let release = updates.available {
                    UpdateAvailableView(release: release, language: language, onOpen: updates.check)
                }
                if let key = updates.statusKey {
                    Text(L(key)).font(.callout).foregroundStyle(.secondary)
                    Link(L("手动查看发行页"), destination: project.appendingPathComponent("releases"))
                }
                if let date = updates.lastChecked {
                    LabeledContent(L("最近检查"), value: date.formatted(.dateTime.year().month().day().hour().minute().locale(language.locale)))
                }
                if !updates.enabled { Text(L("预览和隔离验证不会联网检查更新。")) .font(.callout).foregroundStyle(.secondary) }
            }
            Section(L("项目")) {
                Link(L("项目主页"), destination: project)
                Link(L("发行说明"), destination: project.appendingPathComponent("releases"))
                Link(L("问题反馈"), destination: project.appendingPathComponent("issues"))
            }
            Section(L("许可")) {
                if let license = localResource("LICENSE") {
                    Button(L("MIT 许可证")) { NSWorkspace.shared.open(license) }
                } else {
                    Link(L("MIT 许可证"), destination: project.appendingPathComponent("blob/main/LICENSE"))
                }
                if let licenses = localResource("ThirdPartyLicenses") {
                    Button(L("第三方许可")) { NSWorkspace.shared.open(licenses) }
                }
            }
        }.formStyle(.grouped).textSelection(.enabled)
            .accessibilityIdentifier("monitor.settings.about")
    }

    private func localResource(_ name: String) -> URL? {
        guard let url = resources?.appendingPathComponent(name), FileManager.default.fileExists(atPath: url.path) else { return nil }
        return url
    }
}

struct UpdateAvailableView: LocalizedView {
    let release: UpdateRelease
    let language: MonitorLanguage
    var onOpen: () -> Void
    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: "arrow.down.circle")
            Text(String(format: L("有新版本 %@"), release.version))
                .frame(maxWidth: .infinity, alignment: .leading)
            Button(L("查看更新"), action: onOpen)
        }.accessibilityIdentifier("monitor.updates.available")
    }
}
