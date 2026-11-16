// Opportunities, Sources, Interests, Notifications and System.
import { html, useState } from "../vendor/preact-htm.js";
import { api } from "./api.js";
import { Empty, ErrorNote, Icon, Loading, Segmented, Tabs, hueStyle, toast, useLoad } from "./ui.js";
import { OPPORTUNITY_TYPES, TRACK_STATUSES, buildRoute, bytes, dateTime, plural, timeAgo, usd } from "./util.js";

function fail(error) {
  toast(error.message, { error: true });
}

// ---------- Opportunities ----------

function OrganisationPanel({ organisations, reload }) {
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const add = async (event) => {
    event.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    try {
      const { id } = await api.addOrganisation({ name: name.trim() });
      if (url.trim()) await api.addSource({ url: url.trim(), org_id: id, purpose: "opportunities", name: `${name.trim()}: opportunities` });
      toast(url.trim() ? `Now watching ${name.trim()}` : `${name.trim()} added. Add a page for PIA to watch.`);
      setName(""); setUrl("");
      reload();
    } catch (error) {
      fail(error);
    } finally {
      setBusy(false);
    }
  };
  const watch = async (organisation) => {
    const address = prompt(`Address of a careers, internship or programme page for ${organisation.name}:`);
    if (!address) return;
    try {
      await api.addSource({ url: address, org_id: organisation.id, purpose: "opportunities", name: `${organisation.name}: ${new URL(address.includes("://") ? address : "https://" + address).hostname}` });
      toast("Page added");
      reload();
    } catch (error) {
      fail(error);
    }
  };
  return html`<section class="block">
    <h3>Organisations you monitor</h3>
    <p class="hint">PIA does not search the whole internet for opportunities. It watches the organisations you list here: their careers, internship, research-programme and announcement pages.
      Give a page address and PIA works out how to read it (a feed, a job-board API such as Greenhouse, Lever or Ashby, or the page's links).</p>
    <form class="form-row stack" onSubmit=${add}>
      <input class="field" placeholder="Organisation name" aria-label="Organisation name" value=${name} onInput=${(event) => setName(event.target.value)} />
      <input class="field" placeholder="Careers or programme page address (optional)" aria-label="Page address" value=${url} onInput=${(event) => setUrl(event.target.value)} />
      <button class="button primary" disabled=${busy || !name.trim()}>${busy ? "Checking the page…" : "Add organisation"}</button>
    </form>
    ${organisations.length === 0 && html`<p class="notice">No organisations yet. Until you add some, this space only shows opportunities from sources you follow (for example contest listings).</p>`}
    ${organisations.map((organisation) => html`<div class="card">
      <div class="card-head"><div><h3>${organisation.name}</h3><p class="sub">${plural(organisation.sources.length, "page")} watched, ${plural(organisation.open_count, "open opportunity", "open opportunities")}</p></div>
        <div class="card-actions"><button class="button small" onClick=${() => watch(organisation)}><${Icon} name="plus" size=${14} /> Add a page</button>
          <button class="button small quiet danger" onClick=${() => confirm(`Stop monitoring ${organisation.name}? Opportunities already found are kept.`) && api.deleteOrganisation(organisation.id).then(reload, fail)}>Remove</button></div></div>
      ${organisation.sources.length > 0 && html`<ul class="plain-list">${organisation.sources.map((source) => html`<li><span class=${"dot " + source.health}></span>${source.url}
        <span class="sub"> ${source.last_error ? `(${source.last_error.slice(0, 90)})` : source.last_ok_at ? `(checked ${timeAgo(source.last_ok_at)})` : "(not checked yet)"}</span></li>`)}</ul>`}
    </div>`)}
  </section>`;
}

export function Opportunities({ refreshBoot, route }) {
  const show = route.query.show || "open";
  const firstYear = route.query.first_year === "1";
  const type = route.query.type || "";
  const { data, error, loading, reload, patch } = useLoad(() => api.opportunities({ show, first_year: firstYear, kind: type }), [show, firstYear, type]);
  const set = (change) => (location.hash = buildRoute(route.path, { ...route.query, ...change, item: undefined }));
  const track = async (opportunity, status) => {
    try {
      await api.track(opportunity.item_id, status);
      patch((current) => ({ ...current, opportunities: current.opportunities.map((o) => (o.item_id === opportunity.item_id ? { ...o, status } : o)) }));
      refreshBoot();
      toast(status ? "Tracking updated" : "No longer tracking");
    } catch (failure) {
      fail(failure);
    }
  };
  const counts = data ? data.counts : {};
  return html`<div class="page">
    <header class="page-head"><h1>Opportunities</h1>
      <p>Internships, research programmes, fellowships, summer schools, hackathons and competitions, with deadlines and eligibility up front. Expired ones are never shown as open.</p></header>
    <${Tabs} value=${show} onChange=${(value) => set({ show: value === "open" ? undefined : value })}
      tabs=${[{ value: "open", label: "Open", count: counts.open }, { value: "tracked", label: "Tracking", count: counts.tracked }, { value: "expired", label: "Closed", count: counts.expired }]} />
    <div class="toolbar">
      <label class="check"><input type="checkbox" checked=${firstYear} onChange=${(event) => set({ first_year: event.target.checked ? "1" : undefined })} /> Open to first-year students${counts.first_year ? ` (${counts.first_year})` : ""}</label>
      <select aria-label="Type of opportunity" value=${type} onChange=${(event) => set({ type: event.target.value || undefined })}>
        <option value="">Every type</option>${Object.entries(OPPORTUNITY_TYPES).map(([value, label]) => html`<option value=${value}>${label}</option>`)}</select>
    </div>
    ${error && html`<${ErrorNote} message=${error} retry=${reload} />`}
    ${loading && !data && html`<${Loading} lines=${6} />`}
    ${data && data.opportunities.length === 0 && html`<${Empty}
      title=${show === "tracked" ? "You are not tracking anything yet" : show === "expired" ? "Nothing has closed yet" : firstYear || type ? "Nothing matches these filters" : "No open opportunities yet"}
      hint=${show === "tracked" ? "Set a status on an opportunity (Interested, Planning to apply, Applied) and it appears here." : firstYear || type ? "Clear the filters to see every open opportunity." : "Add the organisations you want watched below, or follow an opportunities source under Sources."}
      action=${(firstYear || type) && { label: "Clear filters", run: () => set({ first_year: undefined, type: undefined }) }} />`}
    ${data && data.opportunities.length > 0 && html`<ul class="feed">${data.opportunities.map((o) => html`<li class="opp" style=${hueStyle(o.domain_hue)} key=${o.item_id}>
      <div class=${"opp-when" + (o.state === "closing" ? " soon" : o.state === "expired" ? " over" : o.state === "undated" ? " none" : "")}>
        ${o.state === "undated" ? html`<b>No deadline given</b>` : o.state === "expired" ? html`<b>Closed</b><span>${dateTime(o.deadline + "T12:00:00").split(",")[0]}</span>`
          : html`<b>${o.days_left === 0 ? "Today" : o.days_left}</b><span>${o.days_left === 0 ? "closes" : o.days_left === 1 ? "day left" : "days left"}</span><span>${dateTime(o.deadline + "T12:00:00").split(",")[0]}</span>`}
      </div>
      <div>
        <div class="opp-org">${[o.organisation || o.source_name, OPPORTUNITY_TYPES[o.type], [o.location, o.mode].filter(Boolean).join(", ")].filter(Boolean).join("  /  ")}</div>
        <h3 class="opp-title"><a href=${buildRoute(route.path, { ...route.query, item: o.item_id })}>${o.programme || o.title}</a></h3>
        ${(o.eligibility || o.year_requirements || o.summary) && html`<p class="opp-line">${o.eligibility || o.year_requirements || o.summary}</p>`}
        <div class="row-foot">
          ${o.first_year === "yes" && html`<span class="badge yes">First-year students can apply</span>`}
          ${o.first_year === "no" && html`<span class="badge no">Not open to first-years</span>`}
          ${o.duration && html`<span class="chip">${o.duration}</span>`}
          ${o.extracted_by !== "model" && !o.deadline && html`<span class="chip" title="Only fields the source supplied are filled in">Details not yet extracted</span>`}
        </div>
      </div>
      <select value=${o.status} aria-label="Your status" onChange=${(event) => track(o, event.target.value)}>
        <option value="">Not tracking</option>${TRACK_STATUSES.map((s) => html`<option value=${s.value}>${s.label}</option>`)}</select>
    </li>`)}</ul>`}
    ${data && html`<${OrganisationPanel} organisations=${data.organisations} reload=${() => { reload(); refreshBoot(); }} />`}
  </div>`;
}

// ---------- Sources ----------

const HEALTH = { ok: "Working", retrying: "Retrying after an error", unhealthy: "Failing repeatedly", pending: "Not fetched yet", paused: "Paused", candidate: "Suggested", rejected: "Declined" };
const KIND_NAME = { rss: "Feed", arxiv: "arXiv API", hn: "Hacker News API", github: "GitHub API", hf_papers: "Hugging Face API", json_api: "JSON API", page: "Page watch", gmail: "Gmail (read-only)" };
const PROVENANCE = { official: "Official channel", established_publication: "Established publication", research_group: "Research group", individual_expert: "Individual expert", aggregator: "Aggregator", commercial_marketing: "Commercial marketing", unknown: "Provenance unknown" };

function AddSource({ reload }) {
  const [url, setUrl] = useState("");
  const [found, setFound] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const inspect = async (event) => {
    event.preventDefault();
    setBusy(true); setError(null); setFound(null);
    try {
      setFound(await api.inspectSource(url.trim()));
    } catch (failure) {
      setError(failure.message);
    } finally {
      setBusy(false);
    }
  };
  const follow = async () => {
    setBusy(true);
    try {
      await api.addSource({ url: found.url });
      toast("Following. PIA is fetching it now.");
      setFound(null); setUrl("");
      reload();
    } catch (failure) {
      fail(failure);
    } finally {
      setBusy(false);
    }
  };
  return html`<div class="card stack">
    <div><h3>Follow something</h3><p class="sub">A website, blog, lab, journal, researcher's page or newsletter. Give its address; PIA finds the feed and shows you what it would bring before you follow it.</p></div>
    <form class="form-row" onSubmit=${inspect}>
      <input class="field" type="text" inputmode="url" placeholder="example.org or a feed address" aria-label="Address to follow" value=${url} onInput=${(event) => setUrl(event.target.value)} />
      <button class="button primary" disabled=${busy || url.trim().length < 4}>${busy && !found ? "Looking…" : "Look it up"}</button>
    </form>
    ${error && html`<p class="notice error" role="alert">${error}</p>`}
    ${found && html`<div class="notice">
      <b>${found.title}</b> <span class="badge">${KIND_NAME[found.kind]}</span>
      <p>${found.summary}</p>
      ${found.evaluation && html`<div class="kv"><span>Match with your interests <b>${Math.round(found.evaluation.relevance * 100)}</b></span>
        <span>New to you <b>${Math.round(found.evaluation.novelty * 100)}%</b></span>
        ${found.evaluation.matched_topics.length > 0 && html`<span>Touches <b>${found.evaluation.matched_topics.join(", ")}</b></span>`}</div>`}
      ${found.samples.length > 0 && html`<ul class="plain-list">${found.samples.map((title) => html`<li>${title}</li>`)}</ul>`}
      <div class="form-row"><button class="button small primary" disabled=${busy || found.already_followed} onClick=${follow}>${found.already_followed ? "Already in your sources" : "Follow this source"}</button>
        <button class="button small quiet" onClick=${() => setFound(null)}>Cancel</button></div>
    </div>`}
  </div>`;
}

function Recommendation({ source, act }) {
  const e = source.evaluation;
  return html`<div class="card">
    <div class="card-head">
      <div><h3>${source.name}</h3><p class="sub">${source.url || source.homepage}</p><p class="sub">${source.origin === "discovered" ? "Found by PIA" : "From PIA's vetted catalogue"}</p></div>
      <div class="card-actions">
        <button class="button small primary" onClick=${() => act(source, "active", `Following ${source.name}`)}>Follow</button>
        <button class="button small quiet" onClick=${() => act(source, "rejected", "Declined. PIA will not suggest it again.")}>Not for me</button>
      </div>
    </div>
    ${source.description && html`<p>${source.description}</p>`}
    ${source.reason && html`<p class="sub">Why it is suggested: ${source.reason}</p>`}
    ${e && html`<div class="kv">
      <span>Match with your interests <b>${Math.round(e.relevance * 100)}</b></span><span>New to you <b>${Math.round(e.novelty * 100)}%</b></span>
      <span>Posts <b>${e.posts_per_week || "?"}</b> a week</span>
      ${e.opinion && html`<span><b>${PROVENANCE[e.opinion.provenance]}</b>, ${e.opinion.noise} noise (a model's opinion)</span>`}
    </div>
    <ul class="plain-list">${e.notes.map((note) => html`<li>${note}</li>`)}${e.opinion && e.opinion.credibility && html`<li>${e.opinion.credibility}</li>`}</ul>
    ${e.matched_topics.length > 0 && html`<div class="row-foot">${e.matched_topics.map((topic) => html`<span class="chip liked">${topic}</span>`)}</div>`}`}
  </div>`;
}

export function Sources({ boot, refreshBoot, route }) {
  const tab = route.query.tab || "following";
  const { data, error, reload } = useLoad(() => api.sources(), []);
  const [looking, setLooking] = useState(false);
  const refresh = () => { reload(); refreshBoot(); };
  const act = (source, status, message) => api.patchSource(source.id, { status }).then(() => { toast(message); refresh(); }, fail);
  if (error) return html`<div class="page"><${ErrorNote} message=${error} retry=${reload} /></div>`;
  if (!data) return html`<div class="page"><${Loading} lines=${8} /></div>`;
  const following = data.sources.filter((source) => source.status === "active" || source.status === "paused");
  const candidates = data.sources.filter((source) => source.status === "candidate").sort((a, b) => ((b.evaluation && b.evaluation.score) || 0) - ((a.evaluation && a.evaluation.score) || 0));
  const hues = Object.fromEntries(boot.domains.map((domain) => [domain.slug, domain]));
  const groups = {};
  for (const source of following) (groups[source.domains[0] || "other"] ||= []).push(source);
  const discover = () => {
    setLooking(true);
    api.discover().then((result) => { toast(result.proposed ? `${plural(result.proposed, "new suggestion")}` : "Nothing new found this time"); refresh(); }, fail).finally(() => setLooking(false));
  };
  return html`<div class="page">
    <header class="page-head"><h1>Sources</h1>
      <p>What PIA monitors for you. It only monitors sources you follow; anything it discovers waits here as a suggestion until you decide.</p></header>
    <${Tabs} value=${tab} onChange=${(value) => (location.hash = buildRoute(route.path, { tab: value === "following" ? undefined : value }))}
      tabs=${[{ value: "following", label: "Following", count: following.length }, { value: "suggested", label: "Suggested", count: candidates.length }, { value: "add", label: "Add a source" }]} />
    ${tab === "add" && html`<div class="toolbar"></div><${AddSource} reload=${refresh} />`}
    ${tab === "suggested" && html`<div>
      <div class="toolbar"><p class="feed-note">Each suggestion is measured before it is shown: how recent and frequent it is, whether it matches your interests, and how much of it you already receive.</p>
        <span class="grow"></span><button class="button small" disabled=${looking} onClick=${discover}><${Icon} name="refresh" size=${14} /> ${looking ? "Looking…" : "Look for new sources"}</button></div>
      ${candidates.length === 0 && html`<${Empty} title="No suggestions waiting" hint="PIA looks for new sources once a day, from sites that well-ranked items keep pointing to and from your interests." />`}
      ${candidates.map((source) => html`<${Recommendation} key=${source.id} source=${source} act=${act} />`)}
    </div>`}
    ${tab === "following" && Object.entries(groups).sort((a, b) => ((hues[a[0]] || {}).position ?? 99) - ((hues[b[0]] || {}).position ?? 99)).map(([slug, list]) => html`<section style=${hueStyle((hues[slug] || {}).hue)}>
      <h2 class="group-title">${(hues[slug] || {}).name || "Other"} <small>${plural(list.length, "source")}</small></h2>
      <table class="grid"><thead><tr><th>Source</th><th>Kind</th><th>Status</th><th class="num">This week</th><th class="num">All</th><th></th></tr></thead>
        <tbody>${list.map((source) => html`<tr key=${source.id}>
          <td class="name">${source.name}<small>${source.description || source.url || ""}</small></td>
          <td>${KIND_NAME[source.kind] || source.kind}${source.organisation ? html`<br /><span class="sub">${source.organisation}</span>` : ""}</td>
          <td><span class=${"dot " + source.health}></span>${HEALTH[source.health]}
            ${source.last_ok_at && html`<br /><span class="sub">fetched ${timeAgo(source.last_ok_at)}</span>`}
            ${source.last_error && html`<br /><span class="sub" title=${source.last_error}>${source.last_error.slice(0, 70)}</span>`}</td>
          <td class="num">${source.week_count}</td><td class="num">${source.item_count}</td>
          <td><div class="card-actions">
            ${source.status === "active" ? html`<button class="button small quiet" onClick=${() => act(source, "paused", `${source.name} paused`)}>Pause</button>`
              : html`<button class="button small" onClick=${() => act(source, "active", `${source.name} resumed`)}>Resume</button>`}
            <button class="button small quiet danger" onClick=${() => confirm(`Stop following ${source.name}? Items it already brought in are kept.`) && api.deleteSource(source.id).then(() => { toast("Removed"); refresh(); }, fail)}>Remove</button>
          </div></td></tr>`)}</tbody></table>
    </section>`)}
  </div>`;
}

// ---------- Interests ----------

const RATING = [[3, "Priority"], [2, "High"], [1, "Some"], [-1, "No"]];
const WEIGHTS = [{ value: 0, label: "Hidden" }, { value: 0.6, label: "Less" }, { value: 1, label: "Normal" }, { value: 1.5, label: "Priority" }];
function nearestWeight(weight) {
  return WEIGHTS.reduce((best, option) => (Math.abs(option.value - weight) < Math.abs(best.value - weight) ? option : best)).value;
}

export function Interests({ refreshBoot }) {
  const { data, error, reload, patch } = useLoad(() => api.profile(), []);
  const [newTopic, setNewTopic] = useState("");
  const [open, setOpen] = useState(null);
  if (error) return html`<div class="page"><${ErrorNote} message=${error} retry=${reload} /></div>`;
  if (!data) return html`<div class="page"><${Loading} lines=${8} /></div>`;

  const rate = async (topic, level) => {
    const next = topic.level === level ? null : level;
    patch((current) => ({
      ...current,
      onboarding: current.onboarding.map((group) => ({ ...group, topics: group.topics.map((t) => (t.id === topic.id ? { ...t, level: next, rated: 1 } : t)) })),
      topics: current.topics.map((t) => (t.id === topic.id ? { ...t, level: next } : t)),
      suggested: current.suggested.filter((t) => t.id !== topic.id || next === null),
    }));
    try {
      await api.rateTopic(topic.id, next);
      refreshBoot();
    } catch (failure) {
      fail(failure);
      reload();
    }
  };
  const weigh = (domain, weight) => {
    patch((current) => ({ ...current, domains: current.domains.map((d) => (d.slug === domain.slug ? { ...d, weight } : d)) }));
    api.weighDomain(domain.slug, weight).then(refreshBoot, fail);
  };
  const add = async (event) => {
    event.preventDefault();
    if (newTopic.trim().length < 2) return;
    try {
      await api.addTopic(newTopic.trim(), null, 2);
      toast(`Following “${newTopic.trim()}”`);
      setNewTopic("");
      reload(); refreshBoot();
    } catch (failure) {
      fail(failure);
    }
  };
  const priorities = data.topics.filter((topic) => topic.level === 3);
  const learned = data.topics.filter((topic) => Math.abs(topic.affinity) >= 0.05).sort((a, b) => Math.abs(b.affinity) - Math.abs(a.affinity)).slice(0, 14);
  const muted = data.topics.filter((topic) => topic.level === -2);
  const own = data.topics.filter((topic) => topic.origin !== "seed" && topic.level !== null && topic.level !== -2);
  const signalNames = { open: "opened", save: "saved", like: "liked", useful: "marked useful", dismiss: "dismissed", not_relevant: "marked not relevant", not_interested_topic: "topic not interesting", low_quality: "marked poor quality" };

  return html`<div class="page page-narrow">
    <header class="page-head"><h1>Interests</h1>
      <p>What PIA believes you care about, and what it does with it. Everything here is yours to correct. A starting profile was built from your brief and existing notes; it is not the whole picture.</p></header>

    <section class="block"><h3>Your priorities</h3>
      <p class="hint">The handful of topics that matter most. Items touching these rank highest and are the likeliest to notify you.</p>
      <div class="row-foot">${priorities.length === 0 ? html`<span class="hint">None yet. Mark a topic Priority below.</span>`
        : priorities.map((topic) => html`<a class="chip liked" href=${buildRoute(["topic", String(topic.id)])}>${topic.name}</a>`)}</div>
    </section>

    <section class="block"><h3>Topics by space</h3>
      <p class="hint">Open a space and rate what you recognise: High, Some or No, and Priority for the few that matter most. Skip anything; nothing needs finishing. Unrated topics are treated as neutral.</p>
      ${data.onboarding.map((group) => {
        const domain = data.domains.find((d) => d.slug === group.slug);
        return html`<details class="card space-card" style=${hueStyle(group.hue)} open=${open === group.slug} onToggle=${(event) => event.target.open && setOpen(group.slug)}>
          <summary><span class="bar"></span>
            <span class="sname">${group.name}<small>${group.topics.filter((t) => t.level !== null).length} of ${group.total} topics rated${domain && Math.abs(domain.affinity) >= 0.05 ? `, learned ${domain.affinity > 0 ? "interest" : "disinterest"}` : ""}</small></span>
            <${Segmented} label=${`How much ${group.short} you want`} value=${nearestWeight(domain ? domain.weight : 1)} options=${WEIGHTS}
              onChange=${(value) => weigh(group, value)} /></summary>
          ${group.topics.map((topic) => html`<div class="topic-row"><a class="tname" href=${buildRoute(["topic", String(topic.id)])}>${topic.name}</a>
            <div class="rate" role="group" aria-label=${`Interest in ${topic.name}`}>
              ${RATING.map(([level, label]) => html`<button class=${level < 0 ? "neg" : ""} aria-pressed=${topic.level === level} onClick=${() => rate(topic, level)}>${label}</button>`)}
              <button class="neg" aria-pressed=${topic.level === -2} title="Mute: keep it off Home and out of notifications" onClick=${() => rate(topic, -2)}><${Icon} name="eyeoff" size=${14} /><span class="sr">Mute</span></button>
            </div></div>`)}
        </details>`;
      })}
    </section>

    <section class="block"><h3>Follow a topic of your own</h3>
      <form class="form-row" onSubmit=${add}>
        <input class="field" placeholder="A subject, technology, person or organisation" aria-label="New topic" value=${newTopic} onInput=${(event) => setNewTopic(event.target.value)} />
        <button class="button primary" disabled=${newTopic.trim().length < 2}>Follow</button>
      </form>
      ${own.length > 0 && html`<div class="row-foot">${own.map((topic) => html`<a class="chip liked" href=${buildRoute(["topic", String(topic.id)])}>${topic.name}</a>`)}</div>`}
    </section>

    ${data.suggested.length > 0 && html`<section class="block"><h3>Subjects PIA has noticed</h3>
      <p class="hint">These keep appearing in what you receive but you have not rated them. Rate one and it starts to count.</p>
      ${data.suggested.map((topic) => html`<div class="topic-row"><a class="tname" href=${buildRoute(["topic", String(topic.id)])}>${topic.name}<small>${plural(topic.item_count, "item")}${topic.domain ? `, ${topic.domain}` : ""}</small></a>
        <div class="rate">${RATING.map(([level, label]) => html`<button class=${level < 0 ? "neg" : ""} onClick=${() => rate(topic, level)}>${label}</button>`)}</div></div>`)}
    </section>`}

    <section class="block"><h3>What PIA has learned from you</h3>
      <p class="hint">Saving, liking and opening items nudges the topics they touch upward; dismissing and "not relevant" nudges them down. Leaving something unread teaches nothing. Learned values fade slowly and never override a rating you set.</p>
      ${data.signals.length > 0 && html`<p class="hint">So far: ${data.signals.map((signal) => `${signal.n} ${signalNames[signal.kind] || signal.kind}`).join(", ")}.</p>`}
      ${learned.length === 0 && html`<p class="notice">Nothing learned yet. This fills in as you use PIA.</p>`}
      ${learned.map((topic) => html`<div class="learned"><a href=${buildRoute(["topic", String(topic.id)])}>${topic.name}</a>
        <div class="bipolar"><i class=${topic.affinity < 0 ? "neg" : ""} style=${{ width: `${Math.abs(topic.affinity) * 50}%` }}></i></div>
        <button class="button small quiet" title="Forget what was learned about this topic" onClick=${() => api.resetTopic(topic.id).then(() => { toast("Learning reset"); reload(); }, fail)}>Reset</button></div>`)}
    </section>

    ${muted.length > 0 && html`<section class="block"><h3>Muted</h3>
      <div class="row-foot">${muted.map((topic) => html`<button class="chip" title="Unmute" onClick=${() => rate(topic, -2)}>${topic.name} <${Icon} name="x" size=${12} /></button>`)}</div></section>`}
  </div>`;
}

// ---------- Notifications ----------

const LEVEL_LABEL = { interesting: "Interesting", important: "Important", critical: "Critical" };

function Range({ label, value, min, max, step, onChange, format = (v) => v }) {
  return html`<div class="range-row"><label>${label}</label>
    <input type="range" min=${min} max=${max} step=${step} value=${value} aria-label=${label} onChange=${(event) => onChange(Number(event.target.value))} /><span>${format(value)}</span></div>`;
}

export function Notifications({ refreshBoot, route }) {
  const { data, error, reload, patch } = useLoad(() => api.notifications(), []);
  const [token, setToken] = useState(null);
  const [deviceName, setDeviceName] = useState("Windows laptop");
  if (error) return html`<div class="page"><${ErrorNote} message=${error} retry=${reload} /></div>`;
  if (!data) return html`<div class="page"><${Loading} lines=${6} /></div>`;
  const s = data.settings;
  const save = (change) => {
    patch((current) => ({ ...current, settings: { ...current.settings, ...change, thresholds: { ...current.settings.thresholds, ...(change.thresholds || {}) },
      daily_caps: { ...current.settings.daily_caps, ...(change.daily_caps || {}) }, quiet_hours: { ...current.settings.quiet_hours, ...(change.quiet_hours || {}) } } }));
    api.saveNotificationSettings(change).then((settings) => patch((current) => ({ ...current, settings })), fail);
  };
  const seen = (ids) => api.markSeen(ids).then(() => { reload(); refreshBoot(); }, fail);
  const addDevice = (event) => {
    event.preventDefault();
    api.addDevice(deviceName.trim() || "Device").then((result) => { setToken(result.token); reload(); }, fail);
  };
  return html`<div class="page page-narrow">
    <header class="page-head"><h1>Notifications</h1>
      <p>PIA tells you when something clears your thresholds. Whether an item notifies never decides whether it is kept: everything stays in its space.</p></header>

    <section><div class="section-title"><h2>Recent</h2>${data.unseen > 0 && html`<button onClick=${() => seen(null)}>Mark all as seen</button>`}</div>
      ${data.notifications.length === 0 && html`<${Empty} title="No notifications yet" hint="When the agent finds something above your thresholds it appears here, and on your desktop if the Windows companion is set up." />`}
      <ul class="inbox feed">${data.notifications.map((n) => html`<li class=${n.seen_at ? "" : "unseen"} key=${n.id}>
        <span class=${"badge level-" + n.level}>${LEVEL_LABEL[n.level]}</span>
        <div><a class="ntitle" href=${buildRoute(route.path, { item: n.item_id })} onClick=${() => !n.seen_at && seen([n.id])}>${n.title}</a>
          <div class="nwhy">${n.reason}</div></div>
        <time>${timeAgo(n.created_at)}</time></li>`)}</ul>
    </section>

    <section class="block"><h3>When to notify</h3>
      <label class="check"><input type="checkbox" checked=${s.enabled} onChange=${(event) => save({ enabled: event.target.checked })} /> Notifications are on</label>
      <p class="hint">Each level needs an item's combined score to reach its threshold, and a minimum of both importance and relevance. Lower a threshold to hear more.</p>
      ${["interesting", "important", "critical"].map((level) => html`<${Range} label=${LEVEL_LABEL[level]} value=${s.thresholds[level]} min=${0.3} max=${0.95} step=${0.01}
        format=${(v) => Math.round(v * 100)} onChange=${(value) => save({ thresholds: { [level]: value } })} />`)}
      <p class="hint">At most this many a day, so a busy day cannot flood you:</p>
      ${["interesting", "important", "critical"].map((level) => html`<${Range} label=${LEVEL_LABEL[level]} value=${s.daily_caps[level]} min=${0} max=${40} step=${1}
        onChange=${(value) => save({ daily_caps: { [level]: value } })} />`)}
    </section>

    <section class="block"><h3>On your desktop</h3>
      <div class="form-row"><span>Show on the desktop from</span>
        <${Segmented} label="Minimum level for desktop notifications" value=${s.desktop_min_level} onChange=${(value) => save({ desktop_min_level: value })}
          options=${[{ value: "interesting", label: "Interesting" }, { value: "important", label: "Important" }, { value: "critical", label: "Critical only" }]} /></div>
      <div class="form-row stack"><label class="check"><input type="checkbox" checked=${s.quiet_hours.enabled} onChange=${(event) => save({ quiet_hours: { enabled: event.target.checked } })} /> Quiet hours</label>
        <input class="field" type="time" aria-label="Quiet hours start" value=${s.quiet_hours.start} onChange=${(event) => save({ quiet_hours: { start: event.target.value } })} />
        <span>to</span><input class="field" type="time" aria-label="Quiet hours end" value=${s.quiet_hours.end} onChange=${(event) => save({ quiet_hours: { end: event.target.value } })} /></div>
      <p class="hint">During quiet hours only Critical notifications reach the desktop; the rest wait until morning.</p>
    </section>

    <section class="block"><h3>Devices</h3>
      <p class="hint">The Windows companion is a small script that shows PIA's notifications as ordinary desktop notifications and opens the item when you click one. It needs a token from here. Setup steps are in companion/README.md.</p>
      ${data.devices.length > 0 && html`<table class="grid"><thead><tr><th>Device</th><th>Added</th><th>Last seen</th><th></th></tr></thead><tbody>
        ${data.devices.map((device) => html`<tr><td class="name">${device.name}</td><td>${dateTime(device.created_at)}</td><td>${device.last_seen_at ? timeAgo(device.last_seen_at) : "Never"}</td>
          <td><button class="button small quiet danger" onClick=${() => confirm(`Revoke ${device.name}? Its token stops working.`) && api.removeDevice(device.id).then(reload, fail)}>Revoke</button></td></tr>`)}</tbody></table>`}
      <form class="form-row stack" onSubmit=${addDevice}><input class="field" aria-label="Device name" value=${deviceName} onInput=${(event) => setDeviceName(event.target.value)} />
        <button class="button">Create a device token</button></form>
      ${token && html`<div class="notice stack"><b>Copy this token now. It is shown once.</b><div class="token">${token}</div>
        <p>Paste it into the companion's settings file as described in companion/README.md.</p></div>`}
    </section>
  </div>`;
}

// ---------- System ----------

const TASKS = { assess: "Assessing new items", brief: "Writing briefs", extract: "Extracting opportunities", source_eval: "Evaluating sources", fit: "Personal-fit judgement (Jev)" };

function runSummary(stats) {
  const parts = [];
  if (stats.acquire) parts.push(`${stats.acquire.new} new from ${stats.acquire.sources} sources${stats.acquire.failed ? ` (${stats.acquire.failed} failed)` : ""}`);
  if (stats.assess) parts.push(`${stats.assess.assessed} assessed`);
  if (stats.briefs) parts.push(`${stats.briefs} briefs`);
  if (stats.notifications) parts.push(`${stats.notifications} notifications`);
  if (stats.discovered) parts.push(`${stats.discovered} sources suggested`);
  if (stats.budget) parts.push(stats.budget);
  return parts.join(", ") || "Nothing to do";
}

export function System({ refreshBoot }) {
  const { data, error, reload } = useLoad(() => api.system(), []);
  if (error) return html`<div class="page"><${ErrorNote} message=${error} retry=${reload} /></div>`;
  if (!data) return html`<div class="page"><${Loading} lines=${8} /></div>`;
  const run = (force) => api.runNow(force).then(() => { toast(data.background ? "The agent is starting a cycle" : "Cycle finished"); setTimeout(() => { reload(); refreshBoot(); }, 1500); }, fail);
  const usage = data.usage;
  const totalItems = Object.values(data.items).reduce((sum, n) => sum + n, 0);
  return html`<div class="page">
    <header class="page-head"><h1>System</h1><p>What the agent has been doing, how the sources are holding up, and what the models cost.</p></header>
    ${data.notes.map((note) => html`<p class="notice">${note}</p>`)}

    <section class="block"><h3>Agent</h3>
      <div class="kv"><span><span class=${"pulse" + (data.running ? " busy" : data.background ? "" : " off")}></span><b>${data.running ? "Working now" : data.background ? "Running in the background" : "Not running in the background"}</b></span>
        <span>Items <b>${totalItems.toLocaleString("en-GB")}</b></span><span>Assessed by a model <b>${(data.items.assessed || 0).toLocaleString("en-GB")}</b></span>
        <span>Waiting for assessment <b>${(data.items.classified || 0).toLocaleString("en-GB")}</b></span><span>Database <b>${bytes(data.db_bytes)}</b></span></div>
      <div class="toolbar"><button class="button" onClick=${() => run(false)}><${Icon} name="refresh" size=${15} /> Run a cycle now</button>
        <button class="button quiet" onClick=${() => run(true)}>Fetch every source now</button><button class="button quiet" onClick=${reload}>Refresh this page</button></div>
      <table class="grid"><thead><tr><th>Started</th><th>Took</th><th>Result</th></tr></thead><tbody>
        ${data.runs.map((entry) => {
          const stats = JSON.parse(entry.stats || "{}");
          const seconds = entry.finished_at ? Math.round((new Date(entry.finished_at) - new Date(entry.started_at)) / 1000) : null;
          return html`<tr><td>${dateTime(entry.started_at)}</td><td>${seconds === null ? "running" : `${seconds}s`}</td>
            <td>${entry.error ? html`<span class="badge bad">Problem</span> ${entry.error.slice(0, 160)}` : runSummary(stats)}</td></tr>`;
        })}</tbody></table>
    </section>

    <section class="block"><h3>Sources</h3>
      <div class="kv">${Object.entries(data.sources).map(([state, n]) => html`<span><span class=${"dot " + state}></span>${HEALTH[state] || state} <b>${n}</b></span>`)}
        <a class="button small" href=${buildRoute(["sources"])}>Manage sources</a></div>
    </section>

    <section class="block"><h3>Models and cost</h3>
      <p class="hint">Estimated from token counts and the prices in config/models.toml. Deterministic steps (classification, ranking, linking related items) cost nothing.
        The agent stops calling models for the day at ${usd(data.budget.daily_usd)}.</p>
      <div class="kv"><span>Today <b>${usd(usage.today_usd)}</b></span><span>Last ${usage.days} days <b>${usd(usage.total_usd)}</b></span>
        <span>Model provider <b>${data.jev ? "Groq, plus Jev for personal fit" : "Groq only"}</b></span></div>
      ${usage.by_task.length === 0 ? html`<p class="notice">No model has been called yet.</p>` : html`<table class="grid"><thead><tr><th>Job</th><th>Model</th><th class="num">Calls</th><th class="num">Items</th><th class="num">Tokens in</th><th class="num">Tokens out</th><th class="num">Failed</th><th class="num">Cost</th></tr></thead><tbody>
        ${usage.by_task.map((row) => html`<tr><td class="name">${row.task === "fit" && !data.jev ? "Personal fit (Jev): past calls, now switched off" : TASKS[row.task] || row.task}</td><td>${row.model}</td><td class="num">${row.calls}</td><td class="num">${row.items}</td>
          <td class="num">${row.input_tokens.toLocaleString("en-GB")}</td><td class="num">${row.output_tokens.toLocaleString("en-GB")}</td><td class="num">${row.failures}</td><td class="num">${usd(row.cost_usd)}</td></tr>`)}</tbody></table>`}
    </section>
  </div>`;
}

