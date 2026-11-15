"""The HTTP API and the static web app.

Handlers are plain `def` (FastAPI runs them in a thread pool) and open their own SQLite connection, because
connections are bound to the thread that created them. Everything the app shows comes from `pia.store.queries`;
everything it changes goes through the same functions the worker and the tests use.

Security (ARCHITECTURE.md, "Security"): strict Content-Security-Policy (no inline script, nothing from other
origins), Host validation, session auth when a password is configured, an Origin check on state-changing
requests, and bearer tokens for devices.
"""

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from pia.acquire import discovery, manage
from pia.http import SourceError
from pia.intel import learn, opportunities, reader
from pia.intel.brief import load_brief, write_brief
from pia.intel.relate import related_for
from pia.intel.usage import usage_summary
from pia.llm.client import LLMError
from pia.notify import engine as notify
from pia.server import auth
from pia.store import db, queries
from pia.worker import Services, Worker, run_cycle

STATIC_DIR = Path(__file__).parent / "static"
LOCAL_HOSTS = ("127.0.0.1", "localhost", "[::1]", "testserver")
CSP = "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; font-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
OPEN_PATHS = ("/api/session", "/api/login", "/api/logout", "/api/health")


class StateChange(BaseModel):
    read: bool | None = None
    saved: bool | None = None
    dismissed: bool | None = None


class BulkRead(BaseModel):
    ids: list[int] = Field(max_length=500)


class FeedbackIn(BaseModel):
    kind: str
    reason: str = Field(default="", max_length=1000)


class TopicLevel(BaseModel):
    level: int | None = None


class TopicIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    domain: str | None = None
    level: int | None = 2


class DomainWeight(BaseModel):
    weight: float = Field(ge=0, le=1.5)


class UrlIn(BaseModel):
    url: str = Field(min_length=4, max_length=600)


class SourceIn(BaseModel):
    url: str = Field(min_length=4, max_length=600)
    name: str | None = Field(default=None, max_length=80)
    domains: list[str] | None = None
    org_id: int | None = None
    purpose: str | None = None


class SourcePatch(BaseModel):
    status: str | None = None
    interval: int | None = None
    domains: list[str] | None = None
    name: str | None = Field(default=None, max_length=80)


class OrganisationIn(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    homepage: str | None = Field(default=None, max_length=400)
    notes: str = Field(default="", max_length=2000)


class OpportunityPatch(BaseModel):
    status: str
    notes: str | None = Field(default=None, max_length=4000)


class LoginIn(BaseModel):
    password: str = Field(max_length=500)


class DeviceIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class Ack(BaseModel):
    ids: list[int] = Field(max_length=100)


class SeenIn(BaseModel):
    ids: list[int] | None = None


class RunIn(BaseModel):
    force: bool = False


def _hostname(header: str) -> str:
    """The host part of a Host header, without the port ('[::1]:8765' -> '[::1]')."""
    header = header.strip().lower()
    if header.startswith("["):
        return header.split("]")[0] + "]"
    return header.rsplit(":", 1)[0]


def create_app(db_path: str | Path, services: Services, *, worker: Worker | None = None, local_only: bool = True, clock=db.utcnow) -> FastAPI:
    db.connect(db_path).close()  # create or migrate before the first request
    app = FastAPI(title="PIA", docs_url=None, redoc_url=None, openapi_url=None)
    throttle = auth.LoginThrottle()
    allowed_hosts = [h.strip().lower() for h in os.environ.get("PIA_ALLOWED_HOSTS", "").split(",") if h.strip()]

    @contextmanager
    def database() -> Iterator[sqlite3.Connection]:
        conn = db.connect(db_path, migrate=False)
        try:
            yield conn
        finally:
            conn.close()

    def now() -> datetime:
        return clock()

    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = _hostname(request.headers.get("host") or "")
        if local_only and host not in LOCAL_HOSTS:
            return JSONResponse({"detail": "unknown host"}, status_code=400)  # DNS-rebinding defence for the local app
        if not local_only and allowed_hosts and host not in allowed_hosts:
            return JSONResponse({"detail": "unknown host"}, status_code=400)
        path = request.url.path
        if path.startswith("/api/") and request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if origin and (urlsplit(origin).netloc.lower() != (request.headers.get("host") or "").lower()):
                return JSONResponse({"detail": "cross-origin request refused"}, status_code=403)
        if path.startswith("/api/") and not path.startswith("/api/device/") and path not in OPEN_PATHS:
            with database() as conn:
                if auth.password_configured(conn) and not auth.session_valid(conn, request.cookies.get(auth.COOKIE), now()):
                    return JSONResponse({"detail": "sign in required"}, status_code=401)
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        if path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        elif path.startswith("/static/") and "/fonts/" not in path and "/vendor/" not in path:
            # Revalidate the app's own files on every load (cheap: an ETag check), so an update is never masked by
            # a cached stylesheet or script. Fonts and vendored libraries change only with a release.
            response.headers["Cache-Control"] = "no-cache"
        return response

    def must(found, what: str = "item"):
        if found is None or found is False:
            raise HTTPException(404, f"no such {what}")
        return found

    # ---------- session ----------

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/api/session")
    def session(request: Request):
        with database() as conn:
            required = auth.password_configured(conn)
            return {"auth_required": required, "signed_in": not required or auth.session_valid(conn, request.cookies.get(auth.COOKIE), now())}

    @app.post("/api/login")
    def login(body: LoginIn, request: Request, response: Response):
        if throttle.locked():
            raise HTTPException(429, "too many attempts; wait a minute")
        with database() as conn:
            if not auth.check_password(conn, body.password):
                throttle.failed()
                raise HTTPException(401, "wrong password")
            throttle.succeeded()
            token = auth.create_session(conn, now())
        response.set_cookie(auth.COOKIE, token, max_age=auth.SESSION_DAYS * 86400, httponly=True, samesite="strict", secure=request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https", path="/")
        return {"signed_in": True}

    @app.post("/api/logout")
    def logout(request: Request, response: Response):
        with database() as conn:
            auth.end_session(conn, request.cookies.get(auth.COOKIE))
        response.delete_cookie(auth.COOKIE, path="/")
        return {"signed_in": False}

    # ---------- reading ----------

    @app.get("/api/bootstrap")
    def bootstrap():
        with database() as conn:
            moment = now()
            groups = queries.onboarding(conn)
            last_run = conn.execute("SELECT started_at, finished_at, ok FROM runs ORDER BY id DESC LIMIT 1").fetchone()
            return {
                "domains": queries.domain_overview(conn, moment),
                "counts": {
                    "saved": conn.execute("SELECT COUNT(*) FROM items WHERE saved_at IS NOT NULL").fetchone()[0],
                    "notifications": notify.unseen_count(conn),
                    "opportunities": conn.execute("SELECT COUNT(*) FROM opportunities o JOIN items i ON i.id = o.item_id WHERE i.dismissed_at IS NULL AND (o.deadline IS NULL OR o.deadline >= ?)", (moment.date().isoformat(),)).fetchone()[0],
                    "recommendations": conn.execute("SELECT COUNT(*) FROM sources WHERE status = 'candidate'").fetchone()[0],
                    "items": conn.execute("SELECT COUNT(*) FROM items").fetchone()[0],
                },
                "onboarding": {"rated": sum(g["rated"] for g in groups), "total": sum(g["total"] for g in groups), "dismissed": bool(db.get_setting(conn, "onboarding_dismissed", False))},
                "agent": {"running": bool(worker and worker.running), "background": worker is not None, "last_run": dict(last_run) if last_run else None, "notes": services.notes,
                          "models": services.groq_key is not None},
                "auth_required": auth.password_configured(conn),
            }

    @app.get("/api/home")
    def home():
        with database() as conn:
            moment = now()
            deadlines = [o for o in queries.opportunities(conn, moment.date(), show="open", limit=60) if o["deadline"]][:6]
            return {"featured": queries.featured(conn, moment), "deadlines": deadlines,
                    "tracked": len(queries.opportunities(conn, moment.date(), show="tracked"))}

    @app.get("/api/items")
    def items(domain: str | None = None, view: str = "latest", state: str = "active", kind: str | None = None, topic: int | None = None,
              source: str | None = None, q: str | None = None, days: int | None = None, limit: int = 30, offset: int = 0):
        with database() as conn:
            return queries.list_items(conn, domain=domain, view=view, state=state, kind=kind, topic_id=topic, source=source, q=q, days=days,
                                      now=now(), limit=max(1, min(limit, 100)), offset=max(0, offset))

    @app.get("/api/items/{item_id}")
    def item(item_id: int):
        with database() as conn:
            detail = must(queries.item_detail(conn, item_id))
            detail["brief"] = load_brief(conn, item_id)
            detail["related"] = related_for(conn, item_id)
            detail["reader"] = reader.reader_view(conn, item_id)
            detail["can_brief"] = services.groq_key is not None
            if not services.config.jev_enabled:
                detail["fit"] = None  # a judgement stored while Jev was in use plays no part now, so it is not shown
            return detail

    @app.post("/api/items/{item_id}/open")
    def open_item(item_id: int):
        """The owner opened the item: it is now read, and that is a (small) signal of interest."""
        with database() as conn:
            moment = now()
            must(conn.execute("SELECT 1 FROM items WHERE id = ?", (item_id,)).fetchone())
            first = conn.execute("UPDATE items SET opened_at = ? WHERE id = ? AND opened_at IS NULL", (db.iso(moment), item_id)).rowcount
            if first:
                learn.record_signal(conn, item_id, "open", moment)
            return learn.set_item_state(conn, item_id, moment, read=True)

    @app.post("/api/items/{item_id}/state")
    def set_state(item_id: int, body: StateChange):
        with database() as conn:
            return must(learn.set_item_state(conn, item_id, now(), read=body.read, saved=body.saved, dismissed=body.dismissed))

    @app.post("/api/items/read")
    def bulk_read(body: BulkRead):
        with database() as conn:
            moment = now()
            for item_id in body.ids:
                learn.set_item_state(conn, item_id, moment, read=True)
            return {"read": len(body.ids)}

    @app.post("/api/items/{item_id}/feedback")
    def give_feedback(item_id: int, body: FeedbackIn):
        with database() as conn:
            try:
                must(learn.add_feedback(conn, item_id, body.kind, now(), body.reason))
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            learn.apply_profile_change(conn, now())
            return {"feedback": [dict(row) for row in conn.execute("SELECT kind, reason FROM feedback WHERE item_id = ?", (item_id,))]}

    @app.delete("/api/items/{item_id}/feedback/{kind}")
    def undo_feedback(item_id: int, kind: str):
        with database() as conn:
            learn.remove_feedback(conn, item_id, kind, now())
            learn.apply_profile_change(conn, now())
            return {"feedback": [dict(row) for row in conn.execute("SELECT kind, reason FROM feedback WHERE item_id = ?", (item_id,))]}

    @app.post("/api/items/{item_id}/brief")
    def brief(item_id: int):
        with database() as conn:
            cached = load_brief(conn, item_id)
            if cached:
                return {"brief": cached}
            meter = services.new_meter()
            if meter is None:
                raise HTTPException(503, "No model is configured, so PIA cannot write a brief. Add GROQ_API_KEY to enable it.")
            moment = now()
            row = must(conn.execute("SELECT LENGTH(COALESCE(content, '')) AS n, canonical_url FROM items WHERE id = ?", (item_id,)).fetchone())
            if row["n"] < 600 or reader.is_open_access(row["canonical_url"]):  # thin source text: read the page first
                reader.fetch_text(conn, services.http, item_id, moment)
            try:
                written = write_brief(conn, meter, services.config.model_for("brief"), item_id, moment)
            except LLMError as exc:
                meter.flush(conn)
                raise HTTPException(502, f"The model could not write a brief just now ({str(exc)[:120]}). Try again shortly.") from exc
            meter.flush(conn)
            return {"brief": written, "reader": reader.reader_view(conn, item_id)}

    @app.post("/api/items/{item_id}/reader")
    def read_item(item_id: int):
        with database() as conn:
            must(conn.execute("SELECT 1 FROM items WHERE id = ?", (item_id,)).fetchone())
            reader.fetch_text(conn, services.http, item_id, now())
            return reader.reader_view(conn, item_id)

    # ---------- interests ----------

    @app.get("/api/profile")
    def profile():
        with database() as conn:
            return {**queries.profile(conn), "suggested": learn.suggested_topics(conn), "onboarding": queries.onboarding(conn)}

    @app.put("/api/topics/{topic_id}")
    def rate_topic(topic_id: int, body: TopicLevel):
        with database() as conn:
            try:
                must(learn.set_topic_level(conn, topic_id, body.level, now()), "topic")
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            learn.apply_profile_change(conn, now())
            return {"id": topic_id, "level": body.level}

    @app.post("/api/topics")
    def new_topic(body: TopicIn):
        with database() as conn:
            domain = conn.execute("SELECT id FROM domains WHERE slug = ?", (body.domain,)).fetchone() if body.domain else None
            try:
                topic_id = learn.create_topic(conn, body.name, now(), domain_id=domain["id"] if domain else None, level=body.level)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            # Find the new topic in what PIA already holds, so following something has an immediate effect.
            from pia.intel.classify import TopicMatcher

            matcher = TopicMatcher.load(conn)
            matcher.patterns = [entry for entry in matcher.patterns if entry[0] == topic_id]
            for row in conn.execute("SELECT id, title, content FROM items ORDER BY id DESC LIMIT 6000").fetchall():
                for found, weight in matcher.match(row["title"], row["content"] or "").items():
                    conn.execute("INSERT OR IGNORE INTO item_topics (item_id, topic_id, weight) VALUES (?, ?, ?)", (row["id"], found, weight))
            conn.execute("UPDATE topics SET item_count = (SELECT COUNT(*) FROM item_topics WHERE topic_id = topics.id) WHERE id = ?", (topic_id,))
            conn.commit()
            learn.apply_profile_change(conn, now())
            return {"id": topic_id}

    @app.post("/api/topics/{topic_id}/reset")
    def reset_topic(topic_id: int):
        with database() as conn:
            learn.reset_topic_learning(conn, topic_id)
            learn.apply_profile_change(conn, now())
            return {"id": topic_id}

    @app.get("/api/topics/{topic_id}")
    def topic(topic_id: int):
        with database() as conn:
            return must(queries.topic_timeline(conn, topic_id), "topic")

    @app.put("/api/domains/{slug}")
    def weigh_domain(slug: str, body: DomainWeight):
        with database() as conn:
            row = must(conn.execute("SELECT id FROM domains WHERE slug = ?", (slug,)).fetchone(), "space")
            learn.set_domain_weight(conn, row["id"], body.weight)
            learn.apply_profile_change(conn, now())
            return {"slug": slug, "weight": body.weight}

    @app.post("/api/onboarding/dismiss")
    def dismiss_onboarding():
        with database() as conn:
            db.set_setting(conn, "onboarding_dismissed", True)
            conn.commit()
            return {"dismissed": True}

    # ---------- sources ----------

    @app.get("/api/sources")
    def sources():
        with database() as conn:
            return {"sources": manage.list_sources(conn, now()), "organisations": manage.list_organisations(conn)}

    @app.post("/api/sources/inspect")
    def inspect_source(body: UrlIn):
        with database() as conn:
            try:
                return manage.inspect(conn, services.http, body.url, now())
            except SourceError as exc:
                raise HTTPException(422, str(exc)[:300]) from exc

    @app.post("/api/sources")
    def add_source(body: SourceIn):
        with database() as conn:
            try:
                found = manage.inspect(conn, services.http, body.url, now())
                source_id = manage.add_source(conn, found, now(), name=body.name, domains=body.domains, org_id=body.org_id, purpose=body.purpose)
            except (SourceError, ValueError) as exc:
                raise HTTPException(422, str(exc)[:300]) from exc
        if worker:
            worker.nudge()
        return {"id": source_id}

    @app.patch("/api/sources/{source_id}")
    def patch_source(source_id: int, body: SourcePatch):
        with database() as conn:
            try:
                if body.status is not None:
                    must(manage.set_status(conn, source_id, body.status, now()), "source")
                must(manage.update_source(conn, source_id, interval=body.interval, domains=body.domains, name=body.name), "source")
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
        if worker and body.status == "active":
            worker.nudge()
        return {"id": source_id}

    @app.delete("/api/sources/{source_id}")
    def remove_source(source_id: int):
        with database() as conn:
            must(manage.delete_source(conn, source_id), "source")
            return {"deleted": source_id}

    @app.post("/api/sources/discover")
    def discover_now():
        """Look for new sources now: sites PIA keeps being pointed at, and model suggestions that verify."""
        with database() as conn:
            moment = now()
            meter = services.new_meter()
            model = services.config.model_for("source_eval")
            found = discovery.discover_from_links(conn, services.http, moment, llm=meter, model=model)
            if meter is not None:
                found += discovery.discover_suggested(conn, services.http, meter, model, moment)
                meter.flush(conn)
            return {"proposed": len(found)}

    @app.post("/api/organisations")
    def add_organisation(body: OrganisationIn):
        with database() as conn:
            try:
                return {"id": manage.add_organisation(conn, body.name, now(), homepage=body.homepage, notes=body.notes)}
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc

    @app.delete("/api/organisations/{org_id}")
    def remove_organisation(org_id: int):
        with database() as conn:
            must(manage.delete_organisation(conn, org_id), "organisation")
            return {"deleted": org_id}

    # ---------- opportunities ----------

    @app.get("/api/opportunities")
    def list_opportunities(show: str = "open", status: str | None = None, first_year: bool = False, kind: str | None = None):
        with database() as conn:
            today = now().date()
            everything = queries.opportunities(conn, today, show="all", limit=2000)
            return {
                "opportunities": queries.opportunities(conn, today, show=show, status=status, first_year=first_year, kind=kind),
                "counts": {"open": sum(1 for o in everything if o["state"] != "expired"), "expired": sum(1 for o in everything if o["state"] == "expired"),
                           "tracked": sum(1 for o in everything if o["status"]), "first_year": sum(1 for o in everything if o["first_year"] == "yes" and o["state"] != "expired"),
                           **{f"status_{name}": sum(1 for o in everything if o["status"] == name) for name in opportunities.TRACKED}},
                "organisations": manage.list_organisations(conn),
            }

    @app.patch("/api/opportunities/{item_id}")
    def track_opportunity(item_id: int, body: OpportunityPatch):
        with database() as conn:
            try:
                must(opportunities.set_status(conn, item_id, body.status, now(), body.notes), "opportunity")
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            if body.status in ("interested", "planning", "applied"):
                learn.set_item_state(conn, item_id, now(), saved=True)
            return {"item_id": item_id, "status": body.status}

    # ---------- notifications and devices ----------

    @app.get("/api/notifications")
    def notifications():
        with database() as conn:
            devices = [dict(row) for row in conn.execute("SELECT id, name, created_at, last_seen_at FROM devices WHERE revoked_at IS NULL ORDER BY id")]
            return {"notifications": notify.inbox(conn), "unseen": notify.unseen_count(conn), "settings": notify.settings(conn), "devices": devices}

    @app.post("/api/notifications/seen")
    def seen(body: SeenIn):
        with database() as conn:
            return {"seen": notify.mark_seen(conn, now(), body.ids)}

    @app.put("/api/notifications/settings")
    def notification_settings(body: dict):
        with database() as conn:
            return notify.update_settings(conn, body)

    @app.post("/api/devices")
    def add_device(body: DeviceIn):
        with database() as conn:
            device_id, token = notify.register_device(conn, body.name, now())
            return {"id": device_id, "token": token}

    @app.delete("/api/devices/{device_id}")
    def remove_device(device_id: int):
        with database() as conn:
            must(notify.revoke_device(conn, device_id, now()), "device")
            return {"revoked": device_id}

    def device_from(request: Request, conn: sqlite3.Connection) -> sqlite3.Row:
        header = request.headers.get("authorization", "")
        device = notify.device_for_token(conn, header[7:].strip(), now()) if header.lower().startswith("bearer ") else None
        if device is None:
            raise HTTPException(401, "unknown or revoked device token")
        return device

    @app.get("/api/device/pending")
    def device_pending(request: Request):
        with database() as conn:
            device = device_from(request, conn)
            return {"notifications": notify.pending_for_device(conn, device, now())}

    @app.post("/api/device/ack")
    def device_ack(request: Request, body: Ack):
        with database() as conn:
            device = device_from(request, conn)
            return {"acknowledged": notify.acknowledge(conn, device["id"], body.ids, now())}

    # ---------- system ----------

    @app.get("/api/system")
    def system():
        with database() as conn:
            moment = now()
            runs = [dict(row) for row in conn.execute("SELECT id, kind, started_at, finished_at, ok, stats, error FROM runs ORDER BY id DESC LIMIT 12")]
            health = {row["h"]: row["n"] for row in conn.execute(
                """SELECT CASE WHEN status != 'active' THEN status WHEN failures >= 5 THEN 'unhealthy' WHEN failures > 0 THEN 'retrying'
                               WHEN last_ok_at IS NULL THEN 'pending' ELSE 'ok' END AS h, COUNT(*) AS n FROM sources GROUP BY h""")}
            size = Path(db_path).stat().st_size if Path(db_path).is_file() else 0
            stages = {row["stage"]: row["n"] for row in conn.execute("SELECT stage, COUNT(*) AS n FROM items GROUP BY stage")}
            return {"runs": runs, "usage": usage_summary(conn, moment), "sources": health, "items": stages, "db_bytes": size,
                    "schema": db.schema_version(conn), "notes": services.notes, "running": bool(worker and worker.running), "background": worker is not None,
                    "budget": services.config.budget, "tasks": services.config.tasks, "jev": services.config.jev_enabled and services.jev is not None,
                    "providers": sorted({services.config.provider(model) for model in services.config.tasks.values()})}

    @app.post("/api/system/run")
    def run_now(body: RunIn):
        if worker is not None:
            worker.nudge(force=body.force)
            return {"started": True, "background": True}
        with database() as conn:  # no background agent (e.g. a cron-driven deployment): run one cycle here
            return {"started": True, "background": False, "stats": run_cycle(conn, services, now(), db_path=db_path, force=body.force)}

    # ---------- the web app ----------

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
