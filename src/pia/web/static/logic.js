"use strict";

/*
 * The front end's PURE logic: no DOM, no network. It lives apart from app.js so it can be unit-tested
 * with plain Node (tests/test_web_logic.py). In the browser it is exposed as window.PiaLogic; under
 * Node it is a normal module. (A tiny "universal module" wrapper, no bundler needed.)
 */
(function (root, factory) {
  const logic = factory();
  if (typeof module === "object" && module.exports) module.exports = logic;
  else root.PiaLogic = logic;
})(typeof self !== "undefined" ? self : this, function () {
  /** Each tab (Library, Favorites) gets its OWN filters; a shared object leaked one tab's search into the other. */
  function emptyFilters() {
    return { q: "", category: "", source: "", includeSkipped: false };
  }

  /** "Active" = something that narrows results. The skipped toggle widens them, so it does not count. */
  function hasActiveFilters(filters) {
    return Boolean(filters.q || filters.category || filters.source);
  }

  /** The query string for GET /api/items. URLSearchParams encodes the search text safely. */
  function buildItemsQuery(mode, filters, offset, limit) {
    const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
    const q = filters.q.trim();
    if (q) params.set("q", q);
    if (filters.category) params.set("category", filters.category);
    if (filters.source) params.set("source", filters.source);
    if (mode === "favorites") params.set("favorite", "true");
    else if (filters.includeSkipped) params.set("include_skipped", "true");
    return params.toString();
  }

  /** What to say when a list is empty. It must not claim "nothing exists" when filters are hiding things. */
  function emptyMessage(mode, filters) {
    const filtered = hasActiveFilters(filters);
    if (mode === "favorites") {
      return filtered ? "No favorites match these filters." : "No favorites yet. Star an item in New or Library and it will be kept here.";
    }
    if (filtered) {
      return filters.includeSkipped
        ? "Nothing matches these filters."
        : 'Nothing matches these filters. Items the briefings skipped are hidden: tick "Include items the briefing skipped" to search them too.';
    }
    return filters.includeSkipped ? "The library is empty." : "Nothing has been shown to you yet. Run pia (or run-pia.bat) to create a briefing.";
  }

  /** Only http(s) URLs become links. A "javascript:" or "data:" URL must never become clickable. */
  function safeHref(url) {
    try {
      const parsed = new URL(url);
      return parsed.protocol === "http:" || parsed.protocol === "https:" ? parsed.href : null;
    } catch (invalid) {
      return null;
    }
  }

  return { emptyFilters, hasActiveFilters, buildItemsQuery, emptyMessage, safeHref };
});
