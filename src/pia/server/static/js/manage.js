// The pages that manage PIA rather than read from it: Interests, Sources and Settings.
import { html, useEffect, useState } from "../vendor/preact-htm.js";
import { api } from "./api.js";
import { ChipGroup, Empty, ErrorNote, Icon, InlineConfirm, Segmented, ShortcutTable, Skeleton, Tabs, Toggle, applyTheme, go, hueStyle, toast, useLoad } from "./ui.js";
import { buildRoute, bytes, dateTime, plural, timeAgo, usd } from "./util.js";

const fail = (error) => toast(error.message, { error: true });
const group = (n) => Number(n || 0).toLocaleString("en-GB");

function SubNav({ label, items, value, path, phoneLinks = false }) {
  return html`<nav class="subnav" aria-label=${label}>
    ${phoneLinks && html`<a class="phone-only" href="#/interests">Interests</a><a class="phone-only" href="#/sources">Sources</a>`}
    ${items.map(([key, name]) => html`<a href=${buildRoute(path, { tab: key === items[0][0] ? undefined : key })} aria-current=${value === key ? "page" : undefined}>${name}</a>`)}
  </nav>`;
}

// ---------- Interests ----------

const RATING = [[3, "Priority"], [2, "High"], [1, "Some"], [-1, "No"], [-2, "Mute"]];
const WEIGHTS = [{ value: 0, label: "Hidden" }, { value: 0.6, label: "Less" }, { value: 1, label: "Normal" }, { value: 1.5, label: "Priority" }];
const nearestWeight = (weight) => WEIGHTS.reduce((best, option) => (Math.abs(option.value - weight) < Math.abs(best.value - weight) ? option : best)).value;
const SIGNAL_NAMES = { open: "opened", save: "saved", like: "liked", useful: "marked useful", dismiss: "dismissed", not_relevant: "marked not relevant", not_interested_topic: "topic not interesting", low_quality: "marked poor quality" };

function RateRow({ topic, onRate, withMute = true, meta }) {
  return html`<div class="topic-row"><a href=${buildRoute(["topic", String(topic.id)])}>${topic.name}${meta && html` <span class="meta inline">${meta}</span>`}</a>
    <div class="rate" role="group" aria-label=${`Interest in ${topic.name}`}>
      ${RATING.filter(([level]) => withMute || level !== -2).map(([level, label]) => html`<button class=${level < 0 ? "neg" : ""} aria-pressed=${topic.level === level} onClick=${() => onRate(topic, level)}>${label}</button>`)}
    </div></div>`;
}

export function Interests({ refreshBoot, route }) {
  const tab = ["rate", "learned"].includes(route.query.tab) ? route.query.tab : "weighting";
  const { data, error, reload, patch } = useLoad(() => api.profile(), []);
  const [newTopic, setNewTopic] = useState("");
  const [space, setSpace] = useState(null);
  if (error) return html`<div class="page"><${ErrorNote} message=${error} retry=${reload} /></div>`;
  if (!data) return html`<div class="page"><${Skeleton} rows=${5} /></div>`;

  const rate = async (topic, level) => {
    const next = topic.level === level ? null : level;
    patch((current) => ({
      ...current,
      onboarding: current.onboarding.map((entry) => ({ ...entry, topics: entry.topics.map((t) => (t.id === topic.id ? { ...t, level: next, rated: 1 } : t)) })),
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
  const follow = async (event) => {
    event.preventDefault();
    if (newTopic.trim().length < 2) return;
    try {
      await api.addTopic(newTopic.trim(), null, 2);
      toast(`Following “${newTopic.trim()}”. PIA is matching it against what it already holds.`);
      setNewTopic("");
      reload(); refreshBoot();
    } catch (failure) {
      fail(failure);
    }
  };

  const priorities = data.topics.filter((topic) => topic.level === 3);
  const learned = data.topics.filter((topic) => Math.abs(topic.affinity) >= 0.05).sort((a, b) => Math.abs(b.affinity) - Math.abs(a.affinity)).slice(0, 14);
  const muted = data.topics.filter((topic) => topic.level === -2);
  const totalTopics = data.onboarding.reduce((sum, entry) => sum + entry.total, 0);
  const rated = data.onboarding.reduce((sum, entry) => sum + entry.topics.filter((t) => t.level !== null).length, 0);
  const current = data.onboarding.find((entry) => entry.slug === (space || (data.onboarding[0] && data.onboarding[0].slug)));

  return html`<div class="page"><div class="settings-layout">
    <${SubNav} label="Interests sections" path=${["interests"]} value=${tab} items=${[["weighting", "Weighting"], ["rate", "Rate topics"], ["learned", "Learned"]]} />
    <div>
      <header class="page-head flush"><h1>Interests</h1><p>What PIA believes you care about. Every rating here changes ranking, the daily briefing and notifications from the next cycle.</p></header>
      ${tab === "weighting" && html`
        <section class="panel"><h2>Priority topics</h2><p class="hint">The few topics that matter most. Items that touch them rank highest and are the likeliest to notify you.</p>
          <div class="chips">${priorities.map((topic) => html`<a class="chip" href=${buildRoute(["topic", String(topic.id)])}>${topic.name}</a>`)}
            <a class="chip" href="#/interests?tab=rate"><${Icon} name="plus" size=${14} />Add a priority</a></div></section>
        <section class="panel"><h2>Space weighting</h2><p class="hint">How much of each space reaches your briefing. Hidden spaces never appear on Today but stay browsable.</p>
          <div class="weights">${data.domains.map((domain) => html`<div class="weight-row" key=${domain.slug} style=${hueStyle(domain.hue)}><span class="swatch"></span>
            <span class="nm">${domain.name}${Math.abs(domain.affinity) >= 0.05 ? html`<small>You read ${domain.affinity > 0 ? "more" : "less"} of this than its weight suggests</small>` : null}</span>
            <${Segmented} label=${`Weight for ${domain.short}`} value=${nearestWeight(domain.weight)} options=${WEIGHTS} onChange=${(value) => weigh(domain, value)} /></div>`)}</div></section>
        <section class="panel"><h2>Follow a topic of your own</h2>
          <form class="form-row" onSubmit=${follow}><input class="field" value=${newTopic} onInput=${(event) => setNewTopic(event.target.value)} placeholder="A subject, method, person, lab or organisation" aria-label="New topic" />
            <button class="btn primary" disabled=${newTopic.trim().length < 2}>Follow</button></form></section>`}
      ${tab === "rate" && html`
        <section class="panel"><h2>Rate topics, one space at a time</h2><p class="hint">${group(rated)} of ${group(totalTopics)} topics rated. Rate what you recognise and skip the rest. Unrated topics count as neutral.</p>
          <${ChipGroup} label="Space" value=${current ? current.slug : ""} onChange=${(value) => value && setSpace(value)} options=${data.onboarding.map((entry) => ({ value: entry.slug, label: entry.short }))} />
          <div>${current && current.topics.map((topic) => html`<${RateRow} key=${topic.id} topic=${topic} onRate=${rate} />`)}</div></section>
        ${data.suggested.length > 0 && html`<section class="panel"><h2>Noticed by PIA</h2><p class="hint">These keep appearing in what you receive. Rate one and it starts to count.</p>
          ${data.suggested.map((topic) => html`<${RateRow} key=${topic.id} topic=${topic} onRate=${rate} withMute=${false} meta=${plural(topic.item_count, "item") + (topic.domain ? `, ${topic.domain}` : "")} />`)}</section>`}`}
      ${tab === "learned" && html`
        <section class="panel"><h2>Learned from what you do</h2><p class="hint">Saving, liking and opening nudge a topic up; dismissing and “not relevant” nudge it down. Learned values fade slowly and never override a rating you set.</p>
          ${data.signals.length > 0 && html`<p class="hint">So far: ${data.signals.map((signal) => `${signal.n} ${SIGNAL_NAMES[signal.kind] || signal.kind}`).join(", ")}.</p>`}
          ${learned.length === 0 ? html`<${Empty} title="Nothing learned yet">This fills in as you read, save and dismiss.<//>`
            : learned.map((topic) => html`<div class="learned" key=${topic.id}><a href=${buildRoute(["topic", String(topic.id)])}>${topic.name}</a>
              <div class="bipolar" role="img" aria-label=${`Learned ${topic.affinity > 0 ? "interest" : "disinterest"}`}><i class=${topic.affinity < 0 ? "neg" : ""} style=${{ width: `${Math.abs(topic.affinity) * 50}%` }}></i></div>
              <button class="btn small quiet" title="Forget what was learned about this topic" onClick=${() => api.resetTopic(topic.id).then(() => { toast("Learning reset"); reload(); }, fail)}>Reset</button></div>`)}</section>
        ${muted.length > 0 && html`<section class="panel"><h2>Muted</h2><div class="chips">${muted.map((topic) => html`<button class="chip" title="Unmute" onClick=${() => rate(topic, -2)}>${topic.name} <${Icon} name="x" size=${12} /></button>`)}</div></section>`}`}
    </div></div></div>`;
}

// ---------- Sources ----------

const HEALTH = { ok: "Working", retrying: "Retrying", unhealthy: "Failing", pending: "Not fetched yet", paused: "Paused", candidate: "Suggested", rejected: "Declined" };
const KIND_NAME = { rss: "Feed", arxiv: "arXiv", hn: "Hacker News", github: "GitHub", hf_papers: "Hugging Face", json_api: "API", page: "Page watch", gmail: "Gmail (read-only)" };
const PROVENANCE = { official: "Official channel", established_publication: "Established publication", research_group: "Research group", individual_expert: "Individual expert", aggregator: "Aggregator", commercial_marketing: "Commercial marketing", unknown: "Provenance unknown" };

function SourceRow({ source, onPause, onRemove }) {
  const [asking, setAsking] = useState(false);
  return html`<tr><td><b class="name">${source.name}</b><span class="sub" title=${source.description || source.url}>${source.description || source.url || ""}</span></td>
      <td>${KIND_NAME[source.kind] || source.kind}${source.organisation ? html`<span class="sub">${source.organisation}</span>` : null}</td>
      <td><span class=${"health " + source.health}><i></i>${HEALTH[source.health] || source.health}</span>
        ${source.last_error ? html`<span class="sub" title=${source.last_error}>${source.last_error.slice(0, 70)}</span>` : source.last_ok_at ? html`<span class="sub">Fetched ${timeAgo(source.last_ok_at)}</span>` : null}</td>
      <td class="n">${source.week_count}</td><td class="n">${group(source.item_count)}</td>
      <td><div class="row-acts">
        ${source.status === "paused" ? html`<button class="btn small" onClick=${() => onPause(source, "active")}>Resume</button>` : html`<button class="btn small quiet" onClick=${() => onPause(source, "paused")}>Pause</button>`}
        <button class="btn small quiet danger" onClick=${() => setAsking(true)}>Remove</button></div></td></tr>
    ${asking && html`<tr><td colspan="6"><${InlineConfirm} message=${`Stop following ${source.name}? Items it already brought in are kept.`} onConfirm=${() => { setAsking(false); onRemove(source); }} onCancel=${() => setAsking(false)} /></td></tr>`}`;
}

function SourceTable({ list, onPause, onRemove }) {
  return html`<div class="table-wrap" tabindex="0"><table class="grid sources"><thead><tr><th>Source</th><th>Reads</th><th>Status</th><th class="n">This week</th><th class="n">All</th><th><span class="sr">Actions</span></th></tr></thead>
    <tbody>${list.map((source) => html`<${SourceRow} key=${source.id} source=${source} onPause=${onPause} onRemove=${onRemove} />`)}</tbody></table></div>`;
}

function Suggestion({ source, onDecide }) {
  const e = source.evaluation;
  return html`<div class="cand"><div><h3>${source.name}</h3>
      <div class="meta"><span>${source.origin === "discovered" ? "Found by PIA" : "From PIA's vetted catalogue"}</span>
        ${e && html`<span>Match ${Math.round(e.relevance * 100)}</span><span>${Math.round(e.novelty * 100)}% new to you</span><span>${e.posts_per_week || "?"} posts a week</span>`}
        ${e && e.opinion && html`<span>${PROVENANCE[e.opinion.provenance]}, ${e.opinion.noise} noise (a model's opinion)</span>`}</div></div>
    ${(source.description || source.reason) && html`<p>${source.description}${source.reason ? ` Why it is suggested: ${source.reason}` : ""}</p>`}
    ${e && html`<ul class="notes">${e.notes.map((note) => html`<li>${note}</li>`)}${e.opinion && e.opinion.credibility && html`<li>${e.opinion.credibility}</li>`}</ul>`}
    ${e && e.matched_topics.length > 0 && html`<div class="chips matched">${e.matched_topics.map((topic) => html`<span class="chip static">${topic}</span>`)}</div>`}
    <div class="acts"><button class="btn small primary" onClick=${() => onDecide(source, "active", `Following ${source.name}`)}>Follow</button>
      <button class="btn small quiet" onClick=${() => onDecide(source, "rejected", "Declined. PIA will not suggest it again.")}>Not for me</button></div></div>`;
}

function Organisations({ organisations, reload }) {
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [adding, setAdding] = useState(null);
  const [page, setPage] = useState("");
  const [removing, setRemoving] = useState(null);
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
    } catch (failure) {
      fail(failure);
    } finally {
      setBusy(false);
    }
  };
  const addPage = async (event, organisation) => {
    event.preventDefault();
    const address = page.trim();
    if (!address) return;
    try {
      const host = new URL(address.includes("://") ? address : "https://" + address).hostname;
      await api.addSource({ url: address, org_id: organisation.id, purpose: "opportunities", name: `${organisation.name}: ${host}` });
      toast("Page added");
      setAdding(null); setPage("");
      reload();
    } catch (failure) {
      fail(failure);
    }
  };
  return html`<div>
    <section class="panel top"><h2>Organisations PIA watches for opportunities</h2>
      <p class="hint">PIA does not search the whole web for opportunities. It reads the careers, internship and programme pages of the organisations listed here, and works out how (a feed, a job board such as Greenhouse or Lever, or the page's links).</p>
      <form class="form-row" onSubmit=${add}>
        <input class="field" placeholder="Organisation name" aria-label="Organisation name" value=${name} onInput=${(event) => setName(event.target.value)} />
        <input class="field" placeholder="Careers or programme page (optional)" aria-label="Page address" value=${url} onInput=${(event) => setUrl(event.target.value)} />
        <button class="btn primary" disabled=${busy || !name.trim()}>${busy ? "Checking the page…" : "Add organisation"}</button></form></section>
    ${organisations.length === 0 ? html`<${Empty} title="No organisations yet">Until you add some, Opportunities only shows what comes from sources you follow, such as contest listings.<//>`
      : html`<div class="table-wrap" tabindex="0"><table class="grid"><thead><tr><th>Organisation</th><th>Pages watched</th><th class="n">Open now</th><th><span class="sr">Actions</span></th></tr></thead><tbody>
        ${organisations.map((organisation) => html`<tr key=${organisation.id}><td><b class="name">${organisation.name}</b>${organisation.homepage && html`<span class="sub">${organisation.homepage}</span>`}</td>
            <td>${organisation.sources.length === 0 ? html`<span class="sub">No pages yet</span>` : organisation.sources.map((source) => html`<span class=${"health block " + source.health} title=${source.last_error || ""}><i></i>${source.url.replace(/^https?:\/\//, "").slice(0, 48)}</span>`)}</td>
            <td class="n">${organisation.open_count}</td>
            <td><div class="row-acts"><button class="btn small quiet" onClick=${() => { setAdding(adding === organisation.id ? null : organisation.id); setPage(""); }}>Add a page</button>
              <button class="btn small quiet danger" onClick=${() => setRemoving(organisation.id)}>Remove</button></div></td></tr>
          ${adding === organisation.id && html`<tr><td colspan="4"><form class="form-row" onSubmit=${(event) => addPage(event, organisation)}>
            <input class="field" autofocus placeholder=${`Careers, internship or programme page for ${organisation.name}`} aria-label="Page address" value=${page} onInput=${(event) => setPage(event.target.value)} />
            <button class="btn small primary" disabled=${!page.trim()}>Add page</button><button class="btn small quiet" type="button" onClick=${() => setAdding(null)}>Cancel</button></form></td></tr>`}
          ${removing === organisation.id && html`<tr><td colspan="4"><${InlineConfirm} message=${`Stop monitoring ${organisation.name}? Opportunities already found are kept.`}
            onConfirm=${() => { setRemoving(null); api.deleteOrganisation(organisation.id).then(reload, fail); }} onCancel=${() => setRemoving(null)} /></td></tr>`}`)}</tbody></table></div>`}
  </div>`;
}

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
  return html`<section class="panel top"><h2>Follow a website, lab, journal or newsletter</h2>
    <p class="hint">Give an address. PIA finds the feed and shows what it would bring before you follow it.</p>
    <form class="form-row" onSubmit=${inspect}>
      <input class="field" inputmode="url" value=${url} onInput=${(event) => setUrl(event.target.value)} placeholder="example.org or a feed address" aria-label="Address to follow" />
      <button class="btn primary" disabled=${busy || url.trim().length < 4}>${busy && !found ? "Looking…" : "Look it up"}</button></form>
    ${error && html`<div class="callout warn" role="alert">${error}</div>`}
    ${found && html`<div class="callout"><b>${found.title}</b>, ${KIND_NAME[found.kind] || "a source"}. ${found.summary}
      ${found.evaluation && html`<p class="basis">Match with your interests ${Math.round(found.evaluation.relevance * 100)}. ${Math.round(found.evaluation.novelty * 100)}% of it is new to you.${found.evaluation.matched_topics.length > 0 ? ` Touches ${found.evaluation.matched_topics.join(", ")}.` : ""}</p>`}
      ${found.samples.length > 0 && html`<ul class="notes">${found.samples.map((title) => html`<li>${title}</li>`)}</ul>`}
      <div class="form-row"><button class="btn small primary" disabled=${busy || found.already_followed} onClick=${follow}>${found.already_followed ? "Already in your sources" : "Follow this source"}</button>
        <button class="btn small quiet" onClick=${() => setFound(null)}>Cancel</button></div></div>`}
  </section>`;
}

export function Sources({ boot, refreshBoot, route }) {
  const tab = ["suggested", "organisations", "add"].includes(route.query.tab) ? route.query.tab : "following";
  const { data, error, reload } = useLoad(() => api.sources(), []);
  const [looking, setLooking] = useState(false);
  const [open, setOpen] = useState({});
  if (error) return html`<div class="page"><${ErrorNote} message=${error} retry=${reload} /></div>`;
  if (!data) return html`<div class="page"><${Skeleton} rows=${6} /></div>`;
  const refresh = () => { reload(); refreshBoot(); };
  const pause = (source, status) => api.patchSource(source.id, { status }).then(() => { toast(status === "paused" ? `${source.name} paused` : `${source.name} resumed`); refresh(); }, fail);
  const remove = (source) => api.deleteSource(source.id).then(() => { toast("Removed. Items it already brought in are kept."); refresh(); }, fail);
  const decide = (source, status, message) => api.patchSource(source.id, { status }).then(() => { toast(message); refresh(); }, fail);
  const discover = () => {
    setLooking(true);
    api.discover().then((result) => { toast(result.proposed ? plural(result.proposed, "new suggestion") : "Nothing new found this time"); refresh(); }, fail).finally(() => setLooking(false));
  };

  const following = data.sources.filter((source) => source.status === "active" || source.status === "paused");
  const candidates = data.sources.filter((source) => source.status === "candidate").sort((a, b) => ((b.evaluation && b.evaluation.score) || 0) - ((a.evaluation && a.evaluation.score) || 0));
  const counts = {};
  for (const source of following) counts[source.health] = (counts[source.health] || 0) + 1;
  const failing = following.filter((source) => source.status === "active" && (source.health === "unhealthy" || source.health === "retrying"));
  const groups = {};
  for (const source of following) (groups[source.domains[0] || "other"] ||= []).push(source);
  const order = boot.domains.map((domain) => domain.slug).concat(["other"]).filter((slug) => groups[slug]);
  const byslug = Object.fromEntries(boot.domains.map((domain) => [domain.slug, domain]));

  return html`<div class="page">
    <header class="page-head"><h1>Sources</h1>
      <p class="summary-line">PIA follows <b>${following.length}</b> sources. <b>${counts.ok || 0}</b> ${(counts.ok || 0) === 1 ? "is" : "are"} working${failing.length ? html`, <span class="bad">${failing.length} need attention</span>` : ""}${counts.paused ? `, ${counts.paused} paused` : ""}.${candidates.length ? ` ${plural(candidates.length, "suggestion")} ${candidates.length === 1 ? "is" : "are"} waiting.` : ""}</p></header>
    <${Tabs} value=${tab} onChange=${(value) => go(["sources"], { tab: value === "following" ? undefined : value })}
      tabs=${[{ value: "following", label: "Following", count: following.length }, { value: "suggested", label: "Suggestions", count: candidates.length }, { value: "organisations", label: "Organisations", count: data.organisations.length }, { value: "add", label: "Add a source" }]} />
    ${tab === "following" && html`<div>
      ${failing.length > 0 && html`<section class="section tight"><div class="section-head"><h2>Needs attention</h2></div><${SourceTable} list=${failing} onPause=${pause} onRemove=${remove} /></section>`}
      ${order.map((slug, index) => {
        const domain = byslug[slug];
        const isOpen = open[slug] !== undefined ? open[slug] : index === 0;
        return html`<section key=${slug}>
          <button class="group-toggle" aria-expanded=${isOpen} onClick=${() => setOpen({ ...open, [slug]: !isOpen })}><span class="swatch" style=${hueStyle(domain ? domain.hue : 225)}></span><b>${domain ? domain.name : "Other"}</b>
            <span>${plural(groups[slug].length, "source")}</span><span class="grow"></span><${Icon} name=${isOpen ? "up" : "down"} size=${16} /></button>
          ${isOpen && html`<${SourceTable} list=${groups[slug]} onPause=${pause} onRemove=${remove} />`}</section>`;
      })}
      ${following.length === 0 && html`<${Empty} title="You are not following any sources">Add one from the Add a source tab, or follow a suggestion.<//>`}
    </div>`}
    ${tab === "suggested" && html`<div>
      <div class="toolbar"><p class="feed-note flush">Each suggestion is measured before it is shown: how often it publishes, how well it matches your interests, and how much of it you already receive.</p>
        <span class="grow"></span><button class="btn small" disabled=${looking} onClick=${discover}><${Icon} name="refresh" size=${14} /> ${looking ? "Looking…" : "Look for new sources"}</button></div>
      <h2 class="sr">Suggested sources</h2>
      ${candidates.length === 0 ? html`<${Empty} title="No suggestions waiting">PIA looks for new sources once a day, from sites that well-ranked items keep pointing to and from your interests.<//>`
        : candidates.map((source) => html`<${Suggestion} key=${source.id} source=${source} onDecide=${decide} />`)}
    </div>`}
    ${tab === "organisations" && html`<${Organisations} organisations=${data.organisations} reload=${refresh} />`}
    ${tab === "add" && html`<${AddSource} reload=${refresh} />`}
  </div>`;
}

// ---------- Settings ----------

const LEVELS = [["critical", "Critical"], ["important", "Important"], ["interesting", "Interesting"]];

function Range({ label, value, min, max, step, onCommit, format = (v) => v }) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  const id = "range-" + label.toLowerCase().replace(/\W+/g, "-");
  return html`<div class="range-row"><label for=${id}>${label}</label>
    <input id=${id} type="range" min=${min} max=${max} step=${step} value=${draft} onInput=${(event) => setDraft(Number(event.target.value))} onChange=${(event) => onCommit(Number(event.target.value))} />
    <output>${format(draft)}</output></div>`;
}

function NotificationsTab() {
  const { data, error, reload, patch } = useLoad(() => api.notifications(), []);
  if (error) return html`<${ErrorNote} message=${error} retry=${reload} />`;
  if (!data) return html`<${Skeleton} rows=${3} />`;
  const s = data.settings;
  const save = (change) => {
    patch((current) => ({ ...current, settings: { ...current.settings, ...change, thresholds: { ...current.settings.thresholds, ...(change.thresholds || {}) },
      daily_caps: { ...current.settings.daily_caps, ...(change.daily_caps || {}) }, quiet_hours: { ...current.settings.quiet_hours, ...(change.quiet_hours || {}) } } }));
    api.saveNotificationSettings(change).then((settings) => patch((current) => ({ ...current, settings })), (failure) => { fail(failure); reload(); });
  };
  return html`<div>
    <section class="panel"><${Toggle} checked=${s.enabled} onChange=${(value) => save({ enabled: value })} label="Notify me when something clears my thresholds" />
      <p class="hint">Notifications never decide what PIA keeps. Everything stays in its space either way.</p></section>
    <section class="panel"><h2>How important it has to be</h2>
      <p class="hint">Each level needs an item's combined score to reach its threshold. Lower a threshold to hear more; the daily limit stops a busy day from flooding you.</p>
      ${LEVELS.map(([level, label]) => html`<${Range} key=${level} label=${label} value=${s.thresholds[level]} min=${0.3} max=${0.95} step=${0.01} format=${(v) => Math.round(v * 100)} onCommit=${(value) => save({ thresholds: { [level]: value } })} />`)}
      <div class="table-wrap" tabindex="0"><table class="grid narrow"><thead><tr><th>Level</th><th class="n">At most per day</th></tr></thead><tbody>
        ${LEVELS.map(([level, label]) => html`<tr key=${level}><td>${label}</td><td class="n"><input class="field cap" type="number" min="0" max="40" aria-label=${`Daily limit for ${label}`} value=${s.daily_caps[level]}
          onChange=${(event) => save({ daily_caps: { [level]: Math.max(0, Math.min(40, Number(event.target.value) || 0)) } })} /></td></tr>`)}</tbody></table></div></section>
    <section class="panel"><h2>On your desktop</h2>
      <div class="form-row"><span>Show from</span><${Segmented} label="Minimum level for desktop notifications" value=${s.desktop_min_level} onChange=${(value) => save({ desktop_min_level: value })}
        options=${[{ value: "interesting", label: "Interesting" }, { value: "important", label: "Important" }, { value: "critical", label: "Critical only" }]} /></div>
      <div class="form-row"><${Toggle} checked=${s.quiet_hours.enabled} onChange=${(value) => save({ quiet_hours: { enabled: value } })} label="Quiet hours" />
        <input class="field time" type="time" aria-label="Quiet hours start" value=${s.quiet_hours.start} onChange=${(event) => save({ quiet_hours: { start: event.target.value } })} /><span>to</span>
        <input class="field time" type="time" aria-label="Quiet hours end" value=${s.quiet_hours.end} onChange=${(event) => save({ quiet_hours: { end: event.target.value } })} /></div>
      <p class="hint">During quiet hours only Critical notifications reach the desktop.${s.opportunity_reminder_days ? ` Opportunity reminders arrive ${s.opportunity_reminder_days} days before a tracked deadline.` : ""}</p></section>
  </div>`;
}

function DevicesTab() {
  const { data, error, reload } = useLoad(() => api.notifications(), []);
  const [token, setToken] = useState(null);
  const [name, setName] = useState("Windows laptop");
  const [revoking, setRevoking] = useState(null);
  if (error) return html`<${ErrorNote} message=${error} retry=${reload} />`;
  if (!data) return html`<${Skeleton} rows=${3} />`;
  const create = (event) => {
    event.preventDefault();
    api.addDevice(name.trim() || "Device").then((result) => { setToken(result.token); reload(); }, fail);
  };
  const copy = () => navigator.clipboard.writeText(token).then(() => toast("Token copied"), () => toast("Copy it by hand: select the text and press Ctrl C.", { error: true }));
  return html`<section class="panel"><h2>Desktop companion</h2>
    <p class="hint">The Windows companion shows PIA's notifications as ordinary desktop notifications and opens the item when you click one. It needs a token from here. Setup steps are in companion/README.md.</p>
    ${data.devices.length > 0 ? html`<div class="table-wrap" tabindex="0"><table class="grid"><thead><tr><th>Device</th><th>Added</th><th>Last seen</th><th><span class="sr">Actions</span></th></tr></thead><tbody>
      ${data.devices.map((device) => html`<tr key=${device.id}><td>${device.name}</td><td>${dateTime(device.created_at)}</td><td>${device.last_seen_at ? timeAgo(device.last_seen_at) : "Never"}</td>
        <td><button class="btn small quiet danger" onClick=${() => setRevoking(device.id)}>Revoke</button></td></tr>
        ${revoking === device.id && html`<tr><td colspan="4"><${InlineConfirm} message=${`Revoke ${device.name}? Its token stops working.`} confirmLabel="Revoke" onConfirm=${() => { setRevoking(null); api.removeDevice(device.id).then(reload, fail); }} onCancel=${() => setRevoking(null)} /></td></tr>`}`)}</tbody></table></div>`
      : html`<p class="meta">No devices yet.</p>`}
    <form class="form-row" onSubmit=${create}><input class="field" aria-label="Device name" value=${name} onInput=${(event) => setName(event.target.value)} /><button class="btn">Create a device token</button></form>
    ${token && html`<div class="callout"><b>Copy this token now. It is shown once.</b>
      <div class="form-row"><input class="field token" readonly aria-label="Device token" value=${token} onFocus=${(event) => event.target.select()} /><button class="btn small" onClick=${copy}>Copy</button></div>
      <p class="basis">Paste it into the companion's settings file as described in companion/README.md.</p></div>`}
  </section>`;
}

function AppearanceTab() {
  const [theme, setTheme] = useState(() => { try { return localStorage.getItem("pia.theme") || "system"; } catch { return "system"; } });
  const choose = (value) => {
    setTheme(value);
    try { if (value === "system") localStorage.removeItem("pia.theme"); else localStorage.setItem("pia.theme", value); } catch { /* storage blocked */ }
    applyTheme(value);
  };
  return html`<div>
    <section class="panel"><h2>Theme</h2><p class="hint">Paper for daytime reading, night desk for late evenings. System follows Windows.</p>
      <${Segmented} label="Theme" value=${theme} onChange=${choose} options=${[{ value: "system", label: "System" }, { value: "light", label: "Paper" }, { value: "dark", label: "Night desk" }]} /></section>
    <section class="panel"><h2>Keyboard</h2><${ShortcutTable} /></section>
  </div>`;
}

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

function SystemTab({ refreshBoot }) {
  const { data, error, reload } = useLoad(() => api.system(), []);
  if (error) return html`<${ErrorNote} message=${error} retry=${reload} />`;
  if (!data) return html`<${Skeleton} rows=${5} />`;
  const run = (force) => api.runNow(force).then(() => { toast(data.background ? "The agent is starting a cycle" : "Cycle finished"); setTimeout(() => { reload(); refreshBoot(); }, 1500); }, fail);
  const usage = data.usage;
  const totalItems = Object.values(data.items).reduce((sum, n) => sum + n, 0);
  return html`<div>
    ${data.notes.map((note) => html`<div class="callout">${note}</div>`)}
    <section class="panel"><h2>Agent</h2>
      <p class="summary-line">${data.running ? "Working now." : data.background ? "Running in the background." : "Not running in the background."} <b>${group(totalItems)}</b> items held, <b>${group(data.items.assessed || 0)}</b> assessed by a model, <b>${group(data.items.classified || 0)}</b> waiting. Database ${bytes(data.db_bytes)}.</p>
      <div class="form-row"><button class="btn" onClick=${() => run(false)}><${Icon} name="refresh" size=${15} /> Run a cycle now</button><button class="btn quiet" onClick=${() => run(true)}>Fetch every source now</button></div>
      <div class="table-wrap" tabindex="0"><table class="grid"><thead><tr><th>Started</th><th>Took</th><th>Result</th></tr></thead><tbody>
        ${data.runs.map((entry) => {
          const stats = JSON.parse(entry.stats || "{}");
          const seconds = entry.finished_at ? Math.round((new Date(entry.finished_at) - new Date(entry.started_at)) / 1000) : null;
          return html`<tr key=${entry.id || entry.started_at}><td>${dateTime(entry.started_at)}</td><td class="num">${seconds === null ? "Running" : `${seconds}s`}</td>
            <td>${entry.error ? html`<span class="health unhealthy"><i></i>Problem</span> ${entry.error.slice(0, 160)}` : runSummary(stats)}</td></tr>`;
        })}</tbody></table></div></section>
    <section class="panel"><h2>Sources</h2>
      <p class="summary-line">${Object.entries(data.sources).map(([state, n], index) => html`${index ? ", " : ""}<b>${n}</b> ${(HEALTH[state] || state).toLowerCase()}`)}. <a class="textlink" href="#/sources">Manage sources</a></p></section>
    <section class="panel"><h2>Models and cost</h2>
      <p class="hint">Estimated from token counts and the prices in config/models.toml. Deterministic steps (classification, ranking, linking related items) cost nothing. The agent stops calling models for the day at ${usd(data.budget.daily_usd)}.</p>
      <p class="summary-line">Today <b>${usd(usage.today_usd)}</b>. Last ${usage.days} days <b>${usd(usage.total_usd)}</b>. Model provider: <b>${data.jev ? "Groq, plus Jev for personal fit" : "Groq only"}</b>.</p>
      ${usage.by_task.length === 0 ? html`<p class="hint">No model has been called yet.</p>` : html`<div class="table-wrap" tabindex="0"><table class="grid"><thead><tr><th>Job</th><th>Model</th><th class="n">Calls</th><th class="n">Items</th><th class="n">Tokens in</th><th class="n">Tokens out</th><th class="n">Failed</th><th class="n">Cost</th></tr></thead><tbody>
        ${usage.by_task.map((row) => html`<tr><td>${row.task === "fit" && !data.jev ? "Personal fit (Jev): past calls, now switched off" : TASKS[row.task] || row.task}</td><td>${row.model}</td><td class="n">${row.calls}</td><td class="n">${row.items}</td>
          <td class="n">${group(row.input_tokens)}</td><td class="n">${group(row.output_tokens)}</td><td class="n">${row.failures}</td><td class="n">${usd(row.cost_usd)}</td></tr>`)}</tbody></table></div>`}</section>
  </div>`;
}

export function Settings({ refreshBoot, route }) {
  const tab = ["devices", "appearance", "system"].includes(route.query.tab) ? route.query.tab : "notifications";
  const titles = { notifications: "Notifications", devices: "Devices", appearance: "Appearance", system: "System" };
  return html`<div class="page"><div class="settings-layout">
    <${SubNav} label="Settings sections" path=${["settings"]} value=${tab} phoneLinks=${true} items=${Object.entries(titles)} />
    <div>
      <header class="page-head flush"><h1>${titles[tab]}</h1></header>
      ${tab === "notifications" && html`<${NotificationsTab} />`}
      ${tab === "devices" && html`<${DevicesTab} />`}
      ${tab === "appearance" && html`<${AppearanceTab} />`}
      ${tab === "system" && html`<${SystemTab} refreshBoot=${refreshBoot} />`}
    </div></div></div>`;
}
