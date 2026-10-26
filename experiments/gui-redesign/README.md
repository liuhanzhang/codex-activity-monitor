# 紧凑 GUI 的合成原生预览

`preview.py` 只生成合成快照，不读取日常聊天。独立 Preview App 使用不同 bundle ID，默认只读，管理操作禁用，不拥有日常观察会话。构建仍复用本机 Swift 工具链及 venv。

```sh
bin/build-gui --preview
python3 experiments/gui-redesign/preview.py --case multiple \
  --output .local/gui-preview/fixtures/snapshot.json --keep-fresh
```

另一个终端打开原生预览：

```sh
open '.local/gui-preview/Codex Monitor Preview.app' --args \
  --snapshot "$PWD/.local/gui-preview/fixtures/snapshot.json" --show
```

需要切换参数时，从预览自身设置菜单正常退出，再打开；不要退出日常 Monitor。`open` 不会为已有进程重载参数。

持续写入时，脚本每 0.5 秒原子替换 JSON；可以修改同目录的 `case.txt` 切换样本：

```sh
printf '%s\n' thinking > .local/gui-preview/fixtures/case.txt
```

| 样本 | 内容 |
| --- | --- |
| `multiple` / `aggregate` | 两个独立聊天，总速 48.2 / 126.3 |
| `empty` / `idle` | 明确空闲，菜单栏显示静态沙滩图标，面板仍为 `0 T/s` |
| `waiting` / `slow` / `generating` / `fast` | 活动聊天等待时的 `0`，以及 `9 / 48 / 9999 T/s` |
| `thinking` / `executing` | 单聊天的三点 / 锤子状态 |
| `huge` | 极端有限数值 `1e100`，检查有界显示与完整悬停值 |
| `partial` | 总速未知；面板保留明确标注的已观测小计 |
| `long` | 12 条长标题及长项目，检查滚动上限 |
| `tree` | 父已完成且超出近期时间，两个子代理仍活跃，算一个主聊天 |

可选参数只影响只读 / 隔离验证 App：

- `--preview-light` / `--preview-dark`：仅改变该 App 外观。
- `--preview-status`：在面板增加与菜单栏等宽的原尺寸状态渲染器（默认 47 pt，更长数值按需扩宽），便于捕捉动效；普通产品不显示此行。
- `--preview-reduce-motion`：强制静态动效状态，不修改系统设置。
- `--preview-recent`：初始展开最近完成分组。
- `--window --preview-fullscreen --preview-autohide`：用预览自身窗口进入全屏、临时隐藏菜单栏和 Dock；退出后系统自动恢复，不写系统偏好。
- `--ui-diagnostics /绝对路径/events.jsonl`：可选记录该 App 的 frame、模式、构建指纹和监听器数量，不记录聊天正文；不启用则不写诊断日志。

`--ui-diagnostics` 也记录菜单栏动画驱动、目标帧率、所在屏幕最大刷新率，以及当前状态内累计的同步回调 / 不同显示时间的绘制数量与平均频率。`draw_calls` 是原始绘制调用数量，AppKit 对同一姿态的多次绘制不重复算作新动画帧。平均频率不能单独证明没有偶发卡顿。

在内屏进行约 10 秒的独立原生采样（短暂显示测试窗口，不接入采集、不创建管理租约，结束自动退出）：

```sh
'.local/gui-preview/Codex Monitor Preview.app/Contents/MacOS/MonitorApp' \
  --probe-status-cadence "$PWD/.local/animation-120hz/builtin-cadence.json"
```

使用同一个 `StatusContentView`，分别采样思考 / 执行约 4 秒，再检查减少动态效果和静态空闲。记录当前屏幕、实际回调 / 绘制频率及停止状态；优先选择内屏，没有内屏时使用主屏。

停止脚本用 `Ctrl-C`，退出预览用其设置菜单。无 `--keep-fresh` 时只写一次；快照三秒后过期，菜单栏降为 `—`。合成快照直接指定展示状态，证明布局与渲染；核心状态判断由契约测试覆盖，不能从预览倒推真实事件语义。测试输出放在忽略目录 `.local/`。


当前沙滩图标的原生离屏导出（共用日常绘制器，47×22 pt、2x，不配置接入）：

```sh
'.local/gui-preview/Codex Monitor Preview.app/Contents/MacOS/MonitorApp' \
  --render-idle-beach-previews "$PWD/.local/idle-beach-native-preview"
```

输出浅色、深色、减少动态效果各一帧和 `render-summary.json`；沙滩图标本身无动效。

锤子角度与曲线的原生采样导出：

```sh
'.local/gui-preview/Codex Monitor Preview.app/Contents/MacOS/MonitorApp' \
  --render-hammer-previews "$PWD/.local/hammer-tuning/native"
```

每种外观导出 7 个时刻，覆盖静止、抬起、敲下、回弹和循环停顿；`frame_times_seconds` 记录实际采样时刻。减少动态效果时 7 帧相同。这是姿态采样，不是均匀帧率的视频。

小狗现仅作为实验素材保留，普通界面不显示，也不提供切换选项。8 帧透明资源及来源记录位于 `macos/Sources/MonitorApp/Resources/IdlePet/`，已有放大动效预览位于 `dev-history/08_gui_ui_ux_redesign/assets/idle-pet-native-4x.gif`。

开发用原生离屏导出仍可复现小狗动效（共用绘制器，不配置接入）：

```sh
'.local/gui-preview/Codex Monitor Preview.app/Contents/MacOS/MonitorApp' \
  --render-idle-pet-previews "$PWD/.local/idle-pet-native-preview"
```

导出 `light`、`dark` 和 `reduced-motion` 各 8 帧 2x 位图，绘制区域为 47×22 pt，小狗高 18 pt。当前 macOS 沙箱若阻止访问 WindowServer，需要在能访问桌面会话的开发终端运行。透明资产由内置 image_gen 处理背景后，用 `prepare-idle-pet.py --sheet <透明图集> --output macos/Sources/MonitorApp/Resources/IdlePet` 包装；公共裁剪框保留帧间动作位置，不逐帧自动居中。资源图集哈希、帧哈希和参数随资源保存，生成提示见 `idle-pet-generation-prompt.txt`。
