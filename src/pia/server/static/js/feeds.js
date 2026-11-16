// Lists of items: the Spaces index, a Space, a Topic, the Library and Search.
import { html, useCallback, useEffect, useRef, useState } from "../vendor/preact-htm.js";
import { api } from "./api.js";
import { ChipGroup, Empty, ErrorNote, Icon, Line, Menu, Row, Skeleton, Tabs, Toggle, go, hueStyle, itemHref, loadSpaceTops, setReadingContext, toast, useItemActions, useItemSync, useLoad, useTriage } from "./ui.js";
import { KIND_SHORT, buildRoute, groupByDay, plural, weightLabel } from "./util.js";

const PAGE = 30;

// Items from /api/items with offset paging. Changing `params` starts again from the first page.
export function usePagedItems(params) {
  const key = JSON.stringify(params);
  const latest = useRef(params);
  latest.current = params;
  const turn = useRef(0);
  const [state, setState] = useState({ items: [], loading: true, error: null, total: 0, hasMore: false });
  const load = useCallback(async (offset) => {
    const mine = ++turn.current;
    setState((current) => ({ ...current, loading: true, error: null }));
    try {
      const page = await api.items({ ...latest.current, limit: PAGE, offset });
      if (mine !== turn.current) return;
      setState((current) => ({ items: offset ? [...current.items, ...page.items] : page.items, loading: false, error: null, total: page.total, hasMore: page.has_more }));
    } catch (failure) {
      if (mine === turn.current) setState((current) => ({ ...current, loading: false, error: failure.message }));
    }
  }, [key]);
  useEffect(() => { setState({ items: [], loading: true, error: null, total: 0, hasMore: false }); load(0); }, [key]);
  const patchItems = useCallback((id, change) => setState((current) => ({ ...current, items: current.items.map((item) => (item.id === id ? { ...item, ...change } : item)) })), []);
  useItemSync(patchItems);
  return { ...state, load, patchItems, loadMore: () => load(state.items.length), reload: () => load(0) };
}

export function MoreButton({ feed }) {
  if (!feed.hasMore) return null;
  return html`<div class="more"><button class="btn" disabled=${feed.loading} onClick=${feed.loadMore}>${feed.loading ? "Loading…" : "Show more"}</button></div>`;
}

const setQuery = (route, change) => go(route.path, { ...route.query, ...change, item: undefined });

// ---------- Spaces index ----------

export function SpacesIndex({ boot, route }) {
  const domains = [...boot.domains].sort((a, b) => b.weight - a.weight);
  const { data, error } = useLoad(() => loadSpaceTops(domains.map((domain) => domain.slug)), [domains.map((domain) => domain.slug).join()]);
  const groups = ["Priority", "Normal", "Less", "Hidden"].map((name) => [name, domains.filter((domain) => weightLabel(domain.weight) === name)]).filter(([, list]) => list.length);
  return html`<div class="page">
    <header class="page-head"><h1>Spaces</h1><p>${domains.length} areas PIA covers for you. Priority spaces feed more of the daily briefing; change a space's weight from its page or from Interests.</p></header>
    ${error && html`<${ErrorNote} message=${error} />`}
    ${groups.map(([name, list]) => html`<section class="section tight" key=${name}><div class="section-head"><h2>${name}</h2><span class="aside">${plural(list.length, "space")}</span></div>
      <div class="spaces-index">${list.map((domain) => {
        const tops = ((data || {})[domain.slug] || []).filter((item) => !item.spam).slice(0, 2);
        return html`<div class="space-line" style=${hueStyle(domain.hue)}><span class="swatch"></span><a href=${buildRoute(["space", domain.slug])}><b>${domain.name}</b></a>
          ${tops.map((item) => html`<a class="top" href=${itemHref(route, item.id)}>${item.title}</a>`)}</div>`;
      })}</div></section>`)}
  </div>`;
}

// ---------- a Space ----------

const WEIGHTS = [
  { value: 1.5, label: "Priority", hint: "More of it in your briefing" }, { value: 1, label: "Normal", hint: "" },
  { value: 0.6, label: "Less", hint: "Only its strongest items reach Today" }, { value: 0, label: "Hidden", hint: "Never on Today; the space stays here" },
];

export function nearestWeight(weight) {
  return WEIGHTS.reduce((best, option) => (Math.abs(option.value - weight) < Math.abs(best.value - weight) ? option : best)).value;
}

function WeightMenu({ domain, refreshBoot }) {
  const current = WEIGHTS.find((option) => option.value === nearestWeight(domain.weight));
  const choose = (value) => {
    const option = WEIGHTS.find((entry) => entry.value === value);
    api.weighDomain(domain.slug, value).then(() => { refreshBoot(); toast(`${domain.short}: ${option.label.toLowerCase()} in your briefing`); }, (failure) => toast(failure.message, { error: true }));
  };
  return html`<${Menu} label=${`In your briefing: ${current.label}`} items=${WEIGHTS} value=${current.value} onSelect=${choose} />`;
}

const PERIODS = { 7: "Past week", 30: "Past month" };

export function Space({ boot, refreshBoot, route, slug }) {
  const domain = boot.domains.find((entry) => entry.slug === slug);
  const view = route.query.view === "latest" ? "latest" : "important";
  const kind = route.query.kind || "";
  const unread = route.query.state === "unread";
  const days = route.query.days || "";
  const summaries = route.query.summaries === "1";
  const feed = usePagedItems({ domain: slug, view, state: unread ? "unread" : "active", kind, days });
  const [kinds, setKinds] = useState([]);
  useEffect(() => {  // kind chips come from the first unfiltered page, so choosing one does not make the others vanish
    if (!kind && feed.items.length) setKinds([...new Set(feed.items.map((item) => item.kind))]);
  }, [feed.items, kind]);

  const act = useItemActions(feed.patchItems, refreshBoot);
  const visible = feed.items.filter((item) => !item.dismissed);
  const href = (item) => itemHref(route, item.id);
  const [cursor] = useTriage(visible, act, href);
  useEffect(() => { setReadingContext(visible.map((item) => item.id), domain ? domain.short : ""); });

  if (!domain) return html`<div class="page"><${Empty} title="No such space">The link may be out of date. <a class="textlink" href="#/spaces">See all spaces</a>.<//></div>`;
  const filtered = Boolean(kind || unread || days);
  const unreadShown = visible.filter((item) => !item.read);
  const clear = () => setQuery(route, { kind: undefined, state: undefined, days: undefined });
  const groups = view === "latest" ? groupByDay(visible) : [];

  let body;
  if (feed.error) body = html`<${ErrorNote} message=${feed.error} retry=${feed.reload} />`;
  else if (feed.loading && !feed.items.length) body = html`<${Skeleton} rows=${5} />`;
  else if (!visible.length) {
    body = filtered
      ? html`<${Empty} title=${unread && !kind && !days ? "You have read everything that matches" : "Nothing matches these filters"} action=${{ label: "Clear filters", run: clear }}>Clear the filters to see the whole space.<//>`
      : html`<${Empty} title="Nothing here yet" action=${html`<a class="btn" href="#/sources">Check sources</a>`}>PIA has not acquired anything for this space. Sources shows what is being watched.<//>`;
  } else if (view === "important") {
    body = html`<ul class="feed">${visible.map((item, index) => html`<${Row} key=${item.id} item=${item} href=${href(item)} act=${act} cursor=${cursor === index} leading=${index < 3 && !filtered} showSpace=${false} />`)}</ul>`;
  } else {
    let index = -1;
    body = groups.map((group) => html`<section key=${group.key}><div class="day-head"><h3>${group.label}</h3><span>${plural(group.items.length, "item")}</span></div>
      <ul class="feed">${group.items.map((item) => { index += 1; return html`<${Line} key=${item.id} item=${item} href=${href(item)} act=${act} cursor=${cursor === index} summaries=${summaries} />`; })}</ul></section>`);
  }

  return html`<div class="page">
    <header class="space-head" style=${hueStyle(domain.hue)}>
      <h1><span class="swatch"></span>${domain.name}</h1>
      <p>${domain.description}</p>
      <div class="weight"><${WeightMenu} domain=${domain} refreshBoot=${refreshBoot} /><span>${plural(domain.total, "item")} held</span></div>
    </header>
    <${Tabs} value=${view} onChange=${(value) => setQuery(route, { view: value === "important" ? undefined : value })} tabs=${[{ value: "important", label: "Important" }, { value: "latest", label: "Latest" }]} />
    <div class="filters">
      <${ChipGroup} label="Kind of item" value=${kind} onChange=${(value) => setQuery(route, { kind: value || undefined })}
        options=${[{ value: "", label: "All kinds" }, ...kinds.map((value) => ({ value, label: KIND_SHORT[value] || value }))]} />
      <span class="grow"></span>
      <${Toggle} checked=${unread} onChange=${(value) => setQuery(route, { state: value ? "unread" : undefined })} label="Unread only" />
      ${view === "latest" && html`<${Toggle} checked=${summaries} onChange=${(value) => setQuery(route, { summaries: value ? "1" : undefined })} label="Summaries" />`}
      <select class="field compact" aria-label="Period" value=${days} onChange=${(event) => setQuery(route, { days: event.target.value || undefined })}>
        <option value="7">${PERIODS[7]}</option><option value="30">${PERIODS[30]}</option><option value="">All time</option></select>
      ${unreadShown.length > 0 && html`<button class="btn small quiet" onClick=${() => api.markRead(unreadShown.map((item) => item.id)).then(() => { unreadShown.forEach((item) => feed.patchItems(item.id, { read: true })); refreshBoot(); }, (failure) => toast(failure.message, { error: true }))}>
        <${Icon} name="check" size=${15} /> Mark shown as read</button>`}
    </div>
    ${view === "important" && html`<p class="feed-note">Ordered by significance and fit with your interests. Older items stay while they still matter.</p>`}
    ${body}
    <${MoreButton} feed=${feed} />
  </div>`;
}

// ---------- a Topic ----------

const RATINGS = [[3, "Priority"], [2, "High"], [1, "Some"], [-1, "None"], [-2, "Mute"]];

export function Topic({ refreshBoot, route, id }) {
  const info = useLoad(() => api.topic(id), [id]);
  const feed = usePagedItems({ topic: id, view: "latest", state: "active" });
  const act = useItemActions(feed.patchItems, refreshBoot);
  const visible = feed.items.filter((item) => !item.dismissed);
  const href = (item) => itemHref(route, item.id);
  const [cursor] = useTriage(visible, act, href);
  const topic = info.data && info.data.topic;
  useEffect(() => { setReadingContext(visible.map((item) => item.id), topic ? topic.name : ""); });
  if (info.error) return html`<div class="page"><${ErrorNote} message=${info.error} retry=${info.reload} /></div>`;
  if (!topic) return html`<div class="page"><${Skeleton} rows=${3} /></div>`;
  const rate = (level) => api.rateTopic(topic.id, level).then(() => { info.reload(); refreshBoot(); toast(level === null ? "Rating cleared" : level === -2 ? `Muted ${topic.name}` : "Interest updated"); },
    (failure) => toast(failure.message, { error: true }));
  return html`<div class="page">
    <header class="page-head"><h1>${topic.name}</h1>
      <p>Everything PIA holds about this ${topic.kind === "topic" ? "topic" : "name"}, newest first. ${plural(topic.item_count, "item")}${topic.domain ? ` in ${topic.domain}` : ""}.</p>
      <div class="form-row"><span class="meta">Your interest</span>
        <div class="rate" role="group" aria-label=${`Your interest in ${topic.name}`}>${RATINGS.map(([level, label]) => html`<button class=${level < 0 ? "neg" : ""} aria-pressed=${topic.level === level}
          onClick=${() => rate(topic.level === level ? null : level)}>${label}</button>`)}</div></div>
    </header>
    ${feed.error && html`<${ErrorNote} message=${feed.error} retry=${feed.reload} />`}
    ${feed.loading && !feed.items.length && html`<${Skeleton} rows=${5} />`}
    ${!feed.loading && !feed.error && !visible.length && html`<${Empty} title="Nothing here yet">PIA has not matched any item to this yet.<//>`}
    <ul class="feed">${visible.map((item, index) => html`<${Row} key=${item.id} item=${item} href=${href(item)} act=${act} cursor=${cursor === index} showSpace=${true} />`)}</ul>
    <${MoreButton} feed=${feed} />
  </div>`;
}
