"""Git-backed canonical store (spec §5.2).

The wiki *is* an Obsidian-style vault that is a git repo; ``main`` is canonical state.
In Phase 1 every ingest auto-commits straight to ``main``. The branch/PR workflow that
Phase 2 needs (pending ingests as branches, PR carries the rejection record) is not
built here, but nothing in this store precludes it — writes go through ``commit_page``,
which a Phase-2 gate can redirect onto a branch.

Git is the source of truth for *why* (commit history); the Postgres ledger (Phase 2)
will be the fast index over *what*.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from git import Repo

from .models import ConceptPage
from .provenance import parse_page, render_page


class VaultStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        self.repo = self._open_or_init()

    def _open_or_init(self) -> Repo:
        git_dir = self.path / ".git"
        if git_dir.exists():
            return Repo(self.path)
        repo = Repo.init(self.path, initial_branch="main")
        with repo.config_writer() as cw:
            cw.set_value("user", "name", "TubeWiki")
            cw.set_value("user", "email", "tubewiki@localhost")
        gitkeep = self.path / ".gitkeep"
        gitkeep.write_text("")
        repo.index.add([str(gitkeep.relative_to(self.path))])
        repo.index.commit("chore: initialize TubeWiki vault")
        return repo

    def _page_path(self, slug: str) -> Path:
        return self.path / f"{slug}.md"

    def read_page(self, slug: str) -> Optional[ConceptPage]:
        p = self._page_path(slug)
        if not p.exists():
            return None
        return parse_page(p.read_text(encoding="utf-8"))

    def list_pages(self) -> list[ConceptPage]:
        pages = []
        for p in sorted(self.path.glob("*.md")):
            try:
                pages.append(parse_page(p.read_text(encoding="utf-8")))
            except ValueError:
                continue  # not a concept page
        return pages

    def search(self, term: str) -> list[ConceptPage]:
        term_l = term.lower()
        hits = []
        for page in self.list_pages():
            hay = " ".join([page.title, page.summary or ""] + [c.text for c in page.claims]).lower()
            if term_l in hay:
                hits.append(page)
        return hits

    def commit_page(self, page: ConceptPage, message: str) -> str:
        """Write a page and commit it. Returns the commit SHA."""
        p = self._page_path(page.slug)
        p.write_text(render_page(page), encoding="utf-8")
        rel = str(p.relative_to(self.path))
        self.repo.index.add([rel])
        commit = self.repo.index.commit(message)
        return commit.hexsha[:10]

    def delete_page(self, slug: str, message: str) -> Optional[str]:
        p = self._page_path(slug)
        if not p.exists():
            return None
        rel = str(p.relative_to(self.path))
        self.repo.index.remove([rel], working_tree=True)
        return self.repo.index.commit(message).hexsha[:10]
