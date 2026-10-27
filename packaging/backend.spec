"""A console-capable onedir backend in a standard nested macOS code bundle."""
import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

root = Path(SPECPATH).parent
assets = Path(os.environ['CAM_PACKAGING_ASSETS'])
a = Analysis([str(root/'packaging/backend_entry.py')], pathex=[str(root/'src')],
             binaries=[], datas=[(str(assets/'tokenizer-cache'), 'tokenizer-cache'),
                                  (str(assets/'build-manifest.json'), '.')],
             hiddenimports=collect_submodules('codex_activity_monitor') + collect_submodules('tiktoken_ext'),
             excludes=['tkinter'], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='monitor-backend',
          console=True, strip=False, upx=False, target_arch='arm64')
collection = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='monitor-backend')
app = BUNDLE(collection, name='MonitorBackend.app',
             bundle_identifier='com.liuhanzhang.codexactivitymonitor.backend',
             info_plist={'LSUIElement': True, 'LSMinimumSystemVersion': '14.0'})
