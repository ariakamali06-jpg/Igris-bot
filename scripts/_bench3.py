import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image

from services.assetgen import ensure_assets
from services.compositor import load_library

ensure_assets()
load_library()

from services.compositor import compositor  # noqa: E402

bg = compositor._assets.get("backgrounds", "alley_neon").copy()
layer = compositor._assets.get("tops", "hunter_trench")
layer_semi = compositor._assets.get("auras", "dark_flame")  # semi-transparent

import io


def m(label, fn, n=40):
    fn()
    t = time.perf_counter()
    for _ in range(n):
        fn()
    print("%-38s %7.3f ms" % (label, (time.perf_counter() - t) / n * 1000))


def a_inplace():
    im = bg.copy()
    im.alpha_composite(layer)


def a_module():
    Image.alpha_composite(bg, layer)


def p_mask_rgba():
    im = bg.copy()
    im.paste(layer, (0, 0), layer)


def p_mask_alpha():
    im = bg.copy()
    im.paste(layer, (0, 0), layer.getchannel("A"))


m("alpha_composite in-place", a_inplace)
m("alpha_composite module", a_module)
m("paste mask=RGBA layer", p_mask_rgba)
m("paste mask=A channel", p_mask_alpha)

# correctness: compare in-place vs paste-vs-rgba on a semi-transparent layer
ref = bg.copy()
ref.alpha_composite(layer_semi)

variants = {}
im1 = bg.copy()
im1.paste(layer_semi, (0, 0), layer_semi)
variants["paste RGBA"] = im1

im2 = bg.copy()
im2.paste(layer_semi, (0, 0), layer_semi.getchannel("A"))
variants["paste A"] = im2

im3 = Image.alpha_composite(bg, layer_semi)
variants["module"] = im3

for name, img in variants.items():
    diff = 0
    a = ref.tobytes()
    b = img.tobytes()
    for i in range(0, len(a), 4):
        for c in range(3):
            d = abs(a[i + c] - b[i + c])
            if d > diff:
                diff = d
    print("%-14s max channel diff vs in-place: %d" % (name, diff))

# region composite cost
small = Image.new("RGBA", (512, 124), (0, 0, 0, 0))


def region():
    im = bg.copy()
    im.alpha_composite(small, dest=(0, 388))


m("full-canvas composite of 512x124 strip", region)

# encode helpers
full = compositor._compose.__wrapped__ if False else None
