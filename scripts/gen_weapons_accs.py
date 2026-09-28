from PIL import Image, ImageDraw, ImageFont

font = ImageFont.truetype("/data/igris_bot/assets/fonts/font.ttf", 15)

def to_fa(n):
    return str(n).translate(str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹'))

weapons = [
    ("police_baton", 1),
    ("combat_knife", 2),
    ("neon_sai", 3),
    ("shadow_katana", 4),
]

accessories = [
    ("tactical_goggles", 1),
    ("half_mask", 2),
    ("arcane_eye_mark", 3),
    ("phantom_visage", 4),
]

w_box = (20, 208, 140, 248)
for name, idx in weapons:
    img = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(w_box, radius=10, fill=(185, 28, 28, 235), outline=(248, 113, 113, 255), width=2)
    draw.text(((w_box[0] + w_box[2]) // 2, (w_box[1] + w_box[3]) // 2), f"سلاح {to_fa(idx)}", font=font, fill=(255, 255, 255, 255), anchor="mm")
    img.save(f"/data/igris_bot/assets/layers/weapons/{name}.png")
    print(f"Saved weapon {name} as سلاح {idx}")

a_box = (350, 95, 495, 140)
for name, idx in accessories:
    img = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(a_box, radius=12, fill=(180, 83, 9, 235), outline=(251, 191, 36, 255), width=2)
    draw.text(((a_box[0] + a_box[2]) // 2, (a_box[1] + a_box[3]) // 2), f"اکسسوری {to_fa(idx)}", font=font, fill=(255, 255, 255, 255), anchor="mm")
    img.save(f"/data/igris_bot/assets/layers/accessories/{name}.png")
    print(f"Saved accessory {name} as اکسسوری {idx}")
