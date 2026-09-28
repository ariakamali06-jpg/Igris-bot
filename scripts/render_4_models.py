import sys
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, '/data/igris_bot')
from services.compositor import compositor, RenderRequest

compositor._assets.load()
compositor.clear_cache()

models_cfg = [
    {
        "id": 1,
        "name": "آریا (مدل ۱)",
        "eye_style": "1",
        "eye_color": "blue",
        "mouth_style": "1",
        "skin_tone": "fair",
        "hair_color": "black",
        "background": "alley_neon",
        "loadout": {
            "head": "street_fade",
            "body": "fitted_tee",
            "legs": "street_slacks",
            "weapon": "police_baton",
            "accessory": "tactical_goggles",
            "aura": "ethereal_smoke",
        }
    },
    {
        "id": 2,
        "name": "آریا (مدل ۲)",
        "eye_style": "2",
        "eye_color": "green",
        "mouth_style": "2",
        "skin_tone": "fair",
        "hair_color": "black",
        "background": "rooftop_zenith",
        "loadout": {
            "head": "hood_up",
            "body": "tactical_hoodie",
            "legs": "combat_boots",
            "weapon": "combat_knife",
            "accessory": "half_mask",
            "aura": "lightning_crackle",
        }
    },
    {
        "id": 3,
        "name": "آریا (مدل ۳)",
        "eye_style": "3",
        "eye_color": "amber",
        "mouth_style": "3",
        "skin_tone": "fair",
        "hair_color": "black",
        "background": "sanctum_abyss",
        "loadout": {
            "head": "raven_shag",
            "body": "leather_bomber",
            "legs": "techwear_cargo",
            "weapon": "neon_sai",
            "accessory": "arcane_eye_mark",
            "aura": "dark_flame",
        }
    },
    {
        "id": 4,
        "name": "آریا (مدل ۴)",
        "eye_style": "4",
        "eye_color": "violet",
        "mouth_style": "4",
        "skin_tone": "fair",
        "hair_color": "black",
        "background": "city_night",
        "loadout": {
            "head": "crown_of_shadows",
            "body": "hunter_trench",
            "legs": "street_slides",
            "weapon": "shadow_katana",
            "accessory": "phantom_visage",
            "aura": "golden_hero",
        }
    },
]

card_images = []
for m in models_cfg:
    req = RenderRequest(
        display_name=m["name"],
        username="rex_lapis",
        level=m["id"],
        atk=10 * m["id"],
        defense=8 * m["id"],
        drip=15 * m["id"],
        body="base_male",
        background=m["background"],
        skin_tone=m["skin_tone"],
        hair_color=m["hair_color"],
        eye_color=m["eye_color"],
        eye_style=m["eye_style"],
        mouth_style=m["mouth_style"],
        gender="مرد",
        loadout=m["loadout"]
    )
    raw = compositor.render(req, use_cache=False)
    with open(f"/data/test_model_{m['id']}.jpg", "wb") as f:
        f.write(raw)
    import io
    img = Image.open(io.BytesIO(raw))
    card_images.append(img)
    print(f"Rendered model {m['id']}")

# Stitch 2x2 grid (1024x1024)
grid = Image.new("RGB", (1024, 1024), (10, 10, 15))
grid.paste(card_images[0], (0, 0))
grid.paste(card_images[1], (512, 0))
grid.paste(card_images[2], (0, 512))
grid.paste(card_images[3], (512, 512))

draw = ImageDraw.Draw(grid)
draw.line([(512, 0), (512, 1024)], fill=(0, 200, 255), width=2)
draw.line([(0, 512), (1024, 512)], fill=(0, 200, 255), width=2)

grid.save("/data/test_4_models_comparison.jpg", quality=92)
print("Saved /data/test_4_models_comparison.jpg")
