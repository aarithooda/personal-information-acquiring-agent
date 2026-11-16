// TEMPORARY (removed before step 6 ends): the old Home, Feed and Topic pages and their row components, kept so every
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

function Spectrum({ domains }) {
  const total = domains.reduce((sum, domain) => sum + domain.unread, 0);
  return html`<nav aria-label="Unread by space">
    <div class="spectrum">
      ${domains.map((domain) => html`<a href=${buildRoute(["space", domain.slug])} class=${domain.unread ? "" : "quiet"} style=${{ ...hueStyle(domain.hue), flexGrow: 0.7 + Math.sqrt(domain.unread) }}
        title=${`${domain.name}: ${plural(domain.unread, "unread item")}`}>
        <b>${domain.unread || ""}</b><span>${domain.short}</span></a>`)}
    </div>
    <div class="spectrum-key"><span>Each band is one of your spaces, sized by what is unread</span><span>${plural(total, "unread item")}</span></div>
  </nav>`;
}

export function Home({ boot, refreshBoot, route }) {
  const { loading, data, error, reload, patch } = useLoad(() => api.home(), [boot.agent.last_run && boot.agent.last_run.finished_at]);
  const patchItems = useCallback((id, change) => patch((current) => current && { ...current, featured: current.featured.map((item) => (item.id === id ? { ...item, ...change } : item)) }), [patch]);
  const act = useItemActions(patchItems, refreshBoot);
  const newToday = boot.domains.reduce((sum, domain) => sum + domain.new_today, 0);
  const active = boot.domains.filter((domain) => domain.new_today > 0).length;
  const href = (item) => buildRoute(route.path, { ...route.query, item: item.id });
  const featured = data ? data.featured.filter((item) => !item.dismissed) : [];
  const [lead, ...rest] = featured;
  const onboarding = boot.onboarding;

  return html`<div class="page">
    <header class="page-head">
      <p class="home-lede">${newToday
        ? html`<b>${plural(newToday, "new item")}</b> in the last day across ${plural(active, "space")}. Here is what deserves your attention first.`
        : html`Nothing new in the last day. ${boot.counts.items ? "Here is what is still worth your attention." : "PIA is gathering its first items."}`}</p>
      <${Spectrum} domains=${boot.domains} />
    </header>
    <div class="home-grid">
      <section aria-label="Featured">
        <div class="section-title"><h2>Featured for you</h2>
          ${featured.length > 0 && html`<button onClick=${() => api.markRead(featured.map((item) => item.id)).then(() => { reload(); refreshBoot(); })}>Mark these read</button>`}</div>
        ${loading && !data && html`<${Loading} lines=${6} />`}
        ${error && html`<${ErrorNote} message=${error} retry=${reload} />`}
        ${data && featured.length === 0 && html`<${Empty} title="You are caught up" hint="Nothing unread is waiting on Home. Every space still has its full feed." />`}
        ${lead && html`<article class="lead" style=${hueStyle(lead.hue)}>
          <div class="row-meta"><span class="kind">${KIND_LABELS[lead.kind]}</span><span>${lead.source}</span><span>${lead.domain_short}</span><time>${timeAgo(lead.sort_at)}</time></div>
          <h3 class="row-title"><a href=${href(lead)}>${lead.title}</a></h3>
          ${lead.summary && html`<p class="row-summary">${lead.summary}</p>`}
          <div class="lead-foot">
            <${Marks} value=${lead.importance} />
            ${(lead.topics || []).map((topic) => html`<a class=${"chip" + (topic.level >= 2 ? " liked" : "")} href=${buildRoute(["topic", String(topic.id)])}>${topic.name}</a>`)}
            <span class="grow"></span>
            <button class=${"button small" + (lead.saved ? " on" : "")} onClick=${() => act(lead, { saved: !lead.saved })}><${Icon} name="bookmark" size=${15} fill=${lead.saved} /> ${lead.saved ? "Saved" : "Save"}</button>
            <a class="button small primary" href=${href(lead)}>Read the brief</a>
          </div>
        </article>`}
        ${rest.length > 0 && html`<ul class="feed">${rest.map((item) => html`<${ItemRow} key=${item.id} item=${item} openHref=${href(item)} act=${act} />`)}</ul>`}
      </section>
      <aside class="rail">
        ${!onboarding.dismissed && onboarding.rated < 25 && html`<div class="prompt-card">
          <h3>Tell PIA what you care about</h3>
          <p>Rate a few topics per space so ranking reflects you. It takes a couple of minutes and you can stop at any point.</p>
          <div class="meter"><i style=${{ width: `${Math.min(100, (onboarding.rated / 25) * 100)}%` }}></i></div>
          <a class="button small primary" href=${buildRoute(["interests"])}>Rate topics</a>
          <button class="button small quiet" onClick=${() => api.dismissOnboarding().then(refreshBoot)}>Not now</button>
        </div>`}
        <section>
          <div class="section-title"><h2>Closing soon</h2><a href=${buildRoute(["opportunities"])}>All opportunities</a></div>
          ${data && data.deadlines.length === 0 && html`<p class="feed-note">No dated opportunities yet. Add the organisations you want watched under Opportunities.</p>`}
          <ul class="rail-list">${(data ? data.deadlines : []).map((o) => html`<li><a href=${buildRoute(route.path, { ...route.query, item: o.item_id })}>
            <span class="t">${o.programme || o.title}</span>
            <span class=${"due" + (o.days_left <= 7 ? " soon" : "")}>${deadlineText(o.days_left)}</span> <span class="s">${o.organisation || o.source_name}</span></a></li>`)}</ul>
        </section>
        <section>
          <div class="section-title"><h2>Your spaces</h2></div>
          <ul class="rail-list">${[...boot.domains].sort((a, b) => b.new_today - a.new_today).slice(0, 6).map((domain) => html`<li><a href=${buildRoute(["space", domain.slug])}>
            <span class="t">${domain.short}</span><span class="s">${plural(domain.new_today, "new item")} today, ${domain.unread} unread</span></a></li>`)}</ul>
        </section>
      </aside>
    </div>
  </div>`;
}

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

export function TopicPage({ boot, refreshBoot, route, id }) {
  const { data, error, reload } = useLoad(() => api.topic(id), [id]);
  if (error) return html`<div class="page"><${ErrorNote} message=${error} retry=${reload} /></div>`;
  if (!data) return html`<div class="page"><${Loading} /></div>`;
  const topic = data.topic;
  const rate = (level) => api.rateTopic(topic.id, level).then(() => { reload(); refreshBoot(); toast(level === null ? "Rating cleared" : "Interest updated"); });
  return html`<div>
    <div class="page"><div class="toolbar">
      <span>Your interest in this ${topic.kind === "topic" ? "topic" : "name"}:</span>
      <div class="rate">${[[3, "Priority"], [2, "High"], [1, "Some"], [-1, "None"], [-2, "Mute"]].map(([level, label]) => html`<button class=${level < 0 ? "neg" : ""}
        aria-pressed=${topic.level === level} onClick=${() => rate(topic.level === level ? null : level)}>${label}</button>`)}</div>
    </div></div>
    <${Feed} boot=${boot} refreshBoot=${refreshBoot} route=${route} base=${{ topic: topic.id }} title=${topic.name}
      description=${`A timeline of everything PIA has acquired about this, newest first. ${plural(topic.item_count, "item")}${topic.domain ? ` in ${topic.domain}` : ""}.`} />
  </div>`;
}

