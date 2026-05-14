# RAG layer — Naive vs. HyQ on the Helios KB

We build two retrieval-augmented-generation indices over the same 18 simulated
proprietary documents in `knowledge_base/`, compare them side-by-side,
and watch the second one win on most queries.

No vector database. The corpus is small (≤ a few hundred chunks × 1024
dims ≈ a few MB), so the index lives on disk as JSON + `.npy` and in
memory as a single Python dict. Query time is one matrix multiplication
against the whole matrix — fast, transparent, and pedagogically honest.

## Concepts you'll see throughout

- **Embedding** — a fixed-length vector of numbers (1024 floats here)
  produced from a piece of text by an embedding model. Two texts with
  similar meaning produce vectors that point in similar directions.
- **Cosine similarity** — a number from -1 to 1 measuring how aligned
  two vectors are in direction (ignoring their lengths). 1.0 means
  identical direction, 0 means unrelated, -1 means opposite. We rank
  retrieved chunks by cosine similarity to the user's query: more
  aligned = more relevant.
- **L2-normalised** — scaled so the vector has length 1. We pre-normalise
  every embedding when we build the index. The reason is mathematical
  convenience: for unit-length vectors, cosine similarity is just the
  dot product (multiply-and-sum). One `matrix @ query` gives every
  chunk's score at once — no division, no extra steps.
- **Dense vs sparse retrieval** — *dense* means each text is represented
  by a smallish vector of floats (1024 in our case) where almost every
  position has a non-zero value; what we do here. *Sparse* means
  representing text as a much larger but mostly-zero vector that tracks
  individual words (e.g. BM25). Dense captures meaning, sparse captures
  literal vocabulary; the two have different failure modes.

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
`embeddings @ query_vec` — no division, no helper functions like scipy's
`cdist`, no specialised vector-search libraries like FAISS.

## When HyQ does NOT win

The eval suite includes one query both modes get wrong:

> "is there a system change scheduled that could explain the current portal slowness"

The intended hit is **KB-015 (the Q2 2026 change calendar)**. Both modes
return the Customer Portal and Analytics Dashboard runbooks instead. KB-015
doesn't even crack the HyQ top-5. Why?

The user query has two parts: a **cause** part ("system change scheduled")
and a **symptom** part ("portal slowness"). Dense vector embeddings give more
weight to the symptom part because words like *slow*, *down*, *portal* are
common and specific, so they push the query vector strongly in that direction.
The runbooks have multiple questions containing exactly those words:

| | Question | Score |
| --- | --- | --- |
| 1 | KB-010 "What can I do about the **dashboard being slow** between 03:00–04:30 UTC?" | 0.783 |
| 2 | KB-010 "Why is the **dashboard slow** at a specific time every night?" | 0.781 |
| 3 | KB-009 "Why is the **portal down** every Sunday?" | 0.725 |
| … | (more runbook hits in the 0.66–0.70 range) | |
| 6 | KB-015 best hit (the failover-drill question above) | 0.661 |

KB-015's best match is genuinely close, just not close enough to win. The
cause part of the query ("scheduled change") is harder for embeddings to
anchor on — it's a phrase about state of the world rather than a concrete
symptom, and there's no single word in it as semantically loaded as *slow*.

**The teaching point.** Embedding-based retrieval ranks by overall semantic
similarity in vector space. When a query has two clauses of unequal
"vividness," the more vivid clause dominates the result. That's a structural
limitation of dense retrieval, not a flaw of HyQ specifically.

**Two practical fixes** worth showing students if you want to extend the
lesson:

1. **Hybrid search.** Combine the dense-vector cosine score with a
   keyword-based score. BM25 is the standard keyword scorer — it ranks
   documents by how often the literal query words appear, adjusted for
   how rare each word is across the corpus. The word *scheduled* appears
   in KB-015's questions and almost nowhere else in the corpus, so BM25
   would lift KB-015 strongly. Hybrid ranking is just a weighted sum of
   the two scores.
2. **Query rewriting.** Before retrieval, ask the LLM to split a multi-part
   query into single-clause queries ("scheduled changes affecting the portal"
   and "portal slowness symptoms"), run each, then merge results. The
   per-clause retrievals each have a chance to win on their own clause.

## Patching retrieval by adding questions

The change-calendar miss above also points at HyQ's most operationally
useful property: **a specific miss can be patched by adding a question —
no re-chunking, no doc edits.** One embedding API call, one row appended
to the matrix.

Three things worth knowing about how this changes the design pattern:

1. **Echo the user's vocabulary.** When patching a miss, repeat the user's
   actual words rather than paraphrasing. Adding *"Could portal slowness
   or downtime right now be caused by a scheduled change?"* to KB-015
   scores **0.874** against the failing query — comfortably above the
   runbook's 0.783. A semantically equivalent *"Is there a planned outage
   that explains current degraded performance?"* only scores **0.781**.
   Same meaning, different words; you're writing magnets for embedding
   similarity, not prose.

2. **One question can live on multiple chunks.** Adding the winning
   question to BOTH KB-015 (calendar) AND KB-009 (portal runbook) makes
   both surface at the top of the result, each matched on the new
   question with the same score. That's the right answer here — the user
   benefits from both *"is anything scheduled?"* (calendar) and *"if not,
   what's the diagnostic procedure?"* (runbook). Chunks are destinations;
   questions are the address book.

3. **Real customer Q&A beats LLM-generated questions.** Whenever you have
   historical tickets that cite which doc resolved each one, you have real
   (question, doc) pairs — with the customer's actual vocabulary and the
   actual query distribution. LLM-generated questions cover docs uniformly;
   real customers ask the same five questions over and over and ignore
   80% of any doc. The dependency is that each historical ticket must tag
   which doc resolved it. The ticket workflow in this repo writes exactly
   that trail to `data/working/` as it runs, so the same trail could seed
   a future, richer HyQ index.

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

Tests call the real TritonAI gateway — embeddings and HyQ generation are
not mocked. The only purely-local tests are for self-contained helpers
(frontmatter parser, chunkers, the JSON+npy round-trip). Pure-LLM
behaviour is tested with looseness-aware asserts (length, structure,
threshold-based ranking) that tolerate model non-determinism without
silently passing on a broken pipeline. Requires `TRITONAI_API_KEY` in
`.env`.

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
