"""Create and verify a portable ControlIOS PC ZIP without unsafe './' entries."""

from __future__ import annotations

import argparse
import hashlib
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


def safe_member(name: str) -> bool:
    path = PurePosixPath(name)
    return bool(name and not name.startswith(("/", "\\")) and
                path.parts and path.parts[0] not in (".", "..") and
                ".." not in path.parts and not path.is_absolute())


def package(source: Path, output: Path) -> tuple[str, int]:
    source = source.resolve()
    exe = source / "ControlIOS PC.exe"
    if not exe.is_file():
        raise SystemExit(f"Missing executable: {exe}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temp_zip = output.with_suffix(output.suffix + ".tmp")
    temp_zip.unlink(missing_ok=True)
    output.unlink(missing_ok=True)

    with zipfile.ZipFile(
        temp_zip, "w", compression=zipfile.ZIP_DEFLATED,
        compresslevel=6, allowZip64=True,
    ) as archive:
        for path in sorted(source.rglob("*")):
            if not path.is_file():
                continue
            member = path.relative_to(source).as_posix()
            if not safe_member(member):
                raise SystemExit(f"Unsafe ZIP member: {member!r}")
            archive.write(path, member)
    temp_zip.replace(output)

    with zipfile.ZipFile(output) as archive:
        bad = [name for name in archive.namelist() if not safe_member(name)]
        if bad:
            raise SystemExit(f"Invalid ZIP members: {bad[:5]}")
        if "ControlIOS PC.exe" not in archive.namelist():
            raise SystemExit("ZIP is missing ControlIOS PC.exe at its root")
        with tempfile.TemporaryDirectory(prefix="controlios-zip-test-") as folder:
            archive.extractall(folder)
            extracted = Path(folder) / "ControlIOS PC.exe"
            if not extracted.is_file() or extracted.stat().st_size != exe.stat().st_size:
                raise SystemExit("ZIP extraction verification failed")

    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    return digest, output.stat().st_size


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    digest, size = package(args.source, args.output)
    print(f"ZIP={args.output.resolve()}")
    print(f"SIZE={size}")
    print(f"SHA256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
