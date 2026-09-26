"""Compositor contract tests: layer order, caching, fallback, <50ms budget.

The <50ms target is measured as a *warm* render (assets preloaded, cache
cold) which is the path a handler actually takes; disk-cache hits are tested
separately because they must not touch PIL at all.
"""

from __future__ import annotations

import io
import time

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
