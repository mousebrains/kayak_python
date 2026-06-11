"""Footer license-label rendering tests."""

from __future__ import annotations

from kayak.web.build import shell


def test_static_footer_uses_escaped_data_license_label(monkeypatch) -> None:
    monkeypatch.setattr(shell, "_data_license_label", lambda: "License <script>alert(1)</script>")

    html = shell._build_footer_html()

    assert "License &lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>alert(1)</script>" not in html
