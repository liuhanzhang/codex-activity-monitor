"""Check every shipped Mach-O for architecture, deployment target and dylib paths."""

from pathlib import Path
import re
import subprocess


def inspect(app):
    binaries = []
    maximum = (0, 0, 0)
    magics = {bytes.fromhex(value) for value in ('cffaedfe', 'feedfacf', 'cafebabe', 'bebafeca', 'cafebabf')}
    for path in sorted(Path(app).rglob('*')):
        if not path.is_file() or path.is_symlink():
            continue
        with path.open('rb') as source:
            if source.read(4) not in magics:
                continue
        architecture = subprocess.check_output(['lipo', '-archs', str(path)], text=True).strip()
        if architecture != 'arm64':
            raise RuntimeError(f'Unexpected architecture: {path.name}: {architecture}')
        loads = subprocess.check_output(['otool', '-l', str(path)], text=True)
        versions = []
        for command in loads.split('Load command'):
            if 'cmd LC_BUILD_VERSION' in command:
                match = re.search(r'\bminos (\d+(?:\.\d+){0,2})', command)
            elif 'cmd LC_VERSION_MIN_MACOSX' in command:
                match = re.search(r'\bversion (\d+(?:\.\d+){0,2})', command)
            else:
                match = None
            if match:
                parts = tuple(map(int, match[1].split('.')))
                version = (*parts, *(0 for _ in range(3-len(parts))))
                if version > (14, 0, 0):
                    raise RuntimeError(f'Deployment target exceeds macOS 14.0: {path.name}: {match[1]}')
                maximum = max(maximum, version)
                versions.append(match[1])
        links = subprocess.check_output(['otool', '-L', str(path)], text=True).splitlines()[1:]
        for link in links:
            dependency = link.strip().split(' (', 1)[0]
            if not dependency.startswith(('@rpath/', '@loader_path/', '@executable_path/', '/usr/lib/', '/System/Library/')):
                raise RuntimeError(f'External dependency: {path.name}: {dependency}')
        for rpath in re.findall(r'cmd LC_RPATH\s+cmdsize \d+\s+path (.+?) \(offset', loads):
            if not rpath.startswith(('@loader_path', '@executable_path', '/usr/lib/', '/System/Library/')):
                raise RuntimeError(f'External rpath: {path.name}: {rpath}')
        binaries.append({'path': str(path.relative_to(app)), 'architecture': architecture, 'minimum_system': versions})
    if not binaries:
        raise RuntimeError('No Mach-O binaries found')
    return {'macho_count': len(binaries), 'architecture': 'arm64',
            'maximum_minimum_system': '.'.join(map(str, maximum)), 'binaries': binaries}
