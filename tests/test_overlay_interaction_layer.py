from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "app/static/overlay-interactions.css").read_text(encoding="utf-8")
EDITOR_TEMPLATE = (ROOT / "app/templates/editor.html").read_text(encoding="utf-8")
PLAYER_TEMPLATE = (ROOT / "app/templates/player.html").read_text(encoding="utf-8")


def test_editor_overlay_sits_below_region_interactions_and_allows_drawing() -> None:
    assert ".hotspot {\n  z-index: 50;" in CSS
    assert ".editor-overlay:not(.is-selected) {\n  pointer-events: none;" in CSS
    assert ".editor-overlay:not(.is-selected) .editor-overlay-label {\n  pointer-events: auto;" in CSS
    assert ".editor-overlay.is-selected {\n  pointer-events: auto;" in CSS


def test_player_video_overlay_no_longer_covers_region_hotspots() -> None:
    assert ".player-overlay.is-image,\n.player-overlay.is-video {\n  z-index: 10;" in CSS
    assert ".player-hotspot {\n  z-index: 20;" in CSS


def test_overlay_interaction_styles_are_loaded_in_editor_and_preview() -> None:
    stylesheet = "/static/overlay-interactions.css?v=20260906-overlay-interactions"
    assert stylesheet in EDITOR_TEMPLATE
    assert stylesheet in PLAYER_TEMPLATE
