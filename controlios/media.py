"""Chuẩn hoá ảnh/video sang định dạng iOS Thư viện Ảnh chấp nhận.

App Ảnh của iOS **kén hơn hẳn** trình phát trên PC: nó chỉ nhận video H.264 (mức
vừa phải, ≤1080p) hoặc HEVC, pixel ``yuv420p``, container mp4/mov. Một file
``.mp4`` mở tốt trên PC vẫn có thể bị ``PHPhotosErrorDomain 3302`` khi nạp — ví
dụ 4K H.264 **level 5.1** từ encoder không phải Apple.

Module này soi file bằng ``ffprobe``; nếu chưa đạt chuẩn thì ``ffmpeg`` re-encode
sang một file iOS chắc chắn nhận, rồi trả về đường dẫn file đó. Ảnh và video vốn
đã đạt chuẩn thì trả về nguyên bản (không đụng gì).

Việc chuẩn hoá cố ý làm **một lần** ở tầng pool trước khi phát cho nhiều máy: một
lần transcode, đẩy cùng một file tới cả 250 máy.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# Thư mục cache cho các file đã transcode (cạnh project/exe, không vào git).
from .config import PROJECT_ROOT
DEFAULT_CACHE = PROJECT_ROOT / "captures" / "_media_tmp"

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".gif", ".webp",
              ".bmp", ".tif", ".tiff"}
# Video container mà iOS *có thể* nhận thẳng nếu codec đạt chuẩn.
READY_VIDEO_EXTS = {".mp4", ".mov", ".m4v"}
# Container iOS không nhận — luôn phải chuyển sang mp4.
OTHER_VIDEO_EXTS = {".mkv", ".avi", ".webm", ".flv", ".wmv", ".ts", ".m2ts",
                    ".mpg", ".mpeg", ".3gp"}
VIDEO_EXTS = READY_VIDEO_EXTS | OTHER_VIDEO_EXTS


def _tool(name: str, env_var: str) -> Optional[str]:
    """Tìm ffmpeg/ffprobe: ưu tiên biến môi trường, rồi PATH."""

    override = os.environ.get(env_var)
    if override and Path(override).exists():
        return override
    return shutil.which(name)


def ffmpeg_path() -> Optional[str]:
    return _tool("ffmpeg", "CONTROLIOS_FFMPEG")


def ffprobe_path() -> Optional[str]:
    return _tool("ffprobe", "CONTROLIOS_FFPROBE")


def is_image(path: Path | str) -> bool:
    return Path(path).suffix.lower() in IMAGE_EXTS


def is_video(path: Path | str) -> bool:
    return Path(path).suffix.lower() in VIDEO_EXTS


def probe_video(path: Path | str) -> Optional[dict]:
    """Trả về dict luồng video đầu tiên (codec_name, level, pix_fmt, w, h) hoặc None."""

    ffprobe = ffprobe_path()
    if not ffprobe:
        return None
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name,profile,level,pix_fmt,width,height",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("ffprobe lỗi với %s: %s", path, exc)
        return None
    if out.returncode != 0:
        return None
    try:
        streams = json.loads(out.stdout).get("streams", [])
    except json.JSONDecodeError:
        return None
    return streams[0] if streams else None


def video_is_ios_ready(path: Path | str) -> bool:
    """File video đã đúng chuẩn iOS chưa (nạp thẳng được, khỏi re-encode)."""

    info = probe_video(path)
    if not info:
        return False                       # không soi được -> cứ chuẩn hoá cho chắc

    codec = str(info.get("codec_name", "")).lower()
    pix = str(info.get("pix_fmt", "")).lower()
    width = int(info.get("width") or 0)
    height = int(info.get("height") or 0)
    long_side = max(width, height)

    if pix not in ("yuv420p", "yuvj420p", "yuv420p10le"):
        return False
    if codec in ("hevc", "h265"):
        return 0 < long_side <= 3840       # iPhone quay 4K bằng HEVC -> nhận
    if codec == "h264":
        # iPhone chỉ quay H.264 tới 1080p; 4K / level cao bị Thư viện từ chối.
        level = info.get("level")
        level = level if isinstance(level, int) and level > 0 else 99
        return level <= 42 and 0 < long_side <= 1920
    return False


def normalize_to_ios(src: Path | str, dst: Path | str) -> None:
    """Re-encode ``src`` sang H.264 1080p chuẩn iOS, ghi ra ``dst`` (mp4).

    Hạ cạnh dài về tối đa 1920 (giữ nguyên nếu đã nhỏ hơn), H.264 High level 4.0,
    ``yuv420p``, AAC, ``+faststart`` — đúng khuôn iPhone tự quay, chắc chắn nạp
    được. Nhanh (preset veryfast) để hợp việc xử lý hàng loạt.
    """

    ffmpeg = ffmpeg_path()
    if not ffmpeg:
        raise RuntimeError("không tìm thấy ffmpeg trên PATH")
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    scale = ("scale='min(1920,iw)':'min(1920,ih)':force_original_aspect_ratio=decrease,"
             "scale=trunc(iw/2)*2:trunc(ih/2)*2")
    cmd = [
        ffmpeg, "-y", "-i", str(src),
        "-vf", scale,
        "-c:v", "libx264", "-profile:v", "high", "-level", "4.0",
        "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(dst),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg re-encode thất bại ({result.returncode}): "
            f"{result.stderr.strip()[-400:]}"
        )


def ensure_ios_media(path: Path | str,
                     cache_dir: Path | str = DEFAULT_CACHE) -> Path:
    """Trả về đường dẫn file **nên đẩy** cho iOS.

    - Ảnh, hoặc video đã đạt chuẩn: trả về nguyên bản.
    - Video chưa đạt chuẩn (và có ffmpeg): re-encode vào ``cache_dir`` rồi trả về
      file mới. Có cache: file nguồn không đổi thì dùng lại bản đã transcode.
    - Thiếu ffmpeg/ffprobe: trả về nguyên bản (để máy tự báo lỗi nếu không nạp
      được, thay vì âm thầm không làm gì).
    """

    path = Path(path)
    if not is_video(path):
        return path                        # ảnh hoặc đuôi lạ -> để nguyên
    if not (ffmpeg_path() and ffprobe_path()):
        log.warning("thiếu ffmpeg/ffprobe — đẩy video nguyên bản, có thể bị iOS từ chối")
        return path
    if path.suffix.lower() in READY_VIDEO_EXTS and video_is_ios_ready(path):
        return path

    cache_dir = Path(cache_dir)
    dst = cache_dir / (path.stem + "_ios.mp4")
    try:
        if (dst.exists() and dst.stat().st_size > 0
                and dst.stat().st_mtime >= path.stat().st_mtime):
            return dst                     # đã transcode trước đó, nguồn không đổi
    except OSError:
        pass
    normalize_to_ios(path, dst)
    return dst
