from PIL import Image, ImageDraw, ImageFont

font = ImageFont.truetype("/data/igris_bot/assets/fonts/font.ttf", 15)

def to_fa(n):
    table = str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹')
    return str(n).translate(table)

img = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
draw = ImageDraw.Draw(img)
box = (20, 30, 150, 75)
draw.rounded_rectangle(box, radius=12, fill=(109, 40, 217, 235), outline=(167, 139, 250, 255), width=2)
draw.text(((box[0] + box[2]) // 2, (box[1] + box[3]) // 2), f"اورا {to_fa(4)}", font=font, fill=(255, 255, 255, 255), anchor="mm")
img.save("/data/igris_bot/assets/layers/auras/golden_hero.png")
print("Saved golden_hero.png")
