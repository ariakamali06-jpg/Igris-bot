import io
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image

from services.assetgen import ensure_assets
from services.compositor import RenderRequest, compositor, load_library

ensure_assets()
load_library()

LIB = compositor._assets
LAYER_KEYS = [
    ("body", "base_street"),
    ("legs", "shadow_wargreaves"),
    ("tops", "void_cuirass"),
    ("hair", "crown_of_shadows"),
    ("accessories", "phantom_visage"),
    ("weapons", "arcane_gauntlet"),
    ("auras", "dark_flame"),
]
bg = LIB.get("backgrounds", "alley_neon")
layers = [LIB.get(f, k) for f, k in LAYER_KEYS]


def bench(label, fn, n=30):
    fn()
    t = time.perf_counter()
    for _ in range(n):
        fn()
    print("%-44s %7.3f ms" % (label, (time.perf_counter() - t) / n * 1000))


def inplace():
    im = bg.copy()
    for lay in layers:
        im.alpha_composite(lay)
    return im


def module():
    im = bg
    for lay in layers:
        im = Image.alpha_composite(im, lay)
    return im


def base_cached():
    # background+body pre-composited once
    b = Image.alpha_composite(bg, layers[0])
    im = b.copy()
    for lay in layers[1:]:
        im.alpha_composite(lay)
    return im


bench("in-place alpha_composite x7", inplace)
bench("module alpha_composite x7", module)
bench("base cache + in-place x6", base_cached)

img = module()
print("mode", img.mode)

enc = {}


def bench_enc(label, fn, n=30):
    fn()
    t = time.perf_counter()
    out = None
    for _ in range(n):
        out = fn()
    ms = (time.perf_counter() - t) / n * 1000
    print("%-44s %7.2f ms  %7d bytes" % (label, ms, len(out)))


def get_rgb_opt():
    lo, _ = img.getchannel("A").getextrema()
    if lo == 255:
        return img.convert("RGB")
    return img


def no_check():
    return img.convert("RGB")





rgb = img.convert("RGB")


def jpg(q, ss):
    b = io.BytesIO()
    rgb.save(b, format="JPEG", quality=q, subsampling=ss)
    return b.getvalue()


bench_enc("jpeg q92 ss=0", lambda: jpg(92, 0))
bench_enc("jpeg q88 ss=0", lambda: jpg(88, 0))
bench_enc("jpeg q85 ss=0", lambda: jpg(85, 0))
bench_enc("jpeg q92 ss=1", lambda: jpg(92, 1))
bench_enc("jpeg q88 ss=1", lambda: jpg(88, 1))
bench_enc("jpeg q92 ss=2", lambda: jpg(92, 2))

# HUD
bench("hud strip", lambda: compositor._hud(RenderRequest(
    display_name="Operator", level=42, atk=99, defense=77, drip=66,
    loadout={}, background="alley_neon")))
