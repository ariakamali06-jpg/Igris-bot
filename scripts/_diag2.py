import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.assetgen import ensure_assets
from services.compositor import RenderRequest, compositor, load_library

ensure_assets()
load_library()

shutil.rmtree(compositor._cache_dir, ignore_errors=True)

OUTFITS = [
    {"legs": "techwear_cargo", "body": "hunter_trench", "head": "raven_shag",
     "accessory": "arcane_eye_mark", "weapon": "shadow_katana", "aura": "dark_flame"},
    {"legs": "shadow_wargreaves", "body": "void_cuirass", "head": "crown_of_shadows",
     "accessory": "phantom_visage", "weapon": "arcane_gauntlet", "aura": "lightning_crackle"},
    {},
    {"legs": "street_slacks", "body": "fitted_tee", "head": "street_fade",
     "accessory": "tactical_goggles", "weapon": "police_baton", "aura": "ethereal_smoke"},
]
reqs = [RenderRequest(display_name="Op %d" % i, level=3 + i * 7, atk=20 + i * 15,
                      defense=12 + i * 11, drip=9 + i * 9, loadout=o,
                      background=("alley_neon", "rooftop_zenith", "sanctum_abyss")[i % 3])
        for i, o in enumerate(OUTFITS)]

phases = {}


def timed(name, fn):
    t = time.perf_counter()
    out = fn()
    phases[name] = phases.get(name, 0.0) + (time.perf_counter() - t) * 1000
    return out


for i, r in enumerate(reqs):
    t0 = time.perf_counter()
    key = timed("key", lambda r=r: compositor._cache_key(r))
    hit = timed("mem", lambda key=key: compositor._memory.get(key))
    disk = timed("disk_read", lambda key=key: compositor._read_disk(key))
    png = timed("compose", lambda r=r: compositor._compose(r))
    timed("disk_write", lambda key=key, png=png: compositor._write_disk(key, png))
    print("req%d total %.1f ms (cache=%s)" % (i, (time.perf_counter() - t0) * 1000,
                                              hit is not None or disk is not None))
    compositor._memory[key] = png

print()
for k, v in phases.items():
    print("%-12s %8.2f ms total" % (k, v))
