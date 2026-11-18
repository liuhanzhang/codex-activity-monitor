# Application icon

`AppIcon.png` is the approved speedometer artwork, generated with ImageGen.
The white arc shows the swept range; the gray arc shows the remaining range.

Both `bin/build-gui` (including Preview) and `packaging/build_app.py` invoke
`bin/build-app-icon` to produce `Contents/Resources/AppIcon.icns` and set
`CFBundleIconFile`. The converter uses macOS `sips` and `iconutil` to preserve
transparency and include 16, 32, 128, 256, and 512 point representations at 1x
and 2x. Generated icon sets are temporary; the PNG is the source of truth.

Settings uses the 64 px app icons in `Sources/MonitorApp/Resources/AgentLogos`
to identify the integrations. The Codex image comes from OpenAI's `icon-codex-light.png` and
the Antigravity image from Google Antigravity's `icon.icns`. Both were resized
from the macOS app resources. The respective owners retain their marks.

Chat rows use the transparent `agent-mark-*` variants. The Codex mark comes
from OpenAI’s bundled `codex-app-ga-logo` artwork. The Antigravity mark comes
from [Google’s Antigravity logo](https://antigravity.google/assets/image/antigravity-logo.png).
