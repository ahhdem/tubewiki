"""HTTP API + minimal browse UI (spec §3, P1.6/P1.10).

Endpoints:
  POST /ingest            — the extension (or a script) posts a video here
  GET  /api/pages         — flat page list (Phase 1 has no taxonomy yet)
  GET  /api/pages/{slug}  — one concept page (markdown + structured claims/sources)
  GET  /api/search?q=     — term search across pages
  GET  /healthz           — mode + counts
  GET  /                  — the browse UI
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .clients import resolve_offline
from .config import settings
from .corpus import Corpus, make_embedder
from .ingest import Pipeline
from .ledger import Ledger
from .llm import make_llm
from .models import IngestRequest, IngestResult
from .provenance import render_page
from .vault import VaultStore

log = logging.getLogger("tubewiki.api")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def build_app() -> FastAPI:
    settings.ensure_dirs()
    offline = resolve_offline()
    log.info("TubeWiki starting (offline=%s, ollama=%s)", offline, settings.ollama_base_url)

    corpus = Corpus(make_embedder(offline))
    llm = make_llm(offline)
    vault = VaultStore(settings.vault_dir)
    ledger = Ledger(settings.ledger_path)
    pipeline = Pipeline(corpus, llm, vault, ledger)

    app = FastAPI(title="TubeWiki", version="0.1.0")
    app.state.offline = offline
    # Dev-open CORS. The extension posts via its background worker (host_permissions),
    # so this is mainly for browsing/other clients; tighten before any public exposure.
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
    )

    @app.get("/healthz")
    def healthz() -> dict:
        return {"offline": offline, "ollama": settings.ollama_base_url,
                "pages": len(vault.list_pages())}

    @app.post("/ingest", response_model=IngestResult)
    def ingest(req: IngestRequest) -> IngestResult:
        return pipeline.ingest(req)

    @app.get("/api/pages")
    def list_pages() -> list[dict]:
        return [
            {"title": p.title, "slug": p.slug, "sources": len(p.sources),
             "claims": len(p.claims), "updated": p.updated}
            for p in sorted(vault.list_pages(), key=lambda x: x.updated, reverse=True)
        ]

    @app.get("/api/pages/{slug}")
    def get_page(slug: str) -> dict:
        page = vault.read_page(slug)
        if not page:
            raise HTTPException(404, "no such page")
        return {
            "title": page.title, "slug": page.slug, "summary": page.summary,
            "updated": page.updated,
            "claims": [{"id": c.id, "text": c.text, "source_id": c.source_id} for c in page.claims],
            "sources": {sid: s.model_dump() for sid, s in page.sources.items()},
            "markdown": render_page(page),
        }

    @app.get("/api/search")
    def search(q: str) -> list[dict]:
        return [{"title": p.title, "slug": p.slug, "claims": len(p.claims)}
                for p in vault.search(q)]

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    if WEB_DIR.exists():
        app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
    return app


def main() -> None:
    # Build once and run. Qdrant's local mode holds an exclusive lock on the data dir,
    # so a single process (no --workers) is the supported Phase-1 topology. For an
    # external ASGI server use the factory: `uvicorn --factory tubewiki.api:build_app`.
    import uvicorn
    uvicorn.run(build_app(), host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
