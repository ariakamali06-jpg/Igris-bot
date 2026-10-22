# Final sprite polish: rembg (u2net) subject segmentation + pixel-snap + limited palette.
# Run with /opt/data/venvs/rembg/bin/python (separate venv, has rembg + onnxruntime).
# Usage: polish_rembg.py in.png [more.png]   -- writes <stem>.png next to input.
import io
import sys
from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter

sys.path.insert(0, "/data/igris_bot/scripts")
from gen_asset import pixel_snap  # noqa: E402

from rembg import new_session, remove as rm  # noqa: E402

# مدل کوچک u2net (176MB) به‌جای bria-rmbg (1GB) — /data فقط 38MB جا دارد.
# سقف cgroup حدود 954MB است؛ ORT با 48 thread کل process را OOM می‌کرد → تک‌thread.
import onnxruntime as ort  # noqa: E402

_so = ort.SessionOptions()
_so.intra_op_num_threads = 1
_so.inter_op_num_threads = 1
_so.enable_mem_pattern = False
_so.enable_cpu_mem_arena = False
_so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_BASIC
# u2netp: 4.6MB به‌جای u2net 176MB — سقف حافظه‌ی cgroup حدود 954MB است
_session = new_session("u2netp", sess_opts=_so)

OUT_W = 640
GRID = 160
COLORS = 32


def sharpen_alpha(im):
    px = im.load()
    w, h = im.size
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a < 8:
                px[x, y] = (r, g, b, 0)
            elif a < 200:
                px[x, y] = (r, g, b, int(a * a / 200))
    return im


def refine_mask(im):
    """پرکردن سوراخ‌های داخل ماسک + حذف جزایر جدا (blade شناور و ...)."""
    from collections import deque

    px = im.load()
    w, h = im.size
    solid = bytearray(w * h)
    for y in range(h):
        row = y * w
        for x in range(w):
            if px[x, y][3] >= 128:
                solid[row + x] = 1
    # 1) flood از لبه‌ها روی non-solid → پس‌زمینه‌ی واقعی؛ non-solidِ نرسیده = سوراخ
    seen = bytearray(w * h)
    dq: deque[int] = deque()
    for x in range(w):
        for i in (x, (h - 1) * w + x):
            if not solid[i] and not seen[i]:
                seen[i] = 1
                dq.append(i)
    for y in range(h):
        for i in (y * w, y * w + w - 1):
            if not solid[i] and not seen[i]:
                seen[i] = 1
                dq.append(i)
    while dq:
        i = dq.popleft()
        x, y = i % w, i // w
        for j in (i - 1 if x else -1, i + 1 if x < w - 1 else -1,
                  i - w if y else -1, i + w if y < h - 1 else -1):
            if j >= 0 and not solid[j] and not seen[j]:
                seen[j] = 1
                dq.append(j)
    for i in range(w * h):
        if not solid[i]:
            x, y = i % w, i // w
            if seen[i]:
                r, g, b, _ = px[x, y]
                px[x, y] = (r, g, b, 0)
            else:
                solid[i] = 1
                r, g, b, _ = px[x, y]
                px[x, y] = (r, g, b, 255)
    # ۲) اتصال روی ماسکِ اِعمال‌شده (dilate 6px شکاف‌های شمشیر/چفته را پل می‌زند)،
    #    بعد فقط پیکسل‌های اصلیِ داخل بزرگ‌ترین جزیره می‌مانند؛ blade شناور حذف می‌شود.
    m = Image.new("L", (w, h), 0)
    mp = m.load()
    for y in range(h):
        row = y * w
        for x in range(w):
            if solid[row + x]:
                mp[x, y] = 255
    md = m.filter(ImageFilter.MaxFilter(13)).load()
    dil = bytearray(w * h)
    for y in range(h):
        row = y * w
        for x in range(w):
            if md[x, y] >= 128:
                dil[row + x] = 1
    # ۲ب) اجزای اصلیِ کوچک (<۱.۵٪ بزرگ‌ترین) حتی اگر dilation به بدنه وصلشان کند = آشغال‌اند
    def components(mask):
        comp = bytearray(w * h)
        cid = 0
        sizes = {}
        for start in range(w * h):
            if not mask[start] or comp[start]:
                continue
            cid += 1
            n = 0
            dq.append(start)
            comp[start] = cid
            while dq:
                i = dq.popleft()
                n += 1
                x, y = i % w, i // w
                for j in (i - 1 if x else -1, i + 1 if x < w - 1 else -1,
                          i - w if y else -1, i + w if y < h - 1 else -1):
                    if j >= 0 and mask[j] and not comp[j]:
                        comp[j] = cid
                        dq.append(j)
            sizes[cid] = n
        return comp, sizes

    solid_comp, solid_sizes = components(solid)
    main_cid = max(solid_sizes, key=solid_sizes.get)
    for i in range(w * h):
        c = solid_comp[i]
        if c and c != main_cid and solid_sizes[c] * 100 < solid_sizes[main_cid] * 3 // 2:
            solid[i] = 0

    comp = bytearray(w * h)
    cid = 0
    best_id, best_len = 0, 0
    for start in range(w * h):
        if not dil[start] or comp[start]:
            continue
        cid += 1
        n = 0
        dq.append(start)
        comp[start] = cid
        while dq:
            i = dq.popleft()
            n += 1
            x, y = i % w, i // w
            for j in (i - 1 if x else -1, i + 1 if x < w - 1 else -1,
                      i - w if y else -1, i + w if y < h - 1 else -1):
                if j >= 0 and dil[j] and not comp[j]:
                    comp[j] = cid
                    dq.append(j)
        if n > best_len:
            best_id, best_len = cid, n
    for i in range(w * h):
        if solid[i] and comp[i] != best_id:
            x, y = i % w, i // w
            r, g, b, _ = px[x, y]
            px[x, y] = (r, g, b, 0)
    # ۳) Erosion هدایت‌شده با روشنایی: حلقه‌ی نورانی که مدل دور سیاه‌پوش‌ها می‌کشد
    #    (luma بالا) فقط لبه‌ی روشن حذف می‌شود؛ اوت‌لاین تیره‌ی سالم دست‌نخورده می‌ماند.
    for _ in range(4):
        kill = []
        for y in range(h):
            for x in range(w):
                r, g, b, a = px[x, y]
                if a == 0:
                    continue
                if 0.299 * r + 0.587 * g + 0.114 * b <= 118:
                    continue
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < w and 0 <= ny < h and px[nx, ny][3] == 0:
                        kill.append((x, y))
                        break
        if not kill:
            break
        for x, y in kill:
            r, g, b, _ = px[x, y]
            px[x, y] = (r, g, b, 0)
    # ۴) despill نهایی: پیکسل خنثی-روشن چسبیده به شفافیت حتی اگر luma مرز را رد کرده باشد
    for _ in range(2):
        kill = []
        for y in range(h):
            for x in range(w):
                r, g, b, a = px[x, y]
                if a == 0:
                    continue
                if min(r, g, b) <= 205 or max(r, g, b) - min(r, g, b) > 34:
                    continue
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < w and 0 <= ny < h and px[nx, ny][3] == 0:
                        kill.append((x, y))
                        break
        if not kill:
            break
        for x, y in kill:
            r, g, b, _ = px[x, y]
            px[x, y] = (r, g, b, 0)
    return im


def process(path: Path, seg: bool = True) -> str:
    src = Image.open(path).convert("RGBA")
    if seg:
        buf = io.BytesIO()
        src.convert("RGB").save(buf, format="PNG")
        cut = rm(buf.getvalue(), alpha_matting=False, session=_session)
        im = Image.open(io.BytesIO(cut)).convert("RGBA")
        im = sharpen_alpha(im)
    else:
        # فقط refine روی ماسک موجود (خروجی مرحله‌ی قبل)
        im = sharpen_alpha(src.copy())
    im = refine_mask(im)
    bbox = im.getbbox()
    if bbox:
        im = im.crop(bbox)
    # آپ‌سکیل بیش از ۱.۳۵x ممنوع — تصویر تار می‌شود و QA آن را «pixel art نیست» می‌خواند
    target = OUT_W if im.width >= OUT_W else min(OUT_W, round(im.width * 1.35))
    if im.width != target:
        ratio = im.height / im.width
        im = im.resize((target, max(1, round(target * ratio))), Image.LANCZOS)
    # erode 1px درست قبل از snap: بعد از snap، هر بلوک 3px است و erode نامرتبش می‌کند
    a = im.split()[3].point(lambda v: 0 if v < 160 else 255)
    im.putalpha(a.filter(ImageFilter.MinFilter(3)).point(lambda v: 0 if v < 160 else 255))
    im = pixel_snap(im, GRID)
    alpha = im.split()[3]
    rgb = im.convert("RGB").quantize(colors=COLORS, method=Image.Quantize.MEDIANCUT).convert("RGB")
    rgb = ImageEnhance.Contrast(rgb).enhance(1.2)
    # اگر اسپرایت خیلی تیره است (لوم < 100) سایه‌های مرده را باز کن
    hist = rgb.convert("L").histogram()
    n_op = sum(hist)
    if n_op:
        lum = sum(i * c for i, c in enumerate(hist)) / n_op
        if lum < 100:
            lo = max(0, min(80, int(lum) - 50))
            lut = [max(0, min(255, int((v - lo) * 255 / (255 - lo)))) for v in range(256)]
            rgb = rgb.point(lut * 3)
    out = rgb.convert("RGBA")
    out.putalpha(alpha)
    g2 = out.split()[3].point(lambda v: 0 if v < 160 else 255)
    out.putalpha(g2)
    bb = out.getbbox()
    if bb:
        out = out.crop(bb)
    dst = path.with_suffix(".png")
    out.save(dst)
    op = sum(1 for p in out.split()[3].getdata() if p > 200)
    tot = out.width * out.height
    return f"{dst.name}: {out.size} opaque={100*op//tot}%"


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--refine"]
    do_seg = "--refine" not in sys.argv
    for f in args:
        try:
            print(process(Path(f), seg=do_seg))
        except Exception as e:
            print(f"{f}: FAILED {e}")
