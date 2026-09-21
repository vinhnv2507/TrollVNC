# -*- mode: python ; coding: utf-8 -*-
import os
import shutil
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import collect_all

import PySide6

datas = []
binaries = []
hiddenimports = []
hiddenimports += collect_submodules('zeroconf')
hiddenimports += collect_submodules('tornado')
tmp_ret = collect_all('tidevice')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


def find_media_tool(name, env_name):
    override = os.environ.get(env_name)
    candidates = [
        override,
        shutil.which(name),
        str(Path('tools') / 'ffmpeg' / f'{name}.exe'),
        str(Path(r'D:\ffmpeg-7.1.1-essentials_build\bin') / f'{name}.exe'),
        str(Path(r'D:\StreamMediaSoftware\tools\ffmpeg-9.0.1-essentials_build\bin') / f'{name}.exe'),
    ]
    return next((path for path in candidates if path and Path(path).is_file()), None)


for tool_name, env_name in (
        ('ffmpeg', 'CONTROLIOS_FFMPEG'), ('ffprobe', 'CONTROLIOS_FFPROBE')):
    tool_path = find_media_tool(tool_name, env_name)
    if not tool_path:
        raise RuntimeError(f'Missing {tool_name}; set {env_name} before building')
    binaries.append((tool_path, '.'))


# PySide6 6.11 is built with a newer MSVC runtime than the Python 3.11
# installation used by the packager. PyInstaller otherwise keeps Python's old
# VCRUNTIME140*.dll at _internal root; Windows loads that copy before Qt's own
# runtime and QtCore fails with "The specified procedure could not be found".
# Replace all root C++ runtime entries with the matching copies shipped by
# PySide6. Newer VC runtimes remain backward-compatible with python311.dll.
pyside_dir = Path(PySide6.__file__).resolve().parent
qt_runtime_names = {
    'msvcp140.dll', 'msvcp140_1.dll', 'msvcp140_2.dll',
    'vcruntime140.dll', 'vcruntime140_1.dll',
}
for runtime_name in sorted(qt_runtime_names):
    runtime_path = pyside_dir / runtime_name
    if not runtime_path.is_file():
        raise RuntimeError(f'Missing PySide6 runtime: {runtime_path}')
    binaries.append((str(runtime_path), '.'))


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['runtime_hook_qt.py'],
    excludes=[],
    noarchive=False,
    optimize=0,
)
# Explicit binaries have priority only after duplicate destinations are removed.
a.binaries = [entry for entry in a.binaries
              if Path(entry[0]).name.casefold() not in qt_runtime_names]
for runtime_name in sorted(qt_runtime_names):
    runtime_path = pyside_dir / runtime_name
    a.binaries.append((runtime_path.name, str(runtime_path), 'BINARY'))

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ControlIOS PC',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='ControlIOS PC',
)
