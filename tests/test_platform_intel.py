"""The intelligence pipeline: classification, scoring and rank, model assessment, briefs, opportunity
extraction, related items and learning from behaviour."""

import json
from datetime import date, timedelta

import pytest

from pia.intel import learn, opportunities, relate
from pia.intel.assess import MAX_ATTEMPTS, assess_pending
from pia.intel.brief import load_brief, section_guide, write_brief
from pia.intel.classify import TopicMatcher, classify_new, detect_kind, pick_domains
from pia.intel.score import RANK_FLOOR, compute, rank_value, refresh_rank, rescore
from pia.llm.client import LLMBadOutput, LLMUnavailable
from pia.store import db, queries
from tests.platform_helpers import NOW, ScriptedLLM, add_item, add_source, assess_everything, item, lexicon, make_db, topic_id

MODEL = "test-model"


def domains_of(conn, item_id):
    return [row[0] for row in conn.execute("SELECT d.slug FROM item_domains x JOIN domains d ON d.id = x.domain_id WHERE x.item_id = ? ORDER BY x.weight DESC", (item_id,))]


def topics_of(conn, item_id):
    return {row[0]: row[1] for row in conn.execute("SELECT t.name, it.weight FROM item_topics it JOIN topics t ON t.id = it.topic_id WHERE it.item_id = ?", (item_id,))}


# ---------- classification ----------


def test_a_focused_source_vouches_for_its_space_and_a_mixed_source_is_sorted_by_vocabulary():
    conn = make_db()
    maths_blog = add_source(conn, "Maths blog", domains=("mathematics",))
    aggregator = add_source(conn, "Aggregator", general=True, domains=("software",), url="https://agg.example/feed")
    plain = add_item(conn, "Weekly notes", source=maths_blog)
    sorted_by_words = add_item(conn, "A new proof of a conjecture in number theory", source=aggregator)
    fallback = add_item(conn, "Something entirely unrelated", source=aggregator)
    assert domains_of(conn, plain) == ["mathematics"]
    assert domains_of(conn, sorted_by_words)[0] == "mathematics"
    assert domains_of(conn, fallback) == ["software"]  # nothing matched: the source's own space, so the item stays reachable


def test_arxiv_categories_decide_the_space_and_papers_are_papers():
    conn = make_db()
    arxiv = add_source(conn, "arXiv", "arxiv", domains=("ai",), url=None)
    paper = add_item(conn, "On primes", source=arxiv, url="https://arxiv.org/abs/2611.00001", signals={"categories": ["math.NT", "cs.LG"]})
    assert domains_of(conn, paper)[0] in ("mathematics", "ai") and set(domains_of(conn, paper)) == {"mathematics", "ai"}
    assert item(conn, paper)["kind"] == "paper"


def test_pick_domains_always_returns_at_least_one_space():
    assert pick_domains({}, ["software"]) == [("software", 0.3)]
    assert pick_domains({"ai": 0.1}, []) == [("science-tech", 0.3)]
    assert [slug for slug, _ in pick_domains({"ai": 1.2, "mathematics": 0.7, "software": 0.2}, [])] == ["ai", "mathematics"]


@pytest.mark.parametrize("title, source_kind, purpose, hint, expected", [
    ("Anything", "arxiv", "news", None, "paper"), ("owner/repo", "github", "news", None, "repo"), ("Summer Internship 2027", "rss", "news", None, "opportunity"),
    ("Applications open for the fellows programme", "rss", "news", None, "opportunity"), ("Staff Engineer", "json_api", "opportunities", None, "opportunity"),
    ("Show HN: my thing", "hn", "news", None, "post"), ("Announcing Rust 1.90", "rss", "news", None, "release"), ("Notes on a quiet week", "rss", "news", None, "article"),
    ("Notes", "rss", "news", "opportunity", "opportunity"), ("Timetable", "gmail", "news", None, "announcement"),
])
def test_the_kind_of_item_is_read_from_its_source_and_title(title, source_kind, purpose, hint, expected):
    assert detect_kind(hint=hint, source_kind=source_kind, purpose=purpose, title=title, url="https://x.example/a", primary_domain="ai", opportunity_domains=set()) == expected


def test_topics_match_whole_phrases_and_short_acronyms_only_in_capitals():
    conn = make_db()
    matcher = TopicMatcher.load(conn)
    names = lambda found: {conn.execute("SELECT name FROM topics WHERE id = ?", (i,)).fetchone()[0]: w for i, w in found.items()}  # noqa: E731
    assert names(matcher.match("RAG pipelines in production", "")) == {"Retrieval-augmented generation": 1.0}
    assert names(matcher.match("A rag and bone story about storage", "we drag things")) == {}  # "rag" the word is not RAG the technique
    assert names(matcher.match("Notes", "Fine-tuning LLMs on a budget")) == {"Large language models": 0.6}  # body-only match counts less
    assert names(matcher.match("Rustic furniture", "")) == {}  # "rust" inside another word is not Rust


def test_a_swarm_of_identically_named_repositories_is_flagged_and_kept_off_home():
    conn = make_db()
    github = add_source(conn, "GitHub", "github", general=True, domains=("software",), url=None, trust=0.55)
    clones = [add_item(conn, f"owner{n}/Free-LLM-App", source=github, url=f"https://github.com/owner{n}/Free-LLM-App", signals={"stars": 900}) for n in range(3)]
    real = add_item(conn, "someone/a-real-llm-project", source=github, url="https://github.com/someone/a-real-llm-project", signals={"stars": 900})
    rescore(conn, NOW)
    assert all(json.loads(item(conn, c)["signals"])["clone_swarm"] == 3 and item(conn, c)["muted"] for c in clones)
    assert not item(conn, real)["muted"] and item(conn, real)["importance"] > 2 * item(conn, clones[0])["importance"]
    assert [entry["id"] for entry in queries.featured(conn, NOW)] == [real]
    assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 4  # flagged, not deleted


# ---------- scoring ----------


def score(**overrides):
    base = dict(kind="article", signals={}, model_importance=None, fit=None, sightings=1, trust=0.7, quality=0.0, general=False,
                domain_weight=1.0, domain_affinity=0.0, topics=[], popularity=0.5)
    return compute(**{**base, **overrides})


def test_relevance_follows_the_owners_levels_and_importance_follows_the_world():
    priority, some, none, disliked = (score(topics=[(level, 0.0, 1.0)]).relevance for level in (3, 1, None, -1))
    assert priority > some > none > disliked
    assert score(topics=[(2, 0.0, 1.0)]).relevance > score(topics=[(2, 0.0, 0.6)]).relevance  # a title match beats a body-only one
    assert score(domain_weight=1.5).relevance > score(domain_weight=1.0).relevance > score(domain_weight=0.0).relevance
    assert score(model_importance=1.0).importance > score(model_importance=0.5).importance > score(model_importance=0.0).importance
    assert score(sightings=3).importance > score(sightings=1).importance and score(trust=0.9).importance > score(trust=0.3).importance
    assert score(quality=-1.0).importance < score().importance
    for result in (score(model_importance=1.0, fit=1.0, topics=[(3, 1.0, 1.0)] * 4, domain_weight=1.5, popularity=1.0, sightings=5), score(topics=[(-1, -1.0, 1.0)], domain_weight=0.0, popularity=0.0, trust=0.0)):
        assert 0 <= result.importance <= 1 and 0 <= result.relevance <= 1 and 0 <= result.score <= 1


def test_learned_affinity_moves_relevance_but_less_than_an_explicit_level():
    assert score(topics=[(None, 0.8, 1.0)]).relevance > score(topics=[(None, 0.0, 1.0)]).relevance > score(topics=[(None, -0.8, 1.0)]).relevance
    assert score(topics=[(2, -1.0, 1.0)]).relevance > score(topics=[(None, 1.0, 1.0)]).relevance  # what the owner said outranks what was inferred


def test_a_muted_topic_keeps_an_item_off_home_unless_something_highly_rated_is_also_present():
    muted = score(topics=[(-2, 0.0, 1.0), (1, 0.0, 1.0)])
    rescued = score(topics=[(-2, 0.0, 1.0), (3, 0.0, 1.0)])
    assert muted.muted and muted.relevance < 0.15 and not rescued.muted and rescued.relevance > 0.7


def test_a_personal_fit_judgement_is_blended_in_not_substituted():
    base = score(topics=[(1, 0.0, 1.0)]).relevance
    assert base < score(topics=[(1, 0.0, 1.0)], fit=1.0).relevance < 1.0 and score(topics=[(1, 0.0, 1.0)], fit=0.0).relevance < base


def test_opportunities_do_not_sink_and_first_year_eligibility_raises_them():
    assert score(kind="opportunity", trust=0.1, popularity=0.0).importance >= 0.45
    assert score(kind="opportunity", signals={"first_year": "yes"}).relevance > score(kind="opportunity", signals={"first_year": "no"}).relevance


def test_rank_fades_with_age_but_never_below_a_floor_and_a_later_development_lifts_it():
    fresh = rank_value(0.8, db.iso(NOW), 0.0, None, NOW)
    three_weeks = rank_value(0.8, db.iso(NOW - timedelta(days=21)), 0.0, None, NOW)
    ancient = rank_value(0.8, db.iso(NOW - timedelta(days=900)), 0.0, None, NOW)
    assert fresh == 0.8 and fresh > three_weeks > ancient >= round(0.8 * RANK_FLOOR, 4) - 0.001
    boosted = rank_value(0.8, db.iso(NOW - timedelta(days=21)), 0.2, db.iso(NOW), NOW)
    assert boosted > three_weeks and rank_value(0.8, db.iso(NOW - timedelta(days=21)), 0.2, db.iso(NOW - timedelta(days=60)), NOW) < boosted  # the lift itself fades


def test_changing_a_level_reranks_existing_items_without_any_model():
    conn = make_db()
    agents = add_item(conn, "AI agents in practice")
    primes = add_item(conn, "Thoughts on prime numbers", source=add_source(conn, "M", domains=("mathematics",), url="https://m.example/f"))
    before = item(conn, primes)["relevance"]
    learn.set_topic_level(conn, topic_id(conn, "Number theory"), 3, NOW)
    learn.set_topic_level(conn, topic_id(conn, "AI agents"), -2, NOW)
    learn.apply_profile_change(conn, NOW)
    assert item(conn, primes)["relevance"] > before and item(conn, agents)["muted"] == 1
    ranked = [entry["id"] for entry in queries.list_items(conn, view="important")["items"]]
    assert ranked.index(primes) < ranked.index(agents)


# ---------- model assessment ----------


def test_assessment_fills_significance_summary_topics_and_keeps_the_raw_answer():
    conn = make_db()
    hn = add_source(conn, "HN", "hn", general=True, domains=("software",), url=None)
    with_text = add_item(conn, "A thing happened", source=hn, content="Some real text about the thing.")
    title_only = add_item(conn, "Another thing", source=hn)
    llm = ScriptedLLM(assess_everything(significance=4, domain="mathematics", kind="news", topics=["sieve methods", "x"], entities=["Clay Institute"], summary="  It   happened.  "))
    report = assess_pending(conn, llm, MODEL, NOW)
    assert (report.assessed, report.failed_items, report.remaining) == (2, 0, 0)
    row = item(conn, with_text)
    assert (row["stage"], row["model_importance"], row["summary"], row["kind"]) == ("assessed", 0.75, "It happened.", "news")
    assert domains_of(conn, with_text)[0] == "mathematics"  # a mixed source's guess is replaced by the model's reading
    assert item(conn, title_only)["summary"] == ""  # no text, no summary: nothing is invented from a headline
    assert set(topics_of(conn, with_text)) == {"sieve methods", "Clay Institute"}  # "x" is too short to be a topic
    discovered = conn.execute("SELECT kind, origin, level FROM topics WHERE name = 'Clay Institute'").fetchone()
    assert tuple(discovered) == ("org", "discovered", None)
    stored = json.loads(conn.execute("SELECT payload FROM judgements WHERE item_id = ? AND task = 'assess'", (with_text,)).fetchone()[0])
    assert stored["significance"] == 4 and llm.calls[0]["schema"]["properties"]["results"]["items"]["properties"]["domain"]["enum"][-1] == "none"


def test_a_focused_sources_space_and_an_adapters_kind_outrank_the_models_reading():
    conn = make_db()
    paper = add_item(conn, "A paper", source=add_source(conn, "arXiv", "arxiv", domains=("ai",), url=None), url="https://arxiv.org/abs/2611.00002", content="Abstract.")
    assess_pending(conn, ScriptedLLM(assess_everything(domain="mathematics", kind="article")), MODEL, NOW)
    assert item(conn, paper)["kind"] == "paper" and domains_of(conn, paper)[0] == "ai" and "mathematics" in domains_of(conn, paper)


def test_unusable_output_is_isolated_to_the_item_that_caused_it():
    conn = make_db()
    ids = [add_item(conn, f"Item {n}", content="text") for n in range(4)]
    conn.execute("UPDATE items SET title = 'POISON' WHERE id = ?", (ids[2],))
    conn.commit()

    def respond(request):
        if "POISON" in request["user"]:
            raise LLMBadOutput("cut off")
        return assess_everything()(request)

    llm = ScriptedLLM(respond)
    report = assess_pending(conn, llm, MODEL, NOW)
    assert report.assessed == 3 and report.failed_items == 1
    assert item(conn, ids[2])["stage"] == "classified" and item(conn, ids[2])["assess_attempts"] == 1
    for _ in range(MAX_ATTEMPTS):
        assess_pending(conn, llm, MODEL, NOW)
    calls = len(llm.calls)
    assess_pending(conn, llm, MODEL, NOW)
    assert len(llm.calls) == calls  # given up on: an item the model can never handle is not retried for ever


def test_an_outage_stops_the_run_and_is_never_held_against_items():
    conn = make_db()
    ids = [add_item(conn, f"Item {n}", content="text") for n in range(25)]
    llm = ScriptedLLM(LLMUnavailable("503"))
    report = assess_pending(conn, llm, MODEL, NOW, batch_size=10)
    assert report.stopped_early and len(llm.calls) == 2 and report.assessed == 0  # circuit breaker after two failed batches
    assert all(item(conn, i)["assess_attempts"] == 0 and item(conn, i)["stage"] == "classified" for i in ids)


def test_entries_the_model_gets_wrong_are_dropped_not_trusted():
    conn = make_db()
    good, bad = add_item(conn, "Good", content="t"), add_item(conn, "Bad", content="t")
    wrong = {"results": [{"index": 0, "domain": "ai", "kind": "article", "significance": 3, "topics": [], "entities": [], "summary": "ok"},
                         {"index": 1, "domain": "astrology", "kind": "article", "significance": 9, "topics": [], "entities": [], "summary": "x"},
                         {"index": 7, "domain": "ai", "kind": "article", "significance": 3, "topics": [], "entities": [], "summary": "x"}]}
    # pending() orders by score then recency; both items score alike, so map by title
    order = [row["title"] for row in __import__("pia.intel.assess", fromlist=["pending"]).pending(conn, 10)]
    assess_pending(conn, ScriptedLLM(wrong), MODEL, NOW)
    first, second = (good, bad) if order[0] == "Good" else (bad, good)
    assert item(conn, first)["stage"] == "assessed" and item(conn, second)["stage"] == "classified"


# ---------- briefs ----------

BRIEF = {"summary": "It establishes a bound.", "why_it_matters": "It matters for theory.", "key_points": ["One.", " ", "Two."],
         "sections": [{"heading": "Method", "body": "They prove it."}, {"heading": "Empty", "body": "  "}]}


def test_a_brief_is_written_once_cached_and_shaped_for_the_kind_of_item():
    conn = make_db()
    paper = add_item(conn, "A paper on primes", content="We prove a bound on prime gaps. " * 20, kind="paper")
    llm = ScriptedLLM(BRIEF)
    first = write_brief(conn, llm, MODEL, paper, NOW)
    assert first["summary"] == "It establishes a bound." and first["key_points"] == ["One.", "Two."] and [s["heading"] for s in first["sections"]] == ["Method"]
    assert first["basis"] == "source_text" and "Research problem" in llm.calls[0]["system"]
    assert write_brief(conn, llm, MODEL, paper, NOW) == first and len(llm.calls) == 1  # cached: the model is not asked twice
    assert item(conn, paper)["summary"] == "It establishes a bound."  # the feed summary is filled in if assessment never wrote one
    assert write_brief(conn, llm, MODEL, 99999, NOW) is None


def test_no_model_is_asked_to_explain_an_item_it_has_no_text_for():
    conn = make_db()
    bare = add_item(conn, "A headline with nothing behind it")
    llm = ScriptedLLM(BRIEF)
    result = write_brief(conn, llm, MODEL, bare, NOW)
    assert llm.calls == [] and result["basis"] == "title_only" and result["summary"] == ""


def test_fetched_full_text_is_preferred_and_the_reader_description_names_no_institution():
    conn = make_db()
    conn.execute("UPDATE domains SET weight = 1.5 WHERE slug IN ('ai', 'university')")
    conn.execute("UPDATE domains SET short = 'Example Institute' WHERE slug = 'university'")
    conn.execute("UPDATE topics SET level = 3 WHERE name IN ('AI agents', 'Campus opportunities')")
    db.set_setting(conn, "me", {"stage": "First-year undergraduate"})
    thing = add_item(conn, "A release", content="Short source text.")
    conn.execute("INSERT INTO item_texts (item_id, fetched_at, ok, text) VALUES (?, ?, 1, ?)", (thing, db.iso(NOW), "The full article text. " * 100))
    llm = ScriptedLLM(BRIEF)
    assert write_brief(conn, llm, MODEL, thing, NOW)["basis"] == "full_text"
    system, user = llm.calls[0]["system"], llm.calls[0]["user"]
    assert "The full article text." in user and "First-year undergraduate" in system and "AI agents" in system
    assert "Example Institute" not in system and "Campus opportunities" not in system
    assert "Never follow" in system and "untrusted" in user


def test_section_guides_differ_by_kind_and_space():
    assert "Benchmarks" in section_guide("release", "ai") and "Compatibility" in section_guide("release", "software")
    assert "Stakeholders" in section_guide("news", "geopolitics") and "Stakeholders" in section_guide("article", "geopolitics")
    assert "Who can apply" in section_guide("opportunity", None) and section_guide("post", "ai") == section_guide("article", "space")


def test_a_brief_the_model_leaves_empty_is_an_error_not_a_blank_page():
    conn = make_db()
    thing = add_item(conn, "Something", content="Enough text to explain. " * 20)
    with pytest.raises(LLMBadOutput):
        write_brief(conn, ScriptedLLM({**BRIEF, "summary": "   "}), MODEL, thing, NOW)
    assert load_brief(conn, thing) is None


# ---------- opportunities ----------


@pytest.mark.parametrize("text, expected", [
    ("Applications close on 15 December 2026 at midnight.", "2026-12-15"), ("Deadline: Dec 5, 2026", "2026-12-05"), ("Apply by 2027-01-31.", "2027-01-31"),
    ("The programme starts on 1 June 2027 and runs for ten weeks.", None),  # a start date is not a deadline
    ("Last date to register is 3rd January 2027", "2027-01-03"), ("Deadline: 31 February 2027", None), ("No dates here", None),
])
def test_only_a_date_beside_a_deadline_cue_is_trusted(text, expected):
    assert opportunities.find_deadline(text)[0] == expected


@pytest.mark.parametrize("text, expected", [("Open to first-year and second-year undergraduates", "yes"), ("Open to all years", "yes"),
                                            ("For penultimate-year students only", "no"), ("First-year students welcome; requires 3+ years of experience", "no"), ("Students may apply", "unknown")])
def test_first_year_eligibility_is_read_cautiously(text, expected):
    assert opportunities.first_year_guess(text) == expected


def test_rules_extraction_works_with_no_model_and_api_supplied_fields_win():
    conn = make_db()
    board = add_source(conn, "Contests", "json_api", domains=("hackathons",), purpose="opportunities", url=None)
    contest = add_item(conn, "Round 900", source=board, kind="opportunity", content="Apply by 1 January 2027.", signals={"deadline": "2026-11-20", "organisation": "Codeforces", "opportunity_type": "competition"})
    assert opportunities.pending_extraction(conn) == [contest]
    assert opportunities.extract_opportunity(conn, contest, NOW) == "rules"
    row = conn.execute("SELECT * FROM opportunities WHERE item_id = ?", (contest,)).fetchone()
    assert (row["deadline"], row["organisation"], row["type"], row["extracted_by"], row["status"]) == ("2026-11-20", "Codeforces", "competition", "rules", "")
    assert opportunities.pending_extraction(conn) == [] and opportunities.extract_opportunity(conn, 424242, NOW) == "missing"


MODEL_FIELDS = {"organisation": "Acme Labs", "programme": "Summer Research Fellowship", "type": "fellowship", "eligibility": "Undergraduates in any year.",
                "year_requirements": "Open to all years", "first_year": "yes", "location": "Bengaluru", "mode": "On-site", "deadline": "2027-01-15",
                "deadline_text": "Applications close 15 January 2027", "start_date": "May 2027", "duration": "8 weeks", "qualifications": "Python", "summary": "A paid research fellowship."}


def test_model_extraction_fills_the_details_and_never_touches_the_owners_tracking():
    conn = make_db()
    posting = add_item(conn, "Summer Research Fellowship", kind="opportunity", content="Acme Labs invites undergraduates in any year to apply. " * 6)
    opportunities.extract_opportunity(conn, posting, NOW)
    assert opportunities.set_status(conn, posting, "planning", NOW, notes="Ask for a reference")
    assert opportunities.extract_opportunity(conn, posting, NOW, llm=ScriptedLLM(MODEL_FIELDS), model=MODEL) == "model"
    row = conn.execute("SELECT * FROM opportunities WHERE item_id = ?", (posting,)).fetchone()
    assert (row["deadline"], row["first_year"], row["duration"], row["extracted_by"]) == ("2027-01-15", "yes", "8 weeks", "model")
    assert (row["status"], row["notes"]) == ("planning", "Ask for a reference")  # theirs alone
    assert json.loads(item(conn, posting)["signals"])["first_year"] == "yes" and item(conn, posting)["summary"] == "A paid research fellowship."
    with pytest.raises(ValueError):
        opportunities.set_status(conn, posting, "maybe", NOW)


def test_a_model_cannot_override_a_deadline_the_api_supplied_and_bad_dates_are_dropped():
    conn = make_db()
    hack = add_item(conn, "Agents Hack", kind="opportunity", content="A hackathon about agents with prizes and mentors. " * 5, signals={"deadline": "2026-12-15"})
    opportunities.extract_opportunity(conn, hack, NOW, llm=ScriptedLLM({**MODEL_FIELDS, "deadline": "2030-01-01"}), model=MODEL)
    assert conn.execute("SELECT deadline FROM opportunities WHERE item_id = ?", (hack,)).fetchone()[0] == "2026-12-15"
    other = add_item(conn, "Vague programme", kind="opportunity", content="A programme with unclear dates and a long description. " * 5)
    opportunities.extract_opportunity(conn, other, NOW, llm=ScriptedLLM({**MODEL_FIELDS, "deadline": "sometime in spring", "type": "wizardry"}), model=MODEL)
    row = conn.execute("SELECT deadline, type FROM opportunities WHERE item_id = ?", (other,)).fetchone()
    assert row["deadline"] is None and row["type"] == "other"
    third = add_item(conn, "Flaky", kind="opportunity", content="Deadline: 4 March 2027. A long enough description of the programme to read. " * 3)
    assert opportunities.extract_opportunity(conn, third, NOW, llm=ScriptedLLM(LLMUnavailable("down")), model=MODEL) == "rules"  # degrades, never fails
    assert conn.execute("SELECT deadline FROM opportunities WHERE item_id = ?", (third,)).fetchone()[0] == "2027-03-04"


def test_a_model_writing_unknown_instead_of_leaving_a_field_empty_is_treated_as_empty():
    conn = make_db()
    job = add_item(conn, "Programme Associate", kind="opportunity", content="A role with few stated details, described at some length for the reader. " * 4)
    vague = {**MODEL_FIELDS, "eligibility": "unknown", "year_requirements": "Not specified.", "location": "N/A", "duration": "TBD", "mode": "unknown", "deadline": None, "deadline_text": "none"}
    opportunities.extract_opportunity(conn, job, NOW, llm=ScriptedLLM(vague), model=MODEL)
    row = conn.execute("SELECT eligibility, year_requirements, location, duration, mode, deadline_text, organisation FROM opportunities WHERE item_id = ?", (job,)).fetchone()
    assert row[:6] == ("", "", "", "", "", "") and row["organisation"] == "Acme Labs"


def test_expired_is_derived_from_the_date_and_never_shown_as_open():
    conn = make_db()
    today = NOW.date()
    for title, deadline in (("Past", today - timedelta(days=1)), ("Today", today), ("Soon", today + timedelta(days=5)), ("Later", today + timedelta(days=40)), ("Undated", None)):
        item_id = add_item(conn, title, kind="opportunity", signals={"deadline": deadline.isoformat()} if deadline else {})
        opportunities.extract_opportunity(conn, item_id, NOW)
    states = {o["title"]: o["state"] for o in queries.opportunities(conn, today, show="all")}
    assert states == {"Past": "expired", "Today": "closing", "Soon": "closing", "Later": "open", "Undated": "undated"}
    assert [o["title"] for o in queries.opportunities(conn, today, show="open")] == ["Today", "Soon", "Later", "Undated"]  # by deadline, undated last
    assert [o["title"] for o in queries.opportunities(conn, today, show="expired")] == ["Past"]
    assert [o["title"] for o in queries.opportunities(conn, today + timedelta(days=10), show="open")] == ["Later", "Undated"]  # time passes, no update needed
    assert opportunities.display_state({"deadline": "2026-11-14"}, date(2026, 11, 15)) == "expired"


# ---------- related items ----------


def test_related_items_same_stories_and_citations_are_linked_and_lift_the_earlier_item():
    conn = make_db()
    conn.execute("UPDATE topics SET item_count = 0")
    paper = add_item(conn, "Sieve bounds for prime gaps", url="https://arxiv.org/abs/2610.12345", published=NOW - timedelta(days=20), acquired=NOW - timedelta(days=20))
    story = add_item(conn, "Acme releases the Falcon language model", url="https://acme.example/falcon")
    retelling = add_item(conn, "Acme releases Falcon, a language model", url="https://news.example/falcon", content="Acme has released Falcon.")
    follow_up = add_item(conn, "A sharper sieve", url="https://blog.example/sharper", content="This improves on https://arxiv.org/abs/2610.12345v2 considerably.")
    unrelated = add_item(conn, "Gardening tips for autumn", url="https://garden.example/a")
    relate.relate_pending(conn, NOW)
    links = {(row["item_id"], row["other_id"]): row["kind"] for row in conn.execute("SELECT * FROM relations")}
    assert links[(retelling, story)] == "same_story" and links[(follow_up, paper)] == "references"
    assert not any(unrelated in pair for pair in links)
    assert item(conn, paper)["context_boost"] > 0 and item(conn, story)["context_boost"] == 0  # the same story twice is not a development
    assert [r["direction"] for r in relate.related_for(conn, paper)] == ["later"] and relate.story_ids(conn, story) == {story, retelling}
    refresh_rank(conn, NOW)
    assert queries.feed_item(conn.execute(f"SELECT {queries.FEED_COLUMNS} FROM items i LEFT JOIN domains d ON d.id = i.domain_id WHERE i.id = ?", (paper,)).fetchone())["resurfaced"]
    assert relate.relate_pending(conn, NOW) == 0  # done once per item


# ---------- learning ----------


def test_state_changes_are_idempotent_and_each_teaches_once():
    conn = make_db()
    thing = add_item(conn, "AI agents in practice")
    agents = topic_id(conn, "AI agents")
    affinity = lambda: conn.execute("SELECT affinity FROM topics WHERE id = ?", (agents,)).fetchone()[0]  # noqa: E731
    state = learn.set_item_state(conn, thing, NOW, saved=True)
    assert state["saved_at"] and state["read_at"]  # saving something means it has been seen
    once = affinity()
    learn.set_item_state(conn, thing, NOW, saved=True)
    assert affinity() == once > 0 and conn.execute("SELECT COUNT(*) FROM events WHERE kind = 'save'").fetchone()[0] == 1
    learn.set_item_state(conn, thing, NOW, saved=False)
    assert 0 < affinity() < once and item(conn, thing)["saved_at"] is None
    assert learn.set_item_state(conn, 99999, NOW, read=True) is None


def test_reading_teaches_little_dismissing_a_little_and_being_unread_teaches_nothing():
    conn = make_db()
    read, dismissed, ignored = (add_item(conn, f"LLM note {n}") for n in range(3))
    llm = topic_id(conn, "Large language models")
    learn.set_item_state(conn, read, NOW, read=True)
    after_read = conn.execute("SELECT affinity FROM topics WHERE id = ?", (llm,)).fetchone()[0]
    learn.set_item_state(conn, dismissed, NOW, dismissed=True)
    after_dismiss = conn.execute("SELECT affinity FROM topics WHERE id = ?", (llm,)).fetchone()[0]
    assert 0 < after_read < 0.05 and after_dismiss < after_read
    assert conn.execute("SELECT COUNT(*) FROM events WHERE item_id = ?", (ignored,)).fetchone()[0] == 0  # never opened: no signal at all
    assert item(conn, dismissed)["dismissed_at"] and queries.list_items(conn)["total"] == 2 and queries.list_items(conn, state="dismissed")["total"] == 1


def test_feedback_is_counted_once_can_be_undone_and_poor_quality_is_about_the_source():
    conn = make_db()
    thing = add_item(conn, "AI agents in practice")
    agents = topic_id(conn, "AI agents")
    affinity = lambda: conn.execute("SELECT affinity FROM topics WHERE id = ?", (agents,)).fetchone()[0]  # noqa: E731
    assert learn.add_feedback(conn, thing, "like", NOW) and learn.add_feedback(conn, thing, "like", NOW, "still like it")
    liked = affinity()
    assert liked == pytest.approx(0.35) and conn.execute("SELECT reason FROM feedback").fetchone()[0] == "still like it"
    learn.remove_feedback(conn, thing, "like", NOW)
    assert abs(affinity()) < 0.13 and conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0] == 0
    learn.add_feedback(conn, thing, "low_quality", NOW, "clickbait")
    assert conn.execute("SELECT quality FROM sources").fetchone()[0] == pytest.approx(-0.12) and abs(affinity()) < 0.13  # the topic is not blamed
    learn.add_feedback(conn, thing, "not_interested_topic", NOW)
    assert affinity() < -0.4
    with pytest.raises(ValueError):
        learn.add_feedback(conn, thing, "meh", NOW)
    assert learn.add_feedback(conn, 99999, "like", NOW) is False


def test_learned_affinity_is_bounded_and_fades_monthly():
    conn = make_db()
    agents = topic_id(conn, "AI agents")
    for n in range(40):
        learn.add_feedback(conn, add_item(conn, f"AI agents story {n}"), "like", NOW)
    assert 0.99 < conn.execute("SELECT affinity FROM topics WHERE id = ?", (agents,)).fetchone()[0] <= 1.0
    assert learn.decay_learning(conn, NOW) is True and learn.decay_learning(conn, NOW) is False  # first run only records the month
    assert learn.decay_learning(conn, NOW + timedelta(days=40)) is True
    assert conn.execute("SELECT affinity FROM topics WHERE id = ?", (agents,)).fetchone()[0] == pytest.approx(0.92, abs=0.01)
    learn.reset_topic_learning(conn, agents)
    assert conn.execute("SELECT affinity FROM topics WHERE id = ?", (agents,)).fetchone()[0] == 0


def test_the_owner_can_add_rate_and_be_offered_topics():
    conn = make_db()
    new = learn.create_topic(conn, "  Sparse   autoencoders ", NOW)
    assert conn.execute("SELECT name, level, origin FROM topics WHERE id = ?", (new,)).fetchone()[:] == ("Sparse autoencoders", 2, "user")
    assert learn.create_topic(conn, "sparse autoencoders", NOW, level=3) == new and conn.execute("SELECT level FROM topics WHERE id = ?", (new,)).fetchone()[0] == 3
    with pytest.raises(ValueError):
        learn.set_topic_level(conn, new, 5, NOW)
    with pytest.raises(ValueError):
        learn.create_topic(conn, "x", NOW)
    conn.execute("INSERT INTO topics (slug, name, origin, item_count, affinity, created_at) VALUES ('seen-often', 'Seen often', 'discovered', 7, 0.3, ?), ('seen-once', 'Seen once', 'discovered', 1, 0, ?)", (db.iso(NOW), db.iso(NOW)))
    assert [t["name"] for t in learn.suggested_topics(conn)] == ["Seen often"]
    assert classify_new(conn, lexicon()) == 0
