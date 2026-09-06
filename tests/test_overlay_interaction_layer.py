from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "app/static/overlay-interactions.css").read_text(encoding="utf-8")
HTML_LAYER_JS = (ROOT / "app/static/html-interaction-layer.js").read_text(encoding="utf-8")
EDITOR_TEMPLATE = (ROOT / "app/templates/editor.html").read_text(encoding="utf-8")
PLAYER_TEMPLATE = (ROOT / "app/templates/player.html").read_text(encoding="utf-8")


def test_editor_overlay_sits_below_every_region_interaction_state() -> None:
    assert ".overlay-layer {\n  z-index: 10;" in CSS
    assert ".hotspot {\n  z-index: 50;" in CSS
    assert ".hotspot:hover,\n.hotspot.is-hovered {\n  z-index: 55;" in CSS
    assert ".hotspot.is-active,\n.hotspot.draft.is-active {\n  z-index: 60;" in CSS


def test_editor_overlay_allows_drawing_without_losing_selection_controls() -> None:
    assert ".editor-overlay:not(.is-selected) {\n  pointer-events: none;" in CSS
    assert ".editor-overlay:not(.is-selected) .editor-overlay-label {\n  pointer-events: auto;" in CSS
    assert ".editor-overlay.is-selected {\n  pointer-events: auto;" in CSS


def test_html_element_interactions_are_mirrored_above_outer_overlays() -> None:
    assert ".html-interaction-layer {" in CSS
    assert "z-index: 50;" in CSS
    assert ".html-interaction-marker.is-active {\n  z-index: 3;" in CSS
    assert "frame.contentDocument" in HTML_LAYER_JS
    assert "target.getBoundingClientRect()" in HTML_LAYER_JS
    assert "selection?.isNew && selection.kind === 'element'" in HTML_LAYER_JS
    assert "htmlInteractionLayer" in HTML_LAYER_JS


def test_player_video_overlay_no_longer_covers_region_hotspots() -> None:
    assert ".player-overlay.is-image,\n.player-overlay.is-video {\n  z-index: 10;" in CSS
    assert ".player-hotspot {\n  z-index: 20;" in CSS


def test_overlay_interaction_assets_are_loaded() -> None:
    stylesheet = "/static/overlay-interactions.css?v=20260906-overlay-interactions-v3"
    html_layer_script = "/static/html-interaction-layer.js?v=20260906-overlay-interactions-v2"
    assert stylesheet in EDITOR_TEMPLATE
    assert stylesheet in PLAYER_TEMPLATE
    assert html_layer_script in EDITOR_TEMPLATE