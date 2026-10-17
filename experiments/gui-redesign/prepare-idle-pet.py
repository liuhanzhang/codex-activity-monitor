#!/usr/bin/env python3
"""Cut a generated transparent 4x2 sheet into registered native preview frames."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sheet', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:
        parser.error('ffmpeg and ffprobe are required to package the generated frames')
    probe = subprocess.run([ffprobe, '-v', 'error', '-show_entries', 'stream=width,height,pix_fmt',
                            '-of', 'json', str(args.sheet)], check=True, capture_output=True, text=True)
    stream = json.loads(probe.stdout)['streams'][0]
    if stream['pix_fmt'] != 'rgba':
        parser.error('expected a transparent RGBA sprite sheet')
    width, height = stream['width'], stream['height']
    cells, boxes = [], []
    for index in range(8):
        col, row = index % 4, index // 4
        x, y = round(col * width / 4), round(row * height / 2)
        w, h = round((col + 1) * width / 4) - x, round((row + 1) * height / 2) - y
        cell = f'crop={w}:{h}:{x}:{y}'
        check = subprocess.run([ffmpeg, '-hide_banner', '-i', str(args.sheet), '-vf',
                                cell + ',alphaextract,bbox=min_val=24', '-frames:v', '1',
                                '-f', 'null', '-'], check=True, capture_output=True, text=True)
        match = re.search(r'x1:(\d+) x2:(\d+) y1:(\d+) y2:(\d+)', check.stderr)
        if not match:
            raise RuntimeError(f'No alpha bounds for frame {index}')
        boxes.append(tuple(map(int, match.groups())))
        cells.append(cell)
    # One shared crop retains the authored motion instead of recentering every pose.
    left, right = min(b[0] for b in boxes) - 2, max(b[1] for b in boxes) + 2
    top, bottom = min(b[2] for b in boxes) - 2, max(b[3] for b in boxes) + 2
    crop = f'crop={right-left+1}:{bottom-top+1}:{left}:{top}'
    args.output.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for index, cell in enumerate(cells):
        target = args.output / f'idle-pet-{index:02d}.png'
        filters = cell + ',' + crop + ',scale=96:96:force_original_aspect_ratio=decrease:flags=lanczos,pad=96:96:(ow-iw)/2:(oh-ih)/2:color=0x00000000,format=rgba'
        subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-i', str(args.sheet),
                        '-vf', filters, '-frames:v', '1', str(target)], check=True)
        hashes[target.name] = hashlib.sha256(target.read_bytes()).hexdigest()
    manifest = dict(version=1, generator='built-in image_gen', reference='user-supplied beagle GIF',
                    sheet_sha256=hashlib.sha256(args.sheet.read_bytes()).hexdigest(),
                    sheet_size=[width, height], grid=[4, 2], shared_crop=[left, top, right-left+1, bottom-top+1],
                    frame_size=[96, 96], frame_count=8, frame_duration_seconds=.2, render_height_pt=18,
                    background='transparent; white fur remains opaque', frames_sha256=hashes)
    (args.output / 'idle-pet-provenance.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == '__main__':
    main()
