"""Dynamic character image compositor.

Renders a 512x512 character card by stacking transparent PNG layers in a fixed
hierarchy, then stamps a cyberpunk HUD bar across the bottom with the player's
name and stats.

Performance contract (<50ms, enforced by ``tests/test_compositor.py``):

* Every asset is **preloaded into memory once** at startup, so a render never
  touches disk. The "hash-based cache" from the brief then sits *above* that:
  identical outfits resolve to a cached PNG without re-compositing at all.
* Fonts are loaded once and reused; the HUD is drawn on a single overlay image.
* A single ``Image.alpha_composite`` pass per layer keeps the work O(layers).

Everything is wrapped so that a bad asset degrades to a styled fallback card
instead of raising into a Telegram handler.
"""

from __future__ import annotations

import hashlib
import io
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from config import settings
from models.enums import LAYER_ORDER, SLOT_RENDER_ORDER, Slot

logger = logging.getLogger(__name__)

CANVAS = 512
HUD_H = 96
SCRIM = 28                      # gradient fade above the bar, keeps text legible
STRIP_H = SCRIM + HUD_H         # height of the HUD overlay strip
HUD_TOP = CANVAS - HUD_H
STRIP_TOP = HUD_TOP - SCRIM     # canvas y where the strip is placed

# HUD layout (strip-local coordinates). Fixed so the template can be prebaked.
BADGE_X0, BADGE_X1 = 16, 92
CHIP_W, CHIP_GAP = 66, 6
CHIP_START_X = CANVAS - 14 - (3 * CHIP_W + 2 * CHIP_GAP)   # = 288
CHIP_LABELS = ("ATK", "DEF", "DRIP")

RGBA = tuple[int, int, int, int]

# --- palette ---------------------------------------------------------------
INK: RGBA = (8, 9, 13, 235)
PANEL: RGBA = (10, 12, 18, 226)
CYAN: RGBA = (0, 229, 255, 255)
MAGENTA: RGBA = (255, 46, 136, 255)
AMBER: RGBA = (255, 176, 32, 255)
VIOLET: RGBA = (168, 85, 247, 255)
OFFWHITE: RGBA = (232, 235, 242, 255)
STEEL: RGBA = (132, 140, 156, 255)

_STAT_ACCENT: dict[str, RGBA] = {"ATK": MAGENTA, "DEF": CYAN, "DRIP": AMBER}

# Candidate font faces, most preferred first. A sensible default is always
# available because ``ImageFont.load_default`` needs no files at all.
_FONT_CANDIDATES = (
    "C:/Windows/Fonts/seguisb.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/consolab.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
)


# ---------------------------------------------------------------------------
# Render request
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RenderRequest:
    """Everything that influences a card's pixels — i.e. the cache key."""

    display_name: str
    level: int
    atk: int
    defense: int
    drip: int
    loadout: dict[str, str | None] = field(default_factory=dict)
    background: str = "alley_neon"
    body: str = "base_street"
    username: str | None = None

    def signature(self) -> str:
        """Stable, order-independent string of everything visible on the card."""
        slots = ",".join(
            f"{slot}={self.loadout.get(slot.value) or '-'}" for slot in SLOT_RENDER_ORDER
        )
        return "|".join(
            (
                f"bg={self.background}",
                f"body={self.body}",
                f"slots={slots}",
                f"name={self.display_name}",
                f"lvl={self.level}",
                f"stats={self.atk},{self.defense},{self.drip}",
            )
        )


# ---------------------------------------------------------------------------
# Asset library
# ---------------------------------------------------------------------------


class AssetLibrary:
    """Preloaded, immutable layer images keyed by ``folder/key``."""

    def __init__(self) -> None:
        self._layers: dict[tuple[str, str], Image.Image] = {}
        self._version = "empty"

    @property
    def version(self) -> str:
        """Content hash of everything loaded; folded into every cache key."""
        return self._version

    @property
    def loaded(self) -> int:
        return len(self._layers)

    def load(self, root: Path | None = None) -> int:
        """Read every PNG under ``root`` into memory exactly once."""
        base = Path(root or settings.assets_dir)
        layers: dict[tuple[str, str], Image.Image] = {}
        digest = hashlib.sha1()

        if base.exists():
            for folder in sorted(LAYER_ORDER):
                folder_path = base / folder
                if not folder_path.is_dir():
                    continue
                for path in sorted(folder_path.glob("*.png")):
                    raw = path.read_bytes()
                    digest.update(path.name.encode())
                    digest.update(raw)
                    try:
                        img = Image.open(io.BytesIO(raw)).convert("RGBA")
                    except Exception:  # noqa: BLE001 - skip unreadable art
                        logger.exception("unreadable asset %s", path)
                        continue
                    if img.size != (CANVAS, CANVAS):
                        img = img.resize((CANVAS, CANVAS), Image.Resampling.LANCZOS)
                    layers[(folder, path.stem)] = img
        else:
            logger.warning("asset directory %s does not exist", base)

        self._layers = layers
        self._version = digest.hexdigest()[:16]
        logger.info("loaded %d layer image(s), library version %s", len(layers), self._version)
        return len(layers)

    def get(self, folder: str, key: str) -> Image.Image | None:
        return self._layers.get((folder, key))

    def has(self, folder: str, key: str) -> bool:
        return (folder, key) in self._layers

    def keys(self, folder: str) -> list[str]:
        return sorted(k for (f, k) in self._layers if f == folder)


# Process-wide singleton (``main.py`` calls ``load_library()`` at boot).
library = AssetLibrary()


def load_library(root: Path | None = None) -> int:
    return library.load(root)


# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------

_FONT_CACHE: dict[int, ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    cached = _FONT_CACHE.get(size)
    if cached is not None:
        return cached
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont | None = None
    for candidate in _FONT_CANDIDATES:
        try:
            font = ImageFont.truetype(candidate, size)
            break
        except OSError:
            continue
    if font is None:
        # Pillow >= 10.1 scales the bundled default face when given a size.
        try:
            font = ImageFont.load_default(size=size)
        except TypeError:
            font = ImageFont.load_default()
    _FONT_CACHE[size] = font
    return font


FontLike = ImageFont.FreeTypeFont | ImageFont.ImageFont


def _text_width(draw: ImageDraw.ImageDraw, text: str, font: FontLike) -> float:
    try:
        return float(draw.textlength(text, font=font))
    except AttributeError:
        bbox = draw.textbbox((0, 0), text, font=font)
        return float(bbox[2] - bbox[0])


def _fit(draw: ImageDraw.ImageDraw, text: str, font: FontLike,
         max_width: float) -> str:
    """Truncate ``text`` with an ellipsis so it never overruns its column."""
    if _text_width(draw, text, font) <= max_width:
        return text
    ellipsis = "..."
    trimmed = text
    while trimmed and _text_width(draw, trimmed + ellipsis, font) > max_width:
        trimmed = trimmed[:-1]
    return (trimmed.rstrip() + ellipsis) if trimmed else ellipsis


# ---------------------------------------------------------------------------
# Compositor
# ---------------------------------------------------------------------------


class Compositor:
    """Layer stacking + HUD rendering."""

    def __init__(self, assets: AssetLibrary | None = None, cache_dir: Path | None = None) -> None:
        self._assets = assets or library
        self._cache_dir = Path(cache_dir or settings.cache_dir)
        self._memory: dict[str, bytes] = {}
        # Pre-flattened background+body plates: (image, is_fully_opaque).
        self._base_cache: dict[tuple[str, str], tuple[Image.Image, bool]] = {}
        # Disk persistence runs on a single background thread so the ~15ms
        # write (antivirus-sensitive on Windows) never lands inside the
        # render path the caller is waiting on.
        self._writer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="card-cache")

    # -- public API --------------------------------------------------------

    def render(self, request: RenderRequest, *, use_cache: bool = True) -> bytes:
        """Return encoded card bytes (JPEG q92). Never raises for bad assets."""
        key = self._cache_key(request)

        if use_cache:
            hit = self._memory.get(key)
            if hit is not None:
                return hit
            hit = self._read_disk(key)
            if hit is not None:
                self._memory[key] = hit
                return hit

        try:
            png = self._compose(request)
        except Exception:  # noqa: BLE001 - degrade, never break a handler
            logger.exception("compositor failed for %r", request.signature())
            png = self._fallback_card(request)

        if use_cache:
            self._memory[key] = png
            self._write_disk(key, png)
        return png

    def clear_cache(self) -> None:
        self._memory.clear()
        self._base_cache.clear()

    def close(self) -> None:
        """Flush pending disk writes; call during shutdown."""
        self._writer.shutdown(wait=True, cancel_futures=False)

    def cache_size(self) -> int:
        return len(self._memory)

    # -- cache -------------------------------------------------------------

    def _cache_key(self, request: RenderRequest) -> str:
        material = f"{self._assets.version}|{request.signature()}"
        return hashlib.sha1(material.encode("utf-8")).hexdigest()

    def _read_disk(self, key: str) -> bytes | None:
        path = self._cache_dir / f"{key}.jpg"
        try:
            return path.read_bytes() if path.is_file() else None
        except OSError:
            return None

    def _write_disk(self, key: str, image_bytes: bytes) -> None:
        """Queue a background write; failures are logged, never raised."""
        self._writer.submit(self._write_disk_sync, key, image_bytes)

    def _write_disk_sync(self, key: str, image_bytes: bytes) -> None:
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            (self._cache_dir / f"{key}.jpg").write_bytes(image_bytes)
        except OSError:
            logger.debug("could not persist composite cache entry %s", key)

    # -- composition -------------------------------------------------------

    def _compose(self, request: RenderRequest) -> bytes:
        # ``Image.alpha_composite(a, b)`` (module form) benchmarks ~30% faster
        # than the in-place instance method on this artwork, and it never
        # mutates its inputs — so the preloaded library stays pristine.
        plate, plate_opaque = self._base(request.background, request.body)
        composited = False

        # Layers 2..7: equipped cosmetics in bottom-to-top order.
        for slot in SLOT_RENDER_ORDER:
            key = request.loadout.get(slot.value)
            if not key:
                continue
            layer = self._assets.get(_folder_for(slot), key)
            if layer is None:
                logger.debug("missing layer art for %s/%s", slot.value, key)
                continue
            plate = Image.alpha_composite(plate, layer)
            composited = True

        if not composited:
            # ``_base`` may return the shared cached image; the in-place HUD
            # composite below must never write into it.
            plate = plate.copy()

        # HUD last, on top of everything. The HUD is a short strip rather than
        # a full 512x512 overlay: compositing 512x124 costs ~1.7ms versus
        # ~3.8ms for a full-canvas pass.
        plate.alpha_composite(self._hud(request), dest=(0, STRIP_TOP))

        # An opaque plate guarantees an opaque result: alpha_composite over an
        # opaque destination is always 255. So the encoder can skip its
        # per-render alpha scan (1ms) and go straight to RGB.
        return self._encode(plate, assume_opaque=plate_opaque)

    def _base(self, background: str, body: str) -> tuple[Image.Image, bool]:
        """Return the cached ``(background ⊕ body)`` plate and its opacity.

        These two layers never change independently of each other in practice,
        so pre-flattening them removes one full-canvas composite (~3ms) from
        every render. The dict is bounded by |backgrounds| x |bodies| (6 today).
        """
        key = (background, body)
        cached = self._base_cache.get(key)
        if cached is not None:
            return cached

        plate = self._assets.get("backgrounds", background) or Image.new(
            "RGBA", (CANVAS, CANVAS), (12, 13, 18, 255)
        )
        opaque = plate.getchannel("A").getextrema()[0] == 255
        figure = self._assets.get("body", body) or self._assets.get("body", "base_street")
        if figure is not None:
            plate = Image.alpha_composite(plate, figure)
            if not opaque:
                opaque = plate.getchannel("A").getextrema()[0] == 255

        if len(self._base_cache) >= 16:
            self._base_cache.clear()
        entry = (plate, opaque)
        self._base_cache[key] = entry
        return entry


    @staticmethod
    def _encode(image: Image.Image, *, assume_opaque: bool = False) -> bytes:
        """Encode the final card.

        JPEG rather than PNG: the finished card is fully opaque (the background
        covers the whole canvas), Telegram re-encodes to JPEG on delivery
        anyway, and it is an order of magnitude faster than PNG on this
        artwork — ``compress_level=1`` PNG still costs 35ms+ on its own, which
        alone would blow the <50ms render budget.

        ``assume_opaque`` skips the alpha scan when the caller already knows
        the plate was opaque (alpha-compositing onto an opaque destination is
        always opaque, so that inference is sound).
        """
        if image.mode != "RGB":
            if assume_opaque:
                image = image.convert("RGB")
            else:
                lo, _hi = image.getchannel("A").getextrema()
                if lo == 255:
                    image = image.convert("RGB")
                else:
                    # Cut-out background art: flatten over the HUD panel colour
                    # so transparency blends instead of exposing raw RGB.
                    rgb = Image.new("RGB", image.size, (10, 12, 18))
                    rgb.paste(image, mask=image.getchannel("A"))
                    image = rgb
        buf = io.BytesIO()
        # subsampling=1 (4:2:2) saves ~4ms over 4:4:4 with no visible loss on
        # the HUD's white-on-dark text.
        image.save(buf, format="JPEG", quality=92, subsampling=1)
        return buf.getvalue()

    # -- HUD ---------------------------------------------------------------

    def _hud(self, request: RenderRequest) -> Image.Image:
        """Build the HUD strip (scrim + stat bar).

        Everything that does not vary per player — the scrim gradient, panel,
        neon rules, badge box, chip outlines and chip labels — is drawn once
        into a template and copied. Only six text runs are painted per card,
        which cuts the HUD from ~9ms to ~3ms.

        Returned image is ``STRIP_H`` tall; the caller composites it at
        ``STRIP_TOP``.
        """
        overlay = hud_template().copy()
        draw = ImageDraw.Draw(overlay)

        # --- level number --------------------------------------------------
        lvl_font = _font(30)
        badge = (BADGE_X0, SCRIM + 14, BADGE_X1, STRIP_H - 14)
        lvl_text = str(request.level)
        lw = _text_width(draw, lvl_text, lvl_font)
        draw.text((badge[0] + (badge[2] - badge[0] - lw) / 2, SCRIM + 44),
                  lvl_text, font=lvl_font, fill=OFFWHITE)

        # --- name + power line --------------------------------------------
        name_font = _font(25)
        sub_font = _font(15)
        name_x, right_edge = 104, 300
        display = request.username or request.display_name
        name = _fit(draw, display, name_font, right_edge - name_x)
        draw.text((name_x, SCRIM + 20), name, font=name_font, fill=OFFWHITE)

        power = request.atk * 2 + request.defense
        sub = _fit(draw, f"POWER {power} • LVL {request.level}", sub_font,
                   right_edge - name_x)
        draw.text((name_x, SCRIM + 56), sub, font=sub_font, fill=STEEL)

        # --- stat values (chips themselves are in the template) ------------
        val_font = _font(24)
        for i, value in enumerate((request.atk, request.defense, request.drip)):
            x0 = CHIP_START_X + i * (CHIP_W + CHIP_GAP)
            y0 = SCRIM + 14
            val = str(value)
            val_w = _text_width(draw, val, val_font)
            draw.text((x0 + (CHIP_W - val_w) / 2, y0 + 30), val,
                      font=val_font, fill=OFFWHITE)

        return overlay

    # -- failure path ------------------------------------------------------

    def _fallback_card(self, request: RenderRequest) -> bytes:
        """Always-returnable card so a broken asset never reaches Telegram as an error."""
        img = _fallback_template().copy()
        draw = ImageDraw.Draw(img)
        for text, font, y, fill in (
            ("CHARACTER UNAVAILABLE", _font(34), 210, MAGENTA),
            (str(request.display_name), _font(26), 268, OFFWHITE),
            ("Layer stack could not be rendered.", _font(20), 316, STEEL),
        ):
            w = _text_width(draw, text, font)
            draw.text(((CANVAS - w) / 2, y), text, font=font, fill=fill)
        return self._encode(img)


def _dim(color: RGBA) -> RGBA:
    """Muted version of an accent, used for chip outlines."""
    return (color[0] // 2, color[1] // 2, color[2] // 2, 255)


_HUD_TEMPLATE: Image.Image | None = None


def hud_template() -> Image.Image:
    """Build (once) the static HUD chrome: scrim, panel, rules, badge, chips."""
    global _HUD_TEMPLATE
    if _HUD_TEMPLATE is not None:
        return _HUD_TEMPLATE

    overlay = Image.new("RGBA", (CANVAS, STRIP_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Scrim above the bar so text stays legible over bright art.
    for i in range(SCRIM):
        a = int(150 * (1 - i / SCRIM))
        draw.line([(0, i), (CANVAS, i)], fill=(6, 7, 11, a))

    # Panel + neon rules.
    draw.rectangle((0, SCRIM, CANVAS, STRIP_H), fill=PANEL)
    draw.line([(0, SCRIM + 1), (CANVAS, SCRIM + 1)], fill=CYAN, width=3)
    draw.line([(0, STRIP_H - 2), (CANVAS, STRIP_H - 2)],
              fill=(0, 90, 110, 255), width=2)
    for x in (14, CANVAS - 14):
        draw.line([(x, SCRIM + 8), (x, SCRIM + 26)], fill=CYAN, width=2)

    # Level badge box + its static caption.
    badge = (BADGE_X0, SCRIM + 14, BADGE_X1, STRIP_H - 14)
    draw.rounded_rectangle(badge, radius=10, fill=(18, 22, 32, 255),
                           outline=VIOLET, width=2)
    cap_font = _font(13)
    cw = _text_width(draw, "LVL", cap_font)
    draw.text((badge[0] + (badge[2] - badge[0] - cw) / 2, SCRIM + 22),
              "LVL", font=cap_font, fill=VIOLET)

    # Stat chips: outline, accent header bar and static label.
    lbl_font = _font(13)
    y0, y1 = SCRIM + 14, STRIP_H - 14
    for i, label in enumerate(CHIP_LABELS):
        x0 = CHIP_START_X + i * (CHIP_W + CHIP_GAP)
        accent = _STAT_ACCENT[label]
        draw.rounded_rectangle((x0, y0, x0 + CHIP_W, y1), radius=8,
                               fill=(16, 19, 28, 255), outline=_dim(accent), width=1)
        draw.line([(x0 + 8, y0 + 4), (x0 + CHIP_W - 8, y0 + 4)],
                  fill=accent, width=2)
        lbl_w = _text_width(draw, label, lbl_font)
        draw.text((x0 + (CHIP_W - lbl_w) / 2, y0 + 9), label,
                  font=lbl_font, fill=accent)

    _HUD_TEMPLATE = overlay
    return overlay


_FALLBACK: Image.Image | None = None


def _fallback_template() -> Image.Image:
    """Build (once) the static backdrop used by the failure card."""
    global _FALLBACK
    if _FALLBACK is not None:
        return _FALLBACK

    img = Image.new("RGBA", (CANVAS, CANVAS), (14, 10, 20, 255))
    draw = ImageDraw.Draw(img)
    # Vertical wash built from horizontal spans (512 fills, not 512 line ops).
    for y in range(CANVAS):
        a = int(60 * (y / CANVAS))
        draw.line([(0, y), (CANVAS, y)], fill=(30, 12, 40, a))
    draw.rounded_rectangle((40, 140, CANVAS - 40, 360), radius=16,
                           outline=MAGENTA, width=3, fill=(18, 12, 26, 255))
    _FALLBACK = img
    return img


def _folder_for(slot: Slot) -> str:
    from models.enums import SLOT_LAYER_FOLDER

    return SLOT_LAYER_FOLDER[slot]


# Process-wide compositor bound to the shared library.
compositor = Compositor()


def render_card(request: RenderRequest) -> bytes:
    """Module-level convenience wrapper used by handlers."""
    return compositor.render(request)


def render_card_timed(request: RenderRequest) -> tuple[bytes, float]:
    """Render and report elapsed milliseconds (used by benchmarks/smoke tests)."""
    start = time.perf_counter()
    png = render_card(request)
    return png, (time.perf_counter() - start) * 1000
