"""The ControlIOS icon shared by the desktop application and its windows."""
from pathlib import Path

from PySide6.QtGui import QIcon


APP_ICON_PATH = Path(__file__).resolve().parent.parent / "assets" / "controlios.ico"


def app_icon() -> QIcon:
    return QIcon(str(APP_ICON_PATH))
