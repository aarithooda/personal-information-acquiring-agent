// The reader: one item in full, in a sheet over whatever page the owner was on. Each kind of item has its own facts.
import { html, useEffect, useRef, useState } from "../vendor/preact-htm.js";
import { api } from "./api.js";
import { ErrorNote, Icon, IconButton, MarginNote, Skeleton, itemHref, publishItem, readingContext, toast, useOverlay } from "./ui.js";
import { KIND_LABELS, OPPORTUNITY_TYPES, TRACK_STATUSES, buildRoute, dueText, dueTone, fullDate, hostOf, reasons, safeUrl, timeAgo } from "./util.js";

const RELATION = { same_story: "the same development, from another source", references: "cites or is cited by this", related: "related work" };
const BASIS = {
  full_text: "Written from the full text.", source_text: "Written from the text the source supplied (abstract or feed summary).",
  title_only: "PIA only has this item's title, so there is nothing to explain yet.",
};
const LEVEL_NAMES = { 3: "Priority", 2: "Following", 1: "Some", "-1": "Not interested", "-2": "Muted" };
const REASONS = [["not_relevant", "Not relevant to me"], ["not_interested_topic", "Not interested in this topic"], ["low_quality", "Poor quality source"]];
const fmt = (n) => Number(n || 0).toLocaleString("en-GB");

// Whole days from today (the owner's calendar day) to a YYYY-MM-DD deadline.
function daysUntil(deadline) {
  if (!deadline) return null;
  return Math.round((new Date(deadline + "T00:00:00") - new Date(new Date().toDateString())) / 86400000);
}

function Facts({ title, rows }) {
  const shown = rows.filter(([, value]) => value);
  if (!shown.length) return null;
  return html`<div><h3>${title}</h3><dl class="facts">${shown.map(([label, value]) => html`<div><dt>${label}</dt><dd>${value}</dd></div>`)}</dl></div>`;
}

function PaperFacts({ item }) {
  const arxiv = /arxiv\.org\/abs\/([\w.]+)/.exec(item.url || "");
  return html`<${Facts} title="The paper" rows=${[
    ["Authors", item.author || html`<span class="meta">Not supplied by ${item.source}</span>`],
    ["Published in", arxiv ? `arXiv preprint ${arxiv[1]}` : item.source],
    ["Published", fullDate(item.published_at) || "Not given"],
    ["Area", item.domains.map((domain) => domain.short).join(", ")],
  ]} />`;
}

function RepoFacts({ item }) {
  const [owner] = item.title.split("/");
  return html`<${Facts} title="The project" rows=${[
    ["Owner", item.title.includes("/") ? owner : null],
    ["Popularity", item.signals && item.signals.stars ? `${fmt(item.signals.stars)} stars` : "Not available"],
    ["First seen", fullDate(item.acquired_at)],
  ]} />`;
}

function StoryFacts({ item }) {
  const signals = item.signals || {};
  return html`<${Facts} title="The story" rows=${[
    ["Reported by", item.sources.map((source) => source.name).join(", ") || item.source],
    ["Published", fullDate(item.published_at) || "Not given"],
    ["Discussion", signals.points ? `${fmt(signals.points)} points, ${fmt(signals.comments)} comments on Hacker News` : null],
  ]} />`;
}

function AnnouncementFacts({ item }) {
  return html`<${Facts} title="The announcement" rows=${[["Issued by", item.source], ["Received", fullDate(item.published_at || item.acquired_at)]]} />`;
}

function ProgressStepper({ status, onTrack }) {
  const order = TRACK_STATUSES.map((entry) => entry.value);
  return html`<div class="progress-block"><span class="meta">Your progress</span>
    <div class="stepper" role="group" aria-label="Your status">${TRACK_STATUSES.map((entry) => html`<button aria-pressed=${status === entry.value}
      class=${status && order.indexOf(entry.value) < order.indexOf(status) && entry.value !== "rejected" ? "past" : ""}
      onClick=${() => onTrack(status === entry.value ? "" : entry.value)}>${entry.label}</button>`)}</div></div>`;
}

function OpportunityFacts({ opportunity: o, onTrack }) {
  const days = daysUntil(o.deadline);
  const tone = dueTone(days);
  const firstYear = { yes: ["yes", "First-year students can apply"], no: ["no", "Not open to first-year students"] }[o.first_year] || ["unknown", "First-year eligibility not stated"];
  const where = [o.location, o.mode].filter(Boolean).join(", ");
  return html`<div class="opp-facts">
    <div class="deadline-big">
      <span class=${"n " + (days === null ? "none" : tone)}>${days === null ? "No date" : days < 0 ? "Closed" : days}</span>
      <span class="what"><b>${days === null ? "No deadline given" : days < 0 ? dueText(days) : days === 0 ? "Closes today" : days === 1 ? "day left to apply" : "days left to apply"}</b>
        ${o.deadline && html`<span>Deadline ${fullDate(o.deadline)}${o.deadline_text ? `. Stated as “${o.deadline_text}”` : ""}</span>`}</span>
    </div>
    <${ProgressStepper} status=${o.status || ""} onTrack=${onTrack} />
    <dl class="facts">
      <div><dt>Eligibility</dt><dd>${o.eligibility || "Not stated"}<div class="elig-line"><span class=${"elig " + firstYear[0]}>${firstYear[1]}</span></div></dd></div>
      ${o.year_requirements && html`<div><dt>Year of study</dt><dd>${o.year_requirements}</dd></div>`}
      ${o.qualifications && html`<div><dt>Qualifications</dt><dd>${o.qualifications}</dd></div>`}
      <div><dt>Where</dt><dd>${where || "Not stated"}</dd></div>
      ${o.duration && html`<div><dt>Duration</dt><dd>${o.duration}</dd></div>`}
      ${o.start_date && html`<div><dt>Starts</dt><dd>${o.start_date}</dd></div>`}
      <div><dt>Details from</dt><dd class="meta">${o.extracted_by === "model" ? "A model read the posting. Check dates on the official page before applying." : "Only fields the source supplied. Details may be incomplete."}</dd></div>
    </dl>
  </div>`;
}

function Brief({ item, brief, writing }) {
  const heading = item.kind === "paper" ? "Key findings" : item.kind === "news" ? "What to know" : "Key points";
  if (writing && !brief) return html`<div aria-busy="true"><p class="basis">Writing the brief from the ${item.kind === "paper" ? "paper" : "source"}…</p><${Skeleton} rows=${2} /></div>`;
  if (!brief) return item.summary ? html`<div class="prose"><p class="lede">${item.summary}</p></div>` : null;
  return html`<div class="prose">
    <p class="lede">${brief.summary}</p>
    ${brief.key_points.length > 0 && html`<h2>${heading}</h2><ul>${brief.key_points.map((point) => html`<li>${point}</li>`)}</ul>`}
    ${brief.sections.map((section) => html`<h2>${section.heading}</h2><p>${section.body}</p>`)}
    <p class="basis">${BASIS[brief.basis] || ""} Generated summaries can be wrong; the original is the authority.</p>
  </div>`;
}

function SourceText({ item, open }) {
  if (!item.content) return null;
  const label = item.kind === "paper" ? "Abstract" : item.kind === "repo" ? "From the README" : "Text supplied by the source";
  return html`<details class="disclose" open=${open}><summary>${label}</summary><p class="source-text">${item.content}</p></details>`;
}

function Connected({ related, route }) {
  if (!related.length) return null;
  return html`<section><h2 class="block-title">Connected<span>${related.length} ${related.length === 1 ? "item" : "items"}</span></h2><ul class="related">
    ${related.map((other) => html`<li><span class="when">${timeAgo(other.sort_at)}</span><span><a href=${itemHref(route, other.id)}>${other.title}</a>
      <span class="rel">${other.direction === "later" ? "Later" : "Earlier"}: ${RELATION[other.kind] || "related work"}. ${other.source_name}</span></span></li>`)}</ul></section>`;
}

function WhyPanel({ item, opportunity }) {
  const significance = Math.round((item.importance ?? 0) * 100);
  const fit = Math.round((item.relevance ?? 0) * 100);
  return html`<div><h3>Why you are seeing this</h3>
    <${MarginNote} reasons=${reasons(item, opportunity)} />
    <dl class="kv">
      <div><dt>Significance in its field</dt><dd><div class="meter" role="img" aria-label=${`Significance ${significance} out of 100`}><i style=${{ width: significance + "%" }}></i></div></dd></div>
      <div><dt>Fit with your interests</dt><dd><div class="meter" role="img" aria-label=${`Fit ${fit} out of 100`}><i style=${{ width: fit + "%" }}></i></div></dd></div>
    </dl></div>`;
}

function Feedback({ item, onSend }) {
  const [open, setOpen] = useState(false);
  const [reasonFor, setReasonFor] = useState(null);
  const [text, setText] = useState("");
  const given = (kind) => item.feedback.some((entry) => entry.kind === kind);
  const yes = given("useful") || given("like");
  const no = REASONS.some(([kind]) => given(kind));
  const send = (kind, reason = "") => { onSend(kind, reason); setReasonFor(null); setText(""); };
  return html`<section class="feedback-wrap">
    <p class="feedback-q">Was this worth your attention?</p>
    <div class="feedback">
      <button class=${"btn small" + (yes ? " on" : "")} aria-pressed=${yes} onClick=${() => send(given("like") && !given("useful") ? "like" : "useful")}>Yes, more like this</button>
      <button class=${"btn small" + (no ? " on" : "")} aria-expanded=${open || no} onClick=${() => setOpen(!open)}>No…</button>
      ${(open || no) && REASONS.map(([kind, label]) => html`<button class=${"btn small" + (given(kind) ? " on" : " quiet")} aria-pressed=${given(kind)}
        onClick=${() => (given(kind) ? send(kind) : setReasonFor(kind))}>${label}</button>`)}
    </div>
    ${reasonFor && html`<form class="stack" onSubmit=${(event) => { event.preventDefault(); send(reasonFor, text); }}>
      <p class="basis">Optional: say why, in your own words. A reason helps PIA tell a bad source from a topic you do not care about.</p>
      <textarea class="field" rows="2" maxlength="1000" value=${text} onInput=${(event) => setText(event.target.value)} aria-label="Why"></textarea>
      <div class="form-row"><button class="btn small primary" type="submit">Send feedback</button><button class="btn small quiet" type="button" onClick=${() => setReasonFor(null)}>Cancel</button></div>
    </form>`}
  </section>`;
}

export function ReaderSheet({ id, close, refreshBoot, route }) {
  const [item, setItem] = useState(null);
  const [error, setError] = useState(null);
  const [briefState, setBriefState] = useState("idle");  // idle | writing | failed
  const [briefError, setBriefError] = useState(null);
  const [readerBusy, setReaderBusy] = useState(false);
  const [focus, setFocus] = useState(false);
  const box = useRef(null);
  const scroller = useRef(null);
  useOverlay("reader", close, box);

  const ids = readingContext.ids;
  const at = ids.indexOf(id);
  const state = useRef({});
  state.current = { at, ids, route, item };
  const step = (offset) => {
    const { at: here, ids: list, route: current } = state.current;
    if (here < 0 || here + offset < 0 || here + offset >= list.length) return;
    location.replace(itemHref(current, list[here + offset]));
  };

  const writeBrief = (current) => {
    if (!current.can_brief || current.brief) return;
    setBriefState("writing");
    api.brief(current.id).then(
      (result) => { setItem((now) => (now && now.id === current.id ? { ...now, brief: result.brief, reader: result.reader || now.reader, summary: now.summary || result.brief.summary } : now)); setBriefState("idle"); },
      (failure) => { setBriefError(failure.message); setBriefState("failed"); },
    );
  };

  useEffect(() => {
    let live = true;
    setItem(null); setError(null); setBriefState("idle"); setBriefError(null);
    if (scroller.current) scroller.current.scrollTop = 0;
    api.item(id).then(
      (data) => {
        if (!live) return;
        setItem(data);
        api.open(id).then(() => { if (live) { setItem((now) => now && { ...now, read: true }); publishItem(id, { read: true }); refreshBoot(); } }, () => {});
        writeBrief(data);
      },
      (failure) => live && setError(failure.message),
    );
    return () => { live = false; };
  }, [id]);

  useEffect(() => {
    document.body.style.overflow = "hidden";
    box.current?.focus();
    return () => { document.body.style.overflow = ""; };
  }, []);

  const change = async (stateChange, message) => {
    try {
      await api.setState(id, stateChange);
      setItem((now) => ({ ...now, ...stateChange }));
      publishItem(id, stateChange);
      refreshBoot();
      if (stateChange.dismissed) {
        toast("Dismissed. It won't appear on Today again.", { undo: () => api.setState(id, { dismissed: false }).then(() => { publishItem(id, { dismissed: false }); refreshBoot(); }, () => {}) });
        close();
      } else if (message) toast(message);
    } catch (failure) {
      toast(failure.message, { error: true });
    }
  };
  state.current.change = change;

  useEffect(() => {
    const onKey = (event) => {
      if (document.body.dataset.overlay !== "reader" || event.metaKey || event.ctrlKey || event.altKey || /INPUT|TEXTAREA|SELECT/.test(event.target.tagName)) return;
      const current = state.current.item;
      if (event.key === "j") step(1);
      else if (event.key === "k") step(-1);
      else if (event.key === "f") setFocus((value) => !value);
      else if (!current) return;
      else if (event.key === "s") state.current.change({ saved: !current.saved }, current.saved ? "Removed from Library" : "Saved to Library");
      else if (event.key === "v") { const url = safeUrl(current.url); if (url) window.open(url, "_blank", "noopener,noreferrer"); }
      else return;
      event.preventDefault();
    };
    addEventListener("keydown", onKey);
    return () => removeEventListener("keydown", onKey);
  }, []);

  const sendFeedback = async (kind, text = "") => {
    const given = item.feedback.some((entry) => entry.kind === kind);
    try {
      const result = given && !text ? await api.undoFeedback(id, kind) : await api.feedback(id, kind, text);
      setItem((now) => ({ ...now, feedback: result.feedback }));
      toast(given && !text ? "Feedback removed" : "Thanks. PIA will use this for ranking.");
    } catch (failure) {
      toast(failure.message, { error: true });
    }
  };
  const track = async (status) => {
    try {
      await api.track(id, status);
      const saves = ["interested", "planning", "applied"].includes(status);
      setItem((now) => ({ ...now, opportunity: { ...now.opportunity, status }, saved: saves ? true : now.saved }));
      if (saves) publishItem(id, { saved: true });
      refreshBoot();
      toast(status ? `Tracking: ${TRACK_STATUSES.find((entry) => entry.value === status).label}` : "No longer tracking");
    } catch (failure) {
      toast(failure.message, { error: true });
    }
  };
  const loadReader = () => {
    setReaderBusy(true);
    api.reader(id).then((reader) => setItem((now) => ({ ...now, reader })), (failure) => toast(failure.message, { error: true })).finally(() => setReaderBusy(false));
  };

  const opportunity = item && item.opportunity;
  const original = item && safeUrl(item.url);
  const primaryUrl = item && ((opportunity && safeUrl(opportunity.apply_url)) || original);
  const primaryLabel = !item ? "" : opportunity && safeUrl(opportunity.apply_url) ? "Apply" : item.kind === "paper" ? "Open paper" : item.kind === "repo" ? "Open repository" : "Open original";
  const brief = item && item.brief && item.brief.summary ? item.brief : null;
  const reader = item && item.reader;
  const [owner, ...rest] = item && item.kind === "repo" ? item.title.split("/") : [];
  const repoName = rest.join("/");
  const title = !item ? "" : item.kind === "repo" && repoName ? html`<span class="owner">${owner}/</span>${repoName}` : opportunity ? opportunity.programme || item.title : item.title;

  return html`<div>
    <div class="scrim" onClick=${close}></div>
    <aside class=${"sheet" + (focus ? " focus" : "")} role="dialog" aria-modal="true" aria-label=${item ? item.title : "Item"} tabindex="-1" ref=${box}>
      <div class="sheet-bar">
        <${IconButton} icon="x" label="Close (Esc)" onClick=${close} />
        ${at >= 0 && html`<${IconButton} icon="up" label="Previous (K)" onClick=${() => step(-1)} className=${at === 0 ? "off" : ""} />
          <${IconButton} icon="down" label="Next (J)" onClick=${() => step(1)} className=${at >= ids.length - 1 ? "off" : ""} />
          <span class="crumb">${at + 1} of ${ids.length}</span>`}
        <span class="grow"></span>
        ${item && html`
          <button class=${"btn small" + (item.saved ? " on" : "")} aria-pressed=${item.saved} onClick=${() => change({ saved: !item.saved }, item.saved ? "Removed from Library" : "Saved to Library")}>
            <${Icon} name="bookmark" size=${15} fill=${item.saved} />${item.saved ? "Saved" : "Save"}</button>
          <button class="btn small quiet wide-only" onClick=${() => change({ read: !item.read })}>${item.read ? "Mark unread" : "Mark read"}</button>
          <button class="btn small quiet wide-only" onClick=${() => change({ dismissed: !item.dismissed }, item.dismissed ? "Restored" : undefined)}>${item.dismissed ? "Restore" : "Dismiss"}</button>
          <${IconButton} icon="expand" label="Focus mode (F)" onClick=${() => setFocus(!focus)} pressed=${focus} className="wide-only" />
          ${primaryUrl && html`<a class="btn small primary" href=${primaryUrl} target="_blank" rel="noopener noreferrer">${primaryLabel}<${Icon} name="external" size=${14} /></a>`}`}
      </div>
      <div class="sheet-scroll" ref=${scroller}>
        ${error && html`<div class="reader"><div class="col"><${ErrorNote} message="This item could not be loaded." /><p class="basis">${error}</p><div><button class="btn" onClick=${close}>Close</button></div></div></div>`}
        ${!item && !error && html`<div class="reader"><div class="col"><${Skeleton} rows=${3} /></div></div>`}
        ${item && html`<article class="reader">
          <div class="col">
            <div class="kicker"><span class="kind"><${Icon} name=${item.kind} size=${15} />${KIND_LABELS[item.kind] || "Item"}</span><span>${item.source}${original ? ` (${hostOf(original)})` : ""}</span>
              <span>${timeAgo(item.sort_at)}</span>${item.resurfaced && html`<span>Back in view</span>`}</div>
            <h1>${title}</h1>
            ${opportunity && html`<div class="byline">${[opportunity.organisation || item.source, OPPORTUNITY_TYPES[opportunity.type] || "Opportunity", opportunity.location].filter(Boolean).join("  ·  ")}</div>`}
            ${item.spam && html`<div class="callout warn">This repository name appears under many different owners, a common pattern for copied or spam projects. Check the owner before relying on it.</div>`}
            ${opportunity && html`<${OpportunityFacts} opportunity=${opportunity} onTrack=${track} />`}
            ${brief && brief.why && html`<div class="why-block"><small>Why it matters</small>${brief.why}</div>`}
            <${Brief} item=${item} brief=${brief} writing=${briefState === "writing"} />
            ${briefState === "failed" && html`<div class="callout warn" role="alert">${briefError} <button class="textlink" onClick=${() => writeBrief({ ...item, brief: null })}>Try again</button></div>`}
            ${!item.can_brief && !brief && html`<div class="callout">No model is configured, so PIA shows what the source supplied. Add a Groq key to get full briefs.</div>`}
            <${SourceText} item=${item} open=${!brief} />
            ${reader && reader.status === "ok" && html`<details class="disclose"><summary>${reader.mode === "full" ? "Full text" : "Excerpt from the page"}${reader.source ? ` (${hostOf(reader.source)})` : ""}</summary>
              <div class="source-text">${reader.paragraphs.map((paragraph) => html`<p>${paragraph}</p>`)}</div>
              ${reader.mode === "excerpt" && original && html`<p class="basis">Only an excerpt is shown, out of respect for the publisher. <a class="textlink" href=${original} target="_blank" rel="noopener noreferrer">Continue at the source</a>.</p>`}</details>`}
            ${(!reader || reader.status === "not_fetched") && html`<div class="read-further"><p class="basis">PIA can fetch the page to show more of it here where that is permitted. Papers on arXiv open in full; other sources show an excerpt.</p>
              <button class="btn small" disabled=${readerBusy} onClick=${loadReader}>${readerBusy ? "Fetching…" : "Read further"}</button></div>`}
            ${reader && reader.status === "unavailable" && html`<p class="basis">The text could not be shown here: ${reader.reason}. ${original && html`<a class="textlink" href=${original} target="_blank" rel="noopener noreferrer">Open the original</a>.`}</p>`}
            <${Connected} related=${item.related} route=${route} />
            <${Feedback} item=${item} onSend=${sendFeedback} />
          </div>
          <aside class="side" aria-label="About this item">
            ${item.kind === "paper" && html`<${PaperFacts} item=${item} />`}
            ${item.kind === "repo" && html`<${RepoFacts} item=${item} />`}
            ${item.kind === "announcement" && html`<${AnnouncementFacts} item=${item} />`}
            ${!["paper", "repo", "announcement", "opportunity"].includes(item.kind) && html`<${StoryFacts} item=${item} />`}
            <${WhyPanel} item=${item} opportunity=${opportunity} />
            ${item.topics.length > 0 && html`<div><h3>Topics</h3><div class="topics">${item.topics.slice(0, 8).map((topic) => html`<a href=${buildRoute(["topic", String(topic.id)])}>${topic.name}<em>${LEVEL_NAMES[topic.level] || ""}</em></a>`)}</div></div>`}
          </aside>
        </article>`}
      </div>
    </aside>
  </div>`;
}
