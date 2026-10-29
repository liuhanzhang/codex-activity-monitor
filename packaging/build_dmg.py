"""Package an existing signed standalone app with a Finder drag-install guide."""

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
APP_NAME = 'Codex Activity Monitor.app'
VOLUME_NAME = 'Codex Activity Monitor'
ICON_LOCATIONS = {APP_NAME: (175, 215), 'Applications': (485, 215)}


def run(*arguments, **kwargs):
    return subprocess.run([str(value) for value in arguments], check=True, **kwargs)


def app_identity(app):
    with (app / 'Contents/Info.plist').open('rb') as source:
        info = plistlib.load(source)
    if (info.get('CFBundleIdentifier') != 'com.liuhanzhang.codexactivitymonitor' or
            info.get('CAMDistribution') != 'standalone'):
        raise ValueError('Package a standalone Codex Activity Monitor app, not a source or preview build.')
    version = info.get('CAMReleaseVersion', '')
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?', version):
        raise ValueError('The app has no valid release version.')
    return info


def bundle_digest(app):
    digest = hashlib.sha256()
    for path in sorted(app.rglob('*')):
        if path.is_symlink():
            data = b'link\0' + os.fsencode(os.readlink(path))
        elif path.is_file():
            data = b'file\0' + path.read_bytes()
        else:
            continue
        digest.update(os.fsencode(path.relative_to(app)) + b'\0' + data + b'\0')
    return digest.hexdigest()


@contextmanager
def mounted_dmg(dmg, mount):
    """Own one temporary read-only mount and always eject it after use."""
    mount.mkdir()
    attached = plistlib.loads(subprocess.check_output([
        '/usr/bin/hdiutil', 'attach', '-readonly', '-nobrowse', '-mountpoint', str(mount), '-plist', str(dmg)]))
    device = attached['system-entities'][0]['dev-entry']
    try:
        yield mount
    finally:
        run('/usr/bin/hdiutil', 'detach', '-quiet', device)


def verify(dmg, source_app, directory):
    """Mount only our image; verify a drag-copy into a disposable directory."""
    from ds_store import DSStore

    run('/usr/bin/hdiutil', 'verify', '-quiet', dmg)
    with mounted_dmg(dmg, directory / 'mounted') as mount:
        app = mount / APP_NAME
        if os.readlink(mount / 'Applications') != '/Applications':
            raise ValueError('The Applications shortcut is invalid.')
        if not os.statvfs(mount).f_flag & os.ST_RDONLY:
            raise ValueError('The release disk image is not read-only.')
        expected = bundle_digest(source_app)
        if bundle_digest(app) != expected:
            raise ValueError('The app changed while creating the disk image.')
        run('codesign', '--verify', '--deep', '--strict', app)
        with DSStore.open(str(mount / '.DS_Store'), 'r') as store:
            for name, location in ICON_LOCATIONS.items():
                if tuple(store[name]['Iloc']) != location:
                    raise ValueError('The Finder drag-install layout is missing.')
        copied = directory / 'drag-copy' / APP_NAME
        copied.parent.mkdir()
        run('/usr/bin/ditto', app, copied)
        if bundle_digest(copied) != expected:
            raise ValueError('The app changed while copying out of the disk image.')
        run('codesign', '--verify', '--deep', '--strict', copied)
        return {'read_only': True, 'applications_shortcut': True, 'finder_layout': True,
                'bundle_sha256': expected, 'mounted_signature': True, 'copied_signature': True}


def build(app, destination):
    import dmgbuild

    app, destination = Path(app).resolve(), Path(destination).resolve()
    info = app_identity(app)
    if destination.suffix != '.dmg' or destination.is_relative_to(app):
        raise ValueError('Write the DMG outside the input app, with a .dmg extension.')
    run('codesign', '--verify', '--deep', '--strict', app)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.cam-dmg-', dir=destination.parent) as temporary:
        work = Path(temporary)
        cache = ROOT / '.local/dmg-module-cache'
        cache.mkdir(parents=True, exist_ok=True)
        run('swift', '-module-cache-path', cache, ROOT / 'packaging/dmg_background.swift', work)
        image = work / 'installer.dmg'
        dmgbuild.build_dmg(str(image), VOLUME_NAME, settings={
            'format': 'UDZO', 'filesystem': 'HFS+',
            'files': [(str(app), APP_NAME)], 'symlinks': {'Applications': '/Applications'},
            'icon': str(app / 'Contents/Resources/AppIcon.icns'),
            'background': str(work / 'background.png'),
            'window_rect': ((160, 160), (660, 430)), 'default_view': 'icon-view',
            'icon_locations': ICON_LOCATIONS, 'icon_size': 128, 'text_size': 13,
            # SetFile's hide-extension flag adds FinderInfo to the app bundle,
            # which fails strict code-signature verification. Leave it intact.
            'show_toolbar': False, 'show_sidebar': False,
            'show_status_bar': False, 'show_pathbar': False, 'show_tab_view': False,
            'arrange_by': None, 'include_icon_view_settings': True,
        })
        checks = verify(image, app, work)
        image.replace(destination)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    checksums = destination.parent / 'SHA256SUMS.txt'
    checksums.write_text(f'{digest}  {destination.name}\n')
    return {'dmg': str(destination), 'dmg_sha256': digest, 'checksums': str(checksums),
            'dmg_bytes': destination.stat().st_size, 'dmg_checks': checks,
            'release_version': info['CAMReleaseVersion']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('app', type=Path, nargs='?', default=ROOT / '.local/standalone' / APP_NAME)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    app = args.app.expanduser().resolve()
    info = app_identity(app)
    destination = args.output.expanduser() if args.output else app.parent / (
        f'Codex-Activity-Monitor-{info["CAMReleaseVersion"]}-macos-arm64.dmg')
    result = build(app, destination)
    result_path = destination.resolve().with_suffix('.json')
    result_path.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
