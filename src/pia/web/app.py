"""The HTTP layer: a thin JSON API over the existing core, plus the static front end.

Design points (see docs/design.md, "Web UI"):
  * Reads use a read-only connection; only the favorite endpoints write.
  * A connection is opened inside each handler. sqlite3 connections belong to the thread that
    created them, and FastAPI runs plain `def` handlers in a thread pool, so sharing one connection
    across requests (or opening it in a dependency) would raise "created in a different thread".
  * Local only: the Host header is validated, CORS is never enabled, and a Content-Security-Policy
    is sent. A local server still has a threat model: without these, a web page you happen to visit
    could talk to it (CSRF, DNS rebinding).
"""

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from pia.db import connect
from pia.history import connect_readonly, default_briefing_id, list_briefings
from pia.web import queries
from pia.web.favorites import ItemNotFound, add_favorite, remove_favorite
from pia.web.schemas import BriefingDetail, BriefingSummary, Category, FavoriteState, ItemsPage, StatusOut

STATIC_DIR = Path(__file__).parent / "static"
LOCAL_HOSTS = ["127.0.0.1", "localhost", "[::1]"]
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)


def create_app(db_path: Path | str, allowed_hosts: list[str] | None = None) -> FastAPI:
    db_path = Path(db_path)
    # Bring the schema up to date (creates the database on a fresh install), exactly as `pia` does.
    connect(db_path).close()

    app = FastAPI(title="PIA", description="Personal Intelligence Agent: local web API", docs_url="/docs")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts or LOCAL_HOSTS)

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
        return response

    @contextmanager
    def reader() -> Iterator:
        conn = connect_readonly(db_path)  # cannot write, cannot migrate
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def writer() -> Iterator:
        conn = connect(db_path)
        try:
            yield conn
        finally:
            conn.close()

    # ---------- reads ----------

    @app.get("/api/status", response_model=StatusOut)
    def get_status():
        with reader() as conn:
            return queries.status(conn)

    @app.get("/api/briefings", response_model=list[BriefingSummary])
    def get_briefings():
        with reader() as conn:
            return [dict(row) | {"chars": None} for row in list_briefings(conn)]

    # Declared before /{briefing_id}: otherwise "current" would be parsed as an id.
    @app.get("/api/briefings/current", response_model=BriefingDetail)
    def get_current_briefing():
        """The latest briefing that actually showed something (the newest is often an empty check)."""
        with reader() as conn:
            briefing_id = default_briefing_id(conn)
            detail = queries.briefing_detail(conn, briefing_id) if briefing_id else None
        if detail is None:
            raise HTTPException(status_code=404, detail="No briefings yet. Run `pia` to create the first one.")
        return detail

    @app.get("/api/briefings/{briefing_id}", response_model=BriefingDetail)
    def get_briefing_by_id(briefing_id: int):
        with reader() as conn:
            detail = queries.briefing_detail(conn, briefing_id)
        if detail is None:
            raise HTTPException(status_code=404, detail=f"No briefing #{briefing_id}.")
        return detail

    @app.get("/api/items", response_model=ItemsPage)
    def get_items(
        q: str | None = Query(None, max_length=200, description="Search titles and summaries"),
        category: Category | None = None,
        source: str | None = Query(None, max_length=100),
        favorite: bool = Query(False, description="Only favorites (any status), newest favorite first"),
        include_skipped: bool = Query(False, description="Also items a briefing considered but did not show"),
        limit: int = Query(30, ge=1, le=100),
        offset: int = Query(0, ge=0),
    ):
        with reader() as conn:
            items, total = queries.library_page(
                conn,
                q=q,
                category=category,
                source=source,
                favorite=favorite,
                include_skipped=include_skipped,
                limit=limit,
                offset=offset,
            )
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    # ---------- the only writes: favorites ----------
    # PUT and DELETE describe the state we want ("this item is / is not a favorite"), so repeating
    # either is harmless. A `POST /toggle` would flip the wrong way on a double click or a second tab.

    @app.put("/api/items/{item_id}/favorite", response_model=FavoriteState)
    def put_favorite(item_id: int):
        try:
            with writer() as conn:
                add_favorite(conn, item_id, now=datetime.now(timezone.utc))
        except ItemNotFound:
            raise HTTPException(status_code=404, detail=f"No item #{item_id}.")
        return {"item_id": item_id, "favorite": True}

    @app.delete("/api/items/{item_id}/favorite", response_model=FavoriteState)
    def delete_favorite(item_id: int):
        try:
            with writer() as conn:
                remove_favorite(conn, item_id)
        except ItemNotFound:
            raise HTTPException(status_code=404, detail=f"No item #{item_id}.")
        return {"item_id": item_id, "favorite": False}

    # ---------- front end ----------

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
