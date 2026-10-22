"""کامپوزيت QA روی سبز خالص: هر residue واقعی (floor/glow/debris) روی سبز لو می‌رود."""
import glob
import sys

from PIL import Image

pattern = sys.argv[1] if len(sys.argv) > 1 else "/data/igris_bot/assets/char/00*.png"
for p in sorted(glob.glob(pattern)):
    im = Image.open(p).convert("RGBA")
    bg = Image.new("RGBA", im.size, (0, 200, 0, 255))
    out = Image.alpha_composite(bg, im).convert("RGB")
    name = p.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    out.save(f"/tmp/qa_green_{name}.jpg", quality=92)
    print("green:", name, im.size)
