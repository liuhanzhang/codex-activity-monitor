"""Backend entrypoints shared by source builds and self-contained bundles."""

import os
from pathlib import Path
import sys


def is_frozen():
    return bool(getattr(sys, 'frozen', False))


def bundle_resources():
    return Path(sys._MEIPASS)


def backend_command(*arguments, executable=None):
    prefix = [] if is_frozen() else ['-m', 'codex_activity_monitor.cli']
    return [str(executable or sys.executable), *prefix, *(str(value) for value in arguments)]


def backend_environment():
    environment = os.environ.copy()
    if is_frozen():
        # A collector is an independent application, not a multiprocessing
        # worker. Do not reuse a parent's PyInstaller bootloader state.
        environment.pop('PYTHONPATH', None)
        environment.pop('PYTHONHOME', None)
        environment['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
    return environment


def initialize_bundle():
    if is_frozen():
        # Select immutable, build-verified assets even when the user's cache is
        # empty or points to another tokenizer installation.
        os.environ['TIKTOKEN_CACHE_DIR'] = str(bundle_resources() / 'tokenizer-cache')
