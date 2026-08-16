# TubeWiki architecture

This documents the Phase-1 implementation and the decisions behind it. The full product
rationale and phasing live in the spec (`tubewikispec.md`).

## Shape

```
Chrome extension ──POST /ingest──▶ Backend (FastAPI)
  (capture, logged-in                 │
   session, residential IP)           ├─▶ Transcript fallback chain (transcripts.py)
                                       ├─▶ Corpus tier: Qdrant + Ollama embeddings (corpus.py)
                                       ├─▶ Generation: concept + claims (llm.py, merge.py)
                                       ├─▶ Git-backed vault (vault.py)  ← canonical state
                                       └─▶ Ledger (ledger.py)          ← fast index / stickiness
                                       │
Browse UI (web/) ◀──GET /api/pages────┘
```

Three storage layers with distinct jobs (spec §3):

| Layer | Role | Phase-1 tech |
|---|---|---|
| **Corpus (RAG)** | Raw transcripts, chunked + embedded; retrieval substrate | Qdrant (embedded/local) + host-Ollama `qwen3-embedding` |
| **Canonical** | Curated wiki content; source of truth | Obsidian-style vault that is a **git repo** (`main`) |
| **Agent memory** | Ledger of decisions (state, stickiness) | JSON file now → Postgres in Phase 2 |

## Key decisions (from the grooming spikes)

### Corpus tier: LlamaIndex/Qdrant + Ollama, **not KAITO** (OQ#2)
KAITO `RAGEngine` was evaluated and rejected: its remote-embedding client is hardcoded to
the HuggingFace **TEI** wire format (`{"inputs": ...}` → bare list), so it cannot talk to
host Ollama's `/api/embed` without a translation shim, and it is a pre-1.0 CNCF-sandbox CRD
(v1alpha1 already deprecated). Since KAITO's engine is LlamaIndex under the hood anyway, we
talk to Qdrant + Ollama directly. The `Corpus` class isolates this — swapping in LlamaIndex
or a remote Qdrant is a drop-in behind `add_video`/`query`.

Embeddings use Ollama's **native `/api/embed`** (not `/v1`); the LLM uses the
**OpenAI-compatible `/v1`** path. `qwen3-embedding` is **2560-dim** on this box — set
explicitly in config (the endpoint would otherwise be mis-assumed as 3072).

### Transcript fallback (OQ#5)
Ordered branch (`transcripts.py`): extension (logged-in, residential IP) → backend
`youtube-transcript-api` **from the homelab's residential IP** (YouTube blocks datacenter
IPs from the caption endpoint) → local Whisper on the GPU box (`faster-whisper` large-v3
INT8; hook present, install `.[whisper]` on the host) → skip-and-log for backfill.

### Per-claim provenance is foundational (§5.1)
The atomic unit is the **Claim** — one assertion, attributed to one source. Pages are
markdown; each claim carries an Obsidian block-ref (`^c3`) and the frontmatter maps
`claim_sources: {c3: yt:ID}`. This is what makes eviction **surgical** (`remove_source`):
pull one source and exactly its claims disappear, the rest of the page intact. Built now
because retrofitting it later is expensive.

### Git-backed store (§5.2)
The vault is a git repo; every ingest is a commit (`ingest(<id>): +N claims → <concept>`).
Phase 1 auto-commits to `main`. The Phase-2 gate redirects writes onto branches (PRs carry
the rejection record) — `vault.commit_page` is the single seam that changes.

## Online vs offline
`clients.resolve_offline()` probes the box at startup. If unreachable (or
`TUBEWIKI_OFFLINE=1`), a deterministic hash embedder + template LLM stand in so the full
pipeline runs and is testable without the GPU. This is also the CI path.

## What Phase 1 deliberately does NOT do
Approval UI, Postgres ledger, rejected-claims collection, taxonomy/canonicalization,
capture controls, the full sidebar, research-deeper — all later phases (see spec §10 and
pad TASK-95..99). The seams for them exist (ledger states, branch-capable vault, per-claim
provenance) so they slot in without a rewrite.

## Layout
```
backend/tubewiki/   config, models, provenance, corpus, llm, transcripts,
                    ledger, vault, merge, ingest, api, clients
backend/web/        minimal browse UI
backend/tests/      end-to-end walking-skeleton test (offline)
extension/          MV3 capture extension
scripts/            bulk-seed helper (for the P1.11 value test)
```
