"""Render preview cards for the Sutemo boy path (manual smoke, not a unit test)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from services.compositor import render_card_timed
from services.compositor import RenderRequest

OUT = Path("/tmp/boy_previews")
OUT.mkdir(exist_ok=True)

cases = [
    ("boy_skin1_hair1_eyes1", dict(
        display_name="آرشیا", body="base_boy_1", skin_tone="pale",
        hair_color="silver", eye_color="violet", eye_style="1",
        hair_style="hair1", mouth_style="1", gender="پسر")),
    ("boy_skin2_hair2_eyes2", dict(
        display_name="بنیامین", body="base_boy_2", skin_tone="fair",
        hair_color="black", eye_color="blue", eye_style="2",
        hair_style="hair2", mouth_style="2", gender="پسر")),
    ("boy_skin3_hair3_eyes3", dict(
        display_name="رضا", body="base_boy_3", skin_tone="natural",
        hair_color="brown", eye_color="green", eye_style="3",
        hair_style="hair3", mouth_style="3", gender="پسر")),
    ("boy_skin5_hair5_eyes2", dict(
        display_name="سامان", body="base_boy_5", skin_tone="dark",
        hair_color="black", eye_color="amber", eye_style="2",
        hair_style="hair5", mouth_style="4", gender="پسر")),
    ("girl_regression", dict(
        display_name="نگین", body="base_vn_3", skin_tone="natural",
        hair_color="brown", eye_color="blue", eye_style="eyes2_3",
        hair_style="hair2", mouth_style="mouth1_3", gender="دختر")),
]

for name, kw in cases:
    req = RenderRequest(**kw, level=12, atk=340, defense=280, drip=95)
    png, ms = render_card_timed(req)
    (OUT / f"{name}.png").write_bytes(png)
    print(f"{name}: {len(png)} bytes, {ms:.1f} ms")
