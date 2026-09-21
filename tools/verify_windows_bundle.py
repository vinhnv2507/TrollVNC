"""Extract a ControlIOS PC ZIP and smoke-test the bundled executable."""

from __future__ import annotations

import argparse
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath



def safe_member(name: str) -> bool:
    path = PurePosixPath(name)
    return bool(name and not name.startswith(("/", "\\")) and
                path.parts and path.parts[0] not in (".", "..") and
                ".." not in path.parts and not path.is_absolute())


def verify(archive_path: Path, seconds: float = 7.0) -> list[str]:
    archive_path = archive_path.resolve()
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        bad = [name for name in names if not safe_member(name)]
        if bad:
            raise SystemExit(f"Unsafe archive members: {bad[:5]}")
        if archive.testzip() is not None:
            raise SystemExit("Archive CRC verification failed")
        if "ControlIOS PC.exe" not in names:
            raise SystemExit("ControlIOS PC.exe is not at ZIP root")
        for runtime in ("vcruntime140.dll", "vcruntime140_1.dll"):
            root_name = f"_internal/{runtime}"
            if root_name not in names:
                raise SystemExit(f"ZIP is missing {runtime} at _internal root")
        with tempfile.TemporaryDirectory(prefix="controlios-smoke-", ignore_cleanup_errors=True) as folder:
            archive.extractall(folder)
            exe = Path(folder) / "ControlIOS PC.exe"
            process = subprocess.Popen([str(exe)], cwd=folder)
            try:
                time.sleep(seconds)
                return_code = process.poll()
                if return_code is not None:
                    raise SystemExit(
                        f"Bundled executable exited during smoke test: {return_code}"
                    )
                return [
                    str((Path(folder) / "_internal" / "PySide6" / "Qt6Core.dll").resolve()),
                    str((Path(folder) / "_internal" / "vcruntime140.dll").resolve()),
                    str((Path(folder) / "_internal" / "vcruntime140_1.dll").resolve()),
                ]
            finally:
                # ControlIOS may start child relay processes. taskkill /T closes
                # only this freshly launched process tree, never an existing app.
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    check=False,
                )
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                time.sleep(1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("--seconds", type=float, default=7.0)
    args = parser.parse_args()
    loaded = verify(args.archive, args.seconds)
    print("EXTRACT_AND_LAUNCH_OK")
    for path in loaded:
        print(f"LOADED={path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
