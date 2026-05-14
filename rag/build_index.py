"""Build a RAG index over ``rag/knowledge_base/``.

Two modes:

- ``naive`` — sliding-window chunking (300 whitespace-tokens, 50 overlap),
  embed each chunk verbatim.
- ``hyq``   — chunk on ``##`` headings, ask an LLM to generate N
  *hypothetical questions* per chunk, then embed the questions.

Each mode writes two artefacts under ``--out-dir`` (default ``rag/index/``):

- ``<mode>_index.json``      — chunk metadata + embedding-row map
- ``<mode>_embeddings.npy``  — the L2-normalised matrix

Usage
-----

    uv run python -m rag.build_index --mode naive
    uv run python -m rag.build_index --mode hyq --questions-per-chunk 5

Tests mock the HyQ generator via the ``HYQ_QUESTIONS_MOCK_JSON`` env var
(same convention as ``FAQ_RESOLUTION_MOCK_JSON`` in the FAQ skill).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import numpy as np
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from utils.connect import ask_json
from utils.embed import DEFAULT_EMBED_MODEL, embed

_REPO_ROOT = Path(__file__).resolve().parent.parent
_KB_DIR = _REPO_ROOT / "rag" / "knowledge_base"
_INDEX_DIR = _REPO_ROOT / "rag" / "index"
DEFAULT_HYQ_MODEL = "api-llama-4-scout"


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split a markdown doc into ``(frontmatter_dict, body)``.

    Hand-rolled to avoid adding a YAML dependency. Supports only the simple
    ``key: value`` lines used by the KB docs in this repo — no nested
    structures, no lists, no multiline scalars.
    """
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    fm_raw = match.group(1)
    body = text[match.end() :]
    meta: dict[str, str] = {}
    for line in fm_raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
    return meta, body


# ---------------------------------------------------------------------------
# Naive chunker (sliding token window)
# ---------------------------------------------------------------------------


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    doc_title: str
    doc_type: str
    section_heading: str
    text: str
    hyq: list[str] = field(default_factory=list)
    embedding_rows: list[int] = field(default_factory=list)


def naive_chunks(doc_id: str, meta: dict, body: str, *, window: int = 300, overlap: int = 50) -> list[Chunk]:
    """Sliding window of ``window`` whitespace-tokens with ``overlap``."""
    tokens = body.split()
    if not tokens:
        return []
    step = max(window - overlap, 1)
    out: list[Chunk] = []
    for i, start in enumerate(range(0, len(tokens), step)):
        piece = tokens[start : start + window]
        if not piece:
            break
        out.append(
            Chunk(
                chunk_id=f"{doc_id}#n{i}",
                doc_id=doc_id,
                doc_title=meta.get("title", doc_id),
                doc_type=meta.get("type", "unknown"),
                section_heading=f"window {i}",
                text=" ".join(piece),
            )
        )
        if start + window >= len(tokens):
            break
    return out


# ---------------------------------------------------------------------------
# HyQ chunker (split on ## headings)
# ---------------------------------------------------------------------------

_H2_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


def section_chunks(doc_id: str, meta: dict, body: str) -> list[Chunk]:
    """Split ``body`` on ``##`` headings. Content before the first ``##`` is
    emitted as a leading chunk under heading ``"<intro>"``."""
    matches = list(_H2_RE.finditer(body))
    if not matches:
        return [
            Chunk(
                chunk_id=f"{doc_id}#0",
                doc_id=doc_id,
                doc_title=meta.get("title", doc_id),
                doc_type=meta.get("type", "unknown"),
                section_heading="<whole>",
                text=body.strip(),
            )
        ]
    chunks: list[Chunk] = []
    if matches[0].start() > 0:
        intro = body[: matches[0].start()].strip()
        if intro:
            chunks.append(
                Chunk(
                    chunk_id=f"{doc_id}#0",
                    doc_id=doc_id,
                    doc_title=meta.get("title", doc_id),
                    doc_type=meta.get("type", "unknown"),
                    section_heading="<intro>",
                    text=intro,
                )
            )
    for idx, m in enumerate(matches, start=1):
        heading = m.group(1).strip()
        start = m.end()
        end = matches[idx].start() if idx < len(matches) else len(body)
        section_body = body[start:end].strip()
        full = f"## {heading}\n\n{section_body}".strip()
        chunks.append(
            Chunk(
                chunk_id=f"{doc_id}#{idx}",
                doc_id=doc_id,
                doc_title=meta.get("title", doc_id),
                doc_type=meta.get("type", "unknown"),
                section_heading=heading,
                text=full,
            )
        )
    return chunks


# ---------------------------------------------------------------------------
# HyQ question generator (LLM)
# ---------------------------------------------------------------------------


class _HyQResult(BaseModel):
    questions: list[str] = Field(min_length=1, max_length=12)


_HYQ_SYSTEM = (
    "You generate hypothetical user questions that a given internal knowledge-"
    "base section answers. Return ONLY valid JSON with a single key "
    "'questions' whose value is a list of plain-text questions. Each question "
    "should be a complete sentence ending in '?', phrased the way an employee "
    "or customer would naturally ask. Avoid restating the section heading."
)


def _hyq_prompt(chunk: Chunk, n: int) -> str:
    return (
        f"Section heading: {chunk.section_heading}\n"
        f"Document: {chunk.doc_title} ({chunk.doc_id})\n\n"
        f"--- SECTION CONTENT ---\n{chunk.text}\n--- END ---\n\n"
        f"Write exactly {n} distinct hypothetical questions this section answers."
    )


def generate_hyq(chunk: Chunk, n: int, *, model: str = DEFAULT_HYQ_MODEL) -> list[str]:
    """Return ``n`` hypothetical questions for ``chunk``.

    Mocked at test time via the ``HYQ_QUESTIONS_MOCK_JSON`` env var (which
    must contain a JSON object ``{"questions": [...]}`` — same questions
    are returned for every chunk).
    """
    mock = os.environ.get("HYQ_QUESTIONS_MOCK_JSON", "").strip()
    if mock:
        payload = json.loads(mock)
        return list(payload["questions"])[:n]

    result = ask_json(
        _hyq_prompt(chunk, n),
        schema=_HyQResult,
        model=model,
        system=_HYQ_SYSTEM,
        temperature=0.2,
    )
    if isinstance(result, _HyQResult):
        return result.questions[:n]
    # ask_json returned a plain dict (no pydantic round-trip)
    return list(result.get("questions", []))[:n]


# ---------------------------------------------------------------------------
# Index assembly
# ---------------------------------------------------------------------------


def load_kb(kb_dir: Path = _KB_DIR) -> list[tuple[str, dict, str]]:
    """Return ``[(doc_id, frontmatter, body)]`` for every ``KB-*.md`` file."""
    out = []
    for path in sorted(kb_dir.glob("KB-*.md")):
        text = path.read_text(encoding="utf-8")
        meta, body = parse_frontmatter(text)
        doc_id = meta.get("doc_id") or path.stem
        out.append((doc_id, meta, body))
    return out


def build_naive_index(
    kb: list[tuple[str, dict, str]],
    *,
    embed_model: str = DEFAULT_EMBED_MODEL,
    window: int = 300,
    overlap: int = 50,
    embedder=embed,
) -> tuple[dict, np.ndarray]:
    chunks: list[Chunk] = []
    for doc_id, meta, body in kb:
        chunks.extend(naive_chunks(doc_id, meta, body, window=window, overlap=overlap))
    texts = [c.text for c in chunks]
    matrix = embedder(texts, model=embed_model) if texts else np.zeros((0, 1), dtype=np.float32)
    for i, c in enumerate(chunks):
        c.embedding_rows = [i]
    return _assemble("naive", embed_model, chunks), matrix


def build_hyq_index(
    kb: list[tuple[str, dict, str]],
    *,
    embed_model: str = DEFAULT_EMBED_MODEL,
    hyq_model: str = DEFAULT_HYQ_MODEL,
    questions_per_chunk: int = 5,
    embedder=embed,
    generator=generate_hyq,
) -> tuple[dict, np.ndarray]:
    chunks: list[Chunk] = []
    for doc_id, meta, body in kb:
        chunks.extend(section_chunks(doc_id, meta, body))
    flat_questions: list[str] = []
    for c in chunks:
        questions = generator(c, questions_per_chunk, model=hyq_model)
        if not questions:
            continue
        c.hyq = questions
        c.embedding_rows = list(range(len(flat_questions), len(flat_questions) + len(questions)))
        flat_questions.extend(questions)
    matrix = embedder(flat_questions, model=embed_model) if flat_questions else np.zeros((0, 1), dtype=np.float32)
    return _assemble("hyq", embed_model, chunks, hyq_model=hyq_model), matrix


def _assemble(mode: str, embed_model: str, chunks: Iterable[Chunk], **extra) -> dict:
    return {
        "schema_version": 1,
        "mode": mode,
        "embed_model": embed_model,
        "built_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "chunks": [chunk_to_dict(c) for c in chunks],
        **extra,
    }


def chunk_to_dict(c: Chunk) -> dict:
    return {
        "chunk_id": c.chunk_id,
        "doc_id": c.doc_id,
        "doc_title": c.doc_title,
        "doc_type": c.doc_type,
        "section_heading": c.section_heading,
        "text": c.text,
        "hyq": c.hyq,
        "embedding_rows": c.embedding_rows,
    }


def write_index(index: dict, matrix: np.ndarray, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{index['mode']}_index.json"
    npy_path = out_dir / f"{index['mode']}_embeddings.npy"
    index_with_ref = {**index, "embeddings_file": npy_path.name}
    json_path.write_text(json.dumps(index_with_ref, indent=2), encoding="utf-8")
    np.save(npy_path, matrix)
    return json_path, npy_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Build a RAG index over rag/knowledge_base/.")
    p.add_argument("--mode", choices=("naive", "hyq"), required=True)
    p.add_argument("--out-dir", default=str(_INDEX_DIR))
    p.add_argument("--kb-dir", default=str(_KB_DIR))
    p.add_argument("--embed-model", default=DEFAULT_EMBED_MODEL)
    p.add_argument(
        "--hyq-model", default=DEFAULT_HYQ_MODEL, help="LLM that generates hypothetical questions (HyQ mode only)"
    )
    p.add_argument("--questions-per-chunk", type=int, default=5)
    p.add_argument("--window", type=int, default=300, help="Naive chunker token window")
    p.add_argument("--overlap", type=int, default=50, help="Naive chunker token overlap")
    return p


def main(argv: list[str] | None = None) -> int:
    load_dotenv(_REPO_ROOT / ".env")
    args = _build_parser().parse_args(argv)
    kb = load_kb(Path(args.kb_dir))
    if not kb:
        print(f"No KB-*.md files found under {args.kb_dir}", file=sys.stderr)
        return 1

    print(f"Loaded {len(kb)} documents from {args.kb_dir}")
    if args.mode == "naive":
        index, matrix = build_naive_index(
            kb,
            embed_model=args.embed_model,
            window=args.window,
            overlap=args.overlap,
        )
    else:
        index, matrix = build_hyq_index(
            kb,
            embed_model=args.embed_model,
            hyq_model=args.hyq_model,
            questions_per_chunk=args.questions_per_chunk,
        )

    json_path, npy_path = write_index(index, matrix, Path(args.out_dir))
    print(f"Wrote {json_path} ({len(index['chunks'])} chunks)")
    print(f"Wrote {npy_path} (matrix shape {matrix.shape})")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
