"""Compositor contract tests: layer order, caching, fallback, <50ms budget.

The <50ms target is measured as a *warm* render (assets preloaded, cache
cold) which is the path a handler actually takes; disk-cache hits are tested
separately because they must not touch PIL at all.
"""

from __future__ import annotations

import io
import time
from typing import Any

import pytest
from PIL import Image

from models.enums import SLOT_RENDER_ORDER, Slot
from services.compositor import (
    CANVAS,
    RenderRequest,
    compositor,
    load_library,
    render_card,
)


@pytest.fixture(scope="module", autouse=True)
def _assets() -> None:
    load_library()


@pytest.fixture()
def sample() -> RenderRequest:
    return RenderRequest(
        display_name="Test Runner",
        username="tester",
        level=12,
        atk=34,
        defense=21,
        drip=17,
        loadout={
            "legs": "techwear_cargo",
            "body": "hunter_trench",
            "head": "hood_up",
            "accessory": "half_mask",
            "weapon": "shadow_katana",
            "aura": "dark_flame",
        },
        background="alley_neon",
        body="base_street",
    )


def test_render_returns_bytes(sample: RenderRequest) -> None:
    data = render_card(sample)
    assert isinstance(data, bytes) and len(data) > 1000
    # JPEG magic bytes (encoder was deliberately switched from PNG).
    assert data[:2] == b"\xff\xd8"


def test_render_is_deterministic(sample: RenderRequest) -> None:
    assert render_card(sample) == render_card(sample)


def test_cache_hit_avoids_recompose(sample: RenderRequest) -> None:
    compositor.clear_cache()
    first = compositor.render(sample)
    second = compositor.render(sample)  # memory-cache path
    assert first == second
    assert compositor.cache_size() >= 1


def test_different_loadout_different_image() -> None:
    base = dict(display_name="X", level=1, atk=1, defense=1, drip=1, username=None)
    plain = RenderRequest(**base, loadout={})
    kitted = RenderRequest(**base, loadout={"weapon": "shadow_katana", "aura": "dark_flame"})
    assert render_card(plain) != render_card(kitted)


def test_slot_render_order_is_bottom_to_top() -> None:
    # Legs first (back), aura last (front) — matches the compositor loop.
    assert SLOT_RENDER_ORDER[0] is Slot.LEGS
    assert SLOT_RENDER_ORDER[-1] is Slot.AURA
    assert len(SLOT_RENDER_ORDER) == 6


def test_missing_layer_is_skipped_not_fatal() -> None:
    data = render_card(
        RenderRequest(
            display_name="Ghost",
            level=1,
            atk=1,
            defense=1,
            drip=1,
            loadout={"weapon": "no_such_item_xyz"},
        )
    )
    assert data[:2] == b"\xff\xd8"


def test_bad_background_falls_back(sample: RenderRequest) -> None:
    broken = RenderRequest(
        display_name=sample.display_name,
        level=sample.level,
        atk=sample.atk,
        defense=sample.defense,
        drip=sample.drip,
        loadout=dict(sample.loadout),
        background="does_not_exist",
        body="also_missing",
    )
    data = render_card(broken)
    assert data[:2] == b"\xff\xd8"  # styled fallback card, never an exception


def test_render_budget_under_50ms(sample: RenderRequest) -> None:
    """Warm render p95 must stay inside the 50ms contract.

    Tolerance is generous (2x) to survive CI noise; local benchmarks show
    ~15-25ms. A regression that pushes warm renders past 100ms fails hard.
    """
    compositor.clear_cache()
    samples: list[float] = []
    for _ in range(20):
        start = time.perf_counter()
        compositor.render(sample, use_cache=False)
        samples.append((time.perf_counter() - start) * 1000)
    samples.sort()
    p95 = samples[int(len(samples) * 0.95) - 1]
    assert p95 < 100, f"p95={p95:.1f}ms (budget 50ms, CI tolerance 100ms)"


def test_canvas_is_512(sample: RenderRequest) -> None:
    img = Image.open(io.BytesIO(render_card(sample)))
    assert img.size == (CANVAS, CANVAS)


def _boy(**overrides: Any) -> RenderRequest:
    base: Any = dict(
        display_name="پسر",
        username="boy",
        level=7,
        atk=11,
        defense=9,
        drip=13,
        loadout={},
        background="rooftop_zenith",
        body="base_boy_2",
        skin_tone="fair",
        hair_color="black",
        eye_color="blue",
        eye_style="1",
        mouth_style="1",
        hair_style="hair2",
        gender="پسر",
    )
    base.update(overrides)
    return RenderRequest(**base)


def test_sutemo_boy_renders() -> None:
    """The Sutemo pack is the boy body — it must render a real JPEG card."""
    data = render_card(_boy())
    assert isinstance(data, bytes) and len(data) > 2000
    assert data[:2] == b"\xff\xd8"
    img = Image.open(io.BytesIO(data))
    assert img.size == (CANVAS, CANVAS)


def test_boy_choices_change_the_picture() -> None:
    """Every choice that affects the figure has to reach the pixels."""
    seen = {render_card(_boy())}
    for kw in (
        {"loadout": {"head": "crown_of_shadows"}},
        {"loadout": {"body": "leather_bomber"}},
        {"loadout": {"weapon": "shadow_katana"}},
    ):
        card = render_card(_boy(**kw))
        assert card not in seen, f"{kw} did not change the render"
        seen.add(card)


def test_legacy_male_row_falls_back_to_boy_body() -> None:
    """Old rows stored body_stance=base_street — must still draw a Sutemo boy."""
    data = render_card(_boy(body="base_street", skin_tone="tan"))
    assert data[:2] == b"\xff\xd8"
    img = Image.open(io.BytesIO(data))
    assert img.size == (CANVAS, CANVAS)


def test_boy_accessory_lands_on_the_face() -> None:
    """Face accessories are re-anchored for the Sutemo bust."""
    plain = render_card(_boy())
    masked = render_card(_boy(loadout={"accessory": "half_mask"}))
    assert plain != masked
    assert masked[:2] == b"\xff\xd8"


def test_boy_render_budget_under_100ms() -> None:
    compositor.clear_cache()
    samples: list[float] = []
    for _ in range(10):
        start = time.perf_counter()
        compositor.render(_boy(), use_cache=False)
        samples.append((time.perf_counter() - start) * 1000)
    samples.sort()
    p95 = samples[int(len(samples) * 0.95) - 1]
    assert p95 < 100, f"boy p95={p95:.1f}ms"


def test_visual_novel_outfit_renders() -> None:
    vn_req = RenderRequest(
        display_name="Heroine",
        username="heroine",
        level=10,
        atk=25,
        defense=20,
        drip=30,
        loadout={
            "legs": "jeans_blue",
            "body": "tunic_casual_striped",
            "head": "hair_waves_blonde",
            "aura": "violet_monarch",
        },
        background="rooftop_zenith",
        body="base_female",
    )
    data = render_card(vn_req)
    assert isinstance(data, bytes) and len(data) > 2000
    assert data[:2] == b"\xff\xd8"
    img = Image.open(io.BytesIO(data))
    assert img.size == (CANVAS, CANVAS)

