// The shell: session, routing, navigation and the overlays. Pages live in their own modules.
import { html, render, useCallback, useEffect, useRef, useState } from "../vendor/preact-htm.js";
import { api, setReachabilityHandler, setUnauthorisedHandler } from "./api.js";
import { Feed, Home, TopicPage } from "./legacy.js";
import { ItemPanel } from "./item.js";
import { Interests, Notifications, Opportunities, Sources, System } from "./pages.js";
import { Empty, Icon, IconButton, NotificationsDrawer, Palette, Shortcuts, Toasts, applyTheme, go, hueStyle } from "./ui.js";
import { buildRoute, foldOpportunities, parseRoute, priorityDomains, timeAgo } from "./util.js";

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
  if (!section) page = html`<${Home} ...${common} />`;
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
    ${route.query.item && html`<${ItemPanel} key=${route.query.item} id=${Number(route.query.item)} close=${closeItem} refreshBoot=${refreshBoot} route=${route} boot=${boot} />`}
    ${drawer && html`<${NotificationsDrawer} onClose=${() => setDrawer(false)} refreshBoot=${refreshBoot} />`}
    ${palette && html`<${Palette} onClose=${() => setPalette(false)} domains=${boot.domains} />`}
    ${keys && html`<${Shortcuts} onClose=${() => setKeys(false)} />`}
    <${Toasts} />
  </div>`;
}

render(html`<${App} />`, document.getElementById("app"));
