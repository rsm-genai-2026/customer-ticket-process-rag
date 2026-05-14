"""Local web demo for the Naive vs HyQ retrieval comparison.

Run from the repo root:

    uv run python scripts/rag_demo.py
    # then open http://127.0.0.1:8766

The server is a thin shim over ``rag.retrieval``: it loads both indices
once at startup, then for each query it runs both modes side-by-side
and returns enough metadata for the UI to explain *why* the result
looks the way it does.

The HTML/CSS/JS for the page lives in ``scripts/rag_demo_ui.html`` and
is loaded at request time by :func:`render_index`. Same pattern as
``scripts/orchestrator.py``.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
from dataclasses import asdict, dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import markdown as md
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rag.preset_queries import PRESET_QUERIES, to_json_serialisable  # noqa: E402
from rag.retrieval import hyq_retrieve, load_index, naive_retrieve  # noqa: E402

INDEX_DIR = REPO_ROOT / "rag" / "index"
MAX_BODY_BYTES = 8_000


# ---------------------------------------------------------------------------
# Cached index state
# ---------------------------------------------------------------------------


@dataclass
class IndexBundle:
    naive: dict
    hyq: dict

    @classmethod
    def load(cls) -> "IndexBundle":
        return cls(
            naive=load_index(INDEX_DIR / "naive_index.json"),
            hyq=load_index(INDEX_DIR / "hyq_index.json"),
        )


def _snippet(text: str, max_chars: int = 220) -> str:
    """Collapse whitespace and trim a chunk's text for compact UI display."""
    flat = " ".join(text.split())
    if len(flat) <= max_chars:
        return flat
    return flat[: max_chars - 1].rstrip() + "…"


_MD = md.Markdown(extensions=["tables", "fenced_code", "sane_lists"], output_format="html5")


def _render_md(text: str) -> str:
    """Render a chunk's raw markdown to HTML for the demo UI.

    Chunks are trusted (authored by us), so we don't sanitise — but we
    reset the converter between calls so heading-id state doesn't leak.
    """
    _MD.reset()
    return _MD.convert(text)


def _serialise_hits(hits: list[dict]) -> list[dict]:
    out = []
    for h in hits:
        out.append(
            {
                "chunk_id": h["chunk_id"],
                "doc_id": h["doc_id"],
                "doc_title": h["doc_title"],
                "doc_type": h.get("doc_type", ""),
                "section_heading": h["section_heading"],
                "snippet": _snippet(h["text"]),
                "text": h["text"],
                "text_html": _render_md(h["text"]),
                "score": round(float(h["score"]), 3),
                "matched_question": h.get("matched_question"),
            }
        )
    return out


def run_retrieve(bundle: IndexBundle, query: str, *, top_k: int = 5, min_score: float = 0.0) -> dict:
    if not query.strip():
        raise ValueError("query must be non-empty")
    naive_hits = naive_retrieve(query, bundle.naive, top_k=top_k, min_score=min_score)
    hyq_hits = hyq_retrieve(query, bundle.hyq, top_k=top_k, min_score=min_score)

    # Coverage statistics — used by the diagnostic panel.
    naive_top_score = naive_hits[0]["score"] if naive_hits else 0.0
    hyq_top_score = hyq_hits[0]["score"] if hyq_hits else 0.0
    return {
        "query": query,
        "naive": {
            "hits": _serialise_hits(naive_hits),
            "top_score": round(float(naive_top_score), 3),
            "matrix_rows": int(bundle.naive["embeddings"].shape[0]),
        },
        "hyq": {
            "hits": _serialise_hits(hyq_hits),
            "top_score": round(float(hyq_top_score), 3),
            "matrix_rows": int(bundle.hyq["embeddings"].shape[0]),
        },
    }


# ---------------------------------------------------------------------------
# HTTP plumbing
# ---------------------------------------------------------------------------


def _html_response(handler: BaseHTTPRequestHandler, body: str, *, status: int = HTTPStatus.OK.value) -> None:
    payload = body.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)


def _json_response(handler: BaseHTTPRequestHandler, body: Any, *, status: int = HTTPStatus.OK.value) -> None:
    payload = json.dumps(body).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)


def _read_json(handler: BaseHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length", "0") or "0")
    if length <= 0 or length > MAX_BODY_BYTES:
        raise ValueError("missing or oversized request body")
    raw = handler.rfile.read(length)
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("request body must be a JSON object")
    return payload


def make_handler(bundle: IndexBundle) -> type[BaseHTTPRequestHandler]:
    class RagDemoHandler(BaseHTTPRequestHandler):
        server_version = "RagDemo/1.0"

        def log_message(self, fmt: str, *args: Any) -> None:
            print(f"{self.address_string()} - {fmt % args}")

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path in {"/", "/index.html"}:
                _html_response(self, render_index())
                return
            if path == "/api/presets":
                _json_response(self, {"presets": to_json_serialisable()})
                return
            if path == "/api/index-stats":
                _json_response(self, _index_stats(bundle))
                return
            if path == "/health":
                _json_response(self, {"status": "ok"})
                return
            if path == "/favicon.ico":
                self.send_response(HTTPStatus.NO_CONTENT.value)
                self.end_headers()
                return
            self.send_error(HTTPStatus.NOT_FOUND.value, "Not found")

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            try:
                payload = _read_json(self)
                if path == "/api/retrieve":
                    query = str(payload.get("query", "")).strip()
                    top_k = int(payload.get("top_k", 5))
                    min_score = float(payload.get("min_score", 0.0))
                    result = run_retrieve(bundle, query, top_k=top_k, min_score=min_score)
                    _json_response(self, result)
                    return
                self.send_error(HTTPStatus.NOT_FOUND.value, "Not found")
            except ValueError as exc:
                _json_response(self, {"error": str(exc)}, status=HTTPStatus.BAD_REQUEST.value)
            except Exception as exc:  # pragma: no cover - defensive
                _json_response(
                    self,
                    {"error": "Unexpected server error", "details": {"message": str(exc)}},
                    status=HTTPStatus.INTERNAL_SERVER_ERROR.value,
                )

    return RagDemoHandler


def _index_stats(bundle: IndexBundle) -> dict:
    return {
        "naive": {
            "mode": bundle.naive["mode"],
            "embed_model": bundle.naive.get("embed_model"),
            "chunks": len(bundle.naive["chunks"]),
            "matrix_shape": list(bundle.naive["embeddings"].shape),
        },
        "hyq": {
            "mode": bundle.hyq["mode"],
            "embed_model": bundle.hyq.get("embed_model"),
            "chunks": len(bundle.hyq["chunks"]),
            "matrix_shape": list(bundle.hyq["embeddings"].shape),
        },
    }


def render_index() -> str:
    """Render the UI page.

    Uses literal ``@@PRESET_OPTIONS@@`` / ``@@PRESETS_JSON@@`` markers in the
    HTML rather than ``string.Template`` so the JS in the page can use ``$``
    freely (for the ``$()`` shorthand and JS template literals).
    """
    template_path = Path(__file__).with_name("rag_demo_ui.html")
    body = template_path.read_text(encoding="utf-8")
    preset_options = "\n".join(
        f'<option value="{html.escape(p.id)}">{html.escape(p.label)}</option>' for p in PRESET_QUERIES
    )
    presets_json = json.dumps([asdict(p) for p in PRESET_QUERIES]).replace("</", "<\\/")
    body = body.replace("@@PRESET_OPTIONS@@", preset_options)
    body = body.replace("@@PRESETS_JSON@@", presets_json)
    return body


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local RAG retrieval demo (Naive vs HyQ).")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args(argv)

    load_dotenv(REPO_ROOT / ".env")
    if not os.environ.get("TRITONAI_API_KEY"):
        print("ERROR: TRITONAI_API_KEY is not set. Copy .env.example to .env.", file=sys.stderr)
        return 1

    print("Loading indices …")
    bundle = IndexBundle.load()
    print(f"  naive: {len(bundle.naive['chunks'])} chunks, matrix {bundle.naive['embeddings'].shape}")
    print(f"  hyq:   {len(bundle.hyq['chunks'])} chunks, matrix {bundle.hyq['embeddings'].shape}")

    handler = make_handler(bundle)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"RAG demo running at http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping RAG demo.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
