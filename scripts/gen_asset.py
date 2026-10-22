#!/usr/bin/env python3
"""خط لوله تولید رایگان asset بازی — بدون کلید، بدون هزینه.

حالت‌ها:
  object : تولید آبجکت روی پس‌زمینه‌ی یکدست بنفش/ماجنتا + حذف خودکار پس‌زمینه -> PNG شفاف
  bg     : تولید بکگراند بازی (برش حاشیه‌ی واترمارک) -> JPG
  batch  : خواندن لیست پرامپت از فایل و تولید یکی‌یکی

منابع (هر دو رایگان و بدون اکانت):
  1) Kavel anonymous API — کیفیت 1024، واترمارک حاشیه‌ی پایین (برش می‌خورد)
  2) Pollinations.ai — پشتیبان؛ پرامپت باید کوتاه باشد و بین درخواست‌ها فاصله بگذارید (402 = فشار بیش از حد)

نمونه:
  python3 gen_asset.py object "single iron sword, flat solid magenta background" -o out.png
  python3 gen_asset.py bg "night medieval city street, 2D painted side scroller" -o out.jpg
  python3 gen_asset.py batch prompts.txt --mode bg --outdir assets/bg
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import random
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"

# پرامپت پایه‌ی آبجکت: پس‌زمینه‌ی یکدست برای chroma-key
OBJECT_STYLE = (
    ", 2D game inventory item, clean bold outlines, flat shading, "
    ", single object centered, object fills most of the frame, no other objects, "
    "isolated on flat solid pure white #FFFFFF background, "
    "no frame, no circle, no border, no shadow, no text"
)
BG_SUFFIX = (
    ", native pixel art game background, crisp square pixels, no anti-aliasing, dithered gradient, "
    "limited retro 16-bit palette, bold outlines, high detail, no text, no watermark"
)
# پیشوند ثابت سبک — از assets/STYLE.md؛ همه‌ی تولیدها با این شروع می‌شوند
STYLE_PREFIX = ("16-bit pixel art, dark fantasy, Castlevania style, dithered shading, "
                "moody sunset palette, ")
CHAR_SUFFIX = (
    ", native pixel art, crisp chunky pixel grid, visible square pixels, no anti-aliasing, "
    "limited retro 16-bit color palette, dithering, bold dark outlines, flat shading, "
    "bright even lighting, vivid colors, high contrast, "
    "full body standing pose, centered, character fills most of the frame, "
    "entire figure visible, no other objects, "
    "isolated on completely plain flat solid pure white #FFFFFF background, "
    "no scene, no floor, no ground, no platform, no spotlight, no glow, no halo, "
    "no frame, no circle, no border, no shadow, no text"
)


def _http(url: str, *, data: bytes = None, headers: dict | None = None,
          timeout: int = 90) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read() or b""
    except Exception as e:  # noqa: BLE001
        return 0, str(e).encode()


# ---------------------------------------------------------------- kavel ----
_KAVEL_WALLED = False  # دیوار روزانه‌ی IP (anon_ip_daily) یک‌بار تشخیص داده شد -> تا آخر روز skip


def kavel_generate(prompt: str, aspect: str = "1:1", tries: int = 3) -> bytes | None:
    """تولید با Kavel anonymous. هر anon-id فقط ۵ اعتبار دارد -> یک uuid تازه per request."""
    global _KAVEL_WALLED
    if _KAVEL_WALLED:
        return None
    for attempt in range(tries):
        anon = f"anon_{uuid.uuid4()}"
        payload = json.dumps({
            "provider": "kie", "mediaType": "image", "model": "kavel-image-v1",
            "scene": "text-to-image", "prompt": prompt,
            "options": {"aspect_ratio": aspect},
        }).encode()
        code, body = _http("https://www.kavel.ai/api/ai/generate", data=payload,
                           headers={"content-type": "application/json", "x-anon-id": anon},
                           timeout=30)
        if code != 200:
            time.sleep(3 * (attempt + 1)); continue
        try:
            resp = json.loads(body)
            if resp.get("data", {}).get("wall"):
                _KAVEL_WALLED = True
                print("kavel: daily wall hit -> switching to pollinations for today", flush=True)
                return None
            tid = resp["data"]["id"]
        except Exception:
            time.sleep(3); continue

        # poll — معمولاً 40 تا 75 ثانیه
        for _ in range(30):
            time.sleep(3)
            _, qb = _http(
                f"https://www.kavel.ai/api/ai/anon-query?taskId={tid}&provider=kie&mediaType=image",
                headers={"x-anon-id": anon}, timeout=25)
            try:
                d = json.loads(qb)
            except Exception:
                continue
            data = d.get("data") or {}
            if (data.get("status") or d.get("status")) == "success":
                imgs = data.get("images") or []
                first = imgs[0] if imgs else None
                img_url = first.get("url") if isinstance(first, dict) else first
                if not img_url:
                    img_url = data.get("image")
                if img_url:
                    _, raw = _http(img_url, headers={"User-Agent": UA,
                                                     "Referer": "https://www.kavel.ai/"})
                    if raw and len(raw) > 3000:
                        return raw
                break
        time.sleep(2 * (attempt + 1))
    return None


# ---------------------------------------------------------- pollinations ----
_ASPECT_PX = {"1:1": (1024, 1024), "2:3": (896, 1344), "16:9": (1280, 720),
              "3:2": (1344, 896), "9:16": (768, 1344)}


def pollinations_generate(prompt: str, aspect: str = "1:1", tries: int = 3) -> bytes | None:
    """پشتیبان رایگان. پرامپت را کوتاه نگه دارید؛ 402/500 یعنی فشار بیش از حد -> صبر."""
    # کلاه ۱۸۰ کاراکتری قدیمی برداشته شد: پرامپت ۴۹۵ کاراکتری هم تست شد و 200 داد؛
    # خطای 402 صرفاً rate-limit است که با فاصله‌گذاری/تلاش مجدد حل می‌شود.
    short = prompt if len(prompt) <= 700 else prompt[:400] + " ... " + prompt[-290:]
    base = "https://image.pollinations.ai/prompt/" + urllib.parse.quote(short)
    w, h = _ASPECT_PX.get(aspect, (1024, 1024))
    sized = f"{base}?width={w}&height={h}&nologo=true&seed={random.randint(1, 2**31)}"
    for attempt in range(tries):
        if attempt:
            time.sleep(20 * attempt)
        # اول با سایز دلخواه؛ اگر سرور پارامتر/سایز را نپذیرفت، بدون پارامتر
        for url in (sized, base):
            code, body = _http(url, headers={"User-Agent": UA}, timeout=110)
            if code == 200 and len(body) > 5000:
                return body
        time.sleep(5)
    return None


def generate(prompt: str, aspect: str = "1:1") -> bytes | None:
    return kavel_generate(prompt, aspect) or pollinations_generate(prompt, aspect)


# ------------------------------------------------------------ postproc ------
def strip_watermark(im):
    """حاشیه‌ی پایین (~70px) و کناره‌ها (~35px) که واترمارک Kavel در آن است."""
    from PIL import Image  # noqa: F401  (import inside so --help works without Pillow)
    w, h = im.size
    return im.crop((35, 0, w - 35, h - 70))


def chroma_key(im, lo: int = 60, hi: int = 130):
    """حذف پس‌زمینه‌ی ماجنتای یکدست -> PNG شفاف با لبه‌ی نرم."""
    im = im.convert("RGBA")
    w, h = im.size
    px = im.load()
    corners = [px[3, 3], px[w - 4, 3], px[3, h - 4], px[w - 4, h - 4]]
    ref = tuple(sum(c[i] for c in corners) // 4 for i in range(3))
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            d = math.sqrt((r - ref[0]) ** 2 + (g - ref[1]) ** 2 + (b - ref[2]) ** 2)
            if d < lo:
                px[x, y] = (r, g, b, 0)
            elif d < hi:
                px[x, y] = (r, g, b, int(255 * (d - lo) / (hi - lo)))
    bbox = im.getbbox()
    return im.crop(bbox) if bbox else im


def pixel_snap(im, grid_w: int = 160):
    """پیکسل‌سازی واقعی: BOX-downscale به شبکه‌ی پیکسلی + NEAREST-upscale.

    خروجی AI صاف/تار است؛ این مرحله پیکسل‌های مربعی واقعی می‌سازد (همان چیزی که
    در QA «crisp pixel art» خوانده می‌شود). برای اسپرایت شفاف RGBA امن است.
    """
    from PIL import Image
    ratio = im.height / im.width
    gw = min(grid_w, im.width)
    gh = max(1, round(gw * ratio))
    small = im.resize((gw, gh), Image.BOX)
    scale = max(1, round(im.width / gw))
    return small.resize((gw * scale, gh * scale), Image.NEAREST)


def save_object(raw: bytes, out: Path, lo: int = 60, hi: int = 130,
                grid_w: int = 160, raw_mode: bool = False) -> str:
    import io
    from PIL import Image
    im = Image.open(io.BytesIO(raw))
    im = strip_watermark(im)
    if raw_mode:
        # rembg جداسازی را بعداً انجام می‌دهد؛ کروما-کی چهره/زره‌ی روشن را سوراخ می‌کند
        out = out.with_suffix(".png")
        im.convert("RGB").save(out)
        return f"raw {im.size} -> {out}"
    im = chroma_key(im, lo, hi)
    if grid_w:
        im = pixel_snap(im, grid_w)  # پیکسل‌سازی واقعی به‌جای آپسکیل نرم LANCZOS
    out = out.with_suffix(".png")
    im.save(out)
    return f"object {im.size} -> {out}"


def save_bg(raw: bytes, out: Path, grid_w: int = 240) -> str:
    import io
    from PIL import Image
    im = Image.open(io.BytesIO(raw)).convert("RGB")
    im = strip_watermark(im)
    if grid_w:
        im = pixel_snap(im, grid_w)  # آسمان/معماری هم پیکسلی واقعی می‌شود
    # پالت محدود ۱۶-بیتی: گرادیان‌های نرم «دیجی‌پینگ» خوانده می‌شوند؛ اینجا flat می‌شود
    im = im.quantize(colors=40, method=Image.Quantize.MEDIANCUT).convert("RGB")
    out = out.with_suffix(".jpg")
    im.save(out, quality=95)
    return f"bg {im.size} -> {out}"


# ------------------------------------------------------------------ main ----
def main() -> int:
    ap = argparse.ArgumentParser(description="free AI game-asset pipeline")
    ap.add_argument("mode", choices=["object", "char", "bg", "batch"])
    ap.add_argument("prompt", nargs="?", help="پرامپت (در حالت batch: فایل لیست پرامپت)")
    ap.add_argument("-o", "--out", default="asset_out")
    ap.add_argument("--outdir", default="assets")
    ap.add_argument("--type", default="object", choices=["object", "char", "bg"],
                    help="نوع پردازش خروجی در حالت batch")
    ap.add_argument("--ar", default="1:1", help="نسبت تصویر مثل 16:9 برای bg")
    ap.add_argument("--grid", type=int, default=160,
                    help="عرض شبکه‌ی پیکسلی برای pixel-snap (0 = خاموش، فقط برای object/char)")
    ap.add_argument("--raw", action="store_true",
                    help="برای char: بدون chroma-key ذخیره کن (rembg بعداً جدا می‌کند)")
    args = ap.parse_args()

    from PIL import Image  # noqa: F401  fail fast if Pillow missing

    eff_mode = args.type if args.mode == "batch" else args.mode
    jobs: list[tuple[str, Path]] = []
    if args.mode == "batch":
        lines = [l.strip() for l in Path(args.prompt).read_text().splitlines()
                 if l.strip() and not l.startswith("#")]
        outdir = Path(args.outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        jobs = [(l, outdir / f"{i:03d}") for i, l in enumerate(lines)]
    else:
        if not args.prompt:
            ap.error("prompt لازم است")
        jobs = [(args.prompt, Path(args.out))]

    ok = 0
    for i, (p, outpath) in enumerate(jobs):
        if i:
            time.sleep(8)  # فاصله‌گذاری برای جلوگیری از 402
        if eff_mode == "object":
            full, aspect = STYLE_PREFIX + p + OBJECT_STYLE, "1:1"
        elif eff_mode == "char":
            full, aspect = STYLE_PREFIX + p + CHAR_SUFFIX, "2:3"
        else:
            full, aspect = STYLE_PREFIX + p + BG_SUFFIX, args.ar
        raw = generate(full, aspect=aspect)
        if not raw:
            print(f"[{i}] FAILED: {p[:60]}", flush=True)
            continue
        try:
            if eff_mode == "object":
                msg = save_object(raw, outpath, grid_w=args.grid)
            elif eff_mode == "char":
                # آستانه‌ی تنگ تا کاپ لنگ قرمزی/طلایی کاراکتر خورده نشود
                msg = save_object(raw, outpath, lo=45, hi=90, grid_w=args.grid,
                                  raw_mode=args.raw)
            else:
                msg = save_bg(raw, outpath, grid_w=max(args.grid, 240))
            print(f"[{i}] {msg}", flush=True)
            ok += 1
        except Exception as e:  # noqa: BLE001
            print(f"[{i}] postproc failed: {e}", flush=True)
    print(f"done: {ok}/{len(jobs)}")
    return 0 if ok == len(jobs) else 1


if __name__ == "__main__":
    sys.exit(main())
