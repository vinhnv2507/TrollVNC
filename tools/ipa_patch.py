"""Hạ yêu cầu iOS / đời máy của một file .ipa.

Yêu cầu phiên bản iOS tối thiểu nằm trong `Info.plist` của app
(`MinimumOSVersion`) và do `installd` kiểm tra **lúc cài**, chứ không phải do
app tự hỏi hệ thống. Yêu cầu đời máy nằm ở `UIRequiredDeviceCapabilities`.

Nên muốn cài một app "đòi iOS cao hơn" thì sửa chính gói app, không cần đụng
tới hệ thống và không cần jailbreak.

    python tools/ipa_patch.py app.ipa --min-ios 14.0
    python tools/ipa_patch.py app.ipa --min-ios 14.0 --drop-capabilities -o ra.ipa
    python tools/ipa_patch.py app.ipa --show

> Hạ con số này **không** thêm được API mà iOS cũ không có. Nếu app thật sự
> dùng tính năng của iOS mới, nó sẽ cài được rồi văng lúc chạy. Cách này chỉ ăn
> khi nhà phát triển đặt mức tối thiểu cao hơn thứ họ thật sự cần — chuyện khá
> phổ biến.
"""

from __future__ import annotations

import argparse
import plistlib
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Optional

# Những mục thường chỉ dùng để chặn máy đời thấp, bỏ đi thường vô hại.
DEVICE_GATES = {"arm64e", "iphone-ipad-minimum-performance-a12", "metal",
                "arkit", "location-services", "gamekit"}


def find_info_plist(archive: zipfile.ZipFile) -> str:
    """Đường dẫn Info.plist của app chính trong .ipa."""

    candidates = [
        name for name in archive.namelist()
        if name.startswith("Payload/") and name.endswith(".app/Info.plist")
        and name.count("/") == 2          # bỏ qua Info.plist của app phụ/extension
    ]
    if not candidates:
        raise ValueError("Không thấy Payload/<App>.app/Info.plist — đây có phải .ipa không?")
    return candidates[0]


def describe(path: Path) -> dict:
    with zipfile.ZipFile(path) as archive:
        info = plistlib.loads(archive.read(find_info_plist(archive)))
    return {
        "bundle_id": info.get("CFBundleIdentifier", ""),
        "name": info.get("CFBundleDisplayName") or info.get("CFBundleName", ""),
        "version": info.get("CFBundleShortVersionString", ""),
        "min_ios": info.get("MinimumOSVersion", ""),
        "capabilities": info.get("UIRequiredDeviceCapabilities", []),
    }


def patch(source: Path, output: Path, min_ios: Optional[str] = None,
          drop_capabilities: bool = False) -> dict:
    """Ghi ra một .ipa mới đã sửa Info.plist. Trả về thay đổi đã làm."""

    if output.exists() and output.samefile(source):
        raise ValueError("File ra phải khác file vào")

    changes: dict = {}
    with zipfile.ZipFile(source) as archive:
        target = find_info_plist(archive)
        info = plistlib.loads(archive.read(target))

        if min_ios is not None:
            changes["min_ios"] = (info.get("MinimumOSVersion", ""), min_ios)
            info["MinimumOSVersion"] = min_ios

        if drop_capabilities:
            caps = info.get("UIRequiredDeviceCapabilities")
            if isinstance(caps, list):
                kept = [c for c in caps if c not in DEVICE_GATES]
                if kept != caps:
                    changes["capabilities"] = (caps, kept)
                info["UIRequiredDeviceCapabilities"] = kept
            elif isinstance(caps, dict):
                kept = {k: v for k, v in caps.items() if k not in DEVICE_GATES}
                if kept != caps:
                    changes["capabilities"] = (caps, kept)
                info["UIRequiredDeviceCapabilities"] = kept

        # Giữ nguyên định dạng gốc: nhiều Info.plist là plist nhị phân.
        raw = archive.read(target)
        fmt = plistlib.FMT_BINARY if raw[:8] == b"bplist00" else plistlib.FMT_XML
        patched = plistlib.dumps(info, fmt=fmt)

        # Chép lại mọi mục khác nguyên vẹn, chỉ thay đúng một file.
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as out:
            for item in archive.infolist():
                data = patched if item.filename == target else archive.read(item.filename)
                out.writestr(item, data)

    return changes


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="ipa_patch")
    parser.add_argument("ipa", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    parser.add_argument("--min-ios", metavar="X.Y",
                        help="đặt lại MinimumOSVersion, ví dụ 14.0")
    parser.add_argument("--drop-capabilities", action="store_true",
                        help="bỏ các mục UIRequiredDeviceCapabilities chặn máy đời thấp")
    parser.add_argument("--show", action="store_true", help="chỉ xem, không sửa")
    args = parser.parse_args(argv)

    if not args.ipa.is_file():
        print(f"Không thấy {args.ipa}", file=sys.stderr)
        return 2

    try:
        info = describe(args.ipa)
    except (ValueError, zipfile.BadZipFile) as exc:
        print(f"LỖI: {exc}", file=sys.stderr)
        return 1

    print(f"App        : {info['name']} ({info['bundle_id']}) {info['version']}")
    print(f"iOS tối thiểu: {info['min_ios'] or '(không khai)'}")
    print(f"Yêu cầu máy : {info['capabilities'] or '(không có)'}")

    if args.show or (args.min_ios is None and not args.drop_capabilities):
        return 0

    output = args.output or args.ipa.with_name(args.ipa.stem + "-patched.ipa")
    changes = patch(args.ipa, output, args.min_ios, args.drop_capabilities)

    print()
    for key, (before, after) in changes.items():
        print(f"  {key}: {before} -> {after}")
    if not changes:
        print("  (không có gì thay đổi)")
    print(f"\nĐã ghi {output}")
    print("Cài bằng TrollStore. Nếu TrollStore từ chối vì chữ ký, ký lại bằng ldid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
