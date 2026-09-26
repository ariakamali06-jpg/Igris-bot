import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image

from services.assetgen import ensure_assets
from services.compositor import CANVAS, RenderRequest, compositor, load_library

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


def bench(label, fn, n=30):
    fn()
    t = time.perf_counter()
    for _ in range(n):
        fn()
    print("%-28s %8.2f ms" % (label, (time.perf_counter() - t) / n * 1000))


# warm the memory cache first so we isolate the real work
compositor.render(req, use_cache=False)

bench("full _compose", lambda: compositor._compose(req))
img = compositor._compose.__wrapped__ if hasattr(compositor._compose, "__wrapped__") else None

# encode variants
canvas = Image.new("RGBA", (CANVAS, CANVAS), (20, 20, 30, 255))
import io


def enc_optimize():
    b = io.BytesIO()
    canvas.save(b, format="PNG", optimize=True)
    return b.getvalue()


def enc_default():
    b = io.BytesIO()
    canvas.save(b, format="PNG")
    return b.getvalue()


def enc_level1():
    b = io.BytesIO()
    canvas.save(b, format="PNG", compress_level=1)
    return b.getvalue()


bench("encode optimize=True", enc_optimize)
bench("encode default", enc_default)
bench("encode compress_level=1", enc_level1)

# alpha composite cost
bg = compositor._assets.get("backgrounds", "alley_neon")
layers = [
    compositor._assets.get("body", "base_street"),
    compositor._assets.get("legs", "techwear_cargo"),
    compositor._assets.get("tops", "hunter_trench"),
    compositor._assets.get("hair", "raven_shag"),
    compositor._assets.get("accessories", "arcane_eye_mark"),
    compositor._assets.get("weapons", "shadow_katana"),
    compositor._assets.get("auras", "dark_flame"),
]


def composite_only():
    c = bg.copy()
    for lay in layers:
        c = Image.alpha_composite(c, lay)
    return c


bench("8x alpha_composite", composite_only)


def hud_only():
    return compositor._hud(req)


bench("hud draw", hud_only)
