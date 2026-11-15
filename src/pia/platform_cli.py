"""Commands for the platform (the continuously running product). Registered on the same `pia` program as v1's
briefing commands; these use their own database (`--data`, default data/platform.db, or the PIA_DATA variable).
"""

import getpass
import json
import logging
import os
import threading
import webbrowser
from pathlib import Path

import typer

from pia.config import DEFAULT_DB as LEGACY_DB
from pia.store import db, seed
from pia.store.legacy import import_legacy

LOCAL_HOSTS = ("127.0.0.1", "localhost")
DataOption = typer.Option(None, "--data", envvar="PIA_DATA", help="The platform database file (default: data/platform.db).")


def _path(data: Path | None) -> Path:
    return Path(data) if data else db.DEFAULT_DB


def open_database(data: Path | None):
    """Open (creating or upgrading) the platform database. A file copy is taken before any schema upgrade."""
    path = _path(data)
    if db.needs_migration(path):
        saved = db.copy_file_backup(path, f"before-schema-upgrade-{db.utcnow().strftime('%Y%m%d-%H%M%S')}")
        typer.echo(f"Upgrading the database schema (backup: {saved}).")
    conn = db.connect(path)
    if conn.execute("SELECT COUNT(*) FROM domains").fetchone()[0] == 0:
        report = seed.seed_all(conn, db.utcnow())
        typer.echo(f"New database: {report['taxonomy']['domains']} spaces, {report['taxonomy']['topics']} topics and {report['sources']} sources seeded.")
    return conn, path


def _logging(verbose: bool) -> None:
    logging.basicConfig(level=logging.INFO if verbose else logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s", force=True)


def register(app: typer.Typer) -> None:
    @app.command()
    def serve(
        data: Path = DataOption,
        host: str = typer.Option("127.0.0.1", envvar="PIA_HOST", help="Address to listen on. Anything other than a local address needs a password."),
        port: int = typer.Option(8765, envvar="PORT", help="Port to listen on."),
        open_browser: bool = typer.Option(False, "--open", help="Open PIA in your browser."),
        no_agent: bool = typer.Option(False, "--no-agent", help="Serve the app only; do not run the background agent."),
        every: int = typer.Option(600, help="Seconds between the agent's cycles."),
        verbose: bool = typer.Option(False, "--verbose", "-v"),
    ) -> None:
        """Run PIA: the web app plus the background agent that acquires and processes information. Ctrl+C to stop."""
        _logging(verbose)
        try:
            import uvicorn

            from pia.server import auth
            from pia.server.app import create_app
            from pia.worker import Worker, build_services
        except ImportError:
            typer.echo('PIA\'s web app needs extra packages. Install them with:  pip install -e ".[web]"', err=True)
            raise typer.Exit(1)

        conn, path = open_database(data)
        db.close_interrupted_runs(conn, db.utcnow())
        local = host in LOCAL_HOSTS
        protected = auth.password_configured(conn)
        conn.close()
        if not local and not protected:
            typer.echo(
                f"Refusing to listen on {host!r} without a password: PIA holds your reading history and interests.\n"
                "Set one with `pia set-password` (or the PIA_PASSWORD environment variable), then start again.", err=True)
            raise typer.Exit(1)

        services = build_services()
        for note in services.notes:
            typer.echo(f"note: {note}")
        worker = None if no_agent else Worker(path, services, interval=every)
        web_app = create_app(path, services, worker=worker, local_only=local)
        if worker:
            worker.start()
        shown = "localhost" if host in ("0.0.0.0", "::") else host
        url = f"http://{shown}:{port}"
        typer.echo(f"PIA is running at {url}   (Ctrl+C to stop)" + ("" if worker else "   [agent off]"))
        if open_browser:
            threading.Timer(1.0, webbrowser.open, [url]).start()
        try:
            uvicorn.run(web_app, host=host, port=port, log_level="info" if verbose else "warning", proxy_headers=not local, forwarded_allow_ips="*" if not local else None)
        finally:
            if worker:
                worker.stop()
            services.close()

    @app.command()
    def web(
        ctx: typer.Context,
        data: Path = DataOption,
        host: str = typer.Option("127.0.0.1", envvar="PIA_HOST"),
        port: int = typer.Option(8765, envvar="PORT"),
        open_browser: bool = typer.Option(False, "--open"),
    ) -> None:
        """Same as `pia serve` (kept so existing shortcuts keep working)."""
        ctx.invoke(serve, data=data, host=host, port=port, open_browser=open_browser, no_agent=False, every=600, verbose=False)

    @app.command("run-once")
    def run_once(
        data: Path = DataOption,
        force: bool = typer.Option(False, "--force", help="Fetch every active source now, not only the ones that are due."),
        no_fetch: bool = typer.Option(False, "--no-fetch", help="Process what is already stored; fetch nothing."),
        no_models: bool = typer.Option(False, "--no-models", help="Deterministic processing only; call no model."),
        verbose: bool = typer.Option(False, "--verbose", "-v"),
    ) -> None:
        """Run one cycle of the agent and exit: acquire, classify, assess, rank, relate, notify. For cron-style hosts."""
        _logging(verbose)
        from pia.worker import build_services, run_cycle

        conn, path = open_database(data)
        services = build_services()
        for note in services.notes:
            typer.echo(f"note: {note}")
        try:
            stats = run_cycle(conn, services, db.utcnow(), db_path=path, fetch=not no_fetch, force=force, models=not no_models)
        finally:
            services.close()
            conn.close()
        typer.echo(json.dumps(stats, indent=2))
        if stats.get("errors"):
            raise typer.Exit(1)

    @app.command("seed")
    def seed_command(
        data: Path = DataOption,
        personal: bool = typer.Option(False, "--personal", help="Also (re)apply config/personal.toml to topics you have not rated in the app."),
    ) -> None:
        """Add any new spaces, topics and catalogue sources from the config files. Never overwrites your edits."""
        from pia.intel.classify import retag_topics
        from pia.intel.score import rescore

        conn, _ = open_database(data)
        report = seed.seed_all(conn, db.utcnow(), personal=personal)
        if report["taxonomy"]["aliases_updated"] or report["taxonomy"]["topics"]:
            report["topic_links_changed"] = retag_topics(conn)  # the vocabulary changed: re-read what is stored
            rescore(conn, db.utcnow())
        typer.echo(json.dumps(report, indent=2))
        conn.close()

    @app.command("import-legacy")
    def import_legacy_command(
        data: Path = DataOption,
        source: Path = typer.Option(LEGACY_DB, "--from", help="The v1 database to import from. It is opened read-only."),
    ) -> None:
        """Import v1's items, triage results and favourites into the platform. Safe to run more than once."""
        conn, _ = open_database(data)
        report = import_legacy(conn, source, db.utcnow())
        conn.close()
        if not report["found"]:
            typer.echo(f"No v1 database at {source}; nothing to import.")
            return
        typer.echo(f"Imported {report['items']} items ({report['judgements']} with triage results, {report['saved']} saved); {report['already_present']} were already present.")

    @app.command()
    def backup(data: Path = DataOption) -> None:
        """Write a consistent snapshot of the platform database to data/backups (safe while PIA is running)."""
        conn, path = open_database(data)
        typer.echo(f"Backup written: {db.backup(conn, path, db.utcnow())}")
        conn.close()

    @app.command("set-password")
    def set_password(
        data: Path = DataOption,
        clear: bool = typer.Option(False, "--clear", help="Remove the password (local use only)."),
    ) -> None:
        """Set the password that protects PIA when it is reachable from other machines. Signs out every browser."""
        from pia.server import auth

        conn, _ = open_database(data)
        try:
            if clear:
                auth.clear_password(conn)
                typer.echo("Password removed. PIA will only listen on local addresses.")
                return
            password = os.environ.get("PIA_NEW_PASSWORD") or getpass.getpass("New password (at least 10 characters): ")
            try:
                auth.set_password(conn, password)
            except ValueError as exc:
                typer.echo(str(exc), err=True)
                raise typer.Exit(1)
            typer.echo("Password set.")
        finally:
            conn.close()

    @app.command("add-device")
    def add_device(data: Path = DataOption, name: str = typer.Argument("Windows laptop")) -> None:
        """Register a notification device (the Windows companion) and print its token once."""
        from pia.notify import engine

        conn, _ = open_database(data)
        _, token = engine.register_device(conn, name, db.utcnow())
        conn.close()
        typer.echo(f"Device token for {name!r} (shown once; paste it into the companion's settings):\n{token}")

    @app.command("gmail-connect")
    def gmail_connect(
        data: Path = DataOption,
        senders: str = typer.Option("", help="Comma-separated sender addresses or domains to read (e.g. your institute's notice senders)."),
        no_browser: bool = typer.Option(False, "--no-browser", help="Print the authorisation address instead of opening it."),
    ) -> None:
        """Authorise READ-ONLY access to your Gmail so PIA can pick up announcements. You approve it in your browser."""
        import http.server
        import json as _json
        import secrets
        import urllib.parse

        import httpx

        from pia.config import ConfigError, _find_key
        from pia.integrations import gmail

        try:
            client_id = _find_key(("GOOGLE_CLIENT_ID",), db.DEFAULT_DB.parents[1], os.environ, "missing")[0]
            client_secret = _find_key(("GOOGLE_CLIENT_SECRET",), db.DEFAULT_DB.parents[1], os.environ, "missing")[0]
        except ConfigError:
            typer.echo("GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are not set. Create an OAuth client first: see docs/platform/GMAIL.md.", err=True)
            raise typer.Exit(1)

        received: dict = {}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - the name is fixed by http.server
                received.update(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query))
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write("PIA received the answer. You can close this tab and return to the terminal.".encode())

            def log_message(self, *args):  # keep the authorisation code out of the terminal
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        redirect = f"http://127.0.0.1:{server.server_port}"
        state = secrets.token_urlsafe(24)
        verifier, challenge = gmail.new_pkce()
        url = gmail.authorisation_url(client_id, redirect, state, challenge)
        typer.echo("PIA is asking for ONE permission: to read your Gmail (it cannot send, change or delete anything).")
        typer.echo("Approve it in your browser:")
        typer.echo(url)
        typer.echo("")
        if not no_browser:
            webbrowser.open(url)
        server.timeout = 300
        server.handle_request()
        server.server_close()
        if received.get("state") != state or "code" not in received:
            typer.echo(f"Not authorised ({received.get('error', 'no answer received')}). Nothing was stored.", err=True)
            raise typer.Exit(1)
        with httpx.Client(timeout=30) as client:
            token = gmail.exchange_code(client, client_id=client_id, client_secret=client_secret, code=received["code"], verifier=verifier, redirect_uri=redirect, now=db.utcnow())
        gmail.save_token(token)
        conn, _ = open_database(data)
        config = {"senders": [sender.strip() for sender in senders.split(",") if sender.strip()]}
        conn.execute(
            """INSERT INTO sources (name, kind, description, config, domains, status, origin, purpose, trust, interval_minutes, created_at, decided_at)
               VALUES ('University mail', 'gmail', 'Announcements and opportunities from your mailbox (read-only).', ?, '["university"]', 'active', 'user', 'news', 0.8, 60, ?, ?)
               ON CONFLICT(name) DO UPDATE SET status = 'active', config = excluded.config, failures = 0, next_fetch_at = NULL""",
            (_json.dumps(config), db.iso(db.utcnow()), db.iso(db.utcnow())),
        )
        conn.commit()
        conn.close()
        typer.echo("Gmail connected (read-only). It appears under Sources as 'University mail'. Disconnect at any time with `pia gmail-disconnect`.")

    @app.command("gmail-disconnect")
    def gmail_disconnect(data: Path = DataOption) -> None:
        """Remove PIA's access to Gmail: deletes the stored authorisation, asks Google to revoke it, pauses the source."""
        import httpx

        from pia.integrations import gmail

        with httpx.Client(timeout=20) as client:
            had = gmail.disconnect(client)
        conn, _ = open_database(data)
        conn.execute("UPDATE sources SET status = 'paused' WHERE kind = 'gmail'")
        conn.commit()
        conn.close()
        typer.echo("Gmail disconnected. Items already acquired from mail are kept." if had else "Gmail was not connected.")
