from PIL import Image, ImageDraw, ImageFont

font = ImageFont.truetype("/data/igris_bot/assets/fonts/font.ttf", 15)

def to_fa(n):
    return str(n).translate(str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹'))

bgs = [
    ("alley_neon", 1, (15, 23, 42, 255)),
    ("rooftop_zenith", 2, (18, 20, 32, 255)),
    ("sanctum_abyss", 3, (12, 16, 28, 255)),
    ("city_night", 4, (14, 22, 38, 255)),
    ("dungeon", 5, (16, 18, 24, 255)),
]

box = (350, 30, 495, 75)
for name, idx, col in bgs:
    img = Image.new("RGBA", (512, 512), col)
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(box, radius=12, fill=(30, 41, 59, 240), outline=(100, 116, 139, 255), width=2)
    draw.text(((box[0] + box[2]) // 2, (box[1] + box[3]) // 2), f"پس‌زمینه {to_fa(idx)}", font=font, fill=(255, 255, 255, 255), anchor="mm")
    img.save(f"/data/igris_bot/assets/layers/backgrounds/{name}.png")
    print(f"Saved background {name} as پس‌زمینه {idx}")
