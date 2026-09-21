"use strict";

/*
 * PIA web UI: plain JavaScript, no framework, no build step.
 *
 * Three views (New, Library, Favorites) drawn by talking to the JSON API in app.py.
 *
 * Rules this file follows (checked by tests/test_web_static.py):
 *   - Text from the web (titles, summaries) is UNTRUSTED. It is only ever inserted as text nodes,
 *     never as HTML, so a title like "<img onerror=...>" is displayed, not executed (XSS).
 *   - Favorite clicks send the state we WANT (PUT or DELETE), not "flip it": repeating a request
 *     is harmless, which matters with double clicks and two open tabs.
 *   - Late answers are discarded: if you type quickly or switch tabs, an older response must not
 *     overwrite a newer one (a classic async race), so each load carries a token.
 */

const CATEGORY_LABELS = { ai: "AI & Agents", software: "Software", research: "Research", other: "Other" };
const PAGE_SIZE = 30;

const viewEl = document.getElementById("view");
const bannerEl = document.getElementById("banner");

// Each tab keeps its own filters, so a search in Library does not silently hide things in Favorites.
// (Pure logic such as query building and empty-state wording lives in logic.js and is unit-tested.)
const { emptyFilters, hasActiveFilters, buildItemsQuery, emptyMessage, safeHref } = window.PiaLogic;
const filtersByTab = { library: emptyFilters(), favorites: emptyFilters() };
let status = null;
let routeToken = 0;

// ---------- small helpers ----------

/** Create an element. Children that are strings become text nodes, so they can never be parsed as HTML. */
function h(tag, props, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

class ApiError extends Error {
  constructor(httpStatus, message) {
    super(message);
    this.httpStatus = httpStatus;
  }
}

async function api(path, options = {}) {
  let response;
  try {
    response = await fetch(path, options);
  } catch (networkError) {
    throw new ApiError(0, "Cannot reach the PIA server. Is `pia web` still running?");
  }
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch (ignored) { /* the body was not JSON */ }
    throw new ApiError(response.status, detail);
  }
  return response.json();
}

function showError(message) {
  bannerEl.textContent = message;
  bannerEl.hidden = false;
}

function clearError() {
  bannerEl.hidden = true;
  bannerEl.textContent = "";
}

function titleLink(title, url) {
  const href = safeHref(url);
  return href ? h("a", { href, target: "_blank", rel: "noopener noreferrer" }, title) : h("span", null, title);
}

function formatDate(iso, withTime = true) {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString(undefined, withTime ? { dateStyle: "medium", timeStyle: "short" } : { dateStyle: "medium" });
}

function chip(text, extraClass) {
  return h("span", { class: "chip" + (extraClass ? " " + extraClass : "") }, text);
}

// ---------- header and status ----------

async function refreshStatus() {
  status = await api("/api/status");
  const lastChecked = document.getElementById("last-checked");
  lastChecked.textContent = status.last_checked
    ? "Last checked " + formatDate(status.last_checked) + " · run pia to check for new items"
    : "Never checked · run pia to create your first briefing";
  document.getElementById("count-library").textContent = status.counts.library || "";
  document.getElementById("count-favorites").textContent = status.counts.favorites || "";
}

function setActiveTab(tab) {
  for (const link of document.querySelectorAll("#tabs a")) {
    if (link.dataset.tab === tab) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
}

// ---------- the favorite star ----------

/**
 * A star button for the item with this id. `onChange(nowFavorite)` runs after the SERVER confirms:
 * we do not pretend it worked before it did.
 */
function starButton(itemId, isFavorite, onChange) {
  if (itemId === null || itemId === undefined) return h("span", { class: "star" }); // entry not linked to an item
  const button = h("button", { type: "button", class: "star" });
  const paint = (favorite) => {
    button.textContent = favorite ? "★" : "☆";
    button.setAttribute("aria-pressed", String(favorite));
    const label = favorite ? "Remove from favorites" : "Add to favorites";
    button.setAttribute("aria-label", label);
    button.title = label;
  };
  paint(isFavorite);
  button.addEventListener("click", async () => {
    const want = button.getAttribute("aria-pressed") !== "true"; // the state we WANT, sent explicitly
    button.disabled = true;
    try {
      await api(`/api/items/${itemId}/favorite`, { method: want ? "PUT" : "DELETE" });
      paint(want);
      clearError();
      if (onChange) onChange(want);
      refreshStatus().catch(() => {}); // keep the tab counts current
    } catch (error) {
      showError("Could not update the favorite: " + error.message);
    } finally {
      button.disabled = false;
    }
  });
  return button;
}

// ---------- New: a briefing ----------

function headlineCard(headline) {
  const meta = [];
  if (headline.via.length) meta.push(h("span", null, "via " + headline.via.join(", ")));
  if (headline.category) meta.push(chip(CATEGORY_LABELS[headline.category] || headline.category));
  return h(
    "article",
    { class: "card headline" },
    h("div", { class: "card-head" }, h("h3", null, titleLink(headline.title, headline.url)), starButton(headline.item_id, headline.favorite)),
    h("p", { class: "explanation" }, headline.explanation),
    headline.why_it_matters ? h("p", null, h("strong", null, "Why it matters: "), headline.why_it_matters) : null,
    meta.length ? h("div", { class: "meta" }, meta) : null,
  );
}

function extraRow(extra) {
  return h(
    "li",
    null,
    starButton(extra.item_id, extra.favorite),
    h("div", { class: "body" }, titleLink(extra.title, extra.url), extra.summary ? h("span", { class: "summary" }, " — " + extra.summary) : null),
  );
}

function briefingPicker(briefings, selectedId) {
  const select = h("select", { id: "briefing-select", "aria-label": "Choose a briefing" });
  for (const b of briefings) {
    const label = `#${b.id} · ${formatDate(b.created_at)} · ` + (b.shown ? `${b.shown} shown` : "nothing new");
    select.append(h("option", { value: b.id, selected: b.id === selectedId }, label));
  }
  select.addEventListener("change", () => { location.hash = "#/new/" + select.value; });
  return h("div", { class: "briefing-picker" }, h("label", { for: "briefing-select", class: "muted" }, "Briefing:"), select);
}

async function buildNew(requestedId) {
  const briefings = await api("/api/briefings");
  let detail;
  try {
    detail = await api(requestedId ? `/api/briefings/${requestedId}` : "/api/briefings/current");
  } catch (error) {
    if (error.httpStatus === 404 && !requestedId) {
      return h("p", { class: "empty" }, "No briefings yet. Double-click run-pia.bat (or run `pia`) to create your first one, then refresh this page.");
    }
    throw error;
  }

  const root = h("div", null, briefingPicker(briefings, detail.id));
  root.append(h("p", { class: "muted" }, detail.parsed && detail.intro ? detail.intro : `Briefing #${detail.id} · ${formatDate(detail.created_at)}`));

  if (!detail.parsed) {
    // Unknown layout: show what was saved, as plain text.
    root.append(h("p", { class: "note" }, "This briefing has a layout the page does not recognise, so it is shown as saved."), h("pre", { class: "raw" }, detail.markdown));
    return root;
  }
  for (const note of detail.notes) root.append(h("p", { class: "note" }, note));
  if (detail.empty_message) root.append(h("p", { class: "empty" }, detail.empty_message));

  if (detail.headlines.length) {
    root.append(h("h2", null, "Worth knowing"));
    for (const headline of detail.headlines) root.append(headlineCard(headline));
  }
  for (const section of detail.sections) {
    root.append(h("section", { class: "section" }, h("h3", null, section.label), h("ul", { class: "list" }, section.items.map(extraRow))));
  }
  if (detail.hidden > 0) {
    root.append(h("p", { class: "footnote" }, `${detail.hidden} lower-priority items were left out of this briefing. Find them in Library with "Include items the briefing skipped".`));
  }
  return root;
}

// ---------- Library and Favorites: the same list, different filter ----------

function itemCard(item, onUnfavorite) {
  const meta = [h("span", null, item.source)];
  if (item.category) meta.push(chip(CATEGORY_LABELS[item.category] || item.category));
  const when = formatDate(item.published_at || item.discovered_at, false);
  if (when) meta.push(h("span", null, when));
  if (item.briefing_id) meta.push(h("span", null, `briefing #${item.briefing_id}`));
  if (item.status === "skipped") meta.push(chip("not shown in the briefing", "skipped"));
  if (item.seen_on.length > 1) meta.push(h("span", null, "seen on " + item.seen_on.join(", ")));

  const card = h(
    "article",
    { class: "card item" },
    h("div", { class: "card-head" }, h("h3", null, titleLink(item.title, item.url)), starButton(item.id, item.favorite, (now) => { if (!now) onUnfavorite(card); })),
    item.summary ? h("p", null, item.summary) : null,
    h("div", { class: "meta" }, meta),
  );
  return card;
}

function buildItems(mode) {
  const isFavorites = mode === "favorites";
  const filters = filtersByTab[mode];
  const results = h("div", { class: "results" });
  const count = h("p", { class: "muted", "aria-live": "polite" });
  const more = h("button", { type: "button", class: "btn", hidden: true }, "Load more");
  let offset = 0;
  let total = 0;
  let loadToken = 0;

  function clearFilters() {
    Object.assign(filters, emptyFilters());
    route(); // rebuild the tab so the controls show the cleared state
  }

  function updateCount() {
    const shown = results.querySelectorAll("article").length;
    count.textContent = total === 0 ? "" : `Showing ${shown} of ${total}`;
    if ((total === 0 || shown === 0) && !results.querySelector(".empty")) {
      const message = h("div", { class: "empty" }, h("p", null, emptyMessage(mode, filters)));
      if (hasActiveFilters(filters)) message.append(h("button", { type: "button", class: "btn", onclick: clearFilters }, "Clear filters"));
      results.replaceChildren(message);
    }
  }

  function onUnfavorite(card) {
    // In Favorites, un-starring removes the card once the server has confirmed.
    if (!isFavorites) return;
    card.remove();
    total = Math.max(0, total - 1);
    offset = Math.max(0, offset - 1);
    updateCount();
  }

  async function load(reset) {
    const token = ++loadToken; // any older request still in flight is now stale
    if (reset) {
      offset = 0;
      results.replaceChildren();
      more.hidden = true;
    }
    try {
      const page = await api("/api/items?" + buildItemsQuery(mode, filters, offset, PAGE_SIZE));
      if (token !== loadToken) return; // a newer search replaced this one
      clearError();
      total = page.total;
      for (const item of page.items) results.append(itemCard(item, onUnfavorite));
      offset += page.items.length;
      more.hidden = offset >= total;
      updateCount();
    } catch (error) {
      if (token === loadToken) showError(error.message);
    }
  }

  let debounce;
  const search = h("input", { type: "search", placeholder: "Search titles and summaries", "aria-label": "Search", value: filters.q });
  search.addEventListener("input", () => {
    clearTimeout(debounce);
    debounce = setTimeout(() => { filters.q = search.value.trim(); load(true); }, 300);
  });

  const category = h("select", { "aria-label": "Category" }, h("option", { value: "" }, "All categories"),
    Object.entries(CATEGORY_LABELS).map(([value, label]) => h("option", { value, selected: filters.category === value }, label)));
  category.addEventListener("change", () => { filters.category = category.value; load(true); });

  const source = h("select", { "aria-label": "Source" }, h("option", { value: "" }, "All sources"),
    ((status && status.sources) || []).map((name) => h("option", { value: name, selected: filters.source === name }, name)));
  source.addEventListener("change", () => { filters.source = source.value; load(true); });

  const controls = h("div", { class: "controls" }, search, category, source);
  if (!isFavorites) {
    const skipped = h("input", { type: "checkbox", id: "include-skipped", checked: filters.includeSkipped });
    skipped.addEventListener("change", () => { filters.includeSkipped = skipped.checked; load(true); });
    controls.append(h("label", { class: "check", for: "include-skipped" }, skipped, "Include items the briefing skipped"));
  }
  more.addEventListener("click", () => load(false));

  const root = h("div", null, h("h2", null, isFavorites ? "Favorites" : "Library"), controls, count, results, more);
  load(true);
  return root;
}

// ---------- routing ----------

async function route() {
  const token = ++routeToken;
  clearError();
  const parts = location.hash.replace(/^#\/?/, "").split("/");
  const tab = ["new", "library", "favorites"].includes(parts[0]) ? parts[0] : "new";
  setActiveTab(tab);
  viewEl.setAttribute("aria-busy", "true");
  try {
    if (status === null) await refreshStatus();
    const node = tab === "new" ? await buildNew(parts[1] ? Number(parts[1]) : null) : buildItems(tab);
    if (token !== routeToken) return; // the user already moved on
    viewEl.replaceChildren(node);
    viewEl.focus({ preventScroll: true });
  } catch (error) {
    if (token === routeToken) {
      viewEl.replaceChildren();
      showError(error.message);
    }
  } finally {
    if (token === routeToken) viewEl.removeAttribute("aria-busy");
  }
}

window.addEventListener("hashchange", () => {
  refreshStatus().catch(() => {}); // counts may have changed since the last visit
  route();
});
route();
