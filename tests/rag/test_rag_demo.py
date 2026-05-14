"""Tests for the rag_demo helpers — no LLM, no network."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.rag_demo import _render_md, _snippet  # noqa: E402


def test_snippet_short_passthrough():
    assert _snippet("hello world") == "hello world"


def test_snippet_collapses_whitespace_and_truncates():
    long = "word " * 200
    out = _snippet(long, max_chars=20)
    assert len(out) <= 20
    assert out.endswith("…")
    assert "  " not in out


def test_render_md_promotes_headings():
    html = _render_md("## Identity verification\n\nSome body text.")
    assert "<h2" in html
    assert "Identity verification" in html
    assert "<p>Some body text.</p>" in html


def test_render_md_handles_lists_and_inline_formatting():
    html = _render_md("- **bold item** with `code`\n- *italic item*\n  - nested\n")
    assert "<ul>" in html
    assert "<strong>bold item</strong>" in html
    assert "<code>code</code>" in html
    assert "<em>italic item</em>" in html


def test_render_md_handles_fenced_code():
    src = "Here is a snippet:\n\n```python\nx = 1\n```\n"
    html = _render_md(src)
    assert "<pre>" in html
    assert "<code" in html
    assert "x = 1" in html


def test_render_md_handles_tables():
    src = "| col a | col b |\n| --- | --- |\n| 1 | 2 |\n"
    html = _render_md(src)
    assert "<table" in html
    assert "<th>col a</th>" in html
    assert "<td>1</td>" in html


def test_render_md_resets_between_calls():
    """Heading-id state must not leak between consecutive renders."""
    a = _render_md("## A heading")
    b = _render_md("## A heading")
    assert a == b
