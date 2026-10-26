#!/usr/bin/env python3
"""Export native-window frames as MP4 / GIF. Requires Pillow and FFmpeg."""

import argparse
from collections import OrderedDict
import json
from pathlib import Path
import subprocess

from PIL import Image, ImageDraw, ImageFont


def run(*args):
    subprocess.run([str(arg) for arg in args], check=True)


def quoted(path):
    return "'" + str(path).replace("'", "'\\''") + "'"


def product_window(source):
    """Remove developer footers and OS capture chrome, preserving product controls."""
    if source.width >= 1500:
        return source.crop((0, 64, source.width, source.height - 64)).convert("RGB")
    return source.crop((0, 0, source.width, source.height - 88)).convert("RGB")


def menu_bar_panel(source, status):
    panel = Image.new("RGB", (source.width, source.height + 60), "white")
    draw = ImageDraw.Draw(panel)
    draw.rectangle((0, 0, source.width, 55), fill="#292c34")
    panel.paste(status, ((source.width - status.width) // 2, 6), status)
    panel.paste(source, (0, 60))
    return panel


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--intro-frames", type=Path)
    parser.add_argument("--status-image", required=True, type=Path)
    args = parser.parse_args()
    frames = args.frames.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    work = frames.parent / "export"
    work.mkdir(exist_ok=True)
    groups = OrderedDict()
    sources = ([args.intro_frames.resolve()] if args.intro_frames else []) + [frames]
    for directory in sources:
        for frame in json.loads((directory / "frames.json").read_text()):
            # Filenames come from the local capture, not from shell input.
            if Path(frame["file"]).name != frame["file"]:
                raise ValueError("Frame must be a basename")
            frame["directory"] = directory
            groups.setdefault(frame["chapter"], []).append(frame)

    chapters = []
    font = "/System/Library/Fonts/Helvetica.ttc"
    for index, (title, items) in enumerate(groups.items()):
        stem = work / f"chapter-{index:02d}"
        plate = Image.new("RGB", (1440, 1000), "#f6f5f2")
        draw = ImageDraw.Draw(plate)
        draw.text((64, 30), "Codex Activity Monitor", font=ImageFont.truetype(font, 34), fill="#262626")
        draw.text((64, 83), title, font=ImageFont.truetype(font, 22), fill="#605e59")
        lines = ["ffconcat version 1.0"]
        duration = 0
        for n, item in enumerate(items):
            interval = ((items[n + 1]["at"] - item["at"]) / 1000) if n + 1 < len(items) else 0.125
            interval = min(0.5, max(0.04, interval))
            duration += interval
            directory = item["directory"]
            status_path = directory / item["status"] if item.get("status") else args.status_image
            with Image.open(directory / item["file"]) as original, Image.open(status_path) as status:
                source = menu_bar_panel(product_window(original), status.convert("RGBA"))
                source.thumbnail((1280, 780), Image.Resampling.LANCZOS)
                composed = plate.copy()
                composed.paste(source, ((1440 - source.width) // 2, 140 + (780 - source.height) // 2))
                # A magnified view of the same native status button keeps its updates readable.
                enlarged = status.convert("RGBA").resize((status.width * 2, status.height * 2), Image.Resampling.LANCZOS)
                ImageDraw.Draw(composed).rounded_rectangle((1136, 26, 1360, 126), radius=12, fill="#292c34")
                composed.paste(enlarged, (1154, 32), enlarged)
                rendered = work / item["file"]
                composed.save(rendered, quality=94, subsampling=0)
            lines.extend(["file " + quoted(rendered), f"duration {interval:.6f}"])
        lines.append("file " + quoted(rendered))
        playlist = stem.with_suffix(".ffconcat")
        playlist.write_text("\n".join(lines) + "\n")
        video = stem.with_suffix(".mp4")
        run("ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-safe", "0", "-i", playlist,
            "-r", "24", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "23", "-pix_fmt", "yuv420p", "-an", "-movflags", "+faststart", video)
        chapters.append({"title": title, "frames": len(items), "captured_seconds": round(duration, 3), "file": video.name})

    playlist = work / "chapters.ffconcat"
    playlist.write_text("ffconcat version 1.0\n" + "\n".join(
        "file " + quoted(work / chapter["file"]) for chapter in chapters) + "\n")
    video = output / "demo.mp4"
    run("ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-safe", "0", "-i", playlist,
        "-c", "copy", "-movflags", "+faststart", video)
    run("ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", video,
        "-frames:v", "1", "-q:v", "2", output / "demo-poster.jpg")
    run("ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", video,
        "-filter_complex", "fps=6,scale=900:-1:flags=lanczos,split[a][b];"
        "[a]palettegen=max_colors=96:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle",
        "-loop", "0", output / "demo.gif")
    (work / "chapters.json").write_text(json.dumps(chapters, indent=2) + "\n")
    print(json.dumps({"chapters": chapters, "mp4_bytes": video.stat().st_size,
                      "gif_bytes": (output / "demo.gif").stat().st_size}, indent=2))


if __name__ == "__main__":
    main()
