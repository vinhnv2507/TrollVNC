"""Scale check: run the pool against N fake phones on this machine.

Validates the core design claim — that connecting to all of them is cheap
because only the visible tier asks for pixels.

    python tools/bench_scale.py --devices 250 --visible 40 --seconds 20
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controlios.config import DeviceSpec, Settings          # noqa: E402
from controlios.vnc.pool import DevicePool                  # noqa: E402
from controlios.vnc.session import Tier                     # noqa: E402
from tests.fake_vnc import FakeVncServer                    # noqa: E402


def rss_mb() -> float:
    try:
        import psutil
        return psutil.Process(os.getpid()).memory_info().rss / 1e6
    except Exception:
        return float("nan")


def cpu_percent(interval: float) -> float:
    try:
        import psutil
        return psutil.Process(os.getpid()).cpu_percent(interval=interval)
    except Exception:
        time.sleep(interval)
        return float("nan")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--devices", type=int, default=250)
    parser.add_argument("--visible", type=int, default=40)
    parser.add_argument("--seconds", type=float, default=20)
    parser.add_argument("--grid-fps", type=float, default=1.0)
    args = parser.parse_args()

    loop = asyncio.new_event_loop()
    servers: list[FakeVncServer] = []

    async def boot():
        for _ in range(args.devices):
            server = FakeVncServer()
            await server.start()
            servers.append(server)

    loop.run_until_complete(boot())
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    print(f"{len(servers)} fake phones listening (RSS {rss_mb():.0f} MB)")

    frames = 0

    def on_frame(_frame):
        nonlocal frames
        frames += 1

    settings = Settings(grid_fps=args.grid_fps, connect_concurrency=24)
    pool = DevicePool(settings, on_frame=on_frame, on_status=lambda k, s, d: None)
    pool.start()

    specs = [DeviceSpec(host="127.0.0.1", port=s.port) for s in servers]
    started = time.monotonic()
    pool.set_devices(specs)

    while time.monotonic() - started < 60:
        stats = pool.stats()
        if stats["online"] == len(specs):
            break
        time.sleep(0.2)
    connect_time = time.monotonic() - started
    print(f"all online in {connect_time:.1f}s · frames so far {frames} · RSS {rss_mb():.0f} MB")

    # Steady state: only `visible` tiles stream, the rest stay IDLE.
    tiers = {spec.key: Tier.IDLE for spec in specs}
    for spec in specs[:args.visible]:
        tiers[spec.key] = Tier.GRID
    pool.set_tiers(tiers)

    time.sleep(2)
    frames_at_start = frames
    t0 = time.monotonic()
    cpu = cpu_percent(args.seconds)
    elapsed = time.monotonic() - t0
    streamed = frames - frames_at_start

    print(
        f"steady state · {args.visible} tiles at {args.grid_fps} fps · "
        f"{streamed} frames in {elapsed:.1f}s "
        f"({streamed / elapsed:.1f} fps total) · CPU {cpu:.0f}% · RSS {rss_mb():.0f} MB"
    )
    print(f"requests served: {sum(s.update_requests for s in servers)}")

    pool.stop()

    async def shutdown():
        await asyncio.gather(*(s.stop() for s in servers))

    asyncio.run_coroutine_threadsafe(shutdown(), loop).result(timeout=15)
    loop.call_soon_threadsafe(loop.stop)
    thread.join(timeout=5)
    return 0


if __name__ == "__main__":
    sys.exit(main())
