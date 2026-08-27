# -*- mode: python ; coding: utf-8 -*-
import os
import shutil
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import collect_all

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


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
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
