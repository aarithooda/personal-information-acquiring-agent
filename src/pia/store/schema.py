"""The platform schema, as forward-only migrations (PRAGMA user_version), like v1's `pia.db`.

Never edit a released migration; append a new one. See docs/platform/ARCHITECTURE.md, "Data model".
"""

MIGRATIONS = [
    """
    CREATE TABLE domains (
        id          INTEGER PRIMARY KEY,
        slug        TEXT NOT NULL UNIQUE,
        name        TEXT NOT NULL,
        short       TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        position    INTEGER NOT NULL DEFAULT 0,
        hue         INTEGER NOT NULL DEFAULT 220,
        weight      REAL NOT NULL DEFAULT 1.0,     -- the owner's explicit setting: 0 hidden, 1 normal, 1.5 priority
        affinity    REAL NOT NULL DEFAULT 0,       -- learned from behaviour, in [-1, 1]
        enabled     INTEGER NOT NULL DEFAULT 1
    );

    CREATE TABLE topics (
        id         INTEGER PRIMARY KEY,
        slug       TEXT NOT NULL UNIQUE,
        name       TEXT NOT NULL,
        kind       TEXT NOT NULL DEFAULT 'topic' CHECK (kind IN ('topic','org','person','tech')),
        domain_id  INTEGER REFERENCES domains(id) ON DELETE SET NULL,
        aliases    TEXT NOT NULL DEFAULT '[]',
        level      INTEGER CHECK (level IN (-2,-1,1,2,3)),  -- NULL unrated, -2 muted, -1 not interested, 1 somewhat, 2 high, 3 priority
        affinity   REAL NOT NULL DEFAULT 0,                  -- learned, in [-1, 1]
        origin     TEXT NOT NULL DEFAULT 'seed' CHECK (origin IN ('seed','user','discovered')),
        item_count INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        rated_at   TEXT
    );
    CREATE INDEX idx_topics_domain ON topics(domain_id);

    CREATE TABLE organisations (
        id         INTEGER PRIMARY KEY,
        name       TEXT NOT NULL UNIQUE,
        homepage   TEXT,
        notes      TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    );

    CREATE TABLE sources (
        id               INTEGER PRIMARY KEY,
        name             TEXT NOT NULL UNIQUE,
        kind             TEXT NOT NULL,
        url              TEXT,
        homepage         TEXT,
        description      TEXT NOT NULL DEFAULT '',
        config           TEXT NOT NULL DEFAULT '{}',
        domains          TEXT NOT NULL DEFAULT '[]',     -- domain slugs this source belongs to
        general          INTEGER NOT NULL DEFAULT 0,     -- 1: mixed-topic source; its domains are only a fallback
        status           TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('candidate','active','paused','rejected')),
        origin           TEXT NOT NULL DEFAULT 'builtin' CHECK (origin IN ('builtin','user','discovered')),
        purpose          TEXT NOT NULL DEFAULT 'news' CHECK (purpose IN ('news','opportunities')),
        trust            REAL NOT NULL DEFAULT 0.6,
        quality          REAL NOT NULL DEFAULT 0,        -- learned adjustment from "poor quality" feedback, in [-1, 0]
        interval_minutes INTEGER NOT NULL DEFAULT 180,
        next_fetch_at    TEXT,
        last_ok_at       TEXT,
        last_error       TEXT,
        failures         INTEGER NOT NULL DEFAULT 0,
        state            TEXT NOT NULL DEFAULT '{}',     -- adapter-private memory (e.g. links a page watcher has seen)
        evaluation       TEXT,                            -- JSON written by source evaluation (candidates)
        reason           TEXT NOT NULL DEFAULT '',       -- why PIA proposed it
        org_id           INTEGER REFERENCES organisations(id) ON DELETE CASCADE,
        created_at       TEXT NOT NULL,
        decided_at       TEXT
    );
    CREATE INDEX idx_sources_status ON sources(status);

    CREATE TABLE fetch_runs (
        id            INTEGER PRIMARY KEY,
        source_id     INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
        started_at    TEXT NOT NULL,
        fetched_until TEXT,                -- the per-source cursor; set on success only
        ok            INTEGER NOT NULL,
        error         TEXT,
        item_count    INTEGER NOT NULL DEFAULT 0,
        new_count     INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX idx_fetch_runs_source ON fetch_runs(source_id, id);

    CREATE TABLE items (
        id               INTEGER PRIMARY KEY,
        canonical_url    TEXT NOT NULL UNIQUE,
        url              TEXT NOT NULL,
        title            TEXT NOT NULL,
        content          TEXT,                 -- text the source itself supplied (abstract, feed summary); never overwritten
        author           TEXT,
        published_at     TEXT,                 -- NULL when the source gave none; never invented
        acquired_at      TEXT NOT NULL,
        sort_at          TEXT NOT NULL,        -- COALESCE(published_at, acquired_at): the Latest ordering
        source_id        INTEGER REFERENCES sources(id) ON DELETE SET NULL,
        source_name      TEXT NOT NULL,        -- kept so attribution survives a deleted source
        kind             TEXT NOT NULL DEFAULT 'article',
        domain_id        INTEGER REFERENCES domains(id) ON DELETE SET NULL,
        signals          TEXT NOT NULL DEFAULT '{}',
        stage            TEXT NOT NULL DEFAULT 'new' CHECK (stage IN ('new','classified','assessed')),
        assess_attempts  INTEGER NOT NULL DEFAULT 0,
        summary          TEXT NOT NULL DEFAULT '',
        model_importance REAL,                 -- what the assessment model said (0-1); NULL = not assessed
        fit              REAL,                 -- optional personal-fit judgement (Jev), 0-1
        importance       REAL NOT NULL DEFAULT 0,
        relevance        REAL NOT NULL DEFAULT 0,
        popularity       REAL NOT NULL DEFAULT 0,
        context_boost    REAL NOT NULL DEFAULT 0,
        boosted_at       TEXT,
        score            REAL NOT NULL DEFAULT 0,   -- time-independent blend
        rank             REAL NOT NULL DEFAULT 0,   -- score with recency applied; refreshed by the worker
        muted            INTEGER NOT NULL DEFAULT 0,
        read_at          TEXT,
        saved_at         TEXT,
        dismissed_at     TEXT,
        opened_at        TEXT,
        notified         INTEGER NOT NULL DEFAULT 0,
        related_done     INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX idx_items_sort ON items(sort_at);
    CREATE INDEX idx_items_rank ON items(rank);
    CREATE INDEX idx_items_stage ON items(stage);
    CREATE INDEX idx_items_saved ON items(saved_at) WHERE saved_at IS NOT NULL;

    CREATE TABLE item_sources (
        item_id     INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
        source_name TEXT NOT NULL,
        source_id   INTEGER REFERENCES sources(id) ON DELETE SET NULL,
        external_id TEXT,
        url         TEXT,
        seen_at     TEXT NOT NULL,
        PRIMARY KEY (item_id, source_name)
    );

    CREATE TABLE item_domains (
        item_id   INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
        domain_id INTEGER NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
        weight    REAL NOT NULL DEFAULT 1,
        PRIMARY KEY (item_id, domain_id)
    );
    CREATE INDEX idx_item_domains_domain ON item_domains(domain_id, item_id);

    CREATE TABLE item_topics (
        item_id  INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
        topic_id INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
        weight   REAL NOT NULL DEFAULT 1,
        PRIMARY KEY (item_id, topic_id)
    );
    CREATE INDEX idx_item_topics_topic ON item_topics(topic_id, item_id);

    CREATE TABLE judgements (
        id         INTEGER PRIMARY KEY,
        item_id    INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
        task       TEXT NOT NULL,
        provider   TEXT NOT NULL,
        model      TEXT NOT NULL,
        version    TEXT NOT NULL,
        payload    TEXT NOT NULL,        -- the model's raw answer for this item (JSON)
        created_at TEXT NOT NULL
    );
    CREATE INDEX idx_judgements_item ON judgements(item_id, task);

    CREATE TABLE briefs (
        item_id    INTEGER PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
        summary    TEXT NOT NULL,
        why        TEXT NOT NULL DEFAULT '',
        key_points TEXT NOT NULL DEFAULT '[]',
        sections   TEXT NOT NULL DEFAULT '[]',   -- [{"heading": ..., "body": ...}], chosen to suit the item kind
        basis      TEXT NOT NULL DEFAULT 'source_text',  -- what the brief was written from
        model      TEXT NOT NULL,
        version    TEXT NOT NULL,
        created_at TEXT NOT NULL
    );

    CREATE TABLE item_texts (
        item_id    INTEGER PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
        final_url  TEXT,
        fetched_at TEXT NOT NULL,
        ok         INTEGER NOT NULL,
        text       TEXT,
        extractor  TEXT NOT NULL DEFAULT '',
        error      TEXT
    );

    CREATE TABLE relations (
        item_id    INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,   -- the newer item
        other_id   INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,   -- the earlier one it relates to
        kind       TEXT NOT NULL CHECK (kind IN ('same_story','related','references')),
        score      REAL NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (item_id, other_id)
    );
    CREATE INDEX idx_relations_other ON relations(other_id);

    CREATE TABLE opportunities (
        item_id           INTEGER PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
        organisation      TEXT NOT NULL DEFAULT '',
        programme         TEXT NOT NULL DEFAULT '',
        type              TEXT NOT NULL DEFAULT 'other',
        eligibility       TEXT NOT NULL DEFAULT '',
        year_requirements TEXT NOT NULL DEFAULT '',
        first_year        TEXT NOT NULL DEFAULT 'unknown' CHECK (first_year IN ('yes','no','unknown')),
        location          TEXT NOT NULL DEFAULT '',
        mode              TEXT NOT NULL DEFAULT '',
        deadline          TEXT,                  -- ISO date, NULL when unknown
        deadline_text     TEXT NOT NULL DEFAULT '',
        start_date        TEXT NOT NULL DEFAULT '',
        duration          TEXT NOT NULL DEFAULT '',
        qualifications    TEXT NOT NULL DEFAULT '',
        apply_url         TEXT,
        extracted_by      TEXT NOT NULL DEFAULT 'rules',
        status            TEXT NOT NULL DEFAULT '' CHECK (status IN ('','interested','planning','applied','accepted','rejected')),
        status_at         TEXT,
        notes             TEXT NOT NULL DEFAULT '',
        created_at        TEXT NOT NULL
    );
    CREATE INDEX idx_opportunities_deadline ON opportunities(deadline);

    CREATE TABLE feedback (
        id         INTEGER PRIMARY KEY,
        item_id    INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
        kind       TEXT NOT NULL CHECK (kind IN ('like','useful','not_relevant','low_quality','not_interested_topic')),
        reason     TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        UNIQUE (item_id, kind)
    );

    CREATE TABLE events (
        id         INTEGER PRIMARY KEY,
        item_id    INTEGER REFERENCES items(id) ON DELETE CASCADE,
        kind       TEXT NOT NULL,
        ref        TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    );
    CREATE INDEX idx_events_kind ON events(kind, created_at);

    CREATE TABLE notifications (
        id         INTEGER PRIMARY KEY,
        item_id    INTEGER REFERENCES items(id) ON DELETE CASCADE,
        level      TEXT NOT NULL CHECK (level IN ('interesting','important','critical')),
        title      TEXT NOT NULL,
        body       TEXT NOT NULL DEFAULT '',
        reason     TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        seen_at    TEXT
    );
    CREATE INDEX idx_notifications_created ON notifications(created_at);

    CREATE TABLE devices (
        id           INTEGER PRIMARY KEY,
        name         TEXT NOT NULL,
        token_hash   TEXT NOT NULL UNIQUE,
        created_at   TEXT NOT NULL,
        last_seen_at TEXT,
        revoked_at   TEXT
    );

    CREATE TABLE deliveries (
        notification_id INTEGER NOT NULL REFERENCES notifications(id) ON DELETE CASCADE,
        device_id       INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
        delivered_at    TEXT NOT NULL,
        PRIMARY KEY (notification_id, device_id)
    );

    CREATE TABLE llm_usage (
        id            INTEGER PRIMARY KEY,
        at            TEXT NOT NULL,
        provider      TEXT NOT NULL,
        model         TEXT NOT NULL,
        task          TEXT NOT NULL,
        input_tokens  INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0,
        cost_usd      REAL NOT NULL DEFAULT 0,
        items         INTEGER NOT NULL DEFAULT 0,
        latency_ms    INTEGER NOT NULL DEFAULT 0,
        ok            INTEGER NOT NULL DEFAULT 1,
        error         TEXT
    );
    CREATE INDEX idx_llm_usage_at ON llm_usage(at);

    CREATE TABLE runs (
        id          INTEGER PRIMARY KEY,
        kind        TEXT NOT NULL,
        started_at  TEXT NOT NULL,
        finished_at TEXT,
        ok          INTEGER,
        stats       TEXT NOT NULL DEFAULT '{}',
        error       TEXT
    );

    CREATE TABLE settings (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );

    CREATE TABLE sessions (
        token_hash TEXT PRIMARY KEY,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    );

    -- Full-text index. Kept in step by triggers so no code path can forget it.
    CREATE VIRTUAL TABLE items_fts USING fts5(title, body, summary, tokenize = 'porter unicode61');
    CREATE TRIGGER items_fts_insert AFTER INSERT ON items BEGIN
        INSERT INTO items_fts(rowid, title, body, summary) VALUES (new.id, new.title, COALESCE(new.content, ''), new.summary);
    END;
    CREATE TRIGGER items_fts_update AFTER UPDATE OF title, content, summary ON items BEGIN
        DELETE FROM items_fts WHERE rowid = old.id;
        INSERT INTO items_fts(rowid, title, body, summary) VALUES (new.id, new.title, COALESCE(new.content, ''), new.summary);
    END;
    CREATE TRIGGER items_fts_delete AFTER DELETE ON items BEGIN
        DELETE FROM items_fts WHERE rowid = old.id;
    END;
    """,
    # v2: the Sources page and source-quality learning look items up by source.
    """
    CREATE INDEX idx_items_source ON items(source_id);
    """,
]
