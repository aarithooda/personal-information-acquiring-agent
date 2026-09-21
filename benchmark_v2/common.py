"""Benchmark v2 constants. Every number here is also written in METHODOLOGY.md, and a test keeps the two identical.

Nothing in this module (or in corpus.py / freeze.py) may import label, arm, analysis or model code: the corpus is built
and frozen without any model output and without any v1 label.
"""

from datetime import datetime, timezone
from pathlib import Path

BENCHMARK_VERSION = "v2"

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent
DATA_DIR = PACKAGE_ROOT / "data"  # git-ignored: it holds item text, the reader's labels and (later) model outputs
METHODOLOGY = PACKAGE_ROOT / "METHODOLOGY.md"

# The only v1 artefact the builder reads: the facts-only item snapshot (no labels, scores or model output).
V1_ITEMS = PROJECT_ROOT / "benchmarks" / "data" / "items.json"
V1_DB = PROJECT_ROOT / "data" / "pia.db"

# ---- corpus ----
WINDOW_START = datetime(2026, 7, 19, tzinfo=timezone.utc)  # inclusive
WINDOW_END = datetime(2026, 9, 17, tzinfo=timezone.utc)  # exclusive; ends before v1's intake began
WINDOW_DAYS = 3  # PIA's FIRST_RUN_LOOKBACK
N_NEW = 160
N_REPEAT = 40
ID_SALT = "benchmark-v2:"

# ---- seeds (fixed before any collection) ----
SAMPLE_SEED = 20260922
REPEAT_SEED = 20260923
QUEUE_SEED = 20260924
BOOTSTRAP_SEED = 20260925
PERMUTATION_SEED = 20260926

# ---- analysis parameters (used later; fixed now) ----
KS = (4, 10, 16, 32)
BOOTSTRAP_RESAMPLES = 5000
PERMUTATION_SHUFFLES = 10000
KAPPA_FLOOR = 0.40

# ---- files under data/ ----
ITEMS_FILE = "items.json"  # the corpus: item facts only, same format as the v1 snapshot
MANIFEST_FILE = "manifest.json"  # INTERNAL: origins, repeat identities, provenance
POOL_DB = "pool.db"  # INTERNAL: everything collected, for audit
FREEZE_FILE = "FREEZE.json"

PAUSE_SECONDS = {"arxiv": 3.0, "github": 7.0}  # politeness between requests (arXiv asks for 3 s; unauthenticated GitHub search allows 10 per minute)
