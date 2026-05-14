# RAG layer — Naive vs. HyQ on the Helios KB

A teaching scaffold for the MeridianLife simulation series. We build two
retrieval-augmented-generation indices over the same 18 simulated
proprietary documents in `knowledge_base/`, compare them side-by-side,
and watch the second one win on most queries.

No vector database. The corpus is small (≤ a few hundred chunks × 1024
dims ≈ a few MB), so the index lives on disk as JSON + `.npy` and in
memory as a single Python dict. Query time is a single numpy matmul
against the whole matrix — fast, transparent, and pedagogically honest.

## The two modes

### Naive RAG (baseline)

- Chunk every doc with a 300-whitespace-token sliding window, 50-token
  overlap.
- Embed each chunk verbatim with `api-tgpt-embeddings` (1024-dim).
- At query time, embed the user's question, take cosine similarity
  against every chunk, return top-k.

What students should notice when they look at the index:
- The natural section headings ("Identity verification", "Lessons for
  Tier-1") have been destroyed. Each chunk gets a label like
  `window 0`, `window 1`. The model has no idea what's in the chunk
  without reading it.
- A chunk can split mid-sentence.

### HyQ — Hypothetical Queries (the upgrade)

- Chunk each doc on `##` headings instead of by token count — every
  section is a self-contained answer to a small question set.
- For each section, ask an LLM (`api-llama-4-scout` by default) to
  generate **N hypothetical questions** the section answers.
- Embed the *questions*, not the section bodies. Each section ends up
  with N rows in the embedding matrix.
- At query time, embed the user's question, take cosine similarity
  against every hypothetical question, de-duplicate to the parent
  section, return top-k unique sections.

Why this tends to win on these docs:
1. The 18 KB docs were *written* as a sequence of self-contained
   sections — HyQ respects the author's boundaries instead of fighting
   them.
2. The user's actual queries look like questions; embeddings of
   questions live closer to other questions in vector space than to
   declarative section bodies.
3. The retrieved chunk carries its real heading ("Identity
   verification") and the matched question ("How do I verify identity
   before a reset?"), so downstream prompts and citations are
   self-explanatory.

## Quickstart

```bash
# 1. Make sure TRITONAI_API_KEY is in .env.
cp .env.example .env && $EDITOR .env

# 2. Build both indices. The HyQ build calls the LLM ~100 times once;
#    after that, retrieval is free.
uv run python -m rag.build_index --mode naive
uv run python -m rag.build_index --mode hyq

# 3. Run the side-by-side comparison.
uv run python rag/eval.py
```

A successful eval prints a table of 10 queries with the top-3 doc IDs
from each mode and a `Hit-rate@3` summary at the bottom. On the current
corpus and embedding model, HyQ typically scores 9–10/10 while Naive
scores 7–8/10.

## What's in this folder

```
rag/
├── knowledge_base/        # 18 KB-*.md docs + INDEX.md (the corpus)
├── index/                 # built artefacts (gitignored if you prefer)
│   ├── naive_index.json
│   ├── naive_embeddings.npy
│   ├── hyq_index.json
│   └── hyq_embeddings.npy
├── build_index.py         # CLI: builds naive or hyq index
├── retrieval.py           # load_index, naive_retrieve, hyq_retrieve
├── eval.py                # side-by-side comparison harness
└── README.md              # this file
```

## Index shape (in memory)

```python
index = {
    "schema_version": 1,
    "mode": "hyq",                  # or "naive"
    "embed_model": "api-tgpt-embeddings",
    "built_at": "2026-05-13T...",
    "chunks": [
        {
            "chunk_id": "KB-001#1",
            "doc_id": "KB-001",
            "doc_title": "Tier-1 Password Reset SOP",
            "doc_type": "sop",
            "section_heading": "Identity verification (required before any reset)",
            "text": "...",
            "hyq": ["How do I verify identity...", "..."],   # empty in naive mode
            "embedding_rows": [3, 4, 5, 6, 7],               # length 1 in naive mode
        },
        ...
    ],
    "embeddings": np.ndarray,       # shape (rows, 1024), L2-normalised
    "embeddings_file": "hyq_embeddings.npy",
}
```

Because the embeddings are pre-normalised, cosine similarity is a plain
`embeddings @ query_vec` — no division, no `cdist`, no FAISS.

## When HyQ does NOT win

The eval suite intentionally includes a query that HyQ also misses (the
"is there a scheduled change explaining portal slowness" case). The
generated questions describe the change-calendar entries, but the
user's query is about a *symptom*; the symptom side lives in the
runbooks, not the calendar. Useful teaching point: HyQ helps when the
generated questions overlap with how users actually ask, and not when
the doc was written from a different angle than the question.

## Graceful degradation

Both retrieve functions accept a `min_score` (default 0.30). When no
embedding clears the threshold they return `[]` and the caller is
expected to fall back — for example, ask the LLM without RAG context,
or hand the ticket off to a human. The downstream skill never has to
guess whether the retrieved chunks are noise.

## Testing

```bash
uv run pytest tests/rag -v
```

All tests run offline: `utils.embed.embed` accepts an injected client,
the HyQ generator looks at `HYQ_QUESTIONS_MOCK_JSON` before calling the
LLM, and the retrieval tests use synthetic embedding matrices.

## What's deliberately not in this milestone

- **Integration into the two LLM skills** (`check-faq-resolution`,
  `investigate-specialist-solution`). The next step is to add a
  `--rag-mode {off,naive,hyq}` flag to each skill so the LLM prompt
  optionally gets the retrieved chunks prepended.
- **MCP tool wrapping.** Once the Python API is stable, a
  `rag_retrieve` MCP tool is a thin shim over `hyq_retrieve`.
- **Other retrieval modes** (BM25, hybrid, reranker). The `naive` and
  `hyq` modes share a `_cosine_topk` helper that makes adding a third
  mode mostly an exercise in chunking and index assembly.
