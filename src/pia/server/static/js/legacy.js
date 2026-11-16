// TEMPORARY (removed in step 8): the old Feed page (Library and Search) and their row components, kept so every
// route keeps working while the redesigned screens replace them one step at a time.
import { html, useCallback, useEffect, useRef, useState } from "../vendor/preact-htm.js";
import { api } from "./api.js";
import { Empty, ErrorNote, Icon, Segmented, Tabs, hueStyle, toast, useItemActions, useLoad } from "./ui.js";
import { KIND_LABELS, buildRoute, deadlineText, emptyMessage, marks, plural, safeUrl, tier, timeAgo } from "./util.js";

const PAGE = 30;
function go(path, query) { location.hash = buildRoute(path, query); }

export function Marks({ value, label = "Importance" }) {
  const lit = marks(value);
  return html`<span class="marks" role="img" aria-label=${`${label} ${lit} of 4`} title=${`${label} ${lit} of 4`}>
    ${[1, 2, 3, 4].map((n) => html`<i class=${n <= lit ? "lit" : ""}></i>`)}
  </span>`;
}

export function Loading({ lines = 4 }) {
  return html`<div aria-busy="true" aria-label="Loading">
    <div class="skeleton title"></div>
    ${Array.from({ length: lines }, () => html`<div class="skeleton line"></div>`)}
  </div>`;
}

// One item in a feed. `act(item, change)` applies a state change; `openHref` opens the reader panel.
export function ItemRow({ item, openHref, act, cursor = false, showSpace = true }) {
  const classes = ["row", `tier-${tier(item)}`, item.read ? "read" : "", cursor ? "cursor" : ""].join(" ");
  const original = safeUrl(item.url);
  return html`<li class=${classes} style=${hueStyle(item.hue)} data-id=${item.id}>
    <${Marks} value=${item.importance} />
    <div>
      <div class="row-meta">
        <span class="kind">${KIND_LABELS[item.kind] || "Item"}</span>
        <span>${item.source}</span>
        ${showSpace && item.domain_short && html`<span>${item.domain_short}</span>`}
        <time datetime=${item.sort_at}>${timeAgo(item.sort_at)}</time>
        ${item.signals.points && html`<span>${item.signals.points} points</span>`}
        ${item.signals.stars && html`<span>${item.signals.stars} stars</span>`}
        ${item.signals.upvotes && html`<span>${item.signals.upvotes} upvotes</span>`}
        ${item.resurfaced && html`<span class="flag" title="A newer item relates to this one">Back in view</span>`}
        ${item.spam ? html`<span title="The same repository name appears under many owners, a common spam pattern">Likely spam</span>`
          : item.muted && html`<span title="Touches a topic you muted">Muted topic</span>`}
      </div>
      <h3 class="row-title"><a href=${openHref}>${item.title}</a></h3>
      ${item.summary && html`<p class="row-summary">${item.summary}</p>`}
      ${item.topics && item.topics.length > 0 && html`<div class="row-foot">
        ${item.topics.map((topic) => html`<a class=${"chip" + (topic.level >= 2 ? " liked" : "") + (topic.level === -2 ? " muted" : "")}
          href=${buildRoute(["topic", String(topic.id)])}>${topic.name}</a>`)}
      </div>`}
    </div>
    <div class="row-actions">
      <button class=${"icon-button" + (item.saved ? " on" : "")} title=${item.saved ? "Remove from Saved" : "Save"} aria-pressed=${item.saved}
        onClick=${() => act(item, { saved: !item.saved })}><${Icon} name="bookmark" fill=${item.saved} /></button>
      <button class="icon-button" title=${item.read ? "Mark unread" : "Mark read"} onClick=${() => act(item, { read: !item.read })}><${Icon} name="check" /></button>
      <button class="icon-button" title=${item.dismissed ? "Restore" : "Dismiss"} onClick=${() => act(item, { dismissed: !item.dismissed })}><${Icon} name=${item.dismissed ? "refresh" : "x"} /></button>
      ${original && html`<a class="icon-button" href=${original} target="_blank" rel="noopener noreferrer" title="Open the original"><${Icon} name="external" /></a>`}
    </div>
  </li>`;
}

// ---------- Home ----------

// ---------- feeds: a space, Saved, search results, a topic ----------

export function Feed({ boot, refreshBoot, route, base, title, description, hue, allowViews = true, defaultShow = "active", emptyState }) {
  const view = route.query.view || (base.q ? "relevance" : "latest");
  const show = route.query.show || defaultShow;
  const kind = route.query.kind || "";
  const [items, setItems] = useState([]);
  const [meta, setMeta] = useState({ loading: true, error: null, total: 0, hasMore: false });
  const [cursor, setCursor] = useState(-1);
  const key = JSON.stringify([base, view, show, kind]);
  const params = { ...base, view, state: show, kind, limit: PAGE };

  const load = useCallback(async (offset) => {
    setMeta((current) => ({ ...current, loading: true, error: null }));
    try {
      const page = await api.items({ ...params, offset });
      setItems((current) => (offset ? [...current, ...page.items] : page.items));
      setMeta({ loading: false, error: null, total: page.total, hasMore: page.has_more });
    } catch (error) {
      setMeta((current) => ({ ...current, loading: false, error: error.message }));
    }
  }, [key]);
  useEffect(() => { setItems([]); setCursor(-1); load(0); }, [key]);

  const patchItems = useCallback((id, change) => setItems((current) => current.map((item) => (item.id === id ? { ...item, ...change } : item))), []);
  const act = useItemActions(patchItems, refreshBoot);
  const setQuery = (change) => go(route.path, { ...route.query, ...change, item: undefined });
  const href = (item) => buildRoute(route.path, { ...route.query, item: item.id });
  const visible = items.filter((item) => (show === "dismissed" ? true : !item.dismissed) && (show === "saved" ? item.saved : true));

  // Keyboard triage: j/k move, o or Enter opens, s saves, e marks read, x dismisses.
  const state = useRef({});
  state.current = { visible, cursor, panelOpen: Boolean(route.query.item) };
  useEffect(() => {
    const onKey = (event) => {
      const { visible: rows, cursor: at, panelOpen } = state.current;
      if (panelOpen || event.metaKey || event.ctrlKey || event.altKey || /INPUT|TEXTAREA|SELECT/.test(event.target.tagName)) return;
      const current = rows[at];
      if (event.key === "j") setCursor(Math.min(rows.length - 1, at + 1));
      else if (event.key === "k") setCursor(Math.max(0, at - 1));
      else if (!current) return;
      else if (event.key === "o" || event.key === "Enter") location.hash = href(current);
      else if (event.key === "s") act(current, { saved: !current.saved });
      else if (event.key === "e") act(current, { read: !current.read });
      else if (event.key === "x") act(current, { dismissed: !current.dismissed });
      else return;
      event.preventDefault();
    };
    addEventListener("keydown", onKey);
    return () => removeEventListener("keydown", onKey);
  }, [act]);
  useEffect(() => {
    if (cursor >= 0) document.querySelector(".row.cursor")?.scrollIntoView({ block: "nearest" });
  }, [cursor]);

  const filtered = Boolean(kind) || show !== defaultShow;
  const empty = emptyState || emptyMessage({ total: meta.total, filtered, state: show, q: base.q });
  const unreadShown = visible.filter((item) => !item.read);

  return html`<div class="page" style=${hueStyle(hue)}>
    <header class=${"page-head" + (hue !== undefined ? " space-head" : "")}><h1>${title}</h1>${description && html`<p>${description}</p>`}</header>
    ${allowViews && html`<${Tabs} value=${view} onChange=${(value) => setQuery({ view: value === "latest" ? undefined : value })}
      tabs=${[...(base.q ? [{ value: "relevance", label: "Best match" }] : []), { value: "latest", label: "Latest" }, { value: "important", label: "Important" }]} />`}
    <div class="toolbar">
      ${base.state === undefined && html`<${Segmented} label="Which items" value=${show} onChange=${(value) => setQuery({ show: value === defaultShow ? undefined : value })}
        options=${[{ value: "active", label: "All" }, { value: "unread", label: "Unread" }, { value: "read", label: "Read" }, { value: "dismissed", label: "Dismissed" }]} />`}
      <select aria-label="Kind of item" value=${kind} onChange=${(event) => setQuery({ kind: event.target.value || undefined })}>
        <option value="">Every kind</option>
        ${Object.entries(KIND_LABELS).map(([value, label]) => html`<option value=${value}>${label}</option>`)}
      </select>
      <span class="grow"></span>
      ${!meta.loading && html`<span class="feed-note">${plural(meta.total, "item")}</span>`}
      ${unreadShown.length > 0 && html`<button class="button small quiet" onClick=${() => api.markRead(unreadShown.map((item) => item.id)).then(() => { unreadShown.forEach((item) => patchItems(item.id, { read: true })); refreshBoot(); })}>
        <${Icon} name="check" size=${15} /> Mark shown as read</button>`}
    </div>
    ${view === "important" && html`<p class="feed-note">Ranked by significance and how closely it matches your interests. Older items stay if they still matter, and return when something new builds on them.</p>`}
    ${meta.error && html`<${ErrorNote} message=${meta.error} retry=${() => load(0)} />`}
    ${meta.loading && items.length === 0 && html`<${Loading} lines=${8} />`}
    ${!meta.loading && !meta.error && visible.length === 0 && html`<${Empty} title=${empty.title} hint=${empty.hint}
      action=${empty.clear && { label: "Clear filters", run: () => go(route.path, {}) }} />`}
    ${visible.length > 0 && html`<ul class="feed">${visible.map((item, index) => html`<${ItemRow} key=${item.id} item=${item} openHref=${href(item)} act=${act}
      cursor=${index === cursor} showSpace=${!base.domain} />`)}</ul>`}
    ${meta.hasMore && html`<div class="more"><button class="button" disabled=${meta.loading} onClick=${() => load(items.length)}>${meta.loading ? "Loading…" : "Show more"}</button></div>`}
  </div>`;
}
