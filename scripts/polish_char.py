"""پولیش نهایی اسپرایت کاراکتر — نسخه ۴ (قرارداد پس‌زمینه‌ی سفید، flood مقایسه‌ای با seed).

طراحی (درس از نسخه‌های قبلی):
  - flood «مقایسه با والد» از outline کاراکتر عبور می‌کرد → داخل شخصیت سوراخ می‌شد.
    اینجا هر پیکسل فقط با رنگ SEED خودش مقایسه می‌شود (نه والد) → هایلایت‌های
    روشن داخلی (صورت کم‌رنگ، فلز) در برابر seed سفید دiffer بزرگ دارند و می‌مانند.
  - floor پایین با چند seed مستقل (هر کدام رنگ خودش) حذف می‌شود؛ زرهِ تیره مرز است.
  - fringe نیمه‌شفاف با آستانه‌ی alpha صفر می‌شود (بدون erode تهاجمی).

استفاده: python3 polish_char.py <file.png> ...
"""
import sys
from pathlib import Path

from PIL import Image, ImageEnhance

SENTINEL = 1  # علامت حذف‌شده در ماسک (bytearray فقط 0-255)


def _cdiff(c1, c2) -> int:
    return abs(c1[0] - c2[0]) + abs(c1[1] - c2[1]) + abs(c1[2] - c2[2])


def _lum(c) -> float:
    return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]


def _sat(c) -> int:
    return max(c) - min(c)


def seed_flood(im: Image.Image, seeds: list[tuple[int, int, tuple[int, int, int]]],
               thresh: int, mask: bytearray) -> None:
    """BFS با مقایسه‌ی ثابت نسبت به رنگ seed (نه رنگ والد)."""
    px = im.load()
    w, h = im.size
    stack = [(x, y) for x, y, _ in seeds]
    seed_map = {(x, y): c for x, y, c in seeds}
    while stack:
        x, y = stack.pop()
        if not (0 <= x < w and 0 <= y < h):
            continue
        i = y * w + x
        if mask[i] == SENTINEL:
            continue
        # رنگ مرجع: seedِ همین جزیره اگر از آن شروع شده، وگرنه seed نزدیک
        ref = seed_map.get((x, y))
        if ref is None:
            # از طریق همسایه آمده؛ ref را از همسایه‌ی حذف‌شده بگیر — برای سادگی
            # مقایسه با رنگ خود پیکسل نسبت به نزدیک‌ترین seed ثبت‌شده در جزیره:
            # چون همه‌ی seeds هم‌رنگ‌اند (سفید/کف زمین)، کافی است نگه داریم کدام seed
            # این جزیره را شروع کرده؛ برای این منظور ref را همسایه تعیین می‌کند:
            ref = None
            for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if 0 <= nx < w and 0 <= ny < h and mask[ny * w + nx] == SENTINEL:
                    # رنگ همسایه‌ی حذف‌شده = رنگ اصلی‌اش در تصویر = تقریباً seed آن جزیره
                    ref = px[nx, ny][:3]
                    break
            if ref is None:
                continue
        c = px[x, y][:3]
        if _cdiff(c, ref) > thresh:
            continue
        mask[i] = SENTINEL
        # رنگ اصلی را نگه دار (فقط alpha صفر) — رفرنسِ همسایه‌ها باید رنگ واقعی
        # همان جزیره باشد؛ صفر کردن RGB باعث می‌شد flood داخل شخصیت تیره بخورد.
        px[x, y] = (c[0], c[1], c[2], 0)
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= nx < w and 0 <= ny < h and mask[ny * w + nx] != SENTINEL:
                stack.append((nx, ny))


def remove_background(im: Image.Image) -> Image.Image:
    px = im.load()
    w, h = im.size
    mask = bytearray(w * h)

    # ۱) پس‌زمینه‌ی روشن از گوشه‌ها/لبه‌ی بالا (فقط اگر واقعاً روشن باشد)
    white_seeds = []
    for x in range(0, w, max(1, w // 12)):
        for y in (0, h - 1):
            if min(px[x, y][:3]) >= 190:
                white_seeds.append((x, y, px[x, y][:3]))
    for y in range(0, h, max(1, h // 12)):
        for x in (0, w - 1):
            if min(px[x, y][:3]) >= 190:
                white_seeds.append((x, y, px[x, y][:3]))
    if white_seeds:
        # همه‌ی seeds تقریباً سفیدند → thresh نسبت به سفید خالص می‌گیریم
        seed_flood(im, [(x, y, (255, 255, 255)) for x, y, _ in white_seeds], 75, mask)

    # ۲) floor/سایه‌ی متصل به لبه‌ی پایین — هر seed رنگ خودش را مرجع دارد؛
    #    زره تیره/رنگی با کفِ خاکستری differ بالا دارد → مرز طبیعی
    floor_seeds = []
    for x in range(0, w, 4):
        c = px[x, h - 1]
        if c[3] > 0 and 40 <= _lum(c) <= 235 and _sat(c) <= 90:
            floor_seeds.append((x, h - 1, c[:3]))
    if floor_seeds:
        seed_flood(im, floor_seeds, 65, mask)

    # ۳) scavenge: کفِ خاکستریِ ته‌تصویر که به لبه‌ی پایین نچسبیده (سایه‌ی زیر پا)
    lo = int(h * 0.96)
    for y in range(lo, h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a > 0 and _sat((r, g, b)) <= 55 and 45 <= _lum((r, g, b)) <= 210:
                px[x, y] = (r, g, b, 0)
    return im


def kill_fringe(im: Image.Image, floor_a: int = 140) -> Image.Image:
    a = im.split()[3].point(lambda v: 255 if v >= floor_a else 0)
    out = im.copy()
    out.putalpha(a)
    return out


def levels_contrast(im: Image.Image) -> Image.Image:
    a = im.split()[3]
    rgb = im.convert("RGB")
    hist = rgb.convert("L").histogram()
    total = sum(hist) or 1
    acc = black = 0
    for v, n in enumerate(hist):
        acc += n
        if acc >= total * 0.02:
            black = v
            break
    black = min(black, 55)
    span = max(1, 255 - black)
    lut = [0 if v <= black else min(255, int((v - black) * 255 / span)) for v in range(256)]
    rgb = rgb.point(lut * 3)
    rgb = ImageEnhance.Contrast(rgb).enhance(1.18)
    out = rgb.convert("RGBA")
    out.putalpha(a)
    return out


def quantize_retro(im: Image.Image, colors: int = 48) -> Image.Image:
    alpha = im.split()[3]
    rgb = im.convert("RGB").quantize(colors=colors, method=Image.Quantize.MEDIANCUT).convert("RGB")
    out = rgb.convert("RGBA")
    out.putalpha(alpha)
    return out


def main() -> int:
    for arg in sys.argv[1:]:
        p = Path(arg)
        im = Image.open(p).convert("RGBA")
        im = remove_background(im)
        im = kill_fringe(im)
        im = levels_contrast(im)
        im = quantize_retro(im)
        bbox = im.getbbox()
        if bbox:
            im = im.crop(bbox)
        im.save(p)
        a = im.split()[3]
        hist = a.histogram()
        total = im.size[0] * im.size[1]
        strip_h = max(1, im.size[1] // 12)
        strip = a.crop((0, im.size[1] - strip_h, im.size[0], im.size[1])).histogram()
        opaque_frac = sum(hist[200:]) / total
        print(f"{p.name}: {im.size} transp={sum(hist[:30]) / total:.0%} "
              f"opaque={opaque_frac:.0%} "
              f"bottom_opaque={sum(strip[200:]) / (im.size[0] * strip_h):.0%}"
              + ("  !! WARN: maybe over/under-keyed" if opaque_frac > 0.45 else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
