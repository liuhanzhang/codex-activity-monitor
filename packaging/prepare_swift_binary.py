"""Remove development-only search paths after frameworks are embedded, before signing."""

from pathlib import Path
import re
import subprocess


def prepare(executable):
    executable = Path(executable)
    swift = Path(subprocess.check_output(['xcrun', '--find', 'swift'], text=True).strip()).resolve()
    toolchain = swift.parents[2]
    swiftpm = Path(__file__).resolve().parents[1]/'macos/.build'
    loads = subprocess.check_output(['otool', '-l', str(executable)], text=True)
    rpaths = re.findall(r'cmd LC_RPATH\s+cmdsize \d+\s+path (.+?) \(offset', loads)
    development_paths = [path for path in rpaths if Path(path).is_absolute()
                         and (Path(path).is_relative_to(toolchain) or Path(path).is_relative_to(swiftpm))]
    if not development_paths:
        return
    required = subprocess.check_output(['xcrun', 'swift-stdlib-tool', '--print',
                                       '--scan-executable', str(executable), '--platform', 'macosx'], text=True).strip()
    if required:
        raise RuntimeError(f'Bundle required Swift runtime libraries before removing toolchain paths: {required}')
    links = subprocess.check_output(['otool', '-L', str(executable)], text=True).splitlines()[1:]
    for line in links:
        dependency = line.strip().split(' (', 1)[0]
        if dependency.startswith('@rpath/') and not dependency.startswith('@rpath/libswift'):
            bundled = executable.parent.parent/'Frameworks'/dependency.removeprefix('@rpath/')
            if not bundled.is_file():
                raise RuntimeError(f'Embed framework dependency before removing development paths: {dependency}')
    # Required libraries must never be hidden by removing their development path.
    for path in development_paths:
        subprocess.run(['install_name_tool', '-delete_rpath', path, str(executable)], check=True)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('executable', type=Path)
    prepare(parser.parse_args().executable)
