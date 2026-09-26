"""Render-budget benchmark.

Measures the full ``Compositor.render`` path with a cold cache (memory + disk),
which is the worst case a Telegram handler can hit.
"""

import shutil
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.assetgen import ensure_assets
from services.compositor import RenderRequest, compositor, load_library

ensure_assets()
load_library()

from database.items import ITEMS  # noqa: E402
from models.enums import Slot  # noqa: E402

by_slot: dict[Slot, list[str]] = {}
for it in ITEMS:
    by_slot.setdefault(it.slot, []).append(it.id)

slots = list(by_slot)
n = max(len(v) for v in by_slot.values())
bgs = ["alley_neon", "rooftop_zenith", "sanctum_abyss"]

samples: list[RenderRequest] = [RenderRequest(display_name="Bare", level=1, atk=10,
                                              defense=8, drip=5)]
for i in range(n):
    lo = {s.value: by_slot[s][i % len(by_slot[s])] for s in slots}
    for bg in bgs:
        samples.append(RenderRequest(
            display_name="Operative %02d" % i, username="op%d" % i,
            level=1 + i * 5, atk=10 + i * 9, defense=8 + i * 7, drip=5 + i * 6,
            loadout=dict(lo), background=bg,
        ))

# One warm-up render outside the sample set: fonts, HUD template, JPEG tables.
compositor.render(RenderRequest(display_name="warmup", level=1, atk=1, defense=1, drip=1),
                  use_cache=False)

shutil.rmtree(compositor._cache_dir, ignore_errors=True)
compositor.clear_cache()

compose_times = []
times = []
for req in samples:
    t = time.perf_counter()
    compositor._compose(req)
    compose_times.append((time.perf_counter() - t) * 1000)

    t = time.perf_counter()
    png = compositor.render(req, use_cache=True)
    times.append((time.perf_counter() - t) * 1000)
    assert png and png[:2] == b"\xff\xd8", "expected JPEG bytes"
    assert compositor.render(req)  # second call should be a cache hit


def report(label, values):
    s = sorted(values)
    print("%-20s mean %5.1f  median %5.1f  p95 %6.1f  max %6.1f  <50ms %d/%d" % (
        label, statistics.mean(values), statistics.median(values),
        s[int(len(s) * 0.95) - 1], max(values),
        sum(1 for v in values if v < 50), len(values)))


print("samples              : %d" % len(times))
report("_compose (pure)", compose_times)
report("render (end to end)", times)

t = time.perf_counter()
for _ in range(100):
    compositor.render(samples[1])
print("cache hit            : %6.2f ms" % ((time.perf_counter() - t) / 100 * 1000))

compositor.close()
