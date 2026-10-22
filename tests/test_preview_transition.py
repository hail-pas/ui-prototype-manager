from __future__ import annotations

import unittest

import app.main as main


class PreviewTransitionTests(unittest.TestCase):
    def test_page_switch_stays_opaque_until_old_view_is_cached(self) -> None:
        player_script = (main.APP_DIR / "static" / "player.js").read_text()
        media_css = (main.APP_DIR / "static" / "media-pages.css").read_text()
        player_template = (main.APP_DIR / "templates" / "player.html").read_text()

        self.assertIn("PAGE_LOADING_DELAY_MS = 250", player_script)
        self.assertIn("outgoing.element.classList.add('is-leaving')", player_script)
        self.assertIn("outgoing.element.classList.add('is-faded')", player_script)
        self.assertIn(".player-page-view.is-leaving{transition:none}", media_css)
        self.assertIn(".player-page-view.is-leaving.is-faded{opacity:1}", media_css)
        self.assertIn("media-pages.css?v=20260906-no-fade-v7", player_template)


if __name__ == "__main__":
    unittest.main()
