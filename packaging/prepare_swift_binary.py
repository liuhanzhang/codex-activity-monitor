"""Remove unused development toolchain search paths before signing an app."""

from pathlib import Path
import re
import subprocess


def prepare(executable):
    executable = Path(executable)
    swift = Path(subprocess.check_output(['xcrun', '--find', 'swift'], text=True).strip()).resolve()
    toolchain = swift.parents[2]
    loads = subprocess.check_output(['otool', '-l', str(executable)], text=True)
    rpaths = re.findall(r'cmd LC_RPATH\s+cmdsize \d+\s+path (.+?) \(offset', loads)
    development_paths = [path for path in rpaths if Path(path).is_absolute()
                         and Path(path).is_relative_to(toolchain)]
    if not development_paths:
        return
    required = subprocess.check_output(['xcrun', 'swift-stdlib-tool', '--print',
                                       '--scan-executable', str(executable), '--platform', 'macosx'], text=True).strip()
    if required:
        raise RuntimeError(f'Bundle required Swift runtime libraries before removing toolchain paths: {required}')
    # SwiftPM can add compatibility-library rpaths even when no such library is
    # linked. Required libraries must never be hidden by removing their path.
    for path in development_paths:
        subprocess.run(['install_name_tool', '-delete_rpath', path, str(executable)], check=True)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('executable', type=Path)
    prepare(parser.parse_args().executable)
