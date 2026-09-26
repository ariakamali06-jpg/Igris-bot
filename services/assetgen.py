"""Procedural placeholder art generator.

The project ships zero licensed art, so this module synthesises a full set of
Urban-Fantasy layer PNGs (512x512 RGBA) with Pillow.  The output is meant to be
*swappable*: real art can be dropped in later under identical filenames with
no code changes.

Art direction
-------------
"Urban fantasy": near-black streetwear silhouettes, cold steel greys, and
three neon accents (cyan / magenta / amber) borrowed from rain-lit backstreets
and the Solo-Leveling / Jujutsu-Kaisen glow language.  Everything is drawn on a
shared ``Canvas`` helper that separates shape fills from blurred glow passes so
neon reads as light rather than flat colour.
"""

from __future__ import annotations

import logging
import math
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from config import settings

logger = logging.getLogger(__name__)

CANVAS = 512
RGBA = tuple[int, int, int, int]

# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------


class C:
    """Central palette so every layer agrees on the look."""

    # Neutrals (streetwear)
    INK = (12, 13, 17, 255)
    CHARCOAL = (28, 30, 36, 255)
    SLATE = (52, 56, 66, 255)
    STEEL = (96, 103, 116, 255)
    OFFWHITE = (226, 229, 236, 255)

    # Skin
    SKIN = (176, 138, 110, 255)
    SKIN_SHADOW = (132, 98, 76, 255)
    SKIN_HIGHLIGHT = (208, 172, 142, 255)

    # Hair
    HAIR_BLACK = (26, 24, 30, 255)
    HAIR_SILVER = (196, 202, 214, 255)

    # Neon accents
    CYAN = (0, 229, 255, 255)
    MAGENTA = (255, 46, 136, 255)
    AMBER = (255, 176, 32, 255)
    VIOLET = (168, 85, 247, 255)
    TOXIC = (122, 255, 122, 255)

    # Fabrics
    COAT = (24, 25, 32, 255)
    COAT_EDGE = (44, 47, 58, 255)
    DENIM = (36, 46, 66, 255)
    LEATHER = (32, 28, 30, 255)
    TACTICAL = (40, 44, 52, 255)

    # FX
    SMOKE = (120, 130, 150, 110)
    DARK_FLAME = (180, 40, 90, 190)


# ---------------------------------------------------------------------------
# Shared figure geometry (front-facing, centred on 512x512)
# ---------------------------------------------------------------------------
HEAD_C = (256, 150)
HEAD_RX, HEAD_RY = 41, 51
SHOULDER_Y = 214
SHOULDER_HALF = 76
WAIST_Y = 300
WAIST_HALF = 56
HIP_Y = 334
HIP_HALF = 62
KNEE_Y = 404
ANKLE_Y = 464
FOOT_Y = 476
ARM_OUTER = SHOULDER_HALF + 26
HAND_Y = 352
FEET_HALF = 46


# ---------------------------------------------------------------------------
# Canvas helper
# ---------------------------------------------------------------------------


@dataclass
class Canvas:
    """A layer under construction, with a separate blurred glow pass.

    Shapes drawn via ``shape()`` land on the sharp layer; shapes drawn via
    ``glow()`` land on an off-screen layer that is blurred and composited on
    top at ``finish()``.  That two-pass approach is what makes thin neon lines
    read as light.
    """

    size: int = CANVAS
    img: Image.Image = field(init=False)
    glow_img: Image.Image = field(init=False)
    _draw: ImageDraw.ImageDraw = field(init=False, repr=False)
    _glow: ImageDraw.ImageDraw = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.img = Image.new("RGBA", (self.size, self.size), (0, 0, 0, 0))
        self.glow_img = Image.new("RGBA", (self.size, self.size), (0, 0, 0, 0))
        self._draw = ImageDraw.Draw(self.img)
        self._glow = ImageDraw.Draw(self.glow_img)

    # -- accessors ---------------------------------------------------------
    @property
    def draw(self) -> ImageDraw.ImageDraw:
        return self._draw

    @property
    def glow_draw(self) -> ImageDraw.ImageDraw:
        return self._glow

    def line(self, xy, fill, width=1, joint=None) -> None:  # noqa: ANN001
        self._draw.line(xy, fill=fill, width=width, joint=joint)
        self._glow.line(xy, fill=fill, width=width, joint=joint)

    def glow_line(self, xy, fill, width=1) -> None:  # noqa: ANN001
        self._glow.line(xy, fill=fill, width=width)

    def polygon(self, points, fill) -> None:  # noqa: ANN001
        self._draw.polygon(points, fill=fill)

    @staticmethod
    def _norm(box) -> tuple[int, int, int, int]:  # noqa: ANN001
        """Order box corners so mirrored (left/right) drawing never reverses."""
        x0, y0, x1, y1 = box
        return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))

    def ellipse(self, box, fill=None, outline=None, width=1) -> None:  # noqa: ANN001
        self._draw.ellipse(self._norm(box), fill=fill, outline=outline, width=width)

    def rect(self, box, fill=None, outline=None, width=1) -> None:  # noqa: ANN001
        self._draw.rectangle(self._norm(box), fill=fill, outline=outline, width=width)

    def rrect(self, box, radius, fill=None, outline=None, width=1) -> None:  # noqa: ANN001
        self._draw.rounded_rectangle(
            self._norm(box), radius=radius, fill=fill, outline=outline, width=width
        )

    def glow_ellipse(self, box, fill) -> None:  # noqa: ANN001
        self._glow.ellipse(box, fill=fill)

    def glow_rrect(self, box, radius, fill) -> None:  # noqa: ANN001
        self._glow.rounded_rectangle(box, radius=radius, fill=fill)

    # -- compositing -------------------------------------------------------
    def finish(self, blur: float = 6.0) -> Image.Image:
        """Binarise the sharp pass, then composite the blurred glow over it.

        ``ImageDraw`` writes the fill's alpha directly instead of blending it
        with what is already there.  A 55%-alpha rain streak drawn on an opaque
        background therefore leaves a 55%-alpha *hole*, and every downstream
        consumer (compositor flatten, JPEG encode) pays for it.  Layers only
        ever need to answer "drawn or not", so collapse the sharp pass to a
        binary mask first; the soft pass keeps its gradients because that is
        exactly what a glow is for.
        """
        alpha = self.img.getchannel("A")
        if alpha.getextrema()[1]:
            self.img.putalpha(alpha.point(lambda a: 255 if a else 0))

        if blur > 0:
            blurred = self.glow_img.filter(ImageFilter.GaussianBlur(blur))
            return Image.alpha_composite(self.img, blurred)
        return Image.alpha_composite(self.img, self.glow_img)


def vertical_gradient(size: int, top: RGBA, bottom: RGBA) -> Image.Image:
    """Two-stop vertical gradient rendered as raw pixel data."""
    w, h = size, size
    data = bytearray(w * h * 4)
    for y in range(h):
        t = y / max(1, h - 1)
        r = int(top[0] + (bottom[0] - top[0]) * t)
        g = int(top[1] + (bottom[1] - top[1]) * t)
        b = int(top[2] + (bottom[2] - top[2]) * t)
        a = int(top[3] + (bottom[3] - top[3]) * t)
        base = y * w * 4
        for x in range(w):
            i = base + x * 4
            data[i] = r
            data[i + 1] = g
            data[i + 2] = b
            data[i + 3] = a
    return Image.frombytes("RGBA", (w, h), bytes(data))


def with_alpha(color: RGBA, alpha: int) -> RGBA:
    return (color[0], color[1], color[2], max(0, min(255, alpha)))


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


# ---------------------------------------------------------------------------
# Figure primitives shared by body / legs / tops / hair
# ---------------------------------------------------------------------------


def draw_body(c: Canvas, *, stance: str = "neutral") -> None:
    """Base character body: head, neck, torso, arms, legs, feet."""
    spread = 10 if stance == "aegis" else 0

    # --- legs -------------------------------------------------------------
    for side in (-1, 1):
        hip_x = 256 + side * 26
        knee_x = 256 + side * (30 + spread)
        ankle_x = 256 + side * (34 + spread)
        c.polygon(
            [
                (hip_x - side * 4, HIP_Y - 6),
                (hip_x + side * 30, HIP_Y - 6),
                (knee_x + side * 26, KNEE_Y),
                (ankle_x + side * 20, ANKLE_Y),
                (ankle_x - side * 16, ANKLE_Y),
                (knee_x - side * 24, KNEE_Y),
            ],
            fill=C.SKIN,
        )

    # --- feet -------------------------------------------------------------
    for side in (-1, 1):
        ax = 256 + side * (34 + spread)
        c.rrect((ax - 24, FOOT_Y - 14, ax + 30, FOOT_Y + 4), 7, fill=C.INK)

    # --- torso ------------------------------------------------------------
    c.polygon(
        [
            (256 - SHOULDER_HALF, SHOULDER_Y),
            (256 + SHOULDER_HALF, SHOULDER_Y),
            (256 + WAIST_HALF, WAIST_Y),
            (256 + HIP_HALF, HIP_Y),
            (256 - HIP_HALF, HIP_Y),
            (256 - WAIST_HALF, WAIST_Y),
        ],
        fill=C.SKIN,
    )

    # --- arms -------------------------------------------------------------
    for side in (-1, 1):
        sx = 256 + side * (SHOULDER_HALF - 6)
        c.polygon(
            [
                (sx, SHOULDER_Y - 4),
                (sx + side * 26, SHOULDER_Y + 4),
                (sx + side * 34, HAND_Y - 40),
                (sx + side * 30, HAND_Y),
                (sx + side * 4, HAND_Y - 6),
                (sx - side * 2, SHOULDER_Y + 40),
            ],
            fill=C.SKIN,
        )
        # hand (normalise x bounds so mirrored sides stay ordered)
        hx0, hx1 = sorted((sx + side * 2, sx + side * 34))
        c.ellipse((hx0, HAND_Y - 12, hx1, HAND_Y + 18), fill=C.SKIN_SHADOW)

    # --- neck + shoulders shading ----------------------------------------
    c.polygon(
        [
            (256 - 20, SHOULDER_Y - 18),
            (256 + 20, SHOULDER_Y - 18),
            (256 + 24, SHOULDER_Y + 4),
            (256 - 24, SHOULDER_Y + 4),
        ],
        fill=C.SKIN_SHADOW,
    )

    # --- head -------------------------------------------------------------
    c.ellipse(
        (HEAD_C[0] - HEAD_RX, HEAD_C[1] - HEAD_RY,
         HEAD_C[0] + HEAD_RX, HEAD_C[1] + HEAD_RY),
        fill=C.SKIN,
    )
    # jaw shading
    c.ellipse(
        (HEAD_C[0] - HEAD_RX + 6, HEAD_C[1] + 4,
         HEAD_C[0] + HEAD_RX - 6, HEAD_C[1] + HEAD_RY - 2),
        fill=C.SKIN_SHADOW,
    )
    # cheek highlight
    c.ellipse(
        (HEAD_C[0] - 26, HEAD_C[1] - 34, HEAD_C[0] + 6, HEAD_C[1] - 6),
        fill=C.SKIN_HIGHLIGHT,
    )

    # --- minimal facial suggestion (eyes as dark slots) -------------------
    for side in (-1, 1):
        ex = HEAD_C[0] + side * 17
        c.ellipse((ex - 9, HEAD_C[1] - 8, ex + 9, HEAD_C[1] + 2), fill=C.INK)


# ---------------------------------------------------------------------------
# Layer renderers — one per folder in LAYER_ORDER
# ---------------------------------------------------------------------------


def render_background(key: str) -> Image.Image:
    if key == "rooftop_zenith":
        img = _bg_rooftop()
    elif key == "sanctum_abyss":
        img = _bg_sanctum()
    else:
        img = _bg_alley()
    # A scene is never see-through: forcing full opacity lets the compositor
    # skip its alpha-flatten pass and go straight to JPEG.
    img.putalpha(255)
    return img


def _bg_alley() -> Image.Image:
    rng = random.Random(7)
    base = vertical_gradient(CANVAS, (6, 8, 14, 255), (20, 12, 30, 255))
    c = Canvas()
    c.img.paste(base, (0, 0))

    # Building silhouettes with lit windows.
    x = 0
    while x < CANVAS:
        w = rng.randint(54, 96)
        h = rng.randint(180, 330)
        top = CANVAS - h - 40
        c.rect((x, top, x + w, CANVAS - 40), fill=(9, 10, 15, 255))
        for wy in range(top + 14, CANVAS - 56, 22):
            for wx in range(x + 8, x + w - 8, 18):
                if rng.random() < 0.34:
                    col = rng.choice([C.CYAN, C.MAGENTA, C.AMBER])
                    c.rect((wx, wy, wx + 9, wy + 12), fill=with_alpha(col, 150))
        x += w + rng.randint(4, 14)

    # Wet ground reflection band.
    c.rect((0, CANVAS - 44, CANVAS, CANVAS), fill=(14, 16, 24, 255))
    for _ in range(16):
        sx = rng.randint(0, CANVAS)
        col = rng.choice([C.CYAN, C.MAGENTA, C.AMBER])
        c.glow_line([(sx, CANVAS - 40), (sx + rng.randint(-8, 8), CANVAS - 6)],
                    fill=with_alpha(col, 120), width=3)

    # Neon signage.
    sign_col = rng.choice([C.CYAN, C.MAGENTA])
    c.glow_rrect((36, 96, 132, 134), 8, fill=with_alpha(sign_col, 90))
    c.rrect((44, 104, 124, 126), 5, fill=with_alpha(sign_col, 235))
    c.glow_rrect((388, 168, 470, 202), 7, fill=with_alpha(C.MAGENTA, 80))
    c.rrect((394, 174, 464, 196), 4, fill=with_alpha(C.MAGENTA, 230))

    # Rain streaks.
    for _ in range(140):
        rx = rng.randint(0, CANVAS)
        ry = rng.randint(0, CANVAS - 40)
        c.draw.line([(rx, ry), (rx - 3, ry + rng.randint(16, 34))],
                    fill=(200, 220, 255, 55), width=1)

    return c.finish(blur=7)


def _bg_rooftop() -> Image.Image:
    rng = random.Random(21)
    base = vertical_gradient(CANVAS, (8, 10, 26, 255), (34, 18, 46, 255))
    c = Canvas()
    c.img.paste(base, (0, 0))

    # Stars.
    for _ in range(160):
        sx, sy = rng.randint(0, CANVAS), rng.randint(0, 240)
        c.draw.point((sx, sy), fill=(255, 255, 255, rng.randint(90, 235)))

    # Distant skyline (low, bottom band).
    x = 0
    while x < CANVAS:
        w = rng.randint(30, 70)
        h = rng.randint(60, 170)
        c.rect((x, CANVAS - 150 - h, x + w, CANVAS - 150), fill=(11, 13, 24, 255))
        x += w + rng.randint(2, 8)

    # Helipad deck.
    c.rect((0, CANVAS - 150, CANVAS, CANVAS), fill=(24, 26, 34, 255))
    c.rect((0, CANVAS - 150, CANVAS, CANVAS - 142), fill=C.SLATE)
    c.glow_ellipse((96, CANVAS - 148, 416, CANVAS - 16), fill=with_alpha(C.AMBER, 60))
    c.ellipse((110, CANVAS - 144, 402, CANVAS - 22), outline=with_alpha(C.AMBER, 200), width=4)
    c.ellipse((150, CANVAS - 124, 362, CANVAS - 44), outline=with_alpha(C.AMBER, 90), width=2)
    # H marking.
    c.draw.line([(224, CANVAS - 116), (224, CANVAS - 52)], fill=with_alpha(C.AMBER, 210), width=8)
    c.draw.line([(288, CANVAS - 116), (288, CANVAS - 52)], fill=with_alpha(C.AMBER, 210), width=8)
    c.draw.line([(224, CANVAS - 84), (288, CANVAS - 84)], fill=with_alpha(C.AMBER, 210), width=8)

    # Rooftop AC units / antenna for depth.
    c.rect((20, 340, 92, CANVAS - 150), fill=(17, 19, 27, 255))
    c.rect((404, 306, 486, CANVAS - 150), fill=(17, 19, 27, 255))
    c.glow_line([(448, 306), (448, 232)], fill=with_alpha(C.MAGENTA, 190), width=3)
    c.glow_ellipse((442, 224, 454, 236), fill=with_alpha(C.MAGENTA, 240))

    return c.finish(blur=6)


def _bg_sanctum() -> Image.Image:
    rng = random.Random(99)
    base = vertical_gradient(CANVAS, (16, 8, 26, 255), (6, 4, 12, 255))
    c = Canvas()
    c.img.paste(base, (0, 0))

    # Stone slab floor with perspective lines.
    horizon = 330
    for y in range(horizon, CANVAS, 26):
        shade = int(20 + (y - horizon) / 6)
        c.draw.line([(0, y), (CANVAS, y)], fill=(shade, shade - 4, shade + 6, 255), width=2)
    for i in range(-6, 7):
        c.draw.line([(256 + i * 26, horizon), (256 + i * 96, CANVAS)],
                    fill=(26, 24, 34, 255), width=2)

    # Back wall runes in a ring.
    cx, cy, radius = 256, 250, 168
    c.glow_ellipse((cx - radius - 14, cy - radius - 14, cx + radius + 14, cy + radius + 14),
                   fill=with_alpha(C.VIOLET, 55))
    c.ellipse((cx - radius, cy - radius, cx + radius, cy + radius),
              outline=with_alpha(C.VIOLET, 170), width=3)
    c.ellipse((cx - radius + 34, cy - radius + 34, cx + radius - 34, cy + radius - 34),
              outline=with_alpha(C.VIOLET, 70), width=2)
    for i in range(18):
        a = (i / 18) * math.tau
        rx, ry = cx + math.cos(a) * radius, cy + math.sin(a) * radius
        c.glow_ellipse((rx - 7, ry - 7, rx + 7, ry + 7), fill=with_alpha(C.CYAN, 220))
        # tick marks
        c.draw.line([(rx, ry), (cx + math.cos(a) * (radius - 20),
                                cy + math.sin(a) * (radius - 20))],
                    fill=with_alpha(C.VIOLET, 120), width=2)

    # Pillars.
    for px in (40, 424):
        c.rect((px, 90, px + 48, horizon + 40), fill=(22, 20, 32, 255))
        c.rect((px, 90, px + 8, horizon + 40), fill=(34, 32, 46, 255))
        c.glow_line([(px + 24, 120), (px + 24, horizon + 20)],
                    fill=with_alpha(C.VIOLET, 150), width=3)

    # Floating motes.
    for _ in range(50):
        mx, my = rng.randint(30, 482), rng.randint(60, 420)
        r = rng.randint(1, 3)
        col = rng.choice([C.VIOLET, C.CYAN])
        c.glow_ellipse((mx - r * 3, my - r * 3, mx + r * 3, my + r * 3),
                       fill=with_alpha(col, 140))

    return c.finish(blur=8)


def render_body(key: str) -> Image.Image:
    c = Canvas()
    draw_body(c, stance="aegis" if key == "base_aegis" else "neutral")
    # Ground contact shadow so the figure never floats.
    shadow = Canvas()
    shadow.glow_ellipse((168, 476, 344, 500), fill=(0, 0, 0, 165))
    return Image.alpha_composite(shadow.finish(blur=10), c.finish(blur=0))


def render_legs(key: str) -> Image.Image:
    c = Canvas()
    spread = 0
    # Pants block from hips to ankle, then footwear.
    specs = {
        "street_slacks": ((36, 38, 46, 255), (18, 18, 22, 255), 0),
        "combat_boots": ((44, 46, 54, 255), (26, 24, 26, 255), 1),
        "techwear_cargo": ((30, 34, 42, 255), (22, 24, 30, 255), 2),
        "street_slides": ((58, 52, 64, 255), (240, 240, 245, 255), 0),
        "shadow_wargreaves": ((20, 16, 34, 255), (14, 12, 24, 255), 3),
    }
    fabric, boot, detail = specs.get(key, ((40, 42, 50, 255), (24, 24, 28, 255), 0))
    accent = _accent_for(key)

    for side in (-1, 1):
        hip_x = 256 + side * 26
        knee_x = 256 + side * (30 + spread)
        ankle_x = 256 + side * (34 + spread)
        c.polygon(
            [
                (hip_x - side * 6, HIP_Y - 14),
                (hip_x + side * 32, HIP_Y - 14),
                (knee_x + side * 28, KNEE_Y + 6),
                (ankle_x + side * 22, ANKLE_Y + 4),
                (ankle_x - side * 18, ANKLE_Y + 4),
                (knee_x - side * 26, KNEE_Y + 6),
            ],
            fill=fabric,
        )
        # knee seam highlight
        c.draw.line([(knee_x - side * 24, KNEE_Y), (knee_x + side * 26, KNEE_Y)],
                    fill=with_alpha(accent, 90), width=2)
        # cargo pouch
        if detail >= 2:
            c.rrect((knee_x + side * 8, KNEE_Y - 44, knee_x + side * 40, KNEE_Y - 6),
                    5, fill=C.TACTICAL, outline=with_alpha(accent, 120), width=2)

        # footwear
        if key == "street_slides":
            c.rrect((ankle_x - 24, FOOT_Y - 16, ankle_x + 30, FOOT_Y + 4), 7, fill=boot)
            c.rrect((ankle_x - 20, FOOT_Y - 22, ankle_x + 26, FOOT_Y - 12), 6,
                    fill=with_alpha(accent, 200))
        else:
            # combat boot: taller shaft + sole
            c.rrect((ankle_x - 22, ANKLE_Y - 26, ankle_x + 24, FOOT_Y + 4), 6, fill=boot)
            c.rect((ankle_x - 24, FOOT_Y - 4, ankle_x + 30, FOOT_Y + 5), fill=C.INK)
            c.glow_line([(ankle_x - 18, FOOT_Y - 8), (ankle_x + 24, FOOT_Y - 8)],
                        fill=with_alpha(accent, 190), width=2)

    # Waistband / belt.
    c.rect((256 - HIP_HALF - 4, HIP_Y - 24, 256 + HIP_HALF + 4, HIP_Y - 8), fill=C.INK)
    c.glow_line([(256 - HIP_HALF, HIP_Y - 16), (256 + HIP_HALF, HIP_Y - 16)],
                fill=with_alpha(accent, 210), width=3)
    if key == "shadow_wargreaves":
        # rune glow trailing off the greaves.
        for side in (-1, 1):
            for y in range(KNEE_Y - 20, ANKLE_Y, 22):
                c.glow_ellipse((256 + side * 34 - 5, y - 5, 256 + side * 34 + 5, y + 5),
                               fill=with_alpha(C.VIOLET, 210))

    return c.finish(blur=5)


def render_top(key: str) -> Image.Image:
    c = Canvas()
    specs = {
        "fitted_tee": ((54, 58, 70, 255), 0),
        "tactical_hoodie": ((34, 36, 44, 255), 2),
        "leather_bomber": ((38, 30, 32, 255), 1),
        "hunter_trench": ((26, 26, 34, 255), 3),
        "void_cuirass": ((18, 14, 30, 255), 4),
    }
    fabric, variant = specs.get(key, ((44, 46, 54, 255), 0))
    accent = _accent_for(key)

    # Sleeves follow the arms.
    for side in (-1, 1):
        sx = 256 + side * (SHOULDER_HALF - 6)
        c.polygon(
            [
                (sx - side * 4, SHOULDER_Y - 6),
                (sx + side * 30, SHOULDER_Y + 2),
                (sx + side * 38, (HAND_Y if variant <= 1 else HAND_Y - 56) - 30),
                (sx + side * 8, (HAND_Y if variant <= 1 else HAND_Y - 56) - 24),
            ],
            fill=fabric,
        )

    # Torso block.
    bottom = HIP_Y + 34 if variant >= 3 else WAIST_Y + 26
    c.polygon(
        [
            (256 - SHOULDER_HALF - 4, SHOULDER_Y - 6),
            (256 + SHOULDER_HALF + 4, SHOULDER_Y - 6),
            (256 + WAIST_HALF + 6, WAIST_Y),
            (256 + (HIP_HALF + 6 if variant >= 3 else WAIST_HALF), bottom),
            (256 - (HIP_HALF + 6 if variant >= 3 else WAIST_HALF), bottom),
            (256 - WAIST_HALF - 6, WAIST_Y),
        ],
        fill=fabric,
    )

    # Collar / neckline.
    c.polygon(
        [(256 - 26, SHOULDER_Y - 8), (256 + 26, SHOULDER_Y - 8),
         (256 + 16, SHOULDER_Y + 16), (256 - 16, SHOULDER_Y + 16)],
        fill=C.SKIN_SHADOW,
    )

    if variant >= 1:
        # Zipper / front seam.
        c.glow_line([(256, SHOULDER_Y + 10), (256, bottom - 6)],
                    fill=with_alpha(accent, 220), width=2)
    if variant == 2:
        # Hood bunched behind the neck.
        c.polygon(
            [(256 - 54, SHOULDER_Y - 10), (256 + 54, SHOULDER_Y - 10),
             (256 + 34, SHOULDER_Y - 46), (256 - 34, SHOULDER_Y - 46)],
            fill=C.COAT_EDGE,
        )
        c.draw.line([(256 - 40, SHOULDER_Y - 44), (256 + 40, SHOULDER_Y - 44)],
                    fill=with_alpha(accent, 150), width=3)
    if variant >= 3:
        # Long coat lapels + hem flare.
        for side in (-1, 1):
            c.polygon(
                [(256 + side * 10, SHOULDER_Y + 6),
                 (256 + side * 66, SHOULDER_Y + 4),
                 (256 + side * (HIP_HALF + 10), bottom + 26),
                 (256 + side * 4, bottom - 10)],
                fill=C.COAT_EDGE,
            )
        # belt
        c.rect((256 - WAIST_HALF - 8, WAIST_Y + 4, 256 + WAIST_HALF + 8, WAIST_Y + 18),
               fill=C.INK)
        c.glow_rrect((256 - 12, WAIST_Y + 4, 256 + 12, WAIST_Y + 18), 3,
                     fill=with_alpha(accent, 235))
    if variant == 4:
        # Armour plating + emissive chest core.
        c.rrect((256 - 44, SHOULDER_Y + 30, 256 + 44, WAIST_Y - 6), 10,
                fill=C.CHARCOAL, outline=with_alpha(accent, 170), width=3)
        c.glow_ellipse((256 - 20, SHOULDER_Y + 52, 256 + 20, SHOULDER_Y + 92),
                       fill=with_alpha(accent, 150))
        c.ellipse((256 - 12, SHOULDER_Y + 60, 256 + 12, SHOULDER_Y + 84),
                  fill=with_alpha(accent, 245))
    if variant == 0:
        # Subtle tee fold lines.
        for i in range(3):
            y = SHOULDER_Y + 34 + i * 22
            c.draw.line([(256 - 40 + i * 6, y), (256 + 40 - i * 6, y + 6)],
                        fill=(0, 0, 0, 60), width=2)

    return c.finish(blur=5)


def render_hair(key: str) -> Image.Image:
    c = Canvas()
    cx, cy = HEAD_C
    accent = _accent_for(key)

    if key == "street_fade":
        c.polygon(
            [(cx - HEAD_RX - 3, cy - 8), (cx - HEAD_RX + 2, cy - HEAD_RY + 4),
             (cx + HEAD_RX - 2, cy - HEAD_RY + 4), (cx + HEAD_RX + 3, cy - 8),
             (cx + HEAD_RX - 6, cy - 22), (cx - HEAD_RX + 6, cy - 22)],
            fill=C.HAIR_BLACK,
        )
        c.ellipse((cx - HEAD_RX + 4, cy - HEAD_RY - 2, cx + HEAD_RX - 4, cy - 18),
                  fill=C.HAIR_BLACK)
        c.draw.line([(cx - 30, cy - 30), (cx + 30, cy - 30)],
                    fill=with_alpha(C.STEEL, 170), width=2)
    elif key == "hood_up":
        # Big hood volume around the head with a dark face shadow.
        c.polygon(
            [(cx - 62, cy + 66), (cx - 74, cy - 6), (cx - 58, cy - 62),
             (cx, cy - 84), (cx + 58, cy - 62), (cx + 74, cy - 6),
             (cx + 62, cy + 66), (cx + 40, cy + 40), (cx - 40, cy + 40)],
            fill=C.COAT,
        )
        c.ellipse((cx - 46, cy - 54, cx + 46, cy + 46), fill=(8, 8, 12, 255))
        c.glow_line([(cx - 56, cy - 40), (cx, cy - 74), (cx + 56, cy - 40)],
                    fill=with_alpha(accent, 170), width=3)
        # drawstrings
        for side in (-1, 1):
            c.draw.line([(cx + side * 44, cy + 46), (cx + side * 50, cy + 96)],
                        fill=C.STEEL, width=3)
    elif key == "raven_shag":
        # Layered black hair with spiky fringe.
        pts: list[tuple[float, float]] = [
            (cx - HEAD_RX - 8, cy + 26), (cx - HEAD_RX - 4, cy - 44)
        ]
        for i in range(9):
            t = i / 8
            px = lerp(cx - HEAD_RX - 4, cx + HEAD_RX + 4, t)
            py = cy - HEAD_RY - (16 if i % 2 == 0 else 2)
            pts.extend([(px - 6, py + 14), (px + 6, py)])
        pts.extend([(cx + HEAD_RX + 8, cy + 26), (cx + HEAD_RX + 14, cy + 70),
                    (cx + 34, cy + 22), (cx - 34, cy + 22),
                    (cx - HEAD_RX - 14, cy + 70)])
        c.polygon(pts, fill=C.HAIR_BLACK)
        # side strands
        for side in (-1, 1):
            c.polygon(
                [(cx + side * (HEAD_RX + 2), cy - 20),
                 (cx + side * (HEAD_RX + 18), cy + 10),
                 (cx + side * (HEAD_RX + 6), cy + 74),
                 (cx + side * (HEAD_RX - 4), cy + 30)],
                fill=C.HAIR_BLACK,
            )
        c.glow_line([(cx - 34, cy - 44), (cx + 34, cy - 44)],
                    fill=with_alpha(C.CYAN, 160), width=2)
    else:  # crown_of_shadows
        c.ellipse((cx - HEAD_RX - 4, cy - HEAD_RY - 6, cx + HEAD_RX + 4, cy + 10),
                  fill=C.HAIR_BLACK)
        # jagged crown silhouette
        crown: list[tuple[float, float]] = [(cx - HEAD_RX - 6, cy - 10)]
        for i in range(7):
            t = i / 6
            px = lerp(cx - HEAD_RX - 6, cx + HEAD_RX + 6, t)
            crown.extend([(px - 7, cy - 60 - (34 if i % 2 else 8)),
                          (px + 7, cy - 40)])
        crown.extend([(cx + HEAD_RX + 6, cy - 10), (cx - HEAD_RX - 6, cy - 10)])
        c.polygon(crown, fill=C.HAIR_BLACK)
        # glowing rune band
        c.glow_rrect((cx - HEAD_RX - 6, cy - 34, cx + HEAD_RX + 6, cy - 20), 5,
                     fill=with_alpha(C.VIOLET, 170))
        c.rrect((cx - HEAD_RX - 4, cy - 32, cx + HEAD_RX + 4, cy - 22), 4,
                fill=with_alpha(C.VIOLET, 240))
        for i in range(6):
            px = lerp(cx - HEAD_RX + 4, cx + HEAD_RX - 4, i / 5)
            c.glow_ellipse((px - 5, cy - 44, px + 5, cy - 26), fill=with_alpha(C.CYAN, 230))

    return c.finish(blur=6)


def render_accessory(key: str) -> Image.Image:
    c = Canvas()
    cx, cy = HEAD_C
    accent = _accent_for(key)

    if key == "tactical_goggles":
        # Wraparound visor across the eyes.
        c.rrect((cx - HEAD_RX - 6, cy - 20, cx + HEAD_RX + 6, cy + 6), 9,
                fill=C.CHARCOAL, outline=C.INK, width=2)
        c.glow_rrect((cx - HEAD_RX - 4, cy - 17, cx + HEAD_RX + 4, cy + 3), 7,
                     fill=with_alpha(C.AMBER, 150))
        c.rrect((cx - HEAD_RX, cy - 15, cx + HEAD_RX, cy + 1), 6,
                fill=with_alpha(C.AMBER, 200))
        c.draw.line([(cx - HEAD_RX - 6, cy - 8), (cx - HEAD_RX - 34, cy - 6)],
                    fill=C.INK, width=5)
        c.draw.line([(cx + HEAD_RX + 6, cy - 8), (cx + HEAD_RX + 34, cy - 6)],
                    fill=C.INK, width=5)
        # lens data ticks
        for i in range(5):
            px = lerp(cx - 30, cx + 30, i / 4)
            c.draw.line([(px, cy - 11), (px, cy - 3)], fill=(40, 26, 8, 255), width=2)
    elif key == "half_mask":
        # Ceramic lower-face mask with vent slits.
        c.polygon(
            [(cx - HEAD_RX + 2, cy + 2), (cx + HEAD_RX - 2, cy + 2),
             (cx + 34, cy + 52), (cx, cy + 62), (cx - 34, cy + 52)],
            fill=C.OFFWHITE,
        )
        c.polygon(
            [(cx - HEAD_RX + 6, cy + 6), (cx - 10, cy + 6), (cx - 16, cy + 56),
             (cx - 34, cy + 50)],
            fill=(206, 210, 220, 255),
        )
        for i in range(3):
            y = cy + 16 + i * 12
            c.draw.line([(cx - 24, y), (cx + 24, y)], fill=(120, 126, 138, 255), width=3)
        c.glow_line([(cx - HEAD_RX + 4, cy + 4), (cx, cy + 10), (cx + HEAD_RX - 4, cy + 4)],
                    fill=with_alpha(accent, 210), width=3)
    elif key == "arcane_eye_mark":
        # Glowing sigil under the left eye + trailing glyphs.
        ex, ey = cx - 17, cy + 10
        c.glow_ellipse((ex - 18, ey - 18, ex + 18, ey + 18), fill=with_alpha(C.VIOLET, 130))
        c.ellipse((ex - 12, ey - 12, ex + 12, ey + 12), outline=with_alpha(C.VIOLET, 245), width=3)
        c.draw.line([(ex, ey - 16), (ex, ey + 16)], fill=with_alpha(C.CYAN, 245), width=3)
        c.draw.line([(ex - 16, ey), (ex + 16, ey)], fill=with_alpha(C.CYAN, 245), width=3)
        c.ellipse((ex - 5, ey - 5, ex + 5, ey + 5), fill=C.OFFWHITE)
        # cheek glyphs
        for i in range(4):
            gy = ey + 22 + i * 13
            c.glow_line([(ex - 14 + (i % 2) * 8, gy), (ex + 14 - (i % 2) * 8, gy)],
                        fill=with_alpha(C.VIOLET, 190), width=2)
        # eye glow (right eye)
        c.glow_ellipse((cx + 8, cy - 10, cx + 26, cy + 4), fill=with_alpha(C.CYAN, 150))
        c.ellipse((cx + 11, cy - 7, cx + 23, cy + 1), fill=with_alpha(C.CYAN, 245))
    else:  # phantom_visage
        # Full-face porcelain mask, cracked, with an emissive seam.
        c.polygon(
            [(cx - HEAD_RX - 2, cy - 34), (cx - HEAD_RX + 8, cy - 54),
             (cx, cy - 66), (cx + HEAD_RX - 8, cy - 54), (cx + HEAD_RX + 2, cy - 34),
             (cx + HEAD_RX, cy + 20), (cx + 34, cy + 56), (cx, cy + 66),
             (cx - 34, cy + 56), (cx - HEAD_RX, cy + 20)],
            fill=(232, 234, 240, 255),
        )
        # eye slits
        for side in (-1, 1):
            c.polygon(
                [(cx + side * 8, cy - 16), (cx + side * 34, cy - 22),
                 (cx + side * 32, cy - 2), (cx + side * 8, cy - 6)],
                fill=C.INK,
            )
            c.glow_ellipse((cx + side * 12 - 8, cy - 20, cx + side * 12 + 8, cy - 4),
                           fill=with_alpha(C.MAGENTA, 190))
        # cracks
        for pts in (
            [(cx - 4, cy + 2), (cx + 14, cy + 20), (cx + 6, cy + 44)],
            [(cx - 4, cy + 2), (cx - 24, cy + 24), (cx - 18, cy + 50)],
        ):
            c.glow_line(pts, fill=with_alpha(C.MAGENTA, 210), width=2)
        c.glow_line([(cx - HEAD_RX + 4, cy + 6), (cx, cy + 14), (cx + HEAD_RX - 4, cy + 6)],
                    fill=with_alpha(C.MAGENTA, 200), width=3)

    return c.finish(blur=6)


def render_weapon(key: str) -> Image.Image:
    c = Canvas()
    # Right hand (viewer's right) at roughly (344, 352).
    hx, hy = 344, 352
    accent = _accent_for(key)

    if key == "combat_knife":
        c.polygon([(hx + 4, hy + 14), (hx + 30, hy - 6), (hx + 96, hy - 78),
                   (hx + 84, hy - 46), (hx + 26, hy + 24), (hx + 4, hy + 34)],
                  fill=C.STEEL)
        c.glow_line([(hx + 10, hy + 16), (hx + 86, hy - 66)],
                    fill=with_alpha(C.OFFWHITE, 200), width=2)
        c.rrect((hx - 4, hy + 6, hx + 30, hy + 44), 6, fill=C.INK,
                outline=with_alpha(C.AMBER, 200), width=2)
    elif key == "neon_sai":
        # central prong
        c.polygon([(hx + 10, hy + 20), (hx + 26, hy + 16), (hx + 104, hy - 62),
                   (hx + 92, hy - 74), (hx + 4, hy - 4)], fill=C.STEEL)
        # side prongs
        for off in (-1, 1):
            c.polygon([(hx + 8 + off * 4, hy + 18), (hx + 20 + off * 4, hy + 26),
                       (hx + 48 + off * 6, hy - 6), (hx + 40 + off * 6, hy - 16)],
                      fill=(70, 76, 88, 255))
        c.glow_line([(hx + 8, hy + 14), (hx + 96, hy - 66)],
                    fill=with_alpha(C.CYAN, 235), width=3)
        c.rrect((hx - 6, hy + 6, hx + 26, hy + 46), 6, fill=C.INK,
                outline=with_alpha(C.CYAN, 220), width=2)
    elif key == "shadow_katana":
        # long slightly curved blade held downward-right
        blade = [(hx + 8, hy + 6), (hx + 34, hy - 14), (hx + 152, hy - 150),
                 (hx + 166, hy - 176), (hx + 158, hy - 132), (hx + 26, hy + 24)]
        c.polygon(blade, fill=(178, 186, 202, 255))
        c.polygon([(hx + 12, hy + 8), (hx + 30, hy - 8), (hx + 154, hy - 148),
                   (hx + 158, hy - 140)], fill=(96, 102, 118, 255))
        # emissive edge
        c.glow_line([(hx + 14, hy + 4), (hx + 160, hy - 168)],
                    fill=with_alpha(C.CYAN, 230), width=3)
        # tsuba + grip
        c.rrect((hx - 4, hy - 4, hx + 34, hy + 14), 5, fill=C.INK,
                outline=with_alpha(C.CYAN, 180), width=2)
        c.rrect((hx - 40, hy + 6, hx + 6, hy + 34), 6, fill=C.CHARCOAL,
                outline=with_alpha(C.AMBER, 200), width=2)
        for i in range(4):
            c.draw.line([(hx - 34 + i * 10, hy + 8), (hx - 40 + i * 10, hy + 32)],
                        fill=C.INK, width=2)
        # shadow wisps along the blade
        for i in range(5):
            t = i / 4
            bx = lerp(hx + 40, hx + 150, t)
            by = lerp(hy - 30, hy - 150, t)
            c.glow_ellipse((bx - 16, by - 16, bx + 16, by + 16),
                           fill=with_alpha(C.VIOLET, 90))
    elif key == "arcane_gauntlet":
        # bulky forearm gauntlet wrapping the hand.
        c.rrect((hx - 34, hy - 46, hx + 62, hy + 54), 14, fill=C.CHARCOAL,
                outline=with_alpha(C.VIOLET, 190), width=3)
        c.rrect((hx - 44, hy - 22, hx - 18, hy + 30), 8, fill=C.TACTICAL)
        # segmented plates
        for i in range(3):
            y = hy - 36 + i * 30
            c.rrect((hx - 26, y, hx + 54, y + 22), 7, fill=C.SLATE,
                    outline=with_alpha(C.VIOLET, 120), width=2)
        # emissive core
        c.glow_ellipse((hx + 4, hy - 12, hx + 44, hy + 28), fill=with_alpha(C.VIOLET, 170))
        c.ellipse((hx + 12, hy - 4, hx + 36, hy + 20), fill=with_alpha(C.VIOLET, 250))
        c.ellipse((hx + 18, hy + 2, hx + 30, hy + 14), fill=C.OFFWHITE)
        # knuckle spikes
        for i in range(4):
            kx = hx + 60 + 0
            ky = hy - 40 + i * 26
            c.polygon([(kx, ky), (kx + 26, ky + 6), (kx, ky + 16)],
                      fill=(120, 128, 144, 255))
            c.glow_line([(kx, ky + 8), (kx + 20, ky + 8)],
                        fill=with_alpha(C.VIOLET, 200), width=2)
    else:  # police_baton
        # extended baton angled down-right
        c.polygon([(hx - 6, hy + 6), (hx + 14, hy - 6), (hx + 116, hy + 86),
                   (hx + 96, hy + 104)], fill=C.SLATE)
        c.glow_line([(hx + 2, hy + 2), (hx + 108, hy + 94)],
                    fill=with_alpha(C.AMBER, 170), width=3)
        c.rrect((hx - 34, hy - 8, hx + 6, hy + 34), 6, fill=C.INK,
                outline=with_alpha(C.STEEL, 200), width=2)
        c.ellipse((hx + 96, hy + 78, hx + 124, hy + 106), fill=(48, 52, 62, 255),
                  outline=with_alpha(accent, 200), width=2)

    return c.finish(blur=6)


def render_aura(key: str) -> Image.Image:
    c = Canvas()

    if key == "ethereal_smoke":
        rng = random.Random(4)
        for _ in range(46):
            side = rng.choice((-1, 1))
            bx = 256 + side * rng.randint(50, 150)
            by = rng.randint(140, 470)
            r = rng.randint(26, 76)
            c.glow_ellipse((bx - r, by - r // 2, bx + r, by + r // 2),
                           fill=with_alpha(C.SMOKE, rng.randint(50, 110)))
        # rising plumes over the shoulders
        for side in (-1, 1):
            pts = [(256 + side * 70, 300), (256 + side * 120, 220),
                   (256 + side * 92, 140), (256 + side * 140, 70)]
            c.glow_line(pts, fill=(150, 160, 180, 120), width=26)
    elif key == "lightning_crackle":
        rng = random.Random(11)
        for _ in range(14):
            side = rng.choice((-1, 1))
            x = 256 + side * rng.randint(60, 170)
            y = rng.randint(60, 300)
            pts = [(x, y)]
            for _step in range(5):
                pts.append((pts[-1][0] + rng.randint(-46, 46),
                            pts[-1][1] + rng.randint(28, 74)))
            c.glow_line(pts, fill=with_alpha(C.CYAN, 245), width=4)
            c.line(pts, fill=C.OFFWHITE, width=2)
        # ambient static field
        for _ in range(40):
            bx, by = rng.randint(40, 472), rng.randint(40, 472)
            c.glow_ellipse((bx - 8, by - 8, bx + 8, by + 8),
                           fill=with_alpha(C.CYAN, rng.randint(40, 120)))
    elif key == "dark_flame":
        rng = random.Random(3)
        # flame tongues rising around the silhouette
        for side in (-1, 1, 0):
            base_x = 256 + side * 108
            for _ in range(3):
                pts = []
                x = base_x + rng.randint(-30, 30)
                y = 470
                for _step in range(7):
                    pts.append((x, y))
                    x += rng.randint(-34, 34)
                    y -= rng.randint(34, 62)
                c.glow_line(pts, fill=with_alpha(C.MAGENTA, 210), width=22)
                c.line(pts, fill=with_alpha(C.DARK_FLAME, 150), width=8)
        # ember motes
        for _ in range(60):
            bx, by = rng.randint(60, 452), rng.randint(40, 460)
            r = rng.randint(1, 4)
            c.glow_ellipse((bx - r * 3, by - r * 3, bx + r * 3, by + r * 3),
                           fill=with_alpha(C.MAGENTA, rng.randint(70, 190)))
        c.glow_ellipse((110, 300, 402, 520), fill=with_alpha(C.MAGENTA, 60))
    else:
        # no aura file expected; return transparent so compositing is a no-op
        pass

    return c.finish(blur=14)


def _accent_for(key: str) -> RGBA:
    """Deterministic per-item neon accent so the set feels curated, not random."""
    order = [C.CYAN, C.MAGENTA, C.AMBER, C.VIOLET, C.TOXIC]
    return order[sum(key.encode()) % len(order)]


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_RENDERERS: dict[str, Callable[[str], Image.Image]] = {
    "backgrounds": render_background,
    "body": render_body,
    "legs": render_legs,
    "tops": render_top,
    "hair": render_hair,
    "accessories": render_accessory,
    "weapons": render_weapon,
    "auras": render_aura,
}


def render_spec(folder: str, key: str) -> Image.Image:
    renderer = _RENDERERS.get(folder)
    if renderer is None:
        raise KeyError(f"no renderer registered for layer folder {folder!r}")
    img = renderer(key)
    if img.size != (CANVAS, CANVAS):
        img = img.resize((CANVAS, CANVAS), Image.Resampling.LANCZOS)
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    return img


def ensure_assets(
    force: bool = False,
    *,
    root: Path | None = None,
    specs: list[tuple[str, str]] | None = None,
) -> list[Path]:
    """Generate any missing placeholder PNGs and return the written paths.

    Idempotent: existing files are skipped unless ``force`` is set, so boot
    cost on a warm machine is a directory listing rather than a render pass.
    """
    from database.items import art_specs  # local import: avoids a cycle at module load

    base = Path(root or settings.assets_dir)
    to_render = specs if specs is not None else art_specs()

    written: list[Path] = []
    for folder, key in to_render:
        target = base / folder / f"{key}.png"
        if target.exists() and not force:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        image = render_spec(folder, key)
        image.save(target, "PNG", optimize=True)
        written.append(target)

    if written:
        logger.info("generated %d placeholder asset(s)", len(written))
    else:
        logger.debug("all %d assets already present", len(to_render))
    return written
