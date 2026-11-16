// The shell: session, routing, navigation and the overlays. Pages live in their own modules.
import { html, render, useCallback, useEffect, useRef, useState } from "../vendor/preact-htm.js";
import { api, setReachabilityHandler, setUnauthorisedHandler } from "./api.js";
import { Feed, TopicPage } from "./legacy.js";
import { ReaderSheet } from "./item.js";
import { Interests, Notifications, Opportunities, Sources, System } from "./pages.js";
import { Empty, ErrorNote, Icon, IconButton, MarginNote, Meta, NotificationsDrawer, Palette, Row, RowActions, Shortcuts, Skeleton, Toasts, applyTheme, go, hueStyle, itemHref, loadSpaceTops, setReadingContext, toast, useItemActions, useItemSync, useLoad, useTriage } from "./ui.js";
import { KIND_LABELS, buildRoute, composeBriefing, dueText, foldOpportunities, parseRoute, plural, priorityDomains, reasons, standfirst, timeAgo } from "./util.js";

// Old addresses that still work: the browser history keeps Back honest because these replace the address.
const REDIRECTS = {
  saved: () => buildRoute(["library"]),
  notifications: () => buildRoute(["settings"]),
  system: () => buildRoute(["settings"], { tab: "system" }),
};

function useRoute() {
  const read = () => {
    const route = parseRoute(location.hash);
    const redirect = REDIRECTS[route.path[0]];
    if (redirect) { location.replace(redirect()); return parseRoute(location.hash); }
    return route;
  };
  const [route, setRoute] = useState(read);
  useEffect(() => {
    const onChange = () => setRoute(read());
    addEventListener("hashchange", onChange);
    return () => removeEventListener("hashchange", onChange);
  }, []);
  return route;
}

try { applyTheme(localStorage.getItem("pia.theme")); } catch { /* storage blocked */ }

// ---------- navigation ----------

function RailLink({ href, icon, label, current, extra, hue }) {
  return html`<a class="link" href=${href} aria-current=${current ? "page" : undefined} style=${hue !== undefined ? hueStyle(hue) : undefined}>
    <span class="ic">${hue !== undefined ? html`<span class="swatch"></span>` : html`<${Icon} name=${icon} size=${18} />`}</span>
    <span class="label">${label}</span>${extra || html`<span></span>`}</a>`;
}

function Rail({ boot, route, dueSoon }) {
  const [section, slug] = route.path;
  const at = (name) => (section || "today") === name;
  const agent = boot.agent;
  const pinned = priorityDomains(boot.domains);
  const status = agent.running ? "Agent working" : agent.background ? "Agent running" : "Agent off";
  return html`<aside class="rail" aria-label="Main navigation">
    <a class="brand" href="#/"><b>PIA</b><span>research desk</span></a>
    <nav class="nav" aria-label="Main">
      <${RailLink} href="#/" icon="today" label="Today" current=${at("today")} />
      <${RailLink} href="#/spaces" icon="spaces" label="Spaces" current=${at("spaces")} />
      <${RailLink} href="#/opportunities" icon="flag" label="Opportunities" current=${at("opportunities")}
        extra=${dueSoon ? html`<span class="badge-due" title=${`${dueSoon} close within 3 days`}>${dueSoon} due</span>` : null} />
      <${RailLink} href="#/library" icon="library" label="Library" current=${at("library")} />
    </nav>
    <nav class="nav spaces-nav" aria-label="Priority spaces">
      <div class="nav-head"><span>Priority spaces</span><a href="#/interests">Edit</a></div>
      ${pinned.map((domain) => html`<${RailLink} href=${buildRoute(["space", domain.slug])} hue=${domain.hue} label=${domain.short} current=${section === "space" && slug === domain.slug} />`)}
      <${RailLink} href="#/spaces" icon="more" label=${`All ${boot.domains.length} spaces`} current=${false} />
    </nav>
    <span class="grow"></span>
    <nav class="nav" aria-label="Manage">
      <${RailLink} href="#/interests" icon="sliders" label="Interests" current=${at("interests")} />
      <${RailLink} href="#/sources" icon="rss" label="Sources" current=${at("sources")} />
      <${RailLink} href="#/settings" icon="gear" label="Settings" current=${at("settings")} />
    </nav>
    <div class="agent" title="The agent checks sources on a schedule"><span class=${"pulse" + (agent.running || agent.background ? "" : " off")}></span><b>${status}</b>
      ${agent.last_run && agent.last_run.finished_at ? html`<span>Last cycle ${timeAgo(agent.last_run.finished_at)}</span>` : html`<span></span>`}</div>
  </aside>`;
}

function TopBar({ boot, onSearch, onKeys, onBell, onSignOut }) {
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => {
    const onScroll = () => setScrolled(scrollY > 8);
    addEventListener("scroll", onScroll, { passive: true });
    return () => removeEventListener("scroll", onScroll);
  }, []);
  const unseen = boot.counts.notifications;
  return html`<div class=${"topbar" + (scrolled ? " scrolled" : "")}>
    <button class="searchbox" onClick=${onSearch} aria-label="Search and go (slash or Ctrl K)"><${Icon} name="search" size=${16} /><span>Search, or jump to a space</span><kbd>/</kbd></button>
    <span class="grow"></span>
    <${IconButton} icon="keyboard" label="Keyboard shortcuts (?)" onClick=${onKeys} size=${18} className="wide-only" />
    <button class=${"icon-btn" + (unseen ? " with-count" : "")} title=${`Notifications, ${unseen} unseen`} aria-label=${`Notifications, ${unseen} unseen`} onClick=${onBell}>
      <${Icon} name="bell" />${unseen > 0 && html`<span class="count">${unseen}</span>`}</button>
    ${boot.auth_required && html`<button class="btn small quiet" onClick=${onSignOut}>Sign out</button>`}
  </div>`;
}

function TabBar({ route }) {
  const [section] = route.path;
  const tab = (href, icon, label, current) => html`<a href=${href} aria-current=${current ? "page" : undefined}><${Icon} name=${icon} size=${20} />${label}</a>`;
  return html`<nav class="tabbar" aria-label="Main">
    ${tab("#/", "today", "Today", !section)}
    ${tab("#/spaces", "spaces", "Spaces", ["spaces", "space", "topic"].includes(section))}
    ${tab("#/opportunities", "flag", "Deadlines", section === "opportunities")}
    ${tab("#/library", "library", "Library", section === "library")}
    ${tab("#/settings", "gear", "More", ["settings", "interests", "sources"].includes(section))}
  </nav>`;
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
    <h1>PIA</h1><p>Enter your password to open your research desk.</p>
    <label class="sr" for="password">Password</label>
    <input id="password" class="field" type="password" autocomplete="current-password" autofocus value=${password} onInput=${(event) => setPassword(event.target.value)} />
    ${error && html`<p class="callout warn" role="alert">${error}</p>`}
    <button class="btn primary" type="submit">Sign in</button>
  </form></main>`;
}

// ---------- Today ----------

const norm = (text) => (text || "").toLowerCase().replace(/[^a-z0-9]+/g, "");

function Masthead({ boot, briefing, loading, opps }) {
  const now = new Date();
  const hour = now.getHours();
  const newCount = boot.domains.reduce((sum, domain) => sum + domain.new_today, 0);
  const parts = standfirst(newCount, briefing.length, opps);
  const run = boot.agent.last_run;
  return html`<header class="masthead">
    <div>
      <h1>${now.toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" })}</h1>
      <p class="standfirst">PIA read <b class="num">${parts.count}</b> ${parts.noun} since yesterday${loading ? "." : html` and chose <b>${parts.chosen}</b> for you.`}
        ${parts.link && html` <a class="textlink" href=${buildRoute([], { item: parts.link.id })}>${parts.link.label}</a> ${parts.link.tail}`}</p>
    </div>
    <div class="edition"><b>${hour < 12 ? "Morning" : hour < 17 ? "Afternoon" : "Evening"} edition</b>
      ${run && run.finished_at && html`<span>Updated ${timeAgo(run.finished_at)}</span>`}</div>
  </header>`;
}

function OnboardingBanner({ boot, refreshBoot }) {
  const onboarding = boot.onboarding;
  if (onboarding.dismissed || onboarding.rated >= 10) return null;
  return html`<div class="onboard"><span><b>Rankings use starting weights.</b> Rate ten topics you recognise and tomorrow's briefing will reflect you. It takes about two minutes.</span>
    <div class="acts"><a class="btn small primary" href="#/interests?tab=rate">Rate topics</a>
      <button class="btn small quiet" onClick=${() => api.dismissOnboarding().then(refreshBoot, (failure) => toast(failure.message, { error: true }))}>Not now</button></div></div>`;
}

function Briefing({ items, cursor, act, route, opps, onMarkRead }) {
  const read = items.filter((item) => item.read).length;
  return html`<div>
    <ol class="briefing" aria-label="Today's briefing, in order of priority">
      ${items.map((item, index) => {
        const opp = item.kind === "opportunity" ? opps.get(item.id) : null;
        const days = opp && opp.days_left;
        return html`<li class=${"entry" + (index === 0 ? " lead" : "") + (item.read ? " read" : "") + (cursor === index ? " cursor" : "")} key=${item.id} data-id=${item.id}>
          <span class="ord" aria-hidden="true">${index + 1}</span>
          <div class="body">
            <${Meta} item=${item} extra=${opp && days >= 0 ? html`<span class=${"due" + (days <= 2 ? " hot" : "")}>${dueText(days)}</span>` : null} />
            <h2><a href=${itemHref(route, item.id)}>${item.title}</a></h2>
            ${item.summary && html`<p class="sum">${item.summary}</p>`}
            <${RowActions} item=${item} act=${act} />
          </div>
          <${MarginNote} reasons=${reasons(item, opp)} />
        </li>`;
      })}
    </ol>
    <div class="briefing-end"><div>
      <span class="progress" role="img" aria-label=${`${read} of ${items.length} read`}>${items.map((item) => html`<i class=${item.read ? "on" : ""}></i>`)}</span>
      <span>${read === items.length ? "You have read today's briefing." : `${read} of ${items.length} read`}</span>
      ${read < items.length && html`<button class="textlink" onClick=${onMarkRead}>Mark briefing read</button>`}
    </div></div>
  </div>`;
}

function DeadlineScale({ opps, route }) {
  const today = new Date();
  const days = Array.from({ length: 14 }, (_, index) => new Date(today.getFullYear(), today.getMonth(), today.getDate() + index));
  const byDay = {};
  for (const o of opps) if (o.days_left !== null && o.days_left >= 0 && o.days_left < 14) (byDay[o.days_left] ||= []).push(o);
  const undated = opps.filter((o) => o.days_left === null).length;
  const dated = opps.filter((o) => o.days_left !== null && o.days_left >= 0 && o.days_left < 14).length;
  return html`<div>
    <div class="scale" role="list">
      ${days.map((day, index) => html`<div role="listitem" class=${"day" + (index === 0 ? " today" : "") + ([0, 6].includes(day.getDay()) ? " weekend" : "")}>
        <span class="d">${index === 0 ? "Today" : day.toLocaleDateString("en-GB", { weekday: "short", day: "numeric" })}</span>
        ${(byDay[index] || []).slice(0, 3).map((o) => html`<a class=${"dl" + (index <= 2 ? " hot" : "")} href=${itemHref(route, o.item_id)} title=${`${o.programme || o.title}, ${o.organisation || o.source_name}`}>${o.programme || o.title}</a>`)}
        ${(byDay[index] || []).length > 3 && html`<span>+${byDay[index].length - 3} more</span>`}
      </div>`)}
    </div>
    ${dated === 0 && html`<p class="undated">No dated opportunities in the next two weeks. <a class="textlink" href="#/opportunities">Browse opportunities</a>.</p>`}
    ${undated > 0 && html`<p class="undated">${plural(undated, "open opportunity", "open opportunities")} ${undated === 1 ? "has" : "have"} no stated deadline. <a class="textlink" href="#/opportunities">See them in Opportunities</a>.</p>`}
  </div>`;
}

function LeadingInSpaces({ boot, tops, shownIds, route }) {
  const spaces = [...boot.domains].filter((domain) => domain.weight > 0).sort((a, b) => b.weight - a.weight).slice(0, 7);
  const headlines = new Set();
  const lines = spaces.map((domain) => {
    const top = (tops[domain.slug] || []).find((item) => !item.spam && !shownIds.has(item.id) && !(item.kind === "repo" && !((item.signals && item.signals.stars) >= 1000)) && !headlines.has(norm(item.title).slice(0, 24)));
    if (top) headlines.add(norm(top.title).slice(0, 24));
    return { domain, top };
  });
  return html`<section class="section" aria-labelledby="sp-h">
    <div class="section-head"><h2 id="sp-h">Leading in your spaces</h2><span class="grow"></span><a href="#/spaces">All spaces</a></div>
    <div class="spaces-index one">${lines.map(({ domain, top }) => html`<div class="space-line" style=${hueStyle(domain.hue)}><span class="swatch"></span><a href=${buildRoute(["space", domain.slug])}><b>${domain.short}</b></a>
      ${top && html`<a class="top" href=${itemHref(route, top.id)}>${top.title}</a>`}</div>`)}</div>
  </section>`;
}

function SavedForLater({ saved, act, route }) {
  return html`<section class="section" aria-labelledby="sv-h">
    <div class="section-head"><h2 id="sv-h">Saved for later</h2><span class="grow"></span><a href="#/library">Library</a></div>
    ${saved === null ? html`<${Skeleton} rows=${1} />` : saved.length === 0
      ? html`<p class="feed-note">Nothing saved yet. Press <kbd>S</kbd> on any item, or use the bookmark, and it waits here and in your Library.</p>`
      : html`<ul class="feed">${saved.slice(0, 4).map((item) => html`<li class="line compact" key=${item.id}><div class="t"><a href=${itemHref(route, item.id)}>${item.title}</a><small>${KIND_LABELS[item.kind]}  ·  ${item.source}</small></div><${RowActions} item=${item} act=${act} /></li>`)}</ul>`}
  </section>`;
}

function Today({ boot, refreshBoot, route, opportunities }) {
  const home = useLoad(() => api.home(), [boot.agent.last_run && boot.agent.last_run.finished_at]);
  const more = useLoad(() => api.items({ view: "important", state: "unread", limit: 8, offset: 0 }), []);
  const savedList = useLoad(() => api.items({ state: "saved", limit: 4 }), []);
  const spaces = [...boot.domains].filter((domain) => domain.weight > 0).sort((a, b) => b.weight - a.weight).slice(0, 7);
  const tops = useLoad(() => loadSpaceTops(spaces.map((domain) => domain.slug)), [spaces.map((domain) => domain.slug).join()]);
  const [extra, setExtra] = useState({ items: [], hasMore: null, busy: false });

  const patchItems = useCallback((id, change) => {
    const apply = (list) => list.map((item) => (item.id === id ? { ...item, ...change } : item));
    home.patch((current) => current && { ...current, featured: apply(current.featured) });
    more.patch((current) => current && { ...current, items: apply(current.items) });
    savedList.patch((current) => current && { ...current, items: apply(current.items) });
    tops.patch((current) => current && Object.fromEntries(Object.entries(current).map(([slug, list]) => [slug, apply(list)])));
    setExtra((current) => ({ ...current, items: apply(current.items) }));
  }, [home.patch, more.patch, savedList.patch, tops.patch]);
  useItemSync(patchItems);
  const baseAct = useItemActions(patchItems, refreshBoot);
  const act = useCallback(async (item, change) => { await baseAct(item, change); if ("saved" in change) savedList.reload(); }, [baseAct, savedList.reload]);

  const briefing = home.data ? composeBriefing(home.data.featured) : [];
  const briefIds = new Set(briefing.map((item) => item.id));
  const opps = opportunities ? foldOpportunities(opportunities.opportunities) : [];
  const oppById = new Map(opps.map((o) => [o.item_id, o]));
  const moreItems = [...(more.data ? more.data.items : []), ...extra.items].filter((item) => !briefIds.has(item.id) && !item.dismissed && !item.spam);
  const hasMore = extra.hasMore === null ? Boolean(more.data && more.data.has_more) : extra.hasMore;
  const offset = (more.data ? more.data.items.length : 0) + extra.items.length;
  const showMore = () => {
    setExtra((current) => ({ ...current, busy: true }));
    api.items({ view: "important", state: "unread", limit: 8, offset }).then(
      (page) => setExtra((current) => ({ items: [...current.items, ...page.items], hasMore: page.has_more, busy: false })),
      (failure) => { setExtra((current) => ({ ...current, busy: false })); toast(failure.message, { error: true }); },
    );
  };
  const href = (item) => itemHref(route, item.id);
  const [cursor] = useTriage(briefing, act, href);
  useEffect(() => { setReadingContext(briefing.map((item) => item.id).concat(moreItems.map((item) => item.id)), "Today"); });

  const markBriefingRead = () => {
    const unread = briefing.filter((item) => !item.read);
    api.markRead(unread.map((item) => item.id)).then(() => { unread.forEach((item) => patchItems(item.id, { read: true })); refreshBoot(); toast("Briefing marked as read"); },
      (failure) => toast(failure.message, { error: true }));
  };
  const nothingYet = boot.counts.items === 0;
  const dated = opps.filter((o) => o.days_left !== null && o.days_left >= 0 && o.days_left < 14).length;

  return html`<div class="page">
    <${Masthead} boot=${boot} briefing=${briefing} loading=${home.loading && !home.data} opps=${opps} />
    <${OnboardingBanner} boot=${boot} refreshBoot=${refreshBoot} />
    ${home.loading && !home.data && html`<${Skeleton} rows=${4} />`}
    ${home.error && html`<div class="pad-top"><${ErrorNote} message=${home.error} retry=${home.reload} /></div>`}
    ${home.data && briefing.length > 0 && html`<${Briefing} items=${briefing} cursor=${cursor} act=${act} route=${route} opps=${oppById} onMarkRead=${markBriefingRead} />`}
    ${home.data && briefing.length === 0 && (nothingYet
      ? html`<${Empty} title="PIA is gathering its first items" action=${html`<a class="btn" href="#/sources">Check sources</a>`}>Nothing has been acquired yet. Sources shows what PIA is watching.<//>`
      : html`<${Empty} title="You have read today's briefing" action=${html`<a class="btn" href="#/spaces">Explore your spaces</a>`}>Nothing new has cleared the bar since this morning. PIA checks again on its next cycle.<//>`)}

    <section class="section strip" aria-labelledby="dl-h">
      <div class="section-head"><h2 id="dl-h">Next two weeks</h2><span class="aside">${plural(dated, "open deadline")}</span><span class="grow"></span><a href="#/opportunities">All opportunities</a></div>
      <${DeadlineScale} opps=${opps} route=${route} />
    </section>

    <div class="two-col">
      <${LeadingInSpaces} boot=${boot} tops=${tops.data || {}} shownIds=${briefIds} route=${route} />
      <${SavedForLater} saved=${savedList.data ? savedList.data.items.filter((item) => item.saved && !item.dismissed) : savedList.error ? [] : null} act=${act} route=${route} />
    </div>

    <section class="section" aria-labelledby="mf-h">
      <div class="section-head"><h2 id="mf-h">More for you</h2><span class="aside">Ranked across every space</span></div>
      ${more.loading && !more.data && html`<${Skeleton} rows=${3} />`}
      ${more.error && html`<${ErrorNote} message=${more.error} retry=${more.reload} />`}
      <ul class="feed">${moreItems.map((item) => html`<${Row} key=${item.id} item=${item} href=${href(item)} act=${act} showSpace=${true} opp=${oppById.get(item.id)} />`)}</ul>
      ${hasMore && html`<div class="more"><button class="btn" disabled=${extra.busy} onClick=${showMore}>${extra.busy ? "Loading…" : "Show more"}</button></div>`}
    </section>
  </div>`;
}

// ---------- the app ----------

function SpacesStub({ boot }) {  // replaced by the Spaces index in step 6
  return html`<div class="page"><header class="page-head"><h1>Spaces</h1></header>
    <ul class="feed">${boot.domains.map((domain) => html`<li class="row"><a href=${buildRoute(["space", domain.slug])}>${domain.name}</a></li>`)}</ul></div>`;
}

function App() {
  const route = useRoute();
  const [session, setSession] = useState(null);
  const [boot, setBoot] = useState(null);
  const [fatal, setFatal] = useState(null);
  const [offline, setOffline] = useState(false);
  const [opportunities, setOpportunities] = useState(null);
  const [palette, setPalette] = useState(false);
  const [drawer, setDrawer] = useState(false);
  const [keys, setKeys] = useState(false);
  const chord = useRef(0);

  // Opportunities are loaded once per session and again whenever the bootstrap refreshes (the rail's "due" badge needs them).
  const refreshBoot = useCallback(() => {
    api.opportunities({ show: "open" }).then(setOpportunities, () => {});
    return api.bootstrap().then(setBoot, (error) => error.status !== 401 && setFatal(error.message));
  }, []);
  const start = useCallback(() => api.session().then((info) => { setSession(info); if (info.signed_in) refreshBoot(); }, (error) => setFatal(error.message)), [refreshBoot]);
  useEffect(() => { setUnauthorisedHandler(() => setSession({ auth_required: true, signed_in: false })); setReachabilityHandler((reachable) => setOffline(!reachable)); start(); }, []);
  useEffect(() => {  // keep the agent's state fresh without hammering the server
    if (!session || !session.signed_in) return undefined;
    const timer = setInterval(() => document.visibilityState === "visible" && refreshBoot(), 45000);
    return () => clearInterval(timer);
  }, [session, refreshBoot]);

  const pathKey = route.path.join("/");
  useEffect(() => { scrollTo(0, 0); }, [pathKey]);

  useEffect(() => {
    const onKey = (event) => {
      const typing = /INPUT|TEXTAREA|SELECT/.test(event.target.tagName);
      if (event.key === "k" && (event.ctrlKey || event.metaKey)) { event.preventDefault(); setPalette(true); return; }
      if (event.metaKey || event.ctrlKey || event.altKey || typing || document.body.dataset.overlay) return;
      if (event.key === "/") { event.preventDefault(); setPalette(true); return; }
      if (event.key === "?") { setKeys(true); return; }
      if (event.key === "g") { chord.current = Date.now(); return; }
      if (Date.now() - chord.current < 900) {
        const destination = { t: [], s: ["spaces"], o: ["opportunities"], l: ["library"], i: ["interests"] }[event.key];
        if (destination) { chord.current = 0; event.preventDefault(); go(destination); }
      }
    };
    addEventListener("keydown", onKey);
    return () => removeEventListener("keydown", onKey);
  }, []);

  if (fatal && !boot) return html`<main class="signin"><div><h1>PIA</h1><p class="callout warn">${fatal}</p><button class="btn" onClick=${() => { setFatal(null); start(); }}>Try again</button></div></main>`;
  if (!session || (session.signed_in && !boot)) return html`<p class="boot">Starting PIA…</p>`;
  if (!session.signed_in) return html`<${SignIn} onDone=${start} />`;

  const [section, arg] = route.path;
  const common = { boot, refreshBoot, route };
  let page;
  if (!section) page = html`<${Today} ...${common} opportunities=${opportunities} />`;
  else if (section === "spaces") page = html`<${SpacesStub} boot=${boot} />`;
  else if (section === "space") {
    const domain = boot.domains.find((entry) => entry.slug === arg);
    page = domain
      ? html`<${Feed} ...${common} key=${arg} base=${{ domain: arg }} title=${domain.name} description=${domain.description} hue=${domain.hue} />`
      : html`<div class="page"><${Empty} title="No such space">The link may be out of date. <a class="textlink" href="#/spaces">See all spaces</a>.<//></div>`;
  } else if (section === "library") page = html`<${Feed} ...${common} key="saved" base=${{ state: "saved" }} defaultShow="saved" title="Library" description="Everything you chose to keep." allowViews=${false} />`;
  else if (section === "search") page = html`<${Feed} ...${common} key=${"q" + route.query.q} base=${{ q: route.query.q || "" }} defaultShow="all" title=${`Search: ${route.query.q || ""}`} description="Across everything PIA has acquired." />`;
  else if (section === "topic") page = html`<${TopicPage} ...${common} key=${arg} id=${Number(arg)} />`;
  else if (section === "opportunities") page = html`<${Opportunities} ...${common} />`;
  else if (section === "sources") page = html`<${Sources} ...${common} />`;
  else if (section === "interests") page = html`<${Interests} ...${common} />`;
  else if (section === "settings") page = route.query.tab === "system" ? html`<${System} ...${common} />` : html`<${Notifications} ...${common} />`;
  else page = html`<div class="page"><${Empty} title="Page not found">The link may be out of date. <a class="textlink" href="#/">Go to Today</a>.<//></div>`;

  const dueSoon = opportunities ? foldOpportunities(opportunities.opportunities).filter((o) => o.days_left !== null && o.days_left >= 0 && o.days_left <= 3).length : 0;
  const closeItem = () => go(route.path, { ...route.query, item: undefined });
  return html`<div class="shell">
    <${Rail} boot=${boot} route=${route} dueSoon=${dueSoon} />
    <main class="main" id="main">
      <${TopBar} boot=${boot} onSearch=${() => setPalette(true)} onKeys=${() => setKeys(true)} onBell=${() => setDrawer(true)} onSignOut=${() => api.logout().then(start)} />
      ${offline && html`<div class="offline" role="alert">PIA is not reachable. Check that it is running (run-pia.bat), then try again. <button class="btn small" onClick=${refreshBoot}>Try again</button></div>`}
      ${page}
    </main>
    <${TabBar} route=${route} />
    ${route.query.item && html`<${ReaderSheet} id=${Number(route.query.item)} close=${closeItem} refreshBoot=${refreshBoot} route=${route} boot=${boot} />`}
    ${drawer && html`<${NotificationsDrawer} onClose=${() => setDrawer(false)} refreshBoot=${refreshBoot} />`}
    ${palette && html`<${Palette} onClose=${() => setPalette(false)} domains=${boot.domains} />`}
    ${keys && html`<${Shortcuts} onClose=${() => setKeys(false)} />`}
    <${Toasts} />
  </div>`;
}

render(html`<${App} />`, document.getElementById("app"));
