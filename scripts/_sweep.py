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

bgs = ["alley_neon", "rooftop_zenith", "sanctum_abyss"]
slots = list(by_slot)
n = max(len(v) for v in by_slot.values())

samples: list[tuple[str, dict[str, str]]] = [("alley_neon", {})]
for i in range(n):
    lo = {s.value: by_slot[s][i % len(by_slot[s])] for s in slots}
    for bg in bgs:
        samples.append((bg, dict(lo)))

# warm up fonts / encoder
for bg, lo in samples[:1]:
    compositor._compose(
        RenderRequest(display_name="x", level=1, atk=1, defense=1, drip=1,
                      loadout=lo, background=bg)
    )

worst = (0.0, None)
total = 0.0
for bg, lo in samples:
    req = RenderRequest(
        display_name="Sweep", level=1, atk=10, defense=8, drip=5,
        loadout=lo, background=bg,
    )
    t = time.perf_counter()
    compositor._compose(req)
    ms = (time.perf_counter() - t) * 1000
    total += ms
    if ms > worst[0]:
        worst = (ms, (bg, lo))

print("samples        : %d" % len(samples))
print("mean _compose  : %.1f ms" % (total / len(samples)))
print("worst _compose : %.1f ms" % worst[0])
print("worst outfit   :", worst[1])
