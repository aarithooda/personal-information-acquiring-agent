"""Benchmark v2 corpus construction: mechanical, identity-only, independent of everything v1 concluded."""

import ast
import builtins
import json
import pathlib
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fakes import T0, make_item

from benchmark_v2 import common as c
from benchmark_v2 import corpus as co
from benchmarks.common import BenchmarkError
from benchmarks.snapshot import ITEM_FIELDS, compute_snapshot_id, load_snapshot
from pia.db import connect, store_items
from pia.normalize import canonicalize_url

UTC = timezone.utc
NOW = datetime(2026, 9, 22, 9, 0, tzinfo=UTC)


# ---------- the frozen constants match the frozen document ----------


def test_the_methodology_states_exactly_the_constants_the_code_uses():
    text = (pathlib.Path(c.__file__).parent / "METHODOLOGY.md").read_text(encoding="utf-8")
    for needle in (
        "2026-07-19T00:00Z", "2026-09-17T00:00Z", "20 consecutive 3-day windows", f"random.Random({c.SAMPLE_SEED})", f"random.Random({c.REPEAT_SEED})",
        f"QUEUE_SEED = {c.QUEUE_SEED}", f"seed {c.BOOTSTRAP_SEED}", f"seed {c.PERMUTATION_SEED}", "5,000 resamples", "10,000 shuffles",
        "**160 items uniformly", "**40 items drawn uniformly", "K = 4, 10, 16, 32", "weighted kappa is below **0.40**", "SHOW = 2, MAYBE = 1, SKIP = 0",
    ):
        assert needle in text, needle
    assert (c.N_NEW, c.N_REPEAT, c.WINDOW_DAYS) == (160, 40, 3)
    assert (c.BOOTSTRAP_RESAMPLES, c.PERMUTATION_SHUFFLES, c.KAPPA_FLOOR, c.KS) == (5000, 10000, 0.40, (4, 10, 16, 32))
    assert c.BENCHMARK_VERSION == "v2" and "Benchmark version: `v2`" in text


def test_the_period_is_sixty_days_ending_before_the_v1_intake_began():
    assert c.WINDOW_END - c.WINDOW_START == timedelta(days=60)
    assert c.WINDOW_END <= datetime(2026, 9, 17, 12, 0, tzinfo=UTC)  # v1 began collecting on 2026-09-17 evening


def test_the_period_splits_into_twenty_contiguous_three_day_windows():
    ws = co.windows(c.WINDOW_START, c.WINDOW_END, c.WINDOW_DAYS)
    assert len(ws) == 20 and ws[0][0] == c.WINDOW_START and ws[-1][1] == c.WINDOW_END
    assert all(a[1] == b[0] for a, b in zip(ws, ws[1:])) and all(w[1] - w[0] == timedelta(days=3) for w in ws)


# ---------- the historical variants of the two adapters that cannot take a past window ----------


def client_for(handler):
    seen = []

    def recording(request):
        seen.append(request)
        return handler(request)

    return httpx.Client(transport=httpx.MockTransport(recording)), seen


def gh_body(*repos):
    return json.dumps({"items": [{"id": i, "html_url": f"https://github.com/o/r{i}", "full_name": f"o/r{i}", "description": "d", "topics": ["ai"], "created_at": created, "stargazers_count": 60, "language": "Python"} for i, created in repos]})


def test_github_history_reuses_the_production_query_and_adds_only_the_upper_date_bound():
    from pia.sources.github import GitHubSource

    client, seen = client_for(lambda r: httpx.Response(200, text=gh_body((1, "2026-07-20T10:00:00Z"))))
    since, until = datetime(2026, 7, 19, tzinfo=UTC), datetime(2026, 7, 22, tzinfo=UTC)
    items = co.HistoricalGitHub(GitHubSource(query="llm OR agent OR ai", min_stars=50, max_results=30)).fetch(client, since, until)
    q = seen[0].url.params["q"]
    assert "llm OR agent OR ai" in q and "stars:>=50" in q and "created:2026-07-19T00:00:00Z..2026-07-21T23:59:59Z" in q
    assert seen[0].url.params["per_page"] == "30" and seen[0].url.params["sort"] == "stars"
    assert [i.source for i in items] == ["github"] and items[0].signals["stars"] == 60  # the production parser


def hf_body(*papers):
    return json.dumps([{"paper": {"id": pid, "title": f"T{pid}", "summary": "s", "publishedAt": "2026-07-19T00:00:00Z", "upvotes": up, "submittedOnDailyAt": featured}} for pid, up, featured in papers])


def test_hf_history_asks_one_day_at_a_time_and_applies_the_production_upvote_and_window_filters():
    from pia.sources.hf_papers import HFPapersSource

    def handler(request):
        day = request.url.params["date"]
        return httpx.Response(200, text=hf_body((f"2607.{day[-2:]}01", 50, f"{day}T05:00:00Z"), (f"2607.{day[-2:]}02", 3, f"{day}T05:00:00Z")))  # second is below min_upvotes

    client, seen = client_for(handler)
    since, until = datetime(2026, 7, 19, tzinfo=UTC), datetime(2026, 7, 22, tzinfo=UTC)
    items = co.HistoricalHFPapers(HFPapersSource(min_upvotes=10, max_results=100)).fetch(client, since, until)
    assert [r.url.params["date"] for r in seen] == ["2026-07-19", "2026-07-20", "2026-07-21"]
    assert len(items) == 3 and all(i.source == "hf_papers" and i.signals["upvotes"] >= 10 for i in items)


def test_hf_papers_featured_outside_the_window_are_dropped():
    from pia.sources.hf_papers import HFPapersSource

    client, _ = client_for(lambda r: httpx.Response(200, text=hf_body(("2607.00001", 50, "2026-07-30T05:00:00Z"))))
    items = co.HistoricalHFPapers(HFPapersSource()).fetch(client, datetime(2026, 7, 19, tzinfo=UTC), datetime(2026, 7, 20, tzinfo=UTC))
    assert items == []


def test_the_variants_are_used_only_for_github_and_hf_and_everything_else_is_the_production_adapter():
    from pia.sources.arxiv import ArxivSource
    from pia.sources.github import GitHubSource
    from pia.sources.hackernews import HackerNewsSource
    from pia.sources.hf_papers import HFPapersSource
    from pia.sources.rss import RssSource

    arxiv, hn, rss = ArxivSource(categories=["cs.AI"]), HackerNewsSource(), RssSource(name="x", url="https://x/feed")
    out = co.replayable([arxiv, HFPapersSource(), hn, GitHubSource(), rss])
    assert out[0] is arxiv and out[2] is hn and out[4] is rss
    assert isinstance(out[1], co.HistoricalHFPapers) and isinstance(out[3], co.HistoricalGitHub)
    assert [s.name for s in out] == ["arxiv", "hf_papers", "hn", "github", "x"]


# ---------- collecting the pool ----------


class Scripted:
    """A source that returns pre-set items per window index (call order), or raises."""

    def __init__(self, name, per_call, error_on=None):
        self.name, self.per_call, self.error_on, self.calls = name, per_call, error_on, []

    def fetch(self, client, since, until):
        self.calls.append((since, until))
        if self.error_on is not None and len(self.calls) - 1 == self.error_on:
            from pia.http import SourceError

            raise SourceError("boom")
        return self.per_call[len(self.calls) - 1] if len(self.calls) - 1 < len(self.per_call) else []


def item(source, n, published=None, **kw):
    return make_item(source, n, published or datetime(2026, 8, 1, tzinfo=UTC), **kw)


def test_the_pool_is_collected_window_by_window_in_order_with_every_source_and_stored_by_production_code():
    ws = co.windows(datetime(2026, 8, 1, tzinfo=UTC), datetime(2026, 8, 7, tzinfo=UTC), 3)
    a = Scripted("arxiv", [[item("arxiv", 1)], [item("arxiv", 2)]])
    b = Scripted("hn", [[item("hn", 3)], []])
    conn = connect(":memory:")
    report = co.collect_pool(conn, None, [a, b], ws, NOW, pause=lambda s: None)
    assert a.calls == ws and b.calls == ws
    assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 3
    assert report.fetches == {("arxiv", 0): 1, ("arxiv", 1): 1, ("hn", 0): 1, ("hn", 1): 0}
    assert report.first_window[canonicalize_url("https://example.com/arxiv/2")] == 1


def test_a_failed_fetch_stops_the_build_instead_of_shrinking_the_pool():
    ws = co.windows(datetime(2026, 8, 1, tzinfo=UTC), datetime(2026, 8, 7, tzinfo=UTC), 3)
    with pytest.raises(BenchmarkError, match="hn.*window 1"):
        co.collect_pool(connect(":memory:"), None, [Scripted("hn", [[], []], error_on=1)], ws, NOW, pause=lambda s: None)


def test_the_same_item_from_two_sources_is_one_item_with_both_sources_signals():
    ws = co.windows(datetime(2026, 8, 1, tzinfo=UTC), datetime(2026, 8, 4, tzinfo=UTC), 3)
    shared = "https://example.com/shared"
    one = make_item("arxiv", 1, None, signals={"categories": ["cs.AI"]})
    two = make_item("hn", 2, None, signals={"points": 200})
    one, two = one.model_copy(update={"url": shared}), two.model_copy(update={"url": shared})
    conn = connect(":memory:")
    co.collect_pool(conn, None, [Scripted("arxiv", [[one]]), Scripted("hn", [[two]])], ws, NOW, pause=lambda s: None)
    rows = conn.execute("SELECT source, signals FROM items").fetchall()
    assert len(rows) == 1 and rows[0]["source"] == "arxiv" and set(json.loads(rows[0]["signals"])) == {"arxiv", "hn"}


# ---------- exclusion: identity only ----------


def v1_items(n=5):
    return [
        {"id": 100 + i, "source": "hn", "external_id": f"v1-{i}", "canonical_url": f"https://v1.example/{i}", "url": f"https://v1.example/{i}", "title": f"V1 title {i}",
         "content_raw": None if i % 2 else "v1 text", "published_at": None, "discovered_at": "2026-09-20T00:00:00+00:00", "signals": {"hn": {"points": 150}}}
        for i in range(n)
    ]


def write_v1(tmp_path, items):
    path = tmp_path / "v1_items.json"
    path.write_text(json.dumps({"format": 1, "snapshot_id": compute_snapshot_id(items), "exported_at": "x", "items": items}), encoding="utf-8")
    return path


def test_v1_identities_are_read_from_the_facts_only_snapshot(tmp_path):
    index = co.v1_identities(write_v1(tmp_path, v1_items(3)))
    assert index.canonical == {"https://v1.example/0", "https://v1.example/1", "https://v1.example/2"} and ("hn", "v1-1") in index.external


def pool_with(*items):
    conn = connect(":memory:")
    store_items(conn, list(items), NOW)
    return conn


def test_only_identity_excludes_an_item_and_every_exclusion_is_counted(tmp_path):
    index = co.v1_identities(write_v1(tmp_path, v1_items(3)))
    clash_url = make_item("arxiv", 1, None).model_copy(update={"url": "https://v1.example/1?utm_source=x"})  # same canonical URL as a v1 item
    clash_id = make_item("hn", 2, None).model_copy(update={"external_id": "v1-2"})  # same (source, external_id) as a v1 item
    fine = item("hn", 3, content="Whatever the text says, an interesting-looking item is not favoured")
    boring = item("hn", 4, content="x")
    rows, excluded = co.eligible_pool(pool_with(clash_url, clash_id, fine, boring), index)
    assert {r["external_id"] for r in rows} == {"3", "4"} and excluded == {"E1_canonical_url_in_v1": 1, "E2_source_external_id_in_v1": 1}


def test_items_in_the_real_database_are_excluded_too(tmp_path):
    db = tmp_path / "pia.db"
    conn = connect(db)
    store_items(conn, [item("hn", 9)], NOW)
    conn.close()
    index = co.v1_identities(write_v1(tmp_path, v1_items(1)), db_path=db)
    rows, excluded = co.eligible_pool(pool_with(item("hn", 9), item("hn", 10)), index)
    assert [r["external_id"] for r in rows] == ["10"] and excluded["E1_canonical_url_in_v1"] == 1


# ---------- sampling ----------


def make_rows(n, prefix="https://p.example/"):
    conn = connect(":memory:")
    store_items(conn, [make_item("hn", i, None).model_copy(update={"url": f"{prefix}{i}"}) for i in range(n)], NOW)
    return conn.execute("SELECT * FROM items").fetchall()


def test_the_sample_is_a_seeded_simple_random_sample_without_replacement():
    rows = make_rows(50)
    a, b = co.sample_new(rows, 10, seed=1), co.sample_new(rows, 10, seed=1)
    assert [r["canonical_url"] for r in a] == [r["canonical_url"] for r in b] and len({r["canonical_url"] for r in a}) == 10
    assert [r["canonical_url"] for r in a] != [r["canonical_url"] for r in co.sample_new(rows, 10, seed=2)]


def test_which_items_are_drawn_depends_on_identity_alone_never_on_titles_text_or_signals():
    rows = make_rows(50)
    other = connect(":memory:")
    store_items(other, [make_item("hn", i, None, content="Completely different, very exciting text", signals={"points": 9999}).model_copy(update={"url": f"https://p.example/{i}", "title": f"Different title {i}"}) for i in range(50)], NOW)
    assert [r["canonical_url"] for r in co.sample_new(rows, 10, seed=7)] == [r["canonical_url"] for r in co.sample_new(other.execute("SELECT * FROM items").fetchall(), 10, seed=7)]


def test_too_small_a_pool_is_an_error_not_a_smaller_corpus():
    with pytest.raises(BenchmarkError, match="only 5 eligible"):
        co.sample_new(make_rows(5), 10, seed=1)


def test_the_repeats_are_drawn_from_the_v1_ids_alone():
    items = v1_items(30)
    a = co.draw_repeats(items, 8, seed=3)
    assert len(a) == 8 and {i["id"] for i in a} <= {i["id"] for i in items}
    assert [i["id"] for i in a] == [i["id"] for i in co.draw_repeats(items, 8, seed=3)]
    assert [i["id"] for i in a] == [i["id"] for i in co.draw_repeats([{**i, "title": "changed", "content_raw": "changed"} for i in items], 8, seed=3)]


# ---------- assembling and freezing the corpus ----------


def build(tmp_path, n_pool=60, n_new=12, n_repeat=4):
    v1 = v1_items(30)
    v1_path = write_v1(tmp_path, v1)
    conn = connect(":memory:")
    store_items(conn, [make_item("hn", i, None, content=None if i % 3 else "text").model_copy(update={"url": f"https://new.example/{i}"}) for i in range(n_pool)], NOW)
    return co.build_corpus(conn, v1_path, first_window={}, n_new=n_new, n_repeat=n_repeat, now=NOW, collected={"note": "test"}), v1


def test_the_corpus_is_new_plus_repeats_with_unique_neutral_ids(tmp_path):
    corpus, v1 = build(tmp_path)
    assert len(corpus.items) == 16 and sorted(i["id"] for i in corpus.items) == list(range(1, 17))
    origins = [corpus.manifest["origin_by_id"][str(i["id"])] for i in corpus.items]
    assert origins.count("new") == 12 and origins.count("repeat") == 4
    assert origins != sorted(origins)  # ids are not grouped by origin


def test_every_corpus_item_has_exactly_the_v1_item_fields_so_nothing_reveals_where_it_came_from(tmp_path):
    corpus, _ = build(tmp_path)
    assert all(set(i) == set(ITEM_FIELDS) for i in corpus.items)


def test_a_repeat_is_the_v1_item_exactly_as_frozen_with_only_its_id_changed(tmp_path):
    corpus, v1 = build(tmp_path)
    by_url = {i["canonical_url"]: i for i in v1}
    repeats = [i for i in corpus.items if i["canonical_url"] in by_url]
    assert len(repeats) == 4
    for r in repeats:
        assert {k: v for k, v in r.items() if k != "id"} == {k: v for k, v in by_url[r["canonical_url"]].items() if k != "id"}
    assert {m["v1_item_id"] for m in corpus.manifest["repeats"]} == {by_url[r["canonical_url"]]["id"] for r in repeats}


def test_all_new_items_are_absent_from_v1_by_identity(tmp_path):
    corpus, v1 = build(tmp_path)
    new_urls = {m["canonical_url"] for m in corpus.manifest["new"]}
    assert new_urls and not new_urls & {i["canonical_url"] for i in v1}
    assert corpus.manifest["absence_check"] == {"new_items": 12, "canonical_url_overlap": 0, "source_external_id_overlap": 0, "passed": True}


def test_the_absence_check_fails_loudly_on_any_overlap():
    idx = co.V1Index(canonical={"https://a"}, external={("hn", "1")})
    with pytest.raises(BenchmarkError, match="present in the v1 corpus"):
        co.assert_absent([{"canonical_url": "https://a", "source": "hn", "external_id": "9"}], idx)
    with pytest.raises(BenchmarkError, match="present in the v1 corpus"):
        co.assert_absent([{"canonical_url": "https://z", "source": "hn", "external_id": "1"}], idx)


def test_the_written_snapshot_loads_with_the_v1_loader_and_is_hash_checked(tmp_path):
    corpus, _ = build(tmp_path)
    data = tmp_path / "data"
    co.write_corpus(corpus, data)
    snap = load_snapshot(data / "items.json")
    assert snap.snapshot_id == corpus.corpus_id and len(snap.items) == 16
    raw = json.loads((data / "items.json").read_text(encoding="utf-8"))
    raw["items"][0]["title"] += "!"
    (data / "items.json").write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(BenchmarkError, match="changed since it was frozen"):
        load_snapshot(data / "items.json")


def test_the_corpus_is_written_once(tmp_path):
    corpus, _ = build(tmp_path)
    co.write_corpus(corpus, tmp_path / "data")
    with pytest.raises(BenchmarkError, match="already exists"):
        co.write_corpus(corpus, tmp_path / "data")


def test_the_manifest_records_repeat_identities_internally_and_the_snapshot_file_does_not(tmp_path):
    corpus, _ = build(tmp_path)
    co.write_corpus(corpus, tmp_path / "data")
    snapshot_text = (tmp_path / "data" / "items.json").read_text(encoding="utf-8")
    assert "repeat" not in snapshot_text and "origin" not in snapshot_text and "v1_item_id" not in snapshot_text
    assert "repeats" in json.loads((tmp_path / "data" / "manifest.json").read_text(encoding="utf-8"))


# ---------- hygiene: construction is independent of v1's labels and results, and of any model ----------

PACKAGE = pathlib.Path(c.__file__).parent


def imports_of(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
    return names


def test_the_corpus_builder_imports_no_label_arm_report_or_model_code():
    forbidden = {"benchmarks.labelset", "benchmarks.arms", "benchmarks.analysis", "benchmarks.labelling", "pia.jev", "pia.jev.triage", "pia.jev.design", "pia.jev.client", "pia.llm.enrich", "pia.llm.client"}
    for module in ("corpus.py", "common.py", "freeze.py"):
        assert not (imports_of(PACKAGE / module) & forbidden), module


def test_building_a_corpus_never_opens_a_label_arm_or_report_file(tmp_path, monkeypatch):
    v1 = write_v1(tmp_path, v1_items(30))
    (tmp_path / "labels_frozen.json").write_text("{}")
    real_open, touched = builtins.open, []

    def guarded(file, *a, **k):
        touched.append(str(file))
        return real_open(file, *a, **k)

    monkeypatch.setattr(builtins, "open", guarded)
    real_read = pathlib.Path.read_text
    monkeypatch.setattr(pathlib.Path, "read_text", lambda self, *a, **k: (touched.append(str(self)), real_read(self, *a, **k))[1])
    conn = connect(":memory:")
    store_items(conn, [make_item("hn", i, None).model_copy(update={"url": f"https://new.example/{i}"}) for i in range(30)], NOW)
    co.build_corpus(conn, v1, first_window={}, n_new=8, n_repeat=3, now=NOW, collected={})
    assert not [p for p in touched if any(w in p.lower() for w in ("label", "arms", "report", "scratch"))], touched
