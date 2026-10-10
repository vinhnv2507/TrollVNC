# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import PySide6

qt = Path(PySide6.__file__).resolve().parent
runtime_names = {'msvcp140.dll','msvcp140_1.dll','msvcp140_2.dll','vcruntime140.dll','vcruntime140_1.dll'}
runtime_binaries = [(str(qt/name),'.') for name in sorted(runtime_names)]
project = Path(SPECPATH).parent
a = Analysis([str(project/'keygen_main.py')], pathex=[str(project)], binaries=runtime_binaries,
             datas=[], hiddenimports=[], hookspath=[], hooksconfig={},
             runtime_hooks=[str(project/'runtime_hook_qt.py')],
             excludes=['numpy','requests','asyncssh','tidevice','zeroconf','PIL'],
             noarchive=False, optimize=0)
a.binaries = [entry for entry in a.binaries if Path(entry[0]).name.casefold() not in runtime_names | {'icuuc.dll'}]
for name in sorted(runtime_names):a.binaries.append((name,str(qt/name),'BINARY'))
pyz = PYZ(a.pure)
exe = EXE(pyz,a.scripts,[],exclude_binaries=True,name='CTLIOS Keygen',debug=False,
          bootloader_ignore_signals=False,strip=False,upx=False,console=False,
          disable_windowed_traceback=False)
coll = COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='CTLIOS Keygen')
