import io
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


def bench(label, fn, n=20):
    fn()
    t = time.perf_counter()
    out = None
    for _ in range(n):
        out = fn()
    ms = (time.perf_counter() - t) / n * 1000
    print("%-34s %8.2f ms  %7d bytes" % (label, ms, len(out)))


captured: dict = {}


def spy(image):
    captured["img"] = image
    return b""


compositor._encode = spy
compositor._compose(req)
img = captured["img"]
print("mode:", img.mode, img.size)


def png(level, optimize=False):
    b = io.BytesIO()
    img.save(b, format="PNG", compress_level=level, optimize=optimize)
    return b.getvalue()


def jpeg(q):
    b = io.BytesIO()
    img.convert("RGB").save(b, format="JPEG", quality=q, subsampling=0)
    return b.getvalue()


def webp(q, lossless=False):
    b = io.BytesIO()
    img.save(b, format="WEBP", quality=q, lossless=lossless, method=0)
    return b.getvalue()


bench("png level=9 (default)", lambda: png(9))
bench("png level=6", lambda: png(6))
bench("png level=3", lambda: png(3))
bench("png level=1", lambda: png(1))
bench("png level=0", lambda: png(0))
bench("png level=6 optimize=True", lambda: png(6, True))
bench("jpeg q=92", lambda: jpeg(92))
bench("jpeg q=85", lambda: jpeg(85))
bench("webp q=90 method=0", lambda: webp(90))
bench("webp lossless method=0", lambda: webp(0, True))


def compose_no_encode():
    compositor._compose(req)
    return captured["img"].tobytes()


bench("compose (raw pixels out)", compose_no_encode, n=30)
