"""Read shipped ASAR files and preserve bundles relevant to file-edit progress."""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import struct


ROOT = Path(__file__).resolve().parent
OLDER_ASAR = (Path.home()/'playground/codex-app-reverse/versions/'
              '26.1002.52244-13536-arm64/original/ChatGPT.app/Contents/Resources/app.asar')
CURRENT_ASAR = Path('/Applications/ChatGPT.app/Contents/Resources/app.asar')
PATTERNS = [b'patchUpdated', b'apply_patch_streaming_events', b'fileChange',
            b'patch_apply', b'patchApply', b'addedLines', b'linesAdded', b'lineCount',
            b'streamingPatch', b'streamingDiff', b'data-agent-activity-file-link']


def walk(directory, prefix=''):
    for name, entry in directory.get('files', {}).items():
        name = prefix + name
        if 'files' in entry:
            yield from walk(entry, name + '/')
        else:
            yield name, entry


def display_source(path):
    """Retain a useful source location without recording the home-directory name."""
    path = Path(os.path.abspath(Path(path).expanduser()))
    try:
        relative = path.relative_to(Path(os.path.abspath(Path.home())))
    except ValueError:
        return str(path)
    return str(Path('~')/relative)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--older-asar', type=Path, default=OLDER_ASAR,
                        help='ASAR for version 26.1002.52244')
    parser.add_argument('--current-asar', type=Path, default=CURRENT_ASAR,
                        help='ASAR for version 26.1007.21159')
    parser.add_argument('--output', type=Path, default=ROOT,
                        help='Directory for extracted files and source-manifest.json')
    args = parser.parse_args(argv)
    sources = {'26.1002.52244': args.older_asar.expanduser(),
               '26.1007.21159': args.current_asar.expanduser()}
    for asar in sources.values():
        if not asar.is_file():
            parser.error(f'ASAR file not found: {display_source(asar)}')

    output = args.output.expanduser()
    manifest = []
    for version, asar in sources.items():
        files = []
        with asar.open('rb') as stream:
            _, header_size, _, json_size = struct.unpack('<4I', stream.read(16))
            header = json.loads(stream.read(json_size))
            data_offset = 8 + header_size
            for name, entry in walk(header):
                if not name.endswith('.js') or entry.get('unpacked') or 'offset' not in entry:
                    continue
                stream.seek(data_offset + int(entry['offset']))
                data = stream.read(entry['size'])
                counts = {p.decode(): data.count(p) for p in PATTERNS if p in data}
                basename = PurePosixPath(name).name
                explicit_module = basename.startswith(('agent-activity-item-', 'patch-diff-stats-', 'patch-message-',
                    'active-tool-activity-label-', 'activity-state-label-', 'mcp-tool-activity-label-',
                    'file-diff-presentation-'))
                locale_module = re.fullmatch(r'[a-z]{2,3}(?:-[A-Za-z]{2})?-[0-9a-f]+\.js', basename)
                if locale_module or (not counts and not explicit_module):
                    continue
                relative = PurePosixPath(name)
                assert not relative.is_absolute() and '..' not in relative.parts
                target = output / version / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                files.append({'path': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
                              'matches': counts})
        manifest.append({'version': version, 'source': display_source(asar), 'files': files})
        print(json.dumps({'version': version, 'extracted_files': len(files),
                          'extracted_bytes': sum(f['bytes'] for f in files)}, ensure_ascii=False))
    output.mkdir(parents=True, exist_ok=True)
    (output / 'source-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')


if __name__ == '__main__':
    main()
