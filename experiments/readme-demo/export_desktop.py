"""Export static images from the desktop captures and native 2x dashboard render for the bilingual README.

Requires Pillow and the local captures in .local/readme-desktop-preview/.
"""

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps


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


def build_composite_scene(source, background, dashboard_fg):
    canvas = source.copy()
    blur_ls = background.filter(ImageFilter.GaussianBlur(radius=60))
    pop_x, pop_y, pop_w, pop_h = 2306, 108, 840, 1032
    pop_bg = Image.new("RGBA", (pop_w, pop_h))
    for y in range(pop_h):
        for x in range(pop_w):
            b = blur_ls.getpixel((pop_x + x, pop_y + y))
            c = tuple(round(0.84 * c0 + 0.16 * b[i]) for i, c0 in enumerate((25, 35, 52))) + (255,)
            pop_bg.putpixel((x, y), c)
    pop_bg.alpha_composite(dashboard_fg)
    mask = Image.new("L", (pop_w, pop_h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((2, 2, pop_w - 3, pop_h - 3), radius=18, fill=255)
    canvas.paste(pop_bg.convert("RGB"), (pop_x, pop_y), mask)

    # Clean menu bar across x=2725..3186, y=0..64 using clean menu bar anchor columns
    anchors = [2725, 2795, 2923, 2987, 3072, 3152, 3186]
    sample_x = [2725, 2795, 2923, 2987, 3072, 3152, 3152]
    for y in range(0, 64):
        anchor_colors = []
        for sx in sample_x:
            cols = [source.getpixel((sx + dx, y)) for dx in range(-2, 3)]
            anchor_colors.append([sum(c[i] for c in cols) / len(cols) for i in range(3)])
        for seg in range(len(anchors) - 1):
            x0, x1 = anchors[seg], anchors[seg + 1]
            c0, c1 = anchor_colors[seg], anchor_colors[seg + 1]
            for x in range(x0, x1 + 1):
                t = (x - x0) / max(1, x1 - x0)
                c = tuple(round(c0[i] * (1 - t) + c1[i] * t) for i in range(3))
                canvas.putpixel((x, y), c)

    # Place clean macOS system menu bar items ([A], Control Center, Date/Time) right of 79 T/s
    for y in range(10, 52):
        for x in range(3000, 3412):
            p = source.getpixel((x, y))
            bg_top = source.getpixel((x, 7))
            bg_bot = source.getpixel((x, 56))
            t = (y - 7) / (56 - 7)
            bg = [bg_top[i] * (1 - t) + bg_bot[i] * t for i in range(3)]
            diffs = [max(0.0, (p[i] - bg[i]) / max(1.0, 245.0 - bg[i])) for i in range(3)]
            alpha = max(0.0, min(1.0, sum(diffs) / 3.0))
            if alpha > 0.03:
                tx = 2736 + (x - 3000)
                dst = canvas.getpixel((tx, y))
                blended = tuple(round(dst[i] * (1 - alpha) + 245 * alpha) for i in range(3))
                canvas.putpixel((tx, y), blended)
    return canvas


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--captures", required=True, type=Path)
    parser.add_argument("--output", default=REPOSITORY / "docs/assets/showcase", type=Path)
    args = parser.parse_args()
    captures = args.captures.resolve()
    output = args.output.resolve()
    names = ("zoom-frame.png", "native-result.png", "launch-settled.png", "dashboard-transparent.png")
    for name in names:
        if not (captures / name).is_file():
            parser.error(f"Missing local capture: {captures / name}")
    output.mkdir(parents=True, exist_ok=True)
    with Image.open(captures / "zoom-frame.png") as source, \
            Image.open(captures / "launch-settled.png") as background, \
            Image.open(captures / "dashboard-transparent.png") as dashboard_fg:
        if source.size != (3456, 2234):
            parser.error("This export expects the original 3456 × 2234 desktop capture")
        source = source.convert("RGB")
        background = background.convert("RGB")
        dashboard_fg = dashboard_fg.convert("RGBA")
        scene = build_composite_scene(source, background, dashboard_fg)
        scene.crop((1830, 0, 3186, 1200)).save(
            output / "desktop-overview.jpg", quality=94, subsampling=0)
        speed = wallpaper(background, (1000, 720))
        status = scene.crop((2564, 10, 2696, 56)).resize((264, 92), Image.Resampling.LANCZOS)
        status_mask = Image.new("L", status.size)
        ImageDraw.Draw(status_mask).rounded_rectangle((0, 0, 263, 91), radius=46, fill=255)
        speed.paste(status, (368, 40), status_mask)
        paste_crop(speed, scene, (2306, 110, 3146, 580), (900, 504), (50, 172))
        speed.save(output / "speed-overview.jpg", quality=94, subsampling=0)
        timeline = wallpaper(background, (1000, 720))
        paste_crop(timeline, scene, (2306, 580, 3146, 1080), (900, 536), (50, 92))
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
        "version": 3,
        "date": "2026-11-18",
        "language": "en",
        "source": "Native read-only preview, entirely invented developer tasks and numeric values",
        "not_a_benchmark": True,
        "fixture": "experiments/readme-demo/preview.py",
        "fixture_sha256": digest(REPOSITORY / "experiments/readme-demo/preview.py"),
        "exporter": "experiments/readme-demo/export_desktop.py",
        "desktop_recording_build_id": "512ddd2a52d8318e18ce42eb6367a4edbaad844daafa594c94c1829f2da0d2b0",
        "detail_capture_build_id": None,
        "capture": {
            "desktop_overview": "3456 × 2234 desktop backdrop with native 2x Retina DashboardView render and clean menu bar",
            "activity_detail": "Separate native-window screenshot via cua_repl",
            "raw_captures": "Local ignored files; not distributed in this repository",
        },
        "edits": {
            "desktop-overview.jpg": "Crop scene x=1830,y=0,w=1356,h=1200 with native v0.2.0 DashboardView and clean menu bar",
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
