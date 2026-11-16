// The shell: session, routing, navigation, Home and the feeds. Other pages live in pages.js; the reader in item.js.
import { html, render, useCallback, useEffect, useRef, useState } from "../vendor/preact-htm.js";
import { api, setUnauthorisedHandler } from "./api.js";
import { ItemPanel } from "./item.js";
import { Interests, Notifications, Opportunities, Sources, System } from "./pages.js";
import { Empty, ErrorNote, Icon, ItemRow, Loading, Marks, Segmented, Tabs, Toasts, hueStyle, toast, useLoad } from "./ui.js";
import { KIND_LABELS, buildRoute, deadlineText, emptyMessage, parseRoute, plural, timeAgo } from "./util.js";

const PAGE = 30;

// Theme: System (default), Paper or Night desk. Stored in localStorage as "pia.theme" and applied as data-theme on <html>.
export function applyTheme(choice) {
  try {
    if (choice === "light" || choice === "dark") document.documentElement.dataset.theme = choice;
    else delete document.documentElement.dataset.theme;
  } catch { /* no DOM */ }
}
try { applyTheme(localStorage.getItem("pia.theme")); } catch { /* storage blocked */ }

function useRoute() {
  const [route, setRoute] = useState(() => parseRoute(location.hash));
  useEffect(() => {
    const onChange = () => setRoute(parseRoute(location.hash));
    addEventListener("hashchange", onChange);
    return () => removeEventListener("hashchange", onChange);
  }, []);
  return route;
}

function go(path, query) {
  location.hash = buildRoute(path, query);
}

// Apply a state change to one item everywhere: optimistically in the list, then on the server.
function useItemActions(patchItems, refreshBoot) {
  return useCallback(async (item, change) => {
    const before = { read: item.read, saved: item.saved, dismissed: item.dismissed };
    const after = { ...before, ...change };
    if (change.saved || change.dismissed) after.read = true;
    patchItems(item.id, after);
    try {
      await api.setState(item.id, change);
      refreshBoot();
      if (change.dismissed) toast("Dismissed", { undo: () => api.setState(item.id, { dismissed: false }).then(() => { patchItems(item.id, { dismissed: false }); refreshBoot(); }) });
      else if (change.saved) toast("Saved");
    } catch (error) {
      patchItems(item.id, before);
      toast(error.message, { error: true });
    }
  }, [patchItems, refreshBoot]);
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

function Home({ boot, refreshBoot, route }) {
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

function Feed({ boot, refreshBoot, route, base, title, description, hue, allowViews = true, defaultShow = "active", emptyState }) {
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

function TopicPage({ boot, refreshBoot, route, id }) {
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

// ---------- shell ----------

function NavLink({ href, current, icon, hue, children, count, hot }) {
  return html`<a href=${href} aria-current=${current ? "page" : undefined} style=${hue !== undefined ? hueStyle(hue) : undefined}>
    ${hue !== undefined ? html`<span class="swatch"></span>` : html`<span class="glyph"><${Icon} name=${icon} size=${16} /></span>`}
    <span>${children}</span>
    ${count ? html`<span class=${"count" + (hot ? " hot" : "")}>${count}</span>` : html`<span></span>`}
  </a>`;
}

function Sidebar({ boot, route, open, close }) {
  const [section, slug] = route.path;
  const at = (name) => (section || "home") === name;
  const agent = boot.agent;
  return html`<aside class=${"side" + (open ? " open" : "")} onClick=${close}>
    <a class="brand" href="#/"><b>PIA</b><span>your information desk</span></a>
    <nav class="nav" aria-label="Main">
      <${NavLink} href="#/" current=${at("home")} icon="home">Home<//>
      <${NavLink} href="#/opportunities" current=${at("opportunities")} icon="target" count=${boot.counts.opportunities}>Opportunities<//>
      <${NavLink} href="#/saved" current=${at("saved")} icon="bookmark" count=${boot.counts.saved}>Saved<//>
      <${NavLink} href="#/notifications" current=${at("notifications")} icon="bell" count=${boot.counts.notifications} hot=${true}>Notifications<//>
    </nav>
    <nav class="nav" aria-label="Spaces">
      <div class="nav-title">Spaces</div>
      ${boot.domains.map((domain) => html`<${NavLink} href=${buildRoute(["space", domain.slug])} current=${section === "space" && slug === domain.slug}
        hue=${domain.hue} count=${domain.unread}>${domain.short}<//>`)}
    </nav>
    <nav class="nav" aria-label="Settings">
      <div class="nav-title">Manage</div>
      <${NavLink} href="#/interests" current=${at("interests")} icon="sliders">Interests<//>
      <${NavLink} href="#/sources" current=${at("sources")} icon="rss" count=${boot.counts.recommendations}>Sources<//>
      <${NavLink} href="#/system" current=${at("system")} icon="pulse">System<//>
    </nav>
    <span class="spacer"></span>
    <div class="agent">
      <span class=${"pulse" + (agent.running ? " busy" : agent.background ? "" : " off")}></span>
      ${agent.running ? html`<b>Agent working…</b>` : agent.background ? html`<b>Agent on</b>` : html`<b>Agent off</b>`}
      ${agent.last_run && agent.last_run.finished_at && html`<div>Last run ${timeAgo(agent.last_run.finished_at)}</div>`}
    </div>
  </aside>`;
}

function SignIn({ onDone }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const submit = async (event) => {
    event.preventDefault();
    try {
      await api.login(password);
      onDone();
    } catch (failure) {
      setError(failure.message);
    }
  };
  return html`<main class="signin"><form onSubmit=${submit}>
    <h1>PIA</h1><p>Enter your password to open your information desk.</p>
    <label class="sr" for="password">Password</label>
    <input id="password" class="field" type="password" autocomplete="current-password" autofocus value=${password} onInput=${(event) => setPassword(event.target.value)} />
    ${error && html`<p class="notice error" role="alert">${error}</p>`}
    <button class="button primary" type="submit">Sign in</button>
  </form></main>`;
}

function App() {
  const route = useRoute();
  const [session, setSession] = useState(null);
  const [boot, setBoot] = useState(null);
  const [fatal, setFatal] = useState(null);
  const [menu, setMenu] = useState(false);
  const [search, setSearch] = useState(route.query.q || "");

  const refreshBoot = useCallback(() => api.bootstrap().then(setBoot, (error) => error.status !== 401 && setFatal(error.message)), []);
  const start = useCallback(() => api.session().then((info) => { setSession(info); if (info.signed_in) refreshBoot(); }, (error) => setFatal(error.message)), [refreshBoot]);
  useEffect(() => { setUnauthorisedHandler(() => setSession({ auth_required: true, signed_in: false })); start(); }, []);
  useEffect(() => {  // keep counts and the agent's state fresh without hammering the server
    if (!session || !session.signed_in) return undefined;
    const timer = setInterval(() => document.visibilityState === "visible" && refreshBoot(), 45000);
    return () => clearInterval(timer);
  }, [session, refreshBoot]);
  useEffect(() => { if (route.path[0] === "search") setSearch(route.query.q || ""); }, [route.path[0], route.query.q]);

  if (fatal && !boot) return html`<main class="signin"><div><h1>PIA</h1><p class="notice error">${fatal}</p><button class="button" onClick=${() => { setFatal(null); start(); }}>Try again</button></div></main>`;
  if (!session) return html`<p class="boot">Starting PIA…</p>`;
  if (!session.signed_in) return html`<${SignIn} onDone=${start} />`;
  if (!boot) return html`<p class="boot">Starting PIA…</p>`;

  const [section, arg] = route.path;
  const common = { boot, refreshBoot, route };
  let page;
  if (!section) page = html`<${Home} ...${common} />`;
  else if (section === "space") {
    const domain = boot.domains.find((entry) => entry.slug === arg);
    page = domain
      ? html`<${Feed} ...${common} key=${arg} base=${{ domain: arg }} title=${domain.name} description=${domain.description} hue=${domain.hue} />`
      : html`<div class="page"><${Empty} title="No such space" hint="Pick a space from the sidebar." /></div>`;
  } else if (section === "saved") page = html`<${Feed} ...${common} key="saved" base=${{ state: "saved" }} defaultShow="saved" title="Saved"
      description="Everything you chose to keep, on every device you sign in from." allowViews=${false} />`;
  else if (section === "search") page = html`<${Feed} ...${common} key=${"q" + route.query.q} base=${{ q: route.query.q || "" }} defaultShow="all" title=${`Search: ${route.query.q || ""}`}
      description="Across everything PIA has acquired: titles, source text and summaries." />`;
  else if (section === "topic") page = html`<${TopicPage} ...${common} key=${arg} id=${Number(arg)} />`;
  else if (section === "opportunities") page = html`<${Opportunities} ...${common} />`;
  else if (section === "sources") page = html`<${Sources} ...${common} />`;
  else if (section === "interests") page = html`<${Interests} ...${common} />`;
  else if (section === "notifications") page = html`<${Notifications} ...${common} />`;
  else if (section === "system") page = html`<${System} ...${common} />`;
  else page = html`<div class="page"><${Empty} title="Page not found" hint="Use the sidebar to find your way back." /></div>`;

  const closeItem = () => go(route.path, { ...route.query, item: undefined });
  return html`<div class="shell">
    <${Sidebar} boot=${boot} route=${route} open=${menu} close=${() => setMenu(false)} />
    <main class="main">
      <div class="topbar">
        <button class="icon-button menu-button" aria-label="Menu" onClick=${() => setMenu(!menu)}><${Icon} name="menu" /></button>
        <form class="search" role="search" onSubmit=${(event) => { event.preventDefault(); if (search.trim()) go(["search"], { q: search.trim() }); }}>
          <${Icon} name="search" size=${16} />
          <input type="search" aria-label="Search everything PIA has acquired" placeholder="Search everything PIA has acquired" value=${search} onInput=${(event) => setSearch(event.target.value)} />
        </form>
        ${boot.auth_required && html`<button class="button small quiet" onClick=${() => api.logout().then(start)}>Sign out</button>`}
      </div>
      ${page}
    </main>
    ${route.query.item && html`<${ItemPanel} key=${route.query.item} id=${Number(route.query.item)} close=${closeItem} refreshBoot=${refreshBoot} route=${route} boot=${boot} />`}
    <${Toasts} />
  </div>`;
}

render(html`<${App} />`, document.getElementById("app"));
