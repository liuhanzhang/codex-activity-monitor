"""Embed the pinned Sparkle artifact into an arm64 app, preserving framework links."""

from pathlib import Path
import plistlib
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def customize_download_button(framework):
    """Override only the informational web-download label in our bundled copy."""
    titles = {'Base': 'Download', 'zh_CN': '下载', 'zh_TW': '下載', 'zh_HK': '下載'}
    tables = []
    for language, title in titles.items():
        path = Path(framework)/'Resources'/f'{language}.lproj/Sparkle.strings'
        values = plistlib.loads(path.read_bytes())
        if 'Learn More…' not in values:
            raise RuntimeError(f'Sparkle web-download label changed in {language}; review the pinned SDK before packaging.')
        values['Learn More…'] = title
        tables.append((path, values))
    for path, values in tables:
        path.write_bytes(plistlib.dumps(values, fmt=plistlib.FMT_BINARY))


def embed(app):
    app = Path(app)
    artifact = ROOT/'macos/.build/artifacts/sparkle/Sparkle'
    source = artifact/'Sparkle.xcframework/macos-arm64_x86_64/Sparkle.framework'
    if not source.is_dir():
        raise RuntimeError('Resolve the pinned Sparkle Swift package before packaging.')
    destination = app/'Contents/Frameworks/Sparkle.framework'
    if destination.exists():
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination, symlinks=True)
    customize_download_button(destination)
    # This product ships arm64 only. Re-sign from the innermost code outward
    # after thinning; never ship the upstream signatures over modified code.
    magics = {bytes.fromhex(value) for value in ('cffaedfe', 'feedfacf', 'cafebabe', 'bebafeca', 'cafebabf')}
    binaries = []
    for path in sorted(destination.rglob('*')):
        if path.is_symlink() or not path.is_file():
            continue
        with path.open('rb') as stream:
            if stream.read(4) not in magics:
                continue
        architectures = subprocess.check_output(['lipo', '-archs', str(path)], text=True).split()
        if 'arm64' not in architectures:
            raise RuntimeError(f'Sparkle binary has no arm64 slice: {path.name}')
        if len(architectures) > 1:
            temporary = path.with_name(path.name + '.arm64')
            subprocess.run(['lipo', str(path), '-thin', 'arm64', '-output', str(temporary)], check=True)
            temporary.chmod(path.stat().st_mode)
            temporary.replace(path)
        binaries.append(path)
    for path in sorted(binaries, key=lambda p: len(p.parts), reverse=True):
        subprocess.run(['codesign', '--force', '--sign', '-', str(path)], check=True)
    bundles = [p for p in destination.rglob('*') if p.is_dir() and not p.is_symlink() and p.suffix in ('.xpc', '.app')]
    for path in sorted(bundles, key=lambda p: len(p.parts), reverse=True):
        subprocess.run(['codesign', '--force', '--sign', '-', str(path)], check=True)
    subprocess.run(['codesign', '--force', '--sign', '-', str(destination)], check=True)
    licenses = app/'Contents/Resources/ThirdPartyLicenses/Sparkle'
    licenses.mkdir(parents=True, exist_ok=True)
    shutil.copy2(artifact/'LICENSE', licenses/'LICENSE')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('app', type=Path)
    embed(parser.parse_args().app)
