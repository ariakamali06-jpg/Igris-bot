import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

t0 = time.perf_counter()
from PIL import ImageFont  # noqa: E402

print("import PIL.ImageFont      %7.1f ms" % ((time.perf_counter() - t0) * 1000))

for size in (13, 15, 24, 25, 30):
    t = time.perf_counter()
    try:
        f = ImageFont.truetype("C:/Windows/Fonts/seguisb.ttf", size)
        kind = "truetype seguisb"
    except OSError as e:
        kind = "err %s" % e
    print("load seguisb %-4s        %7.1f ms  %s" % (
        size, (time.perf_counter() - t) * 1000, kind))

t = time.perf_counter()
f = ImageFont.load_default(size=30)
print("load_default(size=30)     %7.1f ms  %s" % (
    (time.perf_counter() - t) * 1000, type(f).__name__))

t0 = time.perf_counter()
from services.assetgen import ensure_assets  # noqa: E402
from services.compositor import RenderRequest, compositor, hud_template, load_library  # noqa: E402

print("import services           %7.1f ms" % ((time.perf_counter() - t0) * 1000))
ensure_assets()
t = time.perf_counter()
load_library()
print("load_library              %7.1f ms" % ((time.perf_counter() - t) * 1000))

t = time.perf_counter()
hud_template()
print("hud_template build        %7.1f ms" % ((time.perf_counter() - t) * 1000))

req = RenderRequest(display_name="Kaito", level=17, atk=64, defense=41, drip=38,
                    loadout={"legs": "techwear_cargo", "body": "hunter_trench",
                             "head": "raven_shag", "accessory": "arcane_eye_mark",
                             "weapon": "shadow_katana", "aura": "dark_flame"},
                    background="alley_neon")
t = time.perf_counter()
compositor._base(req.background, req.body)
print("first _base               %7.1f ms" % ((time.perf_counter() - t) * 1000))

t = time.perf_counter()
compositor._compose(req)
print("first _compose            %7.1f ms" % ((time.perf_counter() - t) * 1000))
t = time.perf_counter()
compositor._compose(req)
print("second _compose           %7.1f ms" % ((time.perf_counter() - t) * 1000))
t = time.perf_counter()
compositor._compose(req)
print("third _compose            %7.1f ms" % ((time.perf_counter() - t) * 1000))
