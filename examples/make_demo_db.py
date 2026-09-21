"""Build a demo database with SYNTHETIC content, so the web UI can be tried with no API keys and no network.

    python examples/make_demo_db.py                 # writes data/demo.db
    pia --db data/demo.db web                       # then open http://127.0.0.1:8765

Everything here is invented: the items are labelled "[demo]", their URLs are example.com, and the "LLM" is a small
scripted stand-in (the same idea as the test suite's fake), so the categories, scores and explanations mean nothing.
It runs PIA's real pipeline (dedup, checkpoint, ranking, curation, briefing layout, one-transaction commit) end to end;
only the sources and the model are replaced. It never touches data/pia.db.
"""

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pia.briefing.curate import make_curator
from pia.config import PROJECT_ROOT
from pia.db import connect
from pia.models import RawItem
from pia.pipeline import run_briefing
from pia.sources.base import within_window

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)

# (source, title, category, importance, text or None, popularity signals as stored: {source: {metric: value}})
DEMO_ITEMS = [
    ("arxiv", "[demo] A structured-output method that halves tool-call errors", "ai", 5, "Synthetic abstract: an invented technique for reliable tool use in agents.", {}),
    ("arxiv", "[demo] Evaluating long-context retrieval without leaking the answer", "ai", 4, "Synthetic abstract: an invented benchmark design.", {}),
    ("arxiv", "[demo] A short proof of a folklore lemma in linear algebra", "research", 4, "Synthetic abstract: an invented elementary proof.", {}),
    ("arxiv", "[demo] Scaling laws for small specialised models", "ai", 3, "Synthetic abstract: invented scaling curves.", {}),
    ("arxiv", "[demo] Static analysis for prompt templates", "software", 3, "Synthetic abstract: an invented linter for prompts.", {}),
    ("hf_papers", "[demo] Distilling a reranker into a 100M-parameter model", "ai", 4, "Synthetic abstract: an invented distillation recipe.", {"hf_papers": {"upvotes": 61}}),
    ("hf_papers", "[demo] A recipe for reproducible fine-tuning runs", "ai", 3, "Synthetic abstract: invented practices for repeatable training.", {"hf_papers": {"upvotes": 34}}),
    ("hn", "[demo] Show: a tiny local inference server in 400 lines", "software", 4, None, {"hn": {"points": 412}}),
    ("hn", "[demo] Why our database migration took nine hours", "software", 2, None, {"hn": {"points": 260}}),
    ("hn", "[demo] The history of a forgotten programming language", "other", 2, None, {"hn": {"points": 180}}),
    ("hn", "[demo] Ask: how do you evaluate LLM features in production?", "ai", 3, "Synthetic post text: invented discussion prompt.", {"hn": {"points": 150}}),
    ("hn", "[demo] A visual explanation of the singular value decomposition", "research", 4, None, {"hn": {"points": 330}}),
    ("hn", "[demo] Company announces an incremental product update", "other", 1, None, {"hn": {"points": 120}}),
    ("github", "[demo] agent-trace: record and replay agent runs", "ai", 4, "Synthetic description: an invented open-source project.", {"github": {"stars": 830}}),
    ("github", "[demo] sqlite-diff: readable diffs for database files", "software", 3, "Synthetic description: an invented developer tool.", {"github": {"stars": 210}}),
    ("github", "[demo] tiny-grad: an autograd engine you can read in an evening", "software", 4, "Synthetic description: an invented teaching project.", {"github": {"stars": 540}}),
    ("quanta", "[demo] Mathematicians find an unexpected link between two fields", "research", 5, "Synthetic summary: an invented feature story.", {}),
    ("quanta", "[demo] A new bound on a classic packing problem", "research", 3, "Synthetic summary: an invented result.", {}),
    ("deepmind", "[demo] A research update on agent evaluation", "ai", 4, "Synthetic summary: an invented lab blog post.", {}),
    ("openai", "[demo] A minor API changelog entry", "software", 1, "Synthetic summary: an invented changelog.", {}),
    ("hf_blog", "[demo] Getting started with small models on a laptop", "ai", 3, "Synthetic summary: an invented tutorial.", {}),
    ("arxiv", "[demo] Robustness of vision models to compression artefacts", "other", 2, "Synthetic abstract: an invented off-topic result.", {}),
    ("hn", "[demo] Ask: what is the best way to learn category theory?", "research", 3, None, {"hn": {"points": 140}}),
    ("github", "[demo] awesome-lists-of-lists: a list of lists", "other", 1, "Synthetic description: an invented low-value repo.", {"github": {"stars": 90}}),
]


class DemoSource:
    """Serves the synthetic items published in the last two days, like an adapter would."""

    def __init__(self, name: str, items: list[RawItem]):
        self.name = name
        self.items = items

    def fetch(self, client, since: datetime, until: datetime) -> list[RawItem]:
        return within_window(self.items, since, until)


class ScriptedLLM:
    """Answers PIA's two LLM stages from the table above, by reading the request the way a very obedient model would."""

    def __init__(self):
        self.by_title = {title: (category, importance) for _, title, category, importance, _, _ in DEMO_ITEMS}

    def complete_json(self, **kwargs) -> dict:
        payload = json.loads(re.search(r"<(?:items|candidates)>\n(.*)\n</", kwargs["user"], re.S).group(1))
        if kwargs["schema_name"] == "triage_results":
            return {
                "results": [
                    {
                        "index": item["index"],
                        "category": self.by_title.get(item["title"], ("other", 1))[0],
                        "importance": self.by_title.get(item["title"], ("other", 1))[1],
                        "summary": "Synthetic one-line summary (demo data).",  # PIA itself blanks summaries for title-only items
                    }
                    for item in payload
                ]
            }
        limit = int(re.search(r"at most (\d+)", kwargs["user"]).group(1))
        return {
            "headlines": [
                {
                    "index": item["index"],
                    "explanation": "Synthetic explanation. In a real run the editor writes this from the source text only.",
                    "why_it_matters": "Synthetic reason. In a real run this is tied to your interest profile.",
                }
                for item in payload[:limit]
            ]
        }


def demo_sources() -> list[DemoSource]:
    by_source: dict[str, list[RawItem]] = {}
    for n, (source, title, _category, _importance, text, signals) in enumerate(DEMO_ITEMS):
        by_source.setdefault(source, []).append(
            RawItem(
                source=source,
                external_id=f"demo-{n}",
                url=f"https://example.com/demo/{n}",
                title=title,
                content=text,
                published_at=NOW - timedelta(hours=1 + n),
                signals=signals,
            )
        )
    return [DemoSource(name, items) for name, items in by_source.items()]


def build(path: Path) -> None:
    conn = connect(path)
    try:
        run_briefing(conn, None, demo_sources(), NOW, prepare=make_curator(ScriptedLLM()))
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a demo database with synthetic content (no API keys, no network).")
    parser.add_argument("--out", type=Path, default=PROJECT_ROOT / "data" / "demo.db", help="database file to create (default: data/demo.db)")
    parser.add_argument("--force", action="store_true", help="overwrite the file if it exists")
    args = parser.parse_args(argv)
    if args.out.exists() and not args.force:
        print(f"{args.out} already exists. Pass --force to replace it (this only ever touches the file you name).", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    for stale in (args.out, Path(f"{args.out}-wal"), Path(f"{args.out}-shm")):
        stale.unlink(missing_ok=True)
    build(args.out)
    print(f"Built {args.out} with synthetic content. Try it:  pia --db {args.out} web   (or: pia --db {args.out} show)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
