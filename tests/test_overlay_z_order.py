import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EDITOR_TEMPLATE = (ROOT / "app/templates/editor.html").read_text(encoding="utf-8")
EDITOR_Z_ORDER_JS = (ROOT / "app/static/overlay-z-order.js").read_text(encoding="utf-8")
OVERLAY_CSS = (ROOT / "app/static/overlay-interactions.css").read_text(encoding="utf-8")
PLAYER_JS = (ROOT / "app/static/player.js").read_text(encoding="utf-8")


class OverlayZOrderTests(unittest.TestCase):
    def test_editor_loads_z_order_extension_after_editor_core(self) -> None:
        core_index = EDITOR_TEMPLATE.index('/static/editor.js')
        z_order_index = EDITOR_TEMPLATE.index('/static/overlay-z-order.js')
        self.assertGreater(z_order_index, core_index)

    def test_controls_support_step_and_edge_reordering(self) -> None:
        for action in ("bottom", "lower", "raise", "top"):
            self.assertIn(f"mode: '{action}'", EDITOR_Z_ORDER_JS)
        self.assertIn("JSON.stringify({z_index: zIndex})", EDITOR_Z_ORDER_JS)
        self.assertIn(".map((overlay, zIndex) => ({overlay, zIndex}))", EDITOR_Z_ORDER_JS)

    def test_editor_uses_persisted_overlay_z_index_for_visual_order(self) -> None:
        self.assertIn(
            "element.style.zIndex = String(clampOverlayZIndex(overlay.z_index));",
            EDITOR_Z_ORDER_JS,
        )
        self.assertIn("applyEditorOverlayZIndexes();", EDITOR_Z_ORDER_JS)

    def test_overlay_stack_remains_between_page_and_interactions(self) -> None:
        self.assertIn(".overlay-layer {\n  z-index: 10;", OVERLAY_CSS)
        self.assertIn(".hotspot {\n  z-index: 50;", OVERLAY_CSS)
        self.assertIn(".html-interaction-layer {", OVERLAY_CSS)
        self.assertIn("z-index: 50;", OVERLAY_CSS)
        self.assertIn(".player-hotspot {\n  z-index: 20;", OVERLAY_CSS)

    def test_player_keeps_overlay_dom_order_sorted_by_z_index(self) -> None:
        self.assertIn(
            ".sort((left, right) => left.z_index - right.z_index",
            PLAYER_JS,
        )
        self.assertIn("mediaItems = overlays(pageId).map", PLAYER_JS)


if __name__ == "__main__":
    unittest.main()
