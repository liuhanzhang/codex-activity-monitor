# Application icon

`AppIcon.png` is the approved speedometer artwork, generated with ImageGen.
The white arc shows the swept range; the gray arc shows the remaining range.

Both `bin/build-gui` (including Preview) and `packaging/build_app.py` invoke
`bin/build-app-icon` to produce `Contents/Resources/AppIcon.icns` and set
`CFBundleIconFile`. The converter uses macOS `sips` and `iconutil` to preserve
transparency and include 16, 32, 128, 256, and 512 point representations at 1x
and 2x. Generated icon sets are temporary; the PNG is the source of truth.
