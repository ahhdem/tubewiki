# TubeWiki

A self-building wiki that distills open/watched YouTube tabs into topic-organized
**concept pages** with per-source attribution.

> **Videos are the source, not the subject.** Content from many videos merges into
> evolving concept pages; each page carries Wikipedia-style references back to the
> videos that contributed. This is explicitly *not* one wiki page per video.

See [`docs/architecture.md`](docs/architecture.md) for the design, and the spec
(`tubewikispec.md`) for the full rationale and phasing.

## Status — Phase 1 (walking skeleton)

Capture on tab open → transcript fetch → concept-page merging with references →
flat page list. **Auto-approve everything.** The git-backed store and per-claim
provenance are in the foundation (the approval UI is Phase 2).

Implemented so far:

- **Per-claim provenance** (`provenance.py`) — the load-bearing wall. Every claim on
  a page is attributed to one source video and can be surgically removed later.
- **Git-backed canonical store** (`vault.py`) — the wiki is an Obsidian-style vault
  that is a git repo; every ingest is a commit.
- **Corpus tier** (`corpus.py`) — Qdrant (local, no server needed) + embeddings.
- **Ingestion + merge** (`ingest.py`, `merge.py`) — transcript → chunk → embed →
  concept-page merge with references.
- **Transcript fetch** (`transcripts.py`) — `youtube-transcript-api` with a Whisper
  fallback hook.
- **Browse API + minimal UI** (`api.py`, `web/`).
- **Chrome extension** (`extension/`) — MV3, captures the logged-in session's
  transcript on a YouTube tab and posts it to the backend.

### Online vs offline

Everything runs against the homelab's **host Ollama** (`192.168.86.11:11434`) —
`qwen3-embedding` (2560-dim) for embeddings, an OpenAI-compatible chat model for
generation. When Ollama is unreachable (or `TUBEWIKI_OFFLINE=1`), the backend uses
a deterministic **stub embedder + template LLM** so the full pipeline still runs and
is testable without the GPU. Swap is behind the `Corpus`/`LLM` interfaces.

## Quick start

```bash
cd backend
python3 -m venv --without-pip .venv && .venv/bin/python get-pip.py   # runner has no ensurepip
.venv/bin/pip install -e .
# offline demo (no GPU needed):
TUBEWIKI_OFFLINE=1 .venv/bin/python -m tubewiki.api
# real: point at host Ollama (default endpoints are already the homelab's)
.venv/bin/python -m tubewiki.api
```

Then open http://localhost:8000/ and POST a video to `/ingest` (or load the
extension).

## Tests

```bash
cd backend && .venv/bin/pytest -q
```

The end-to-end test ingests two videos on the same topic, asserts they merge into a
single concept page with two sources, then evicts one source and asserts its claims
(and only its claims) are surgically removed — proving the provenance foundation.
