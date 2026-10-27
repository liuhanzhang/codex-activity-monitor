"""Export static images from the 2026-10-27 captures for the bilingual README.

Requires Pillow and the original local captures. Crop coordinates are
specific to this recording; review them when replacing the source captures.
"""

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps


FONT = "/System/Library/Fonts/Helvetica.ttc"
REPOSITORY = Path(__file__).resolve().parents[2]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def wallpaper(source, size):
    # Discard the desktop menu bar; only the captured wallpaper frames the UI.
    return ImageOps.fit(source.crop((0, 80, source.width, source.height)), size)


def paste_crop(canvas, source, box, size, position):
    crop = source.crop(box).resize(size, Image.Resampling.LANCZOS)
    canvas.paste(crop, position)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--captures", required=True, type=Path)
    parser.add_argument("--output", default=REPOSITORY / "docs/assets/showcase", type=Path)
    args = parser.parse_args()
    captures = args.captures.resolve()
    output = args.output.resolve()
    names = ("zoom-frame.png", "native-result.png", "launch-settled.png")
    for name in names:
        if not (captures / name).is_file():
            parser.error(f"Missing local capture: {captures / name}")
    output.mkdir(parents=True, exist_ok=True)
    with Image.open(captures / "zoom-frame.png") as source, \
            Image.open(captures / "launch-settled.png") as background:
        if source.size != (3456, 2234):
            parser.error("This export expects the original 3456 × 2234 desktop capture")
        source = source.convert("RGB")
        background = background.convert("RGB")
        source.crop((2100, 0, 3456, 1200)).save(
            output / "desktop-overview.jpg", quality=94, subsampling=0)
        speed = wallpaper(background, (1000, 720))
        status = source.crop((2564, 10, 2696, 56)).resize((264, 92), Image.Resampling.LANCZOS)
        status_mask = Image.new("L", status.size)
        ImageDraw.Draw(status_mask).rounded_rectangle((0, 0, 263, 91), radius=46, fill=255)
        speed.paste(status, (368, 40), status_mask)
        paste_crop(speed, source, (2306, 110, 3146, 580), (900, 504), (50, 172))
        speed.save(output / "speed-overview.jpg", quality=94, subsampling=0)
        timeline = wallpaper(background, (1000, 720))
        paste_crop(timeline, source, (2306, 580, 3146, 1080), (900, 536), (50, 92))
        timeline.save(output / "timeline.jpg", quality=94, subsampling=0)

        with Image.open(captures / "native-result.png") as native:
            detail = native.crop((0, 64, native.width, min(1000, native.height - 64))).convert("RGB")
        framed = wallpaper(background, (detail.width + 96, detail.height + 96))
        framed.paste(detail, (48, 32))
        ImageDraw.Draw(framed).text((52, framed.height - 37), "Demonstration data",
                                   font=ImageFont.truetype(FONT, 17), fill="white")
        framed.save(output / "activity-detail.jpg", quality=94, subsampling=0)

    with Image.open(REPOSITORY / "macos/Assets/AppIcon.png") as icon:
        icon.resize((192, 192), Image.Resampling.LANCZOS).save(output / "app-icon.png")

    provenance = {
        "version": 2,
        "date": "2026-10-27",
        "language": "en",
        "source": "Native read-only preview, entirely invented developer tasks and numeric values",
        "not_a_benchmark": True,
        "fixture": "experiments/readme-demo/preview.py",
        "fixture_sha256": digest(REPOSITORY / "experiments/readme-demo/preview.py"),
        "exporter": "experiments/readme-demo/export_desktop.py",
        "desktop_recording_build_id": "43bfe5b812ce2cf791254e3c88c1f554c441a96bcfa2a65b3b8426ef41f70662",
        "detail_capture_build_id": None,
        "capture": {
            "desktop_overview": "Source second 42 of a macOS native recording of the built-in display; 3456 × 2234",
            "activity_detail": "Separate native-window screenshot via cua_repl",
            "raw_captures": "Local ignored files; not distributed in this repository",
        },
        "edits": {
            "desktop-overview.jpg": "Crop source frame x=2100,y=0,w=1356,h=1200; preserve native controls and values; no presentation-text overlay",
            "speed-overview.jpg": "Reframe native menu bar crop (2564,10,132,46) and overview crop (2306,110,840,470) on captured wallpaper; magnify the menu bar crop and mask its rounded boundary",
            "timeline.jpg": "Reframe native timeline crop (2306,580,840,500) on captured wallpaper",
            "activity-detail.jpg": "Crop native window to remove OS sharing chrome, preview footer and unused lower space; frame on captured wallpaper",
            "app-icon.png": "Resize the existing repository AppIcon.png to 192 × 192",
        },
        "source_sha256": {name: digest(captures / name) for name in names},
        "assets": {p.name: {"bytes": p.stat().st_size, "sha256": digest(p)}
                   for p in sorted(output.iterdir())
                   if p.name in {"desktop-overview.jpg", "speed-overview.jpg", "timeline.jpg",
                                 "activity-detail.jpg", "app-icon.png"}},
    }
    (output / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    print(json.dumps({"output": str(output), "assets": provenance["assets"]}, indent=2))


if __name__ == "__main__":
    main()
