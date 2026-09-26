import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.assetgen import ensure_assets
from services.compositor import RenderRequest, load_library, render_card, render_card_timed

ensure_assets()
print("library layers:", load_library())

OUTFITS = [
    {
        "legs": "techwear_cargo",
        "body": "hunter_trench",
        "head": "raven_shag",
        "accessory": "arcane_eye_mark",
        "weapon": "shadow_katana",
        "aura": "dark_flame",
    },
    {
        "legs": "shadow_wargreaves",
        "body": "void_cuirass",
        "head": "crown_of_shadows",
        "accessory": "phantom_visage",
        "weapon": "arcane_gauntlet",
        "aura": "lightning_crackle",
    },
    {},
    {
        "legs": "street_slacks",
        "body": "fitted_tee",
        "head": "street_fade",
        "accessory": "tactical_goggles",
        "weapon": "police_baton",
        "aura": "ethereal_smoke",
    },
]

reqs = [
    RenderRequest(
        display_name=f"Operative {i}",
        username=f"op{i}",
        level=3 + i * 7,
        atk=20 + i * 15,
        defense=12 + i * 11,
        drip=9 + i * 9,
        loadout=outfit,
        background=("alley_neon", "rooftop_zenith", "sanctum_abyss")[i % 3],
    )
    for i, outfit in enumerate(OUTFITS)
]

# --- first-ever render (cold: font loading, encoder init) -------------------
png, ms = render_card_timed(reqs[0])
print("cold render      : %7.1f ms  %6d bytes" % (ms, len(png)))

# --- subsequent distinct renders (cache misses, steady state) ---------------
times = []
for i, r in enumerate(reqs):
    _, ms = render_card_timed(r)
    times.append(ms)
    if i == 0:
        continue
print("distinct renders : %s ms" % ", ".join("%.1f" % t for t in times[1:]))
print("worst miss       : %7.1f ms" % max(times))

# --- cache hits -------------------------------------------------------------
t = time.perf_counter()
for _ in range(50):
    render_card(reqs[1])
print("cache hit        : %7.2f ms" % ((time.perf_counter() - t) / 50 * 1000))

open("cache/_preview.jpg", "wb").write(render_card(reqs[0]))
open("cache/_preview2.jpg", "wb").write(render_card(reqs[1]))
print("wrote cache/_preview.jpg and cache/_preview2.jpg")
