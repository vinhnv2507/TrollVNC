"""Keep PySide6 extension modules and Qt DLLs on the same DLL search path."""

import os
import sys


if getattr(sys, "frozen", False):
    qt_dir = os.path.join(sys._MEIPASS, "PySide6")
    if os.path.isdir(qt_dir):
        os.add_dll_directory(qt_dir)
        os.environ["PATH"] = qt_dir + os.pathsep + os.environ.get("PATH", "")
