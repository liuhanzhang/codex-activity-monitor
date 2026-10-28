"""Build a relocatable arm64 macOS development build, separate from the daily app."""

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess
import sys
import sysconfig
import tomllib
import urllib.request

from inspect_app import inspect
from prepare_swift_binary import prepare

ROOT = Path(__file__).resolve().parents[1]


def run(*arguments, **kwargs):
    return subprocess.run([str(value) for value in arguments], check=True, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'.local/standalone')
    args = parser.parse_args()
    metadata = tomllib.loads((ROOT/'pyproject.toml').read_text())
    version = metadata['project']['version']
    prerelease = metadata.get('tool', {}).get('codex-activity-monitor', {}).get('prerelease')
    release_version = version + (f'-{prerelease}' if prerelease else '')
    if sys.platform != 'darwin' or platform.machine() != 'arm64':
        parser.error('This dev build supports native arm64 macOS only.')
    distribution_build = Path(sys.base_prefix)/'BUILD'
    if sys.version_info[:3] != (3, 14, 9) or not distribution_build.is_file() or distribution_build.read_text().strip() != '20261021':
        parser.error('Use the pinned standalone Python; run bin/prepare-standalone first.')
    output = args.output.expanduser().resolve()
    if output == ROOT or ROOT.is_relative_to(output):
        parser.error('Output must be a separate build directory.')
    output.mkdir(parents=True, exist_ok=True)
    build = output/'build'
    build.mkdir(exist_ok=True)
    os.environ['PYINSTALLER_CONFIG_DIR'] = str(build/'pyinstaller-cache')
    assets = build/'assets'
    assets.mkdir(exist_ok=True)
    notices = assets/'pbs-licenses.rst'
    notices_hash = 'e43fb936c6655d7996dba480d7ebdea492d6040ec388eb8ed9d1000f72de8cab'
    if not notices.exists() or hashlib.sha256(notices.read_bytes()).hexdigest() != notices_hash:
        with urllib.request.urlopen('https://raw.githubusercontent.com/astral-sh/python-build-standalone/20261021/python-licenses.rst', timeout=30) as response:
            content = response.read()
        if hashlib.sha256(content).hexdigest() != notices_hash:
            raise RuntimeError('Python distribution notices checksum mismatch')
        notices.write_bytes(content)
    cache = assets/'tokenizer-cache'
    cache.mkdir(exist_ok=True)
    os.environ['TIKTOKEN_CACHE_DIR'] = str(cache)
    import tiktoken
    encodings = sorted(tiktoken.list_encoding_names())
    for name in encodings:
        tiktoken.get_encoding(name).encode_ordinary('Hello, 世界！打包验证。')

    files = sorted([*ROOT.glob('src/codex_activity_monitor/*.py'), *ROOT.glob('macos/Sources/**/*'),
                    *ROOT.glob('packaging/*'), ROOT/'pyproject.toml', ROOT/'LICENSE',
                    ROOT/'macos/Assets/AppIcon.png', ROOT/'bin/build-app-icon'])
    digest = hashlib.sha256()
    for path in files:
        if path.is_file():
            digest.update(str(path.relative_to(ROOT)).encode()+b'\0'+path.read_bytes())
    build_id = digest.hexdigest()
    modules = {path.stem: hashlib.sha256(path.read_bytes()).hexdigest()
               for path in sorted(ROOT.glob('src/codex_activity_monitor/*.py'))}
    dependencies = {dist.metadata['Name']: dist.version for dist in importlib.metadata.distributions()
                    if dist.metadata.get('Name')}
    manifest = {'version': 1, 'release_version': release_version, 'build_id': build_id, 'modules': modules, 'encodings': encodings,
                'python': platform.python_version(), 'dependencies': dependencies,
                'python_distribution': {'provider': 'python-build-standalone', 'build': '20261021'},
                'tokenizer_assets': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                     for p in sorted(cache.iterdir()) if p.is_file()}}
    (assets/'build-manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True)+'\n')
    os.environ['CAM_PACKAGING_ASSETS'] = str(assets)
    run(sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
        '--distpath', build/'dist', '--workpath', build/'work', ROOT/'packaging/backend.spec', cwd=ROOT)

    run(sys.executable, ROOT/'bin/export-localizations', cwd=ROOT)
    environment = os.environ.copy()
    environment['CLANG_MODULE_CACHE_PATH'] = str(ROOT/'.local/clang-module-cache')
    swift = ['swift', 'build', '--disable-sandbox', '--cache-path', str(ROOT/'.local/swift-cache'),
             '--package-path', str(ROOT/'macos'), '--configuration', 'release']
    run(*swift, env=environment, cwd=ROOT)
    binary_dir = Path(subprocess.check_output([*swift, '--show-bin-path'], env=environment, text=True).strip())
    stage = build/'Codex Activity Monitor.app'
    if stage.exists():
        shutil.rmtree(stage)
    contents = stage/'Contents'
    (contents/'MacOS').mkdir(parents=True)
    resources = contents/'Resources'
    resources.mkdir()
    run(ROOT/'bin/build-app-icon', resources/'AppIcon.icns')
    shutil.copy2(ROOT/'LICENSE', resources/'LICENSE')
    shutil.copy2(binary_dir/'MonitorApp', contents/'MacOS/MonitorApp')
    prepare(contents/'MacOS/MonitorApp')
    for bundle in binary_dir.glob('*.bundle'):
        if not bundle.name.endswith('Tests.bundle'):
            shutil.copytree(bundle, resources/bundle.name, symlinks=True)
    backend = contents/'Helpers/MonitorBackend.app'
    shutil.copytree(build/'dist/MonitorBackend.app', backend, symlinks=True)
    config = {'mode': 'bundled', 'backend': 'Contents/Helpers/MonitorBackend.app/Contents/MacOS/monitor-backend', 'build_id': build_id}
    (resources/'backend.json').write_text(json.dumps(config, indent=2)+'\n')
    (resources/'build-manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True)+'\n')
    licenses = resources/'ThirdPartyLicenses'
    licenses.mkdir()
    python_license = Path(sysconfig.get_path('stdlib'))/'LICENSE.txt'
    (licenses/'Python').mkdir()
    shutil.copy2(python_license, licenses/'Python/LICENSE.txt')
    shutil.copy2(notices, licenses/'Python/standalone-distribution-notices.rst')
    for dist in importlib.metadata.distributions():
        name = dist.metadata.get('Name', '')
        if name.lower() in ('codex-activity-monitor', 'pip'):
            continue
        for file in dist.files or []:
            if any(part.lower().startswith(('license', 'copying', 'notice')) for part in file.parts):
                source = Path(dist.locate_file(file))
                if source.is_file():
                    destination = licenses/name/Path(*file.parts[1:])
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
    info = {'CFBundleExecutable': 'MonitorApp', 'CFBundleIdentifier': 'com.liuhanzhang.codexactivitymonitor',
            'CFBundleName': 'Codex Activity Monitor', 'CFBundleDisplayName': 'Codex Activity Monitor', 'CFBundlePackageType': 'APPL',
            'CFBundleShortVersionString': version, 'CFBundleVersion': '2', 'LSMinimumSystemVersion': '14.0',
            'LSUIElement': True, 'NSPrincipalClass': 'NSApplication', 'CFBundleIconFile': 'AppIcon.icns'}
    (contents/'Info.plist').write_bytes(plistlib.dumps(info))
    run('codesign', '--force', '--deep', '--sign', '-', stage)
    run('codesign', '--verify', '--deep', '--strict', stage)
    inspection = inspect(stage)
    final = output/stage.name
    if final.exists():
        shutil.rmtree(final)
    stage.rename(final)
    archive = output/f'Codex-Activity-Monitor-{release_version}-macos-arm64.zip'
    run('/usr/bin/ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', final, archive)
    summary = {'app': str(final), 'archive': str(archive), 'release_version': release_version, 'build_id': build_id,
               'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
               'archive_bytes': archive.stat().st_size,
               'binary_inspection': inspection,
               'installed_bytes': sum(p.stat().st_size for p in final.rglob('*') if p.is_file() and not p.is_symlink())}
    (output/'build-result.json').write_text(json.dumps(summary, indent=2)+'\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
