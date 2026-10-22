"""QA programmatic اسپرایت‌ها: شفافیت، روشنایی، زمینه‌ی جا‌مانده، بررسی گوشه."""
import glob
import sys

from PIL import Image

for p in sorted(glob.glob(sys.argv[1] if len(sys.argv) > 1 else "/data/igris_bot/assets/char/*.png")):
    im = Image.open(p).convert("RGBA")
    w, h = im.size
    a = im.split()[3]
    hist = a.histogram()
    total = w * h
    transp = sum(hist[:30]) / total
    opaque = sum(hist[200:]) / total
    px = im.load()
    step = max(1, w // 120)
    lum_sum = lum_n = 0
    for y in range(0, h, step):
        for x in range(0, w, step):
            r, g, b, al = px[x, y]
            if al > 200:
                lum_sum += 0.299 * r + 0.587 * g + 0.114 * b
                lum_n += 1
    lum = lum_sum / lum_n if lum_n else -1
    strip_h = max(1, int(h * 0.08))
    s_hist = a.crop((0, h - strip_h, w, h)).histogram()
    strip_opaque = sum(s_hist[200:]) / (w * strip_h)
    corner = px[2, 2][3]
    name = p.rsplit("/", 1)[-1]
    print(f"{name}: {w}x{h} transp={transp:.0%} opaque={opaque:.0%} "
          f"lum={lum:.0f} bottom_opaque={strip_opaque:.0%} corner_alpha={corner}")
