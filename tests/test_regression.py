"""Unit tests for the regression-content sanitizer (``kayak.web.regression``).

These are the security boundary for dataset-authored report content served to
anonymous users (S2): the Markdown→HTML allowlist, the strict SVG validator, and
the JSON-sidecar shape/size gate. The suite asserts the real published artifacts
pass and that each XSS / injection class is rejected or stripped.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kayak.web.regression import (
    UnsafeContentError,
    render_markdown_safe,
    render_markdown_to_html,
    validate_json_sidecar,
    validate_svg,
)

# Representative regression artifacts. The real published reports live in the
# kayak_data dataset (DATASET_DIR/regression/, not in this repo since S2-E3); the
# committed test fixture mirrors their shape, so these run deterministically in CI
# (a dataset clone is absent here). The real 25 reports are sanitizer-gated by
# kayak_data's own `validate` CI (validate-dataset → _check_regression).
_REG = Path(__file__).resolve().parent / "fixtures" / "dataset" / "regression"
_REAL_SVGS = sorted(_REG.glob("*.svg"))
_REAL_MDS = sorted(p for p in _REG.glob("*.md") if p.stem.lower() != "readme")
_REAL_JSONS = sorted(_REG.glob("*.json"))


def _svg(body: str) -> str:
    return f'<svg xmlns="http://www.w3.org/2000/svg">{body}</svg>'


# --------------------------------------------------------------------------- #
# Representative (fixture) artifacts must pass the sanitizer/validator
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(not _REAL_SVGS, reason="no committed regression SVGs")
@pytest.mark.parametrize("path", _REAL_SVGS, ids=lambda p: p.name)
def test_real_svg_validates_and_reserializes(path: Path) -> None:
    out = validate_svg(path.read_text(encoding="utf-8"))
    # Re-serialized output parses again as an <svg> and is not byte-identical input.
    again = validate_svg(out)  # idempotent: a re-serialized SVG re-validates
    assert again.startswith("<svg")


@pytest.mark.skipif(not _REAL_MDS, reason="no committed regression reports")
@pytest.mark.parametrize("path", _REAL_MDS, ids=lambda p: p.name)
def test_real_md_renders_without_active_content(path: Path) -> None:
    html = render_markdown_safe(path.read_text(encoding="utf-8")).lower()
    assert "<script" not in html
    assert "onerror" not in html and "onload" not in html
    assert "javascript:" not in html


@pytest.mark.skipif(not _REAL_JSONS, reason="no committed regression sidecars")
@pytest.mark.parametrize("path", _REAL_JSONS, ids=lambda p: p.name)
def test_real_json_validates(path: Path) -> None:
    validate_json_sidecar(path.read_text(encoding="utf-8"))  # must not raise


# --------------------------------------------------------------------------- #
# Markdown sanitization
# --------------------------------------------------------------------------- #


def test_markdown_strips_script_and_handlers() -> None:
    html = render_markdown_safe("# T\n\n<script>alert(1)</script>\n\n<b onclick='x()'>hi</b>\n")
    low = html.lower()
    assert "<script" not in low
    assert "onclick" not in low
    assert "hi" in low  # text content survives


def test_markdown_strips_unsafe_url_schemes() -> None:
    html = render_markdown_safe("[x](javascript:alert(1)) and [y](data:text/html,evil)\n")
    assert "javascript:" not in html.lower()
    assert "data:text/html" not in html.lower()


def test_markdown_keeps_local_image_drops_external() -> None:
    html = render_markdown_safe(
        "![local](./fixture.svg)\n\n![remote](https://evil.example/track.png)\n"
    )
    assert "./fixture.svg" in html  # local sibling image kept
    assert "evil.example" not in html  # external (tracking) image src dropped


def test_markdown_rewrites_md_links_to_html() -> None:
    html = render_markdown_safe("[companion](./other_leadlag.md)\n")
    assert "./other_leadlag.html" in html
    assert "./other_leadlag.md" not in html


def test_render_markdown_to_html_is_generic_no_regression_filtering() -> None:
    # The prose renderer (S3c) sanitizes but does NOT apply the regression-only
    # filtering: it keeps a "## Future" section (dropped by render_markdown_safe)
    # and leaves a ./x.md link untouched (render_markdown_safe rewrites it to .html).
    # The link sits OUTSIDE the Future section so render_markdown_safe still emits
    # it (rewritten) — render_markdown_safe drops the whole Future section.
    md = "# Doc\n\n[c](./x.md)\n\n## Future\n\nplanned stuff\n"
    generic = render_markdown_to_html(md)
    assert "planned stuff" in generic and "./x.md" in generic
    safe = render_markdown_safe(md)
    assert "planned stuff" not in safe and "./x.html" in safe
    # Both still sanitize active content.
    assert "<script" not in render_markdown_to_html("<script>alert(1)</script>\n\n# T")


def test_markdown_keeps_tables() -> None:
    html = render_markdown_safe("| a | b |\n|---|---|\n| 1 | 2 |\n")
    assert "<table>" in html and "<td>" in html


def test_markdown_drops_maintainer_sections() -> None:
    md = (
        "# Report\n\nBody text.\n\n"
        "## `calc_expression` row\n\n```\nprovenance_slug: x\n```\n\n"
        "## Future\n\nTODO maintainer notes.\n"
    )
    html = render_markdown_safe(md)
    assert "Body text" in html
    assert "provenance_slug" not in html
    assert "maintainer notes" not in html


# --------------------------------------------------------------------------- #
# SVG validation — rejections
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "body",
    [
        "<script>alert(1)</script>",
        "<foreignObject><b>x</b></foreignObject>",
        "<image href='http://evil/x.png'/>",
        "<use href='#x'/>",
        '<rect style="fill:url(javascript:1)"/>',
        '<rect fill="url(http://evil#a)"/>',
        '<rect fill="URL(http://evil#a)"/>',  # case-insensitive bypass of the url() check
        '<rect stroke="uRl(http://evil)"/>',
        '<rect fill="image(http://evil)"/>',  # image() is also resource-fetching
        '<rect fill="url(data:image/png;base64,AAA)"/>',
    ],
    ids=[
        "script",
        "foreignObject",
        "image",
        "use",
        "style",
        "url-http",
        "url-http-upper",
        "url-mixed",
        "image-fn",
        "url-data",
    ],
)
def test_svg_rejects_active_content(body: str) -> None:
    with pytest.raises(UnsafeContentError):
        validate_svg(_svg(body))


def test_svg_rejects_css_escape_in_attr() -> None:
    # `\75rl(` is the CSS escape for `url(` — reject backslash escapes that could
    # obfuscate a resource function past the substring check.
    with pytest.raises(UnsafeContentError):
        validate_svg(
            r'<svg xmlns="http://www.w3.org/2000/svg"><rect fill="\75rl(http://evil)"/></svg>'
        )


@pytest.mark.parametrize(
    "payload",
    [
        '<svg xmlns="http://www.w3.org/2000/svg"><?xml-stylesheet href="javascript:1"?><rect/></svg>',
        '<svg xmlns="http://www.w3.org/2000/svg"><!-- comment --><rect/></svg>',
        # A LEADING xml-stylesheet PI must not be mistaken for the XML declaration
        # (its target is `xml-stylesheet`, not `xml` + whitespace).
        '<?xml-stylesheet type="text/xsl" href="javascript:1"?>'
        '<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>',
    ],
    ids=["in-body-pi", "comment", "leading-stylesheet-pi"],
)
def test_svg_rejects_pi_and_comment(payload: str) -> None:
    # Explicit rejection so the "reject nonconforming" contract doesn't rest on
    # ElementTree silently dropping PIs/comments.
    with pytest.raises(UnsafeContentError):
        validate_svg(payload)


def test_svg_accepts_leading_xml_declaration() -> None:
    # A genuine `<?xml …?>` declaration (target `xml` + whitespace) is allowed.
    out = validate_svg(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<svg xmlns="http://www.w3.org/2000/svg"><rect x="1" y="2"/></svg>'
    )
    assert out.startswith('<svg xmlns="http://www.w3.org/2000/svg"')


def test_svg_rejects_foreign_namespace_element() -> None:
    # A foreign-namespace element with an allowlisted localname (parser-differential).
    with pytest.raises(UnsafeContentError):
        validate_svg(
            '<svg xmlns="http://www.w3.org/2000/svg">'
            '<x:rect xmlns:x="http://www.w3.org/1999/xhtml"/></svg>'
        )


def test_svg_rejects_event_handler() -> None:
    with pytest.raises(UnsafeContentError):
        validate_svg('<svg xmlns="http://www.w3.org/2000/svg" onload="x()"><rect/></svg>')


def test_svg_rejects_xlink_href() -> None:
    with pytest.raises(UnsafeContentError):
        validate_svg(
            '<svg xmlns="http://www.w3.org/2000/svg" '
            'xmlns:xlink="http://www.w3.org/1999/xlink">'
            '<a xlink:href="http://evil"><rect/></a></svg>'
        )


def test_svg_rejects_dtd_entity() -> None:
    payload = (
        '<?xml version="1.0"?>'
        '<!DOCTYPE svg [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
        '<svg xmlns="http://www.w3.org/2000/svg"><text>&xxe;</text></svg>'
    )
    with pytest.raises(UnsafeContentError):
        validate_svg(payload)


def test_svg_rejects_oversize() -> None:
    big = _svg("<rect/>" + "<!-- " + "A" * (2 * 1024 * 1024) + " -->")
    with pytest.raises(UnsafeContentError):
        validate_svg(big)


def test_svg_rejects_non_svg_root() -> None:
    with pytest.raises(UnsafeContentError):
        validate_svg('<html xmlns="http://www.w3.org/2000/svg"><body/></html>')


# --------------------------------------------------------------------------- #
# SVG validation — acceptances
# --------------------------------------------------------------------------- #


def test_svg_accepts_local_url_fragment_and_clippath() -> None:
    out = validate_svg(
        '<svg xmlns="http://www.w3.org/2000/svg">'
        '<defs><clipPath id="c"><rect width="10" height="10"/></clipPath></defs>'
        '<rect clip-path="url(#c)" fill="#06c"/></svg>'
    )
    assert "clipPath" in out and "url(#c)" in out


def test_svg_reserializes_with_clean_namespace() -> None:
    out = validate_svg(_svg('<rect x="1" y="2" fill="#fff"/>'))
    assert out.startswith('<svg xmlns="http://www.w3.org/2000/svg"')
    assert "ns0:" not in out  # default namespace, not a prefixed serialization


# --------------------------------------------------------------------------- #
# JSON sidecar validation
# --------------------------------------------------------------------------- #


def test_json_accepts_pair_linear_and_leadlag_shapes() -> None:
    validate_json_sidecar('{"slug":"a","coefs":[{"name":"x","value":1.0}],"r2":0.9}')
    validate_json_sidecar('{"slug":"a_leadlag","lags":[{"site":"1","corr":0.5}]}')


@pytest.mark.parametrize(
    "text",
    [
        '{"slug":"a","v":NaN}',
        '{"slug":"a","v":Infinity}',
        '{"target":"x"}',  # no slug
        '{"slug":""}',  # empty slug
        "[1,2,3]",  # not an object
        '{"slug":"a","coefs":{"not":"a list"}}',
        "not json at all",
    ],
    ids=["nan", "inf", "no-slug", "empty-slug", "not-object", "coefs-not-list", "garbage"],
)
def test_json_rejects_bad(text: str) -> None:
    with pytest.raises(UnsafeContentError):
        validate_json_sidecar(text)


def test_json_rejects_oversize() -> None:
    with pytest.raises(UnsafeContentError):
        validate_json_sidecar('{"slug":"a","pad":"' + "A" * (64 * 1024) + '"}')
