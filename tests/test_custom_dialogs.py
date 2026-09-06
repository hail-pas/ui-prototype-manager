import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app/static"
EDITOR_JS = (STATIC / "editor.js").read_text(encoding="utf-8")
EDITOR_TEMPLATE = (ROOT / "app/templates/editor.html").read_text(encoding="utf-8")
NATIVE_DIALOG = re.compile(r"(?<![\w.])(?:alert|confirm|prompt)\s*\(")


def test_editor_uses_styled_dialogs_for_requested_prompts() -> None:
    assert "window.UIPMDialog = Object.freeze({alert: showMessage, confirm: showConfirm});" in EDITOR_JS
    assert "showConfirm('当前交互配置尚未保存，是否放弃修改？'" in EDITOR_JS
    assert "showMessage('请选择目标页面')" in EDITOR_JS


def test_frontend_does_not_use_native_browser_dialogs() -> None:
    violations = []
    for path in sorted(STATIC.glob("*.js")):
        source = path.read_text(encoding="utf-8")
        for match in NATIVE_DIALOG.finditer(source):
            line = source.count("\n", 0, match.start()) + 1
            violations.append(f"{path.relative_to(ROOT)}:{line}: {match.group(0)}")
    assert not violations, "Native browser dialogs found:\n" + "\n".join(violations)


def test_dialog_changes_have_fresh_editor_asset_versions() -> None:
    for asset in ("editor.js", "page-actions.js", "editor-media.js"):
        assert f"/static/{asset}?v=20260906-styled-dialogs" in EDITOR_TEMPLATE
