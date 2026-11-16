// Opportunities: deadline-first, duplicates folded, with a tracking board.
import { html, useEffect, useState } from "../vendor/preact-htm.js";
import { api } from "./api.js";
import { ChipGroup, Empty, ErrorNote, Menu, Skeleton, Tabs, Toggle, go, itemHref, setReadingContext, toast, useLoad } from "./ui.js";
import { OPPORTUNITY_TYPES, TRACK_STATUSES, dueText, dueTone, foldOpportunities, plural, urgencyGroup } from "./util.js";

const GROUPS = [["week", "This week"], ["month", "This month"], ["later", "Later"], ["undated", "No deadline given"]];
const shortDate = (deadline) => new Date(deadline + "T12:00:00").toLocaleDateString("en-GB", { day: "numeric", month: "short" });
const statusLabel = (status) => (TRACK_STATUSES.find((entry) => entry.value === status) || {}).label;

function TrackMenu({ opportunity, onTrack }) {
  const status = opportunity.status || "";
  return html`<${Menu} className="track" label=${status ? statusLabel(status) : "Track"} active=${Boolean(status)} value=${status}
    items=${TRACK_STATUSES} after=${status ? [{ value: "", label: "Stop tracking" }] : []} onSelect=${(value) => onTrack(opportunity, value)} />`;
}

function OppRow({ opportunity: o, route, onTrack, closed = false }) {
  const days = o.days_left;
  const tone = closed ? "" : dueTone(days);
  const where = [o.location, o.mode].filter(Boolean).join(", ");
  return html`<li class=${"opp" + (closed ? " closed" : "")} data-id=${o.item_id}>
    <div class=${"when " + tone}>${days === null || days === undefined ? html`<b>Undated</b>`
      : closed ? html`<b class="small-n">Closed</b><span>${shortDate(o.deadline)}</span>`
      : html`<b>${days}</b><span>${days === 1 ? "day left" : "days left"}</span><span>${shortDate(o.deadline)}</span>`}</div>
    <div class="body">
      <div class="org">${[o.organisation || o.source_name, OPPORTUNITY_TYPES[o.type], where].filter(Boolean).join("   ·   ")}</div>
      <h3><a href=${itemHref(route, o.item_id)}>${o.programme || o.title}</a></h3>
      ${(o.eligibility || o.summary) && html`<p>${o.eligibility || o.summary}</p>`}
      <div class="meta">
        ${o.first_year === "yes" ? html`<span class="elig yes">First-years can apply</span>` : o.first_year === "no" ? html`<span class="elig no">Not open to first-years</span>` : null}
        ${o.duration && html`<span>${o.duration}</span>`}
        ${o.dup ? html`<span class="dup">Also posted by ${plural(o.dup, "other source")}</span>` : null}
      </div>
    </div>
    ${!closed && html`<${TrackMenu} opportunity=${o} onTrack=${onTrack} />`}
  </li>`;
}

function TrackingBoard({ list, route }) {
  const columns = [["interested", "Interested"], ["planning", "Planning"], ["applied", "Applied"], ["outcome", "Outcome"]];
  return html`<div class="board"><h2 class="sr">Tracking board</h2>${columns.map(([key, label]) => {
    const column = list.filter((o) => (key === "outcome" ? ["accepted", "rejected"].includes(o.status) : o.status === key));
    return html`<div class="col" key=${key}><h3>${label}<span>${column.length}</span></h3>
      ${column.length === 0 ? html`<p class="meta ticket-empty">None</p>` : column.map((o) => html`<a class="ticket" href=${itemHref(route, o.item_id)}><b>${o.programme || o.title}</b>
        <span class="meta"><span>${o.organisation || o.source_name}</span><span class=${"due" + (o.days_left !== null && o.days_left !== undefined && o.days_left <= 2 ? " hot" : "")}>${dueText(o.days_left)}</span>
          ${key === "outcome" && html`<span>${statusLabel(o.status)}</span>`}</span></a>`)}</div>`;
  })}</div>`;
}

export function Opportunities({ refreshBoot, route }) {
  const show = ["tracked", "expired"].includes(route.query.show) ? route.query.show : "open";
  const firstYear = route.query.first_year === "1";
  const kind = route.query.kind || "";
  const { data, error, loading, reload, patch } = useLoad(() => api.opportunities({ show, first_year: firstYear, kind }), [show, firstYear, kind]);
  const [types, setTypes] = useState([]);
  useEffect(() => { if (data && show === "open" && !kind && !firstYear) setTypes([...new Set(data.opportunities.map((o) => o.type))]); }, [data]);
  const set = (change) => go(route.path, { ...route.query, ...change, item: undefined });

  const track = async (opportunity, status) => {
    try {
      await api.track(opportunity.item_id, status);
      const delta = (status ? 1 : 0) - (opportunity.status ? 1 : 0);
      patch((current) => ({ ...current, opportunities: current.opportunities.map((o) => (o.item_id === opportunity.item_id ? { ...o, status } : o)), counts: { ...current.counts, tracked: current.counts.tracked + delta } }));
      refreshBoot();
      toast(status ? `Tracking: ${statusLabel(status)}` : "No longer tracking");
    } catch (failure) {
      toast(failure.message, { error: true });
    }
  };

  const counts = data ? data.counts : {};
  const folded = data ? (show === "tracked" ? data.opportunities : foldOpportunities(data.opportunities)) : [];
  const week = data && show === "open" ? folded.filter((o) => urgencyGroup(o.days_left) === "week").length : 0;
  const filtered = firstYear || Boolean(kind);
  useEffect(() => { setReadingContext(folded.map((o) => o.item_id), "Opportunities"); });

  let body;
  if (error) body = html`<${ErrorNote} message=${error} retry=${reload} />`;
  else if (loading && !data) body = html`<${Skeleton} rows=${4} />`;
  else if (show === "tracked") {
    body = folded.length === 0
      ? html`<${Empty} title="You are not tracking anything yet" action=${html`<a class="btn" href="#/opportunities">Browse open opportunities</a>`}>Choose Track on any opportunity to follow it from interest to application. Tracked deadlines appear on Today.<//>`
      : html`<${TrackingBoard} list=${folded} route=${route} />`;
  } else if (folded.length === 0) {
    body = show === "expired"
      ? html`<${Empty} title="Nothing has closed yet">Opportunities appear here once their deadline passes. They are never shown as open.<//>`
      : filtered
        ? html`<${Empty} title="Nothing matches these filters" action=${{ label: "Clear filters", run: () => set({ kind: undefined, first_year: undefined }) }}>Clear the filters to see every open opportunity.<//>`
        : html`<${Empty} title="No open opportunities yet" action=${html`<a class="btn" href="#/sources?tab=organisations">Add organisations</a>`}>Add the organisations you want watched, or follow an opportunities source, and they appear here with their deadlines.<//>`;
  } else if (show === "expired") {
    body = html`<ul>${folded.map((o) => html`<${OppRow} key=${o.item_id} opportunity=${o} route=${route} closed=${true} />`)}</ul>`;
  } else {
    body = GROUPS.map(([group, label]) => {
      const list = folded.filter((o) => urgencyGroup(o.days_left) === group);
      return list.length ? html`<section class="opp-group" key=${group}><div class="section-head"><h2>${label}</h2><span class="aside">${list.length}</span></div>
        <ul>${list.map((o) => html`<${OppRow} key=${o.item_id} opportunity=${o} route=${route} onTrack=${track} />`)}</ul></section>` : null;
    });
  }

  return html`<div class="page">
    <header class="page-head"><h1>Opportunities</h1>
      <p>${data && show === "open" ? `${week} close this week. ${counts.first_year || 0} ${counts.first_year === 1 ? "is" : "are"} open to first-year students. ` : ""}PIA watches ${data ? plural(data.organisations.length, "organisation") : "your organisations"} and every source you follow for internships, fellowships, programmes and competitions.</p></header>
    <${Tabs} value=${show} onChange=${(value) => set({ show: value === "open" ? undefined : value, kind: undefined, first_year: undefined })}
      tabs=${[{ value: "open", label: "Open", count: counts.open }, { value: "tracked", label: "Tracking", count: counts.tracked }, { value: "expired", label: "Closed", count: counts.expired }]} />
    ${show !== "tracked" && html`<div class="filters">
      <${ChipGroup} label="Type of opportunity" value=${kind} onChange=${(value) => set({ kind: value || undefined })}
        options=${[{ value: "", label: "Every type" }, ...(types.length ? types : kind ? [kind] : []).map((value) => ({ value, label: OPPORTUNITY_TYPES[value] || value }))]} />
      <span class="grow"></span>
      <${Toggle} checked=${firstYear} onChange=${(value) => set({ first_year: value ? "1" : undefined })} label="Open to first-years" />
    </div>`}
    ${body}
    <p class="feed-note foot">Missing an organisation? <a class="textlink" href="#/sources?tab=organisations">Add it to the organisations PIA watches</a>.</p>
  </div>`;
}
