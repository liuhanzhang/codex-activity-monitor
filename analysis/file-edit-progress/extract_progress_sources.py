"""Read shipped ASAR files and preserve bundles relevant to file-edit progress."""

import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import struct


ROOT = Path(__file__).resolve().parent
SOURCES = {
    '26.1002.52244': Path('/Users/bytedance/playground/codex-app-reverse/versions/'
                        '26.1002.52244-13536-arm64/original/ChatGPT.app/Contents/Resources/app.asar'),
    '26.1007.21159': Path('/Applications/ChatGPT.app/Contents/Resources/app.asar'),
}
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


manifest = []
for version, asar in SOURCES.items():
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
            target = ROOT / version / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            files.append({'path': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
                          'matches': counts})
    manifest.append({'version': version, 'source': str(asar), 'files': files})
    print(json.dumps({'version': version, 'extracted_files': len(files),
                      'extracted_bytes': sum(f['bytes'] for f in files)}, ensure_ascii=False))
(ROOT / 'source-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
