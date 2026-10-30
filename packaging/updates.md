# Sparkle 更新源与发布步骤

当前模式：Sparkle 2.10.1 自动检查、应用内提醒、标准更新窗口、网页手动下载。信息更新按钮显示 Download（中文“下载”），点击后打开对应版本的 GitHub Release 页面。不会下载或安装更新包，不会重启 App 或 Codex。

## 配置位置

- `macos/Package.swift` 和 `macos/Package.resolved`：固定 SDK 与依赖 revision。
- `pyproject.toml`：版本、显示后缀、独立发布分类、递增构建号、公钥与更新源基础地址。
- `packaging/app_metadata.py`：统一生成 `SUFeedURL`、`SUDefaultsDomain`、每日检查、禁止自动安装与关闭可选统计等 Info.plist 字段。
- `packaging/embed_sparkle.py`：嵌入 framework、保留符号链接、arm64 裁剪、内到外签名与第三方许可。
- `website/updates/releases.json`：已发布发行记录。机器比较构建号，界面显示完整版本；每次发行的构建号必须全局递增。
- `bin/generate-appcast`：生成稳定与预发布清单，或检查生成结果、验证 GitHub Release 和 arm64 DMG／ZIP 存在。

配置的 HTTPS 地址：

- 正式版：`https://liuhanzhang.github.io/codex-activity-monitor/updates/appcast.xml`
- 预发布版：`https://liuhanzhang.github.io/codex-activity-monitor/updates/appcast-prerelease.xml`

Pages 已于 2026-10-28 上线；三个地址（含旧兼容地址）通过无鉴权 HTTPS 内容核对。发行与 SDK 验证见[公网验收结果](../dev-history/26_about_and_updates/channel-release-results.md)。

## 首次启用 Pages

1. 检查本轮方案与实际结果，并按维护者的提交／推送约定将文件同步至 GitHub。
2. 在仓库 Settings → Pages 的 Source 选择 **GitHub Actions**，或由有权的维护者使用 Pages API 配置 `build_type=workflow`。
3. 运行 **Publish update feeds** workflow；它只上传 `website/`，不会上传 `.local/`、源码运行数据、凭证或签名私钥。
4. 检查 workflow 成功后，以无鉴权 HTTPS 请求读取两个清单，验证内容与本地生成结果一致。再使用正式身份的本地测试构建验证一次实际 HTTPS 检查。

部署使用 `GITHUB_TOKEN` 的 `pages:write` 和 `id-token:write`。源仓库只需 `contents:read`；App 不持有 GitHub token。Workflow 的 Actions 已固定 commit SHA。

## 后续发布

1. 在 `pyproject.toml` 更新版本，并递增 `build-number`。alpha.2 至 alpha.5 分别为构建 `2` 至 `5`，0.1.0 正式版使用构建 `6`。构建号可以跳号，但不能复用旧号发布新内容；将预发布转为正式版时也须递增。
2. 构建 DMG，检查自动验证记录，并运行 DMG 隔离验收，确认包内 `CFBundleVersion` 与记录的构建号一致。
3. 发布 GitHub Release，上传 `Codex-Activity-Monitor-{完整版本}-macos-arm64.dmg` 和 `SHA256SUMS.txt`；发行说明使用 DMG 拖拽安装步骤。
4. 将实际发行追加到 `website/updates/releases.json`，记录完整版本、包内构建号；分类和发布时间通过下一步从 GitHub 同步。两项初始值可先抄取刚发布的 Release，不能从版本后缀推断。
5. 运行：

   ```sh
   bin/generate-appcast --sync-releases
   bin/generate-appcast --check --verify-releases
   ```

6. 提交并推送清单，等待 Pages workflow 成功，最后用旧构建检查新版。

普通构建不会自动发布清单。部署前脚本会拒绝草稿、缺失的 Release／匹配安装包、重复构建号、非法版本及错误预发布分类；匹配安装包接受 DMG 或旧版 ZIP。清单没有 `<enclosure>`，客户端也拒绝安装类型的条目。预发布清单包含稳定发行，稳定清单过滤预发布，因此 alpha.5 客户端也能发现构建 `6` 的正式版。稳定清单在没有正式发行时为空；0.1.0 发布后两个频道均包含该版本。版本名称带 `alpha` 可以对应 GitHub 正式 Release；纯数字版本也可以标记为 Pre-release。旧客户端使用的 `appcast-alpha.xml` 保留为预发布清单的兼容副本。

## 密钥与偏好

本轮用 Sparkle 官方 `generate_keys --account com.liuhanzhang.codexactivitymonitor` 生成应用专用密钥。私钥保存在维护者 macOS 钥匙串；仓库只保存公钥，不导出私钥。可通过同一工具的 `-p` 查看公钥。当前通过 HTTPS 读取信息清单，未启用清单签名；公钥配置也为未来更新包验证预留。启用自动安装时须另行加入更新包签名、签名／公证发布流程与安装生命周期验收。

Sparkle 偏好存于 `{bundleID}.updates` 域；不同 bundle ID 不共享。首次启动只迁移旧 `updates.json` 的自动检查选择，不迁移旧 API 请求时间、旧版本提示或网络状态。卸载通过现有 `--login-remove` 清理更新偏好域。旧文件保留至卸载以兼容此前数据，应用不再写入它。

## 本地验收

```sh
bin/build-gui --preview
python3 tests/acceptance/verify_sparkle.py \
  --app '.local/gui-preview/Codex Activity Monitor Preview.app' \
  --output .local/sparkle-acceptance-new-run
```

`--feed-url` 可指向本项目三个固定公网 HTTPS 清单，配合 `--client-build`、`--client-version`、`--expected-version`、`--case` 复核生产内容。`--manual` 通过独立测试 App 的手动入口发起请求；默认使用真正的自动调度。原始安装包保持不变，测试只修改临时副本。

原生验收需要可访问桌面的 macOS 上下文，并在外层 sandbox 之外运行。脚本复制临时 App、生成独立 bundle ID／偏好域，默认用 loopback HTTP 清单测试真实 Sparkle，禁止读取 SwiftPM 构建目录。它不访问日常 Codex；只有显式提供 `--feed-url` 才读取允许的生产更新源，合成版本从不发布到 Pages。`--interactive` 可检查提示和标准更新窗口，Ctrl-C 退出并清理。脚本清理进程使用终止信号，与产品正常 GUI 退出验收是不同证据。

标准 Sparkle 窗口使用系统语言；Monitor 自身页面继续使用设置内的语言。实际结果与未验证边界见 [本轮结果](../dev-history/26_about_and_updates/sparkle-results.md)。

打包只覆盖 App 随包 Sparkle Base／简繁中文资源里的 `Learn More…` 按钮标题，不修改 SwiftPM checkout 或原始 artifact。覆盖先于签名；SDK 资源键变化时打包会失败，要求重新审查。窗口布局、稍后提醒、跳过及网页手动下载行为保持不变；其他系统语言沿用上游文案。
