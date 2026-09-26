import cProfile
import pstats
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.assetgen import ensure_assets
from services.compositor import RenderRequest, compositor, load_library

ensure_assets()
load_library()

req = RenderRequest(
    display_name="Kaito Ren",
    level=17,
    atk=64,
    defense=41,
    drip=38,
    loadout={
        "legs": "techwear_cargo",
        "body": "hunter_trench",
        "head": "raven_shag",
        "accessory": "arcane_eye_mark",
        "weapon": "shadow_katana",
        "aura": "dark_flame",
    },
)
compositor._compose(req)

pr = cProfile.Profile()
pr.enable()
for _ in range(10):
    compositor._compose(req)
pr.disable()
pstats.Stats(pr).sort_stats("cumulative").print_stats(22)
