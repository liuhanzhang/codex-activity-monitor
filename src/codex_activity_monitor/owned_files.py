"""Known filesystem metadata allowed inside Monitor-owned directories."""

from pathlib import Path
import stat


def is_finder_metadata(path):
    path = Path(path)
    if path.name != '.DS_Store':
        return False
    try:
        # A directory or symlink with this name is still unregistered data.
        return stat.S_ISREG(path.lstat().st_mode)
    except FileNotFoundError:
        return False
