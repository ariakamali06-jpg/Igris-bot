import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image

from services.assetgen import ensure_assets
from services.compositor import RenderRequest, compositor, load_library

ensure_assets()
load_library()
LIB = compositor._assets

for key in ("alley_neon", "rooftop_zenith", "sanctum_abyss"):
    img = LIB.get("backgrounds", key)
    print("bg %-16s alpha %s" % (key, img.getchannel("A").getextrema()))

for key in ("base_street", "base_aegis"):
    img = LIB.get("body", key)
    print("body %-15s alpha %s" % (key, img.getchannel("A").getextrema()))

plate, _opaque = compositor._base("alley_neon", "base_street")
print("plate           alpha", plate.getchannel("A").getextrema())

img = LIB.get("auras", "dark_flame")
print("aura alpha", img.getchannel("A").getextrema())

# what does the in-place dest composite produce?
hud = compositor._hud(RenderRequest(display_name="T", level=1, atk=1, defense=1, drip=1))
print("hud strip alpha", hud.getchannel("A").getextrema())

canvas = plate
canvas = Image.alpha_composite(canvas, LIB.get("legs", "techwear_cargo"))
print("after legs alpha", canvas.getchannel("A").getextrema())
canvas.alpha_composite(hud, dest=(0, 388))
print("after hud  alpha", canvas.getchannel("A").getextrema())
