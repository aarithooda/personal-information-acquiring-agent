"""The agent's cycle, the reader, the cost meter, the optional Jev fit, read-only Gmail, and the web app's
pure logic (run with Node)."""

import base64
import inspect
import json
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from pia.acquire import runner
from pia.integrations import gmail
from pia.intel import jevfit, reader
from pia.intel.usage import Meter, ModelConfig, spent_today, usage_summary
from pia.jev.client import SystemOneResponse
from pia.llm.client import LLMUnavailable
from pia.models import RawItem
from pia.profile import load_profile
from pia.server.app import STATIC_DIR
from pia.store import db
from pia.worker import Services, run_cycle
from tests.platform_helpers import NOW, ScriptedLLM, add_item, add_source, assess_everything, item, make_db

PUBLIC = lambda host: ["93.184.216.34"]  # noqa: E731
ROOT = Path(__file__).parent.parent


def services(llm=None, handler=None):
    http = httpx.Client(transport=httpx.MockTransport(handler or (lambda request: httpx.Response(404))))
    config = ModelConfig()
    return Services(http=http, config=config, llm=Meter(llm, config, clock=lambda: NOW) if llm else None)


class OneItemAdapter:
    def __init__(self, items):
        self.items = items

    def fetch(self, client, since, until):
        return self.items


# ---------- the cycle ----------


def test_a_cycle_takes_new_items_all_the_way_to_ranked_and_notified(monkeypatch):
    conn = make_db()
    conn.execute("UPDATE topics SET level = 3 WHERE name = 'AI agents'")
    conn.execute("UPDATE domains SET weight = 1.5 WHERE slug = 'ai'")
    add_source(conn, "AI Lab", domains=("ai",), trust=0.9)
    new = [RawItem(source="x", external_id="1", url="https://lab.example/agents", title="A landmark result on AI agents", content="A long description of the result. " * 10, published_at=NOW),
           RawItem(source="x", external_id="2", url="https://lab.example/minor", title="A minor note", content="Short text.", published_at=NOW)]
    monkeypatch.setattr(runner, "build_adapter", lambda row: OneItemAdapter(new))
    def respond(request):
        if "<items>" not in request["user"]:
            return {"summary": "It shows a result.", "why_it_matters": "It matters.", "key_points": ["A."], "sections": [{"heading": "What happened", "body": "It happened."}]}
        answer = assess_everything(significance=5)(request)
        listed = json.loads(request["user"].split("<items>\n")[1].split("\n</items>")[0])
        for entry, shown in zip(answer["results"], listed):
            entry["significance"] = 5 if "landmark" in shown["title"] else 2
        return answer

    llm = ScriptedLLM(respond)
    stats = run_cycle(conn, services(llm), NOW)
    assert stats["acquire"] == {"sources": 1, "failed": 0, "new": 2, "merged": 0, "errors": {}} and stats["classified"] == 2
    assert stats["assess"]["assessed"] == 2 and stats["notifications"] == 1 and stats["briefs"] == 2 and "errors" not in stats
    top = conn.execute("SELECT * FROM items ORDER BY rank DESC").fetchone()
    assert top["title"] == "A landmark result on AI agents" and top["stage"] == "assessed" and top["rank"] > 0.8
    assert conn.execute("SELECT level FROM notifications").fetchone()[0] == "critical"
    run = conn.execute("SELECT ok, stats FROM runs").fetchone()
    assert run["ok"] == 1 and json.loads(run["stats"])["classified"] == 2
    assert conn.execute("SELECT COUNT(*), SUM(items) FROM llm_usage WHERE task = 'assess'").fetchone()[:] == (1, 2)  # one batched call, metered
    again = run_cycle(conn, services(llm), NOW + timedelta(minutes=10))
    assert again["acquire"]["sources"] == 0 and again["notifications"] == 0 and again["assess"]["assessed"] == 0  # nothing is done twice


def test_without_any_model_the_cycle_still_delivers_a_ranked_library(monkeypatch):
    conn = make_db()
    add_source(conn, "AI Lab", domains=("ai",))
    monkeypatch.setattr(runner, "build_adapter", lambda row: OneItemAdapter([RawItem(source="x", external_id="1", url="https://lab.example/a", title="LLM agents", content="Text.", published_at=NOW)]))
    stats = run_cycle(conn, services(), NOW)
    assert "assess" not in stats and "briefs" not in stats and stats["rescored"] == 1
    row = conn.execute("SELECT stage, score, rank FROM items").fetchone()
    assert row["stage"] == "classified" and row["score"] > 0.3 and row["rank"] > 0.3


def test_a_model_outage_or_a_broken_step_never_stops_the_cycle(monkeypatch):
    conn = make_db()
    add_source(conn, "AI Lab", domains=("ai",))
    monkeypatch.setattr(runner, "build_adapter", lambda row: OneItemAdapter([RawItem(source="x", external_id="1", url="https://lab.example/a", title="LLM agents", content="Text.", published_at=NOW)]))
    monkeypatch.setattr("pia.worker.relate_pending", lambda conn, now: (_ for _ in ()).throw(RuntimeError("boom")))
    stats = run_cycle(conn, services(ScriptedLLM(LLMUnavailable("down"))), NOW)
    assert stats["assess"]["failed_batches"] == 1 and stats["errors"] == {"relations": "RuntimeError: boom"}
    assert stats["ranked"] == 1 and conn.execute("SELECT rank FROM items").fetchone()[0] > 0  # later steps still ran
    assert conn.execute("SELECT ok, error FROM runs").fetchone()[:] == (0, "relations: RuntimeError: boom")


def test_the_daily_budget_switches_model_steps_off_for_the_rest_of_the_day():
    conn = make_db()
    add_item(conn, "LLM agents", content="Text.")
    conn.execute("INSERT INTO llm_usage (at, provider, model, task, cost_usd) VALUES (?, 'groq', 'm', 'assess', 5.0)", (db.iso(NOW),))
    conn.commit()
    llm = ScriptedLLM(assess_everything())
    stats = run_cycle(conn, services(llm), NOW, fetch=False)
    assert "budget" in stats and llm.calls == [] and "assess" not in stats
    assert run_cycle(conn, services(llm), NOW + timedelta(days=1), fetch=False)["assess"]["assessed"] == 1  # a new day


# ---------- the cost meter ----------


def test_every_model_call_is_metered_including_failures():
    conn = make_db()
    config = ModelConfig()
    config.prices = {"cheap": {"provider": "groq", "input": 1.0, "output": 2.0}}
    llm = ScriptedLLM({"ok": True}, LLMUnavailable("down"))
    meter = Meter(llm, config, clock=lambda: NOW)
    meter.for_task("assess", 10).complete_json(model="cheap", system="", user="", schema_name="x", schema={})
    with pytest.raises(LLMUnavailable):
        meter.for_task("brief", 1).complete_json(model="cheap", system="", user="", schema_name="x", schema={})
    meter.flush(conn)
    rows = conn.execute("SELECT task, items, input_tokens, output_tokens, cost_usd, ok FROM llm_usage ORDER BY id").fetchall()
    assert rows[0][:] == ("assess", 10, 100, 50, pytest.approx(0.0002), 1) and rows[1]["ok"] == 0 and rows[1]["cost_usd"] == 0
    assert spent_today(conn, NOW) == pytest.approx(0.0002) and meter.records == []
    summary = usage_summary(conn, NOW)
    assert summary["by_task"][0]["task"] == "assess" and summary["by_day"][0]["calls"] == 2
    assert ModelConfig.load().model_for("assess") == "openai/gpt-oss-20b" and config.cost("unknown-model", 1000, 1000) == 0.0


# ---------- the reader ----------


def test_the_reader_caches_text_shows_papers_in_full_and_everything_else_as_an_excerpt():
    conn = make_db()
    paper = add_item(conn, "A paper", url="https://arxiv.org/abs/2611.00003")
    article = add_item(conn, "An article", url="https://news.example/story")
    body = "<html><body><article>" + "".join(f"<p>Paragraph {n} of the text, long enough to be kept by the extractor as real prose.</p>" for n in range(40)) + "</article></body></html>"
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text=body, headers={"content-type": "text/html"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert reader.reader_view(conn, paper)["status"] == "not_fetched"
    assert reader.fetch_text(conn, client, paper, NOW, resolver=PUBLIC)["ok"] and "https://arxiv.org/html/2611.00003" in seen  # arXiv's own HTML rendering
    assert reader.fetch_text(conn, client, article, NOW, resolver=PUBLIC)["ok"]
    full, excerpt = reader.reader_view(conn, paper), reader.reader_view(conn, article)
    assert full["mode"] == "full" and len(full["paragraphs"]) == 40 and not full["truncated"]
    assert excerpt["mode"] == "excerpt" and excerpt["truncated"] and sum(map(len, excerpt["paragraphs"])) <= reader.EXCERPT_CHARS
    calls = len(seen)
    reader.fetch_text(conn, client, article, NOW, resolver=PUBLIC)
    assert len(seen) == calls and reader.reader_view(conn, 99999) is None  # cached


def test_a_refused_or_failed_fetch_is_remembered_and_explained_not_retried_every_time():
    conn = make_db()
    private = add_item(conn, "Internal", url="http://intranet.example/x")
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, text="secret")))
    resolver_calls = []
    resolver = lambda host: resolver_calls.append(host) or ["10.0.0.5"]  # noqa: E731
    assert not reader.fetch_text(conn, client, private, NOW, resolver=resolver)["ok"]
    assert "not fetched" in reader.reader_view(conn, private)["reason"]
    reader.fetch_text(conn, client, private, NOW + timedelta(days=1), resolver=resolver)
    assert len(resolver_calls) == 1  # negative-cached
    reader.fetch_text(conn, client, private, NOW + timedelta(days=8), resolver=resolver)
    assert len(resolver_calls) == 2  # retried after a week


# ---------- the dormant Jev integration still works when called directly ----------


class FakeJev:
    """Answers every question of every request with a fixed probability, in the documented response shape."""

    def __init__(self, fail_on=None):
        self.requests, self.fail_on = 0, fail_on

    def system_one(self, state, questions):
        self.requests += 1
        if self.fail_on and self.fail_on in json.dumps(state):
            raise LLMUnavailable("jev down")
        answers = {}
        for name, question in questions.items():
            if question["type"] == "noul":
                answers[name] = {"type": "noul", "noul": 0.6}
            else:
                options = list(question["criteria"])
                answers[name] = {"type": "choice", "choice": options[0], "probabilities": {option: 1 / len(options) for option in options}, "confidence": 0.5}
        return SystemOneResponse.model_validate({"model": "jev-test-1", "answers": answers, "usage": {"input_tokens": 1000, "output_tokens": 0}})


def test_jev_judges_only_the_shortlist_stores_raw_answers_and_a_failure_costs_nothing():
    conn = make_db()
    profile = load_profile(ROOT / "config" / "interests.example.toml")
    good, broken = add_item(conn, "LLM agents that work", content="Text."), add_item(conn, "BROKENITEM about LLMs", content="Text.")
    unassessed = add_item(conn, "Not assessed", content="Text.")
    conn.execute("UPDATE items SET stage = 'assessed' WHERE id IN (?, ?)", (good, broken))
    meter = Meter(ScriptedLLM({}), ModelConfig.load(), clock=lambda: NOW)
    jev = FakeJev(fail_on="BROKENITEM")
    assert jevfit.judge_shortlist(conn, jev, profile, meter, NOW) == 1
    assert 0 <= item(conn, good)["fit"] <= 1 and item(conn, broken)["fit"] is None and item(conn, unassessed)["fit"] is None
    stored = json.loads(conn.execute("SELECT payload FROM judgements WHERE item_id = ? AND task = 'fit'", (good,)).fetchone()[0])
    assert stored["profile_hash"] == profile.hash and len(stored["answers"]) > 5 and "plan" in stored
    assert meter.records[0].task == "fit" and meter.records[0].provider == "typesafe" and meter.records[0].cost_usd > 0
    assert jevfit.judge_shortlist(conn, jev, profile, meter, NOW) == 0 or item(conn, good)["fit"] is not None  # never judged twice


# ---------- Groq-only operation: Jev is integrated but dormant ----------


def test_the_committed_configuration_is_groq_only():
    config = ModelConfig.load()
    assert config.jev_enabled is False and config.providers == {"jev": False}
    assert {config.provider(model) for model in config.tasks.values()} == {"groq"}  # every model job goes to Groq
    assert ModelConfig().jev_enabled is False  # also the default with no config file at all
    assert ModelConfig(providers={"jev": "yes"}).jev_enabled is False  # only an explicit `true` switches it on


def test_with_jev_off_its_key_is_never_read_and_no_client_is_built(monkeypatch):
    import pia.worker as worker

    def forbidden(*args, **kwargs):
        raise AssertionError("the Jev key and profile must not be touched in Groq-only operation")

    monkeypatch.setattr(worker, "get_jev_api_key", forbidden)
    monkeypatch.setattr(worker, "load_profile", forbidden)
    monkeypatch.setattr(worker, "get_groq_api_key", lambda: "gsk_test_key_for_this_test")
    monkeypatch.setenv("JEV_API_KEY", "a-jev-key-that-must-be-ignored")
    monkeypatch.setenv("TYPESAFE_API_KEY", "a-jev-key-that-must-be-ignored")
    built = worker.build_services()
    try:
        assert built.jev is None and built.profile is None and built.llm is not None and built.notes == []
    finally:
        built.close()


def test_a_cycle_makes_no_jev_call_unless_jev_is_deliberately_switched_on(monkeypatch):
    conn = make_db()
    add_source(conn, "AI Lab", domains=("ai",))
    monkeypatch.setattr(runner, "build_adapter", lambda row: OneItemAdapter([RawItem(source="x", external_id="1", url="https://lab.example/a", title="LLM agents at scale", content="Text. " * 30, published_at=NOW)]))
    hosts = []

    def handler(request):
        hosts.append(request.url.host)
        return httpx.Response(404)

    built = services(ScriptedLLM(lambda request: assess_everything()(request) if "<items>" in request["user"] else
                                 {"summary": "S.", "why_it_matters": "", "key_points": [], "sections": []}), handler)
    built.jev, built.profile = FakeJev(), load_profile(ROOT / "config" / "interests.example.toml")  # a client lying around must still not be used
    stats = run_cycle(conn, built, NOW)
    assert "fit" not in stats and built.jev.requests == 0 and stats["assess"]["assessed"] == 1
    assert not any("typesafe" in host for host in hosts)
    assert conn.execute("SELECT COUNT(*) FROM judgements WHERE task = 'fit'").fetchone()[0] == 0
    assert {row[0] for row in conn.execute("SELECT DISTINCT provider FROM llm_usage")} == {"groq"}
    assert conn.execute("SELECT fit FROM items").fetchone()[0] is None

    built.config.providers["jev"] = True  # the integration is dormant, not removed: switched on, the same cycle uses it
    later = run_cycle(conn, built, NOW + timedelta(minutes=10))
    assert later["fit"] == 1 and built.jev.requests > 0 and conn.execute("SELECT fit FROM items").fetchone()[0] is not None


def test_judgements_stored_while_jev_was_in_use_are_kept_but_do_not_affect_ranking():
    from pia.intel.score import rescore

    conn = make_db()
    favoured, disfavoured = add_item(conn, "LLM agents, part one"), add_item(conn, "LLM agents, part two")
    conn.execute("UPDATE items SET fit = 1.0 WHERE id = ?", (favoured,))
    conn.execute("UPDATE items SET fit = 0.0 WHERE id = ?", (disfavoured,))
    rescore(conn, NOW)  # the default follows the committed configuration: Groq only
    assert item(conn, favoured)["relevance"] == item(conn, disfavoured)["relevance"]
    assert item(conn, favoured)["fit"] == 1.0  # the stored judgement is untouched, only unused
    rescore(conn, NOW, use_fit=True)
    assert item(conn, favoured)["relevance"] > item(conn, disfavoured)["relevance"]


def test_only_the_dormant_module_and_the_guarded_factory_refer_to_jev():
    platform = [path for folder in ("store", "acquire", "intel", "notify", "integrations", "server") for path in (ROOT / "src" / "pia" / folder).glob("*.py")]
    platform += [ROOT / "src" / "pia" / "worker.py", ROOT / "src" / "pia" / "platform_cli.py"]
    importers = sorted(path.name for path in platform if "pia.jev" in path.read_text(encoding="utf-8"))
    assert importers == ["jevfit.py", "worker.py"]
    worker_source = (ROOT / "src" / "pia" / "worker.py").read_text(encoding="utf-8")
    assert worker_source.index("if not services.config.jev_enabled") < worker_source.index("from pia.jev.client import")  # the switch comes first
    assert (ROOT / "src" / "pia" / "jev" / "client.py").is_file() and (ROOT / "src" / "pia" / "intel" / "jevfit.py").is_file()  # kept for later


# ---------- Gmail, read-only ----------


def encode(text):
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


MESSAGE = {"id": "m1", "threadId": "t1", "snippet": "snippet", "internalDate": "1794744000000", "payload": {
    "headers": [{"name": "Subject", "value": "Summer  internship: applications open"}, {"name": "From", "value": '"Dean, Academics" <dean@institute.example>'},
                {"name": "Date", "value": "Sat, 14 Nov 2026 10:00:00 +0530"}],
    "mimeType": "multipart/alternative", "parts": [
        {"mimeType": "text/plain", "body": {"data": encode("Apply by 1 December 2026.\nOpen to all years.")}},
        {"mimeType": "text/html", "body": {"data": encode("<p>ignored when plain text exists</p>")}},
        {"mimeType": "application/pdf", "filename": "form.pdf", "body": {"data": encode("binary")}}]}}


def test_the_integration_asks_for_one_read_only_permission_and_calls_no_modifying_endpoint():
    assert gmail.SCOPE == "https://www.googleapis.com/auth/gmail.readonly"
    url = gmail.authorisation_url("client", "http://127.0.0.1:9999", "state123", "challenge")
    assert "gmail.readonly" in url and "code_challenge_method=S256" in url and url.count("scope=") == 1
    source = inspect.getsource(gmail)
    for forbidden in ("/send", "/modify", "/trash", "/delete", "gmail.modify", "gmail.send", "mail.google.com/\"", "/drafts", "/labels"):
        assert forbidden not in source, forbidden
    verifier, challenge = gmail.new_pkce()
    assert len(verifier) > 60 and "=" not in challenge and challenge != verifier


def test_a_message_becomes_an_announcement_and_the_query_is_selective():
    announcement = gmail.message_to_item(MESSAGE, "University mail")
    assert announcement.title == "Summer internship: applications open" and announcement.author == "Dean, Academics"
    assert announcement.content == "Apply by 1 December 2026. Open to all years." and announcement.kind == "announcement"
    assert announcement.signals == {"from": "dean@institute.example", "mail": True} and announcement.published_at.hour == 4  # converted to UTC
    assert announcement.url.endswith("#all/t1") and announcement.url.startswith("https://mail.google.com/mail/u/0/")
    # Regression: items are identified by canonical URL, which drops the fragment. Two messages must stay two items.
    from pia.normalize import canonicalize_url
    other = {**MESSAGE, "id": "another-message", "threadId": "t2"}
    assert canonicalize_url(announcement.url) != canonicalize_url(gmail.message_to_item(other, "University mail").url)
    html_only = {"id": "m2", "payload": {"headers": [], "mimeType": "text/html", "body": {"data": encode("<p>A workshop on proof assistants will be held on campus next week for everyone.</p>")}}}
    assert "proof assistants" in gmail.message_to_item(html_only, "x").content and gmail.message_to_item(html_only, "x").title == "(no subject)"
    query = gmail.build_query(NOW, ["notices@institute.example", "dean@institute.example"], ["internship", "summer school"])
    assert query.startswith(f"after:{int(NOW.timestamp())}") and "from:(notices@institute.example OR dean@institute.example)" in query
    assert '"summer school"' in query and "-in:spam" in query and "{" in query


def test_fetching_refreshes_the_token_reads_messages_and_fails_clearly_when_not_connected(tmp_path):
    token_file = tmp_path / "gmail.json"
    source = gmail.GmailSource(name="University mail", senders=["dean@institute.example"], token_file=str(token_file))
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500)))
    with pytest.raises(gmail.GmailNotConnected, match="gmail-connect"):
        source.fetch(client, NOW - timedelta(days=1), NOW)

    gmail.save_token({"client_id": "id", "client_secret": "secret", "refresh_token": "refresh", "access_token": "stale", "expires_at": (NOW - timedelta(hours=1)).isoformat()}, token_file)
    seen = []

    def handler(request):
        seen.append((request.method, str(request.url).split("?")[0], request.headers.get("authorization")))
        if str(request.url).startswith(gmail.TOKEN_URL):
            return httpx.Response(200, json={"access_token": "fresh", "expires_in": 3600})
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"messages": [{"id": "m1"}]})
        return httpx.Response(200, json=MESSAGE)

    (found,) = source.fetch(httpx.Client(transport=httpx.MockTransport(handler)), NOW - timedelta(days=1), NOW)
    assert found.title.startswith("Summer") and json.loads(token_file.read_text())["access_token"] == "fresh"
    assert [method for method, _, _ in seen] == ["POST", "GET", "GET"] and all(auth == "Bearer fresh" for _, url, auth in seen if "gmail.googleapis" in url)
    assert all(method == "GET" for method, url, _ in seen if "gmail.googleapis" in url)  # mail is only ever read

    revoked = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(400, json={"error": "invalid_grant"})))
    gmail.save_token({**json.loads(token_file.read_text()), "expires_at": (NOW - timedelta(hours=1)).isoformat()}, token_file)
    with pytest.raises(gmail.GmailNotConnected, match="expired or was revoked"):
        source.fetch(revoked, NOW - timedelta(days=1), NOW)
    assert gmail.disconnect(None, token_file) is True and not token_file.exists() and gmail.disconnect(None, token_file) is False


def test_the_authorisation_code_exchange_insists_on_the_read_only_scope_and_a_refresh_token():
    def respond(payload):
        return httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)))

    kwargs = dict(client_id="id", client_secret="secret", code="code", verifier="v", redirect_uri="http://127.0.0.1:1", now=NOW)
    token = gmail.exchange_code(respond({"access_token": "a", "refresh_token": "r", "expires_in": 3600, "scope": gmail.SCOPE}), **kwargs)
    assert token["refresh_token"] == "r" and token["scope"] == gmail.SCOPE
    from pia.http import SourceError

    with pytest.raises(SourceError, match="read-only"):
        gmail.exchange_code(respond({"access_token": "a", "refresh_token": "r", "scope": "https://www.googleapis.com/auth/userinfo.email"}), **kwargs)
    with pytest.raises(SourceError, match="refresh token"):
        gmail.exchange_code(respond({"access_token": "a", "scope": gmail.SCOPE}), **kwargs)


# ---------- the web app's pure logic, run with Node ----------

NODE = shutil.which("node")
LOGIC_TEST = """
import assert from "node:assert/strict";
import * as u from "./util.js";

assert.deepEqual(u.parseRoute("#/space/ai?view=important&item=12"), { path: ["space", "ai"], query: { view: "important", item: "12" } });
assert.deepEqual(u.parseRoute(""), { path: [], query: {} });
assert.deepEqual(u.parseRoute("#/search?q=prime+gaps%20%26%20more"), { path: ["search"], query: { q: "prime gaps & more" } });
assert.equal(u.buildRoute(["space", "quant-finance"], { view: "important", item: undefined, show: "", kind: false }), "#/space/quant-finance?view=important");
assert.equal(u.buildRoute([], {}), "#/");
assert.deepEqual(u.parseRoute(u.buildRoute(["search"], { q: "a&b=c d" })).query, { q: "a&b=c d" });

for (const bad of ["javascript:alert(1)", "data:text/html,x", " https://x", "//evil.example", "", null, undefined, 42]) assert.equal(u.safeUrl(bad), null);
assert.equal(u.safeUrl("https://example.org/a"), "https://example.org/a");
assert.equal(u.hostOf("https://www.example.org/a/b"), "example.org");
assert.equal(u.hostOf("not a url"), "");

const now = new Date("2026-11-15T12:00:00Z");
assert.equal(u.timeAgo("2026-11-15T11:59:30Z", now), "just now");
assert.equal(u.timeAgo("2026-11-15T11:15:00Z", now), "45 min ago");
assert.equal(u.timeAgo("2026-11-15T07:00:00Z", now), "5 h ago");
assert.equal(u.timeAgo("2026-11-12T12:00:00Z", now), "3 d ago");
assert.match(u.timeAgo("2026-09-01T12:00:00Z", now), /1 Sep/);
assert.match(u.timeAgo("2025-09-01T12:00:00Z", now), /2025/);
assert.equal(u.timeAgo(null, now), "");
assert.equal(u.timeAgo("2026-11-16T12:00:00Z", now), "just now");

assert.equal(u.deadlineText(null), null);
assert.equal(u.deadlineText(0), "closes today");
assert.equal(u.deadlineText(1), "closes tomorrow");
assert.equal(u.deadlineText(9), "9 days left");
assert.equal(u.deadlineText(-1), "closed yesterday");
assert.equal(u.deadlineText(-4), "closed 4 days ago");

assert.equal(u.levelLabel(3), "Priority");
assert.equal(u.levelLabel(null), "Not rated");
assert.equal(u.plural(1, "item"), "1 item");
assert.equal(u.plural(1200, "item"), "1,200 items");
assert.equal(u.plural(2, "opportunity", "opportunities"), "2 opportunities");
assert.equal(u.usd(0), "$0");
assert.equal(u.usd(0.0034), "$0.0034");
assert.equal(u.usd(1.5), "$1.50");

// An empty state is a claim about the data, so it must be the true one.
assert.match(u.emptyMessage({ total: 0, filtered: false, state: "active", q: "zeta" }).title, /zeta/);
assert.equal(u.emptyMessage({ total: 0, filtered: true, state: "active" }).clear, true);
assert.match(u.emptyMessage({ total: 12, filtered: false, state: "unread" }).title, /caught up/);
assert.match(u.emptyMessage({ total: 0, filtered: false, state: "saved" }).title, /saved/i);
assert.match(u.emptyMessage({ total: 0, filtered: false, state: "active" }).hint, /Sources/);

// ---- the redesign's presentation rules ----
assert.equal(u.weightLabel(0), "Hidden");
assert.equal(u.weightLabel(0.6), "Less");
assert.equal(u.weightLabel(0.79), "Less");
assert.equal(u.weightLabel(0.8), "Normal");
assert.equal(u.weightLabel(1), "Normal");
assert.equal(u.weightLabel(1.3), "Priority");
assert.equal(u.weightLabel(1.5), "Priority");
const spaces = [1.5, 1.4, 1, 0.6, 1.3, 1.6, 1.35, 1.45, 0].map((weight, index) => ({ slug: "s" + index, weight }));
assert.deepEqual(u.priorityDomains(spaces).map((d) => d.weight), [1.6, 1.5, 1.45, 1.4, 1.35, 1.3]);  // priority only, strongest first, six at most
assert.deepEqual(u.priorityDomains([{ weight: 1 }, { weight: 0 }]), []);

// Margin notes: at most two clauses, in a fixed order.
assert.deepEqual(u.reasons({ topics: [{ name: "AI agents", level: 3 }, { name: "Large language models", level: 3 }, { name: "Other", level: 3 }], signals: { stars: 1487 } }),
  ["Your priority: AI agents and Large language models.", "1,487 stars on GitHub."]);
assert.deepEqual(u.reasons({ topics: [{ name: "Linear algebra", level: 2 }], signals: { points: 817, comments: 691 } }),
  ["You follow Linear algebra.", "817 points and 691 comments on Hacker News."]);
assert.deepEqual(u.reasons({ topics: [], signals: { points: 120 }, resurfaced: true, importance: 0.9 }), ["Newer work builds on this.", "Assessed as significant in its field."]);
assert.deepEqual(u.reasons({ topics: [], signals: { upvotes: 66 }, importance: 0.9 }), ["66 upvotes on Hugging Face Papers.", "Assessed as significant in its field."]);
assert.deepEqual(u.reasons({ topics: [], signals: {}, importance: 0.1, domain_short: "Maths" }), ["Ranks near the top of Maths."]);
assert.equal(u.reasons({ topics: [{ name: "A", level: 3 }], signals: { stars: 5 }, resurfaced: true, importance: 1 }).length, 2);

// The briefing: no clones or dismissed items, thin repositories dropped, one repository, a story leads, six at most.
const story = (id, kind, extra = {}) => ({ id, kind, title: "t" + id, signals: {}, ...extra });
const featured = [story(1, "repo", { signals: { stars: 1487 } }), story(2, "repo", { signals: { stars: 12 } }), story(3, "paper"), story(4, "paper", { spam: true }),
  story(5, "repo", { signals: { stars: 9000 } }), story(6, "news", { dismissed: true }), story(7, "post"), story(8, "news"), story(9, "article"), story(10, "paper"), story(11, "news"), story(12, "news")];
const briefing = u.composeBriefing(featured);
assert.deepEqual(briefing.map((x) => x.id), [3, 1, 7, 8, 9, 10]);  // repo 2 (12 stars) and 5 (second repo) dropped, clone and dismissed dropped, paper 3 leads
assert.equal(u.composeBriefing([story(1, "repo", { signals: { stars: 2000 } }), story(2, "post")]).length, 2);  // nothing else could lead: order is kept
assert.deepEqual(u.composeBriefing([]), []);
assert.equal(u.composeBriefing(featured.concat(featured)).length, 6);

// Duplicates: the same posting from two mails folds into one, keeping the first and counting the rest.
const vault = { item_id: 1, programme: "Venture Vault - Startup Idea Pitch Competition", deadline: "2026-11-18" };
const vaultAgain = { item_id: 2, title: "Venture Vault: Startup Idea Pitch Competition (reminder)", deadline: "2026-11-18" };
const india = { item_id: 3, programme: "IndiaAI Fellowship", deadline: "2026-12-01" };
const indiaAgain = { item_id: 4, programme: "IndiaAI Fellowship 2026", deadline: "2026-12-01" };
const indiaLater = { item_id: 5, programme: "IndiaAI Fellowship", deadline: "2027-01-10" };
const folded = u.foldOpportunities([vault, india, vaultAgain, indiaAgain, indiaLater]);
assert.deepEqual(folded.map((o) => [o.item_id, o.dup]), [[1, 1], [3, 1], [5, 0]]);
assert.equal(u.oppKey({ programme: "A.B-C d e f g h" }), "abcdefgh|");
assert.equal(u.oppKey({ title: "Same Title Here", deadline: null }), "sametitleher|");
assert.deepEqual(u.foldOpportunities([]), []);

// Urgency groups at their boundaries.
assert.deepEqual([0, 7, 8, 31, 32, null, undefined, 400].map(u.urgencyGroup), ["week", "week", "month", "month", "later", "undated", "undated", "later"]);
assert.deepEqual([null, undefined, -3, 0, 1, 2, 3, 14, 15].map(u.dueTone), ["none", "none", "", "hot", "hot", "hot", "soon", "soon", ""]);
assert.deepEqual([null, -1, -4, 0, 1, 2].map(u.dueText), ["No deadline given", "Closed yesterday", "Closed 4 days ago", "Closes today", "Closes tomorrow", "Closes in 2 days"]);

// Days: labels and grouping use the owner's local calendar day.
const noon = new Date(2026, 10, 16, 12, 0, 0);
const at = (day, hour) => new Date(2026, 10, day, hour, 0, 0).toISOString();
assert.equal(u.dayLabel(at(16, 9), noon), "Today");
assert.equal(u.dayLabel(at(15, 23), noon), "Yesterday");
assert.equal(u.dayLabel(at(12, 10), noon), "Thursday 12 November");
const days = u.groupByDay([{ id: 1, sort_at: at(16, 11) }, { id: 2, sort_at: at(16, 8) }, { id: 3, sort_at: at(15, 20) }, { id: 4, sort_at: at(12, 9) }], noon);
assert.deepEqual(days.map((g) => [g.label, g.items.map((x) => x.id)]), [["Today", [1, 2]], ["Yesterday", [3]], ["Thursday 12 November", [4]]]);
assert.deepEqual(u.groupByDay([], noon), []);

// The masthead sentence: counts as text, the nearest deadline as a link, others counted.
const soon = (id, days, name) => ({ item_id: id, days_left: days, programme: name });
assert.deepEqual(u.standfirst(1964, 6, []), { count: "1,964", noun: "new items", chosen: 6, link: null });
assert.equal(u.standfirst(1, 6, []).noun, "new item");
assert.deepEqual(u.standfirst(5, 3, [soon(1, 2, "Venture Vault"), soon(2, 40, "Later")]).link, { id: 1, label: "Venture Vault", tail: "closes in 2 days." });
assert.equal(u.standfirst(5, 3, [soon(1, 3, "B"), soon(2, 0, "A")]).link.tail, "closes today, and one more deadline falls in the next three days.");
assert.equal(u.standfirst(5, 3, [soon(1, 1, "A"), soon(2, 2, "B"), soon(3, 3, "C")]).link.tail, "closes tomorrow, and 2 more deadlines fall in the next three days.");
assert.equal(u.standfirst(5, 3, [soon(1, null, "No date"), soon(2, -1, "Closed")]).link, null);
console.log("ok");
"""


@pytest.mark.skipif(NODE is None, reason="Node.js is not installed")
def test_the_web_apps_pure_logic(tmp_path):
    shutil.copy(STATIC_DIR / "js" / "util.js", tmp_path / "util.js")
    (tmp_path / "check.mjs").write_text(LOGIC_TEST, encoding="utf-8")
    (tmp_path / "package.json").write_text('{"type": "module"}', encoding="utf-8")
    result = subprocess.run([NODE, "check.mjs"], cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0 and result.stdout.strip() == "ok", result.stderr


@pytest.mark.skipif(NODE is None, reason="Node.js is not installed")
def test_every_script_is_valid_javascript():
    for script in sorted((STATIC_DIR / "js").glob("*.js")):
        result = subprocess.run([NODE, "--check", str(script)], capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, f"{script.name}: {result.stderr}"
