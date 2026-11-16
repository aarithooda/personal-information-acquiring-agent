// The reader: one item in full, in a panel over whatever page the owner was on.
import { html, useEffect, useRef, useState } from "../vendor/preact-htm.js";
import { api } from "./api.js";
import { ErrorNote, Icon, Loading, hueStyle, toast } from "./ui.js";
import { KIND_LABELS, OPPORTUNITY_TYPES, TRACK_STATUSES, buildRoute, deadlineText, fullDate, hostOf, safeUrl, timeAgo } from "./util.js";

const FEEDBACK = [
  { kind: "like", label: "I liked this" },
  { kind: "useful", label: "Useful" },
  { kind: "not_relevant", label: "Not relevant to me" },
  { kind: "low_quality", label: "Poor quality" },
  { kind: "not_interested_topic", label: "Not interested in this topic" },
];
const RELATION = { same_story: "The same development, from another source", references: "Cited by or cites this item", related: "Related" };
const BASIS = {
  full_text: "Written from the full text.", source_text: "Written from the text the source supplied (abstract or feed summary).",
  title_only: "PIA only has this item's title, so there is nothing to explain yet.",
};

function Meter({ label, value, note }) {
  return html`<div><div class="label"><span>${label}</span><span>${Math.round((value ?? 0) * 100)}</span></div>
    <div class="meter"><i style=${{ width: `${Math.round((value ?? 0) * 100)}%` }}></i></div>
    ${note && html`<p class="hint">${note}</p>`}</div>`;
}

function OpportunityFacts({ item, onTrack }) {
  const o = item.opportunity;
  const days = o.deadline ? Math.round((new Date(o.deadline + "T00:00:00") - new Date(new Date().toDateString())) / 86400000) : null;
  const fact = (label, value, extra = "") => value && html`<div class=${extra}><dt>${label}</dt><dd class=${extra === "wide" ? "plain" : ""}>${value}</dd></div>`;
  return html`<dl class="facts">
    <div><dt>Deadline</dt><dd class="big">${o.deadline ? fullDate(o.deadline) : "Not stated"}</dd>
      ${days !== null && html`<span class=${"due" + (days < 0 ? " over" : days <= 7 ? " soon" : "")}>${deadlineText(days)}</span>`}</div>
    <div><dt>First-year students</dt><dd><span class=${"badge " + (o.first_year === "yes" ? "yes" : o.first_year === "no" ? "no" : "")}>
      ${o.first_year === "yes" ? "Can apply" : o.first_year === "no" ? "Not eligible" : "Not stated"}</span></dd></div>
    <div><dt>Your status</dt><dd><select value=${o.status} onChange=${(event) => onTrack(event.target.value)} aria-label="Your status for this opportunity">
      <option value="">Not tracking</option>${TRACK_STATUSES.map((s) => html`<option value=${s.value}>${s.label}</option>`)}</select></dd></div>
    ${fact("Organisation", o.organisation)}${fact("Type", OPPORTUNITY_TYPES[o.type] || "Opportunity")}
    ${fact("Location", [o.location, o.mode].filter(Boolean).join(", "))}${fact("Starts", o.start_date)}${fact("Duration", o.duration)}
    ${fact("Eligibility", o.eligibility, "wide")}${fact("Year of study", o.year_requirements, "wide")}${fact("Qualifications", o.qualifications, "wide")}
    ${o.deadline_text && fact("Deadline as stated", o.deadline_text, "wide")}
    <div class="wide"><dt>Extracted by</dt><dd class="plain">${o.extracted_by === "model" ? "A model, from the posting's text. Check the original before relying on dates." : "Rules only (dates and fields the source supplied). Details may be incomplete."}</dd></div>
  </dl>`;
}

export function ItemPanel({ id, close, refreshBoot, route, boot }) {
  const [item, setItem] = useState(null);
  const [error, setError] = useState(null);
  const [briefState, setBriefState] = useState("idle");  // idle | writing | failed
  const [briefError, setBriefError] = useState(null);
  const [readerBusy, setReaderBusy] = useState(false);
  const [reasonFor, setReasonFor] = useState(null);
  const [reason, setReason] = useState("");
  const panel = useRef(null);

  const writeBrief = (current) => {
    if (!current.can_brief || current.brief) return;
    setBriefState("writing");
    api.brief(id).then(
      (result) => { setItem((now) => now && { ...now, brief: result.brief, reader: result.reader || now.reader, summary: now.summary || result.brief.summary }); setBriefState("idle"); },
      (failure) => { setBriefError(failure.message); setBriefState("failed"); },
    );
  };

  useEffect(() => {
    let live = true;
    api.item(id).then(
      (data) => {
        if (!live) return;
        setItem(data);
        api.open(id).then(() => { setItem((now) => now && { ...now, read: true }); refreshBoot(); }, () => {});
        writeBrief(data);
      },
      (failure) => live && setError(failure.message),
    );
    panel.current?.focus();
    const onKey = (event) => event.key === "Escape" && close();
    addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => { live = false; removeEventListener("keydown", onKey); document.body.style.overflow = ""; };
  }, [id]);

  const change = async (stateChange, message) => {
    try {
      await api.setState(id, stateChange);
      setItem((now) => ({ ...now, ...stateChange }));
      refreshBoot();
      if (message) toast(message);
      if (stateChange.dismissed) close();
    } catch (failure) {
      toast(failure.message, { error: true });
    }
  };
  const sendFeedback = async (kind, text = "") => {
    const given = item.feedback.some((entry) => entry.kind === kind);
    try {
      const result = given && !text ? await api.undoFeedback(id, kind) : await api.feedback(id, kind, text);
      setItem((now) => ({ ...now, feedback: result.feedback }));
      toast(given && !text ? "Feedback removed" : "Thanks. PIA will use this for ranking.");
      setReasonFor(null); setReason("");
    } catch (failure) {
      toast(failure.message, { error: true });
    }
  };
  const track = async (status) => {
    try {
      await api.track(id, status);
      setItem((now) => ({ ...now, opportunity: { ...now.opportunity, status }, saved: status ? true : now.saved }));
      refreshBoot();
      toast(status ? "Tracking updated" : "No longer tracking");
    } catch (failure) {
      toast(failure.message, { error: true });
    }
  };
  const loadReader = () => {
    setReaderBusy(true);
    api.reader(id).then((reader) => setItem((now) => ({ ...now, reader })), (failure) => toast(failure.message, { error: true })).finally(() => setReaderBusy(false));
  };

  const original = item && safeUrl(item.url);
  const brief = item && item.brief;
  const hasBrief = brief && brief.summary;
  const reader = item && item.reader;

  return html`<div>
    <div class="scrim" onClick=${close}></div>
    <aside class="panel" role="dialog" aria-modal="true" aria-label="Item" tabindex="-1" ref=${panel} style=${hueStyle(item ? item.hue : undefined)}>
      <div class="panel-bar">
        <button class="button small quiet" onClick=${close}><${Icon} name="back" size=${15} /> Back</button>
        <span class="grow"></span>
        ${item && html`
          <button class=${"button small" + (item.saved ? " on" : "")} aria-pressed=${item.saved} onClick=${() => change({ saved: !item.saved }, item.saved ? "Removed from Saved" : "Saved")}>
            <${Icon} name="bookmark" size=${15} fill=${item.saved} /> ${item.saved ? "Saved" : "Save"}</button>
          <button class="button small" onClick=${() => change({ read: !item.read })}>${item.read ? "Mark unread" : "Mark read"}</button>
          <button class="button small" onClick=${() => change({ dismissed: !item.dismissed }, item.dismissed ? "Restored" : "Dismissed")}>${item.dismissed ? "Restore" : "Dismiss"}</button>
          ${original && html`<a class="button small primary" href=${original} target="_blank" rel="noopener noreferrer" onClick=${() => {}}>Open original <${Icon} name="external" size=${14} /></a>`}`}
      </div>
      <div class="panel-body">
        ${error && html`<${ErrorNote} message=${error} />`}
        ${!item && !error && html`<${Loading} lines=${7} />`}
        ${item && html`
          <div class="item-meta">
            <span class="kind">${KIND_LABELS[item.kind] || "Item"}</span>
            <span>${item.source}${original ? ` (${hostOf(original)})` : ""}</span>
            ${item.author && html`<span>${item.author}</span>`}
          </div>
          <h2 class="item-title">${item.title}</h2>
          <div class="item-meta">
            ${item.published_at ? html`<span>Published ${fullDate(item.published_at)}</span>` : html`<span>Publication date not given</span>`}
            <span>Acquired ${timeAgo(item.acquired_at)}</span>
            ${item.domains.map((domain) => html`<a class="chip" href=${buildRoute(["space", domain.slug])}>${domain.short}</a>`)}
          </div>

          ${item.opportunity && html`<${OpportunityFacts} item=${item} onTrack=${track} />`}

          <div class="block">
            ${briefState === "writing" && !hasBrief && html`<div aria-busy="true"><p class="hint">Writing the brief…</p><${Loading} lines=${6} /></div>`}
            ${hasBrief && html`<div class="prose"><p>${brief.summary}</p></div>`}
            ${hasBrief && brief.why && html`<div class="why"><b>Why it matters</b>${brief.why}</div>`}
            ${hasBrief && brief.key_points.length > 0 && html`<div class="prose"><h3>Key points</h3><ul>${brief.key_points.map((point) => html`<li>${point}</li>`)}</ul></div>`}
            ${hasBrief && brief.sections.map((section) => html`<div class="prose"><h3>${section.heading}</h3><p>${section.body}</p></div>`)}
            ${brief && html`<p class="basis">${BASIS[brief.basis] || ""} ${hasBrief ? "Summaries are generated; the original is the authority." : ""}</p>`}
            ${!hasBrief && briefState !== "writing" && item.summary && html`<div class="prose"><p>${item.summary}</p></div>`}
            ${briefState === "failed" && html`<div class="notice error">${briefError} <button class="button small quiet" onClick=${() => writeBrief({ ...item, brief: null })}>Try again</button></div>`}
            ${!item.can_brief && !brief && html`<p class="notice">No model is configured, so PIA shows what the source supplied. Add a Groq key to get full briefs.</p>`}
          </div>

          ${item.content && html`<div class="block"><h3>From the source</h3>
            <details open=${!hasBrief}><summary>${item.kind === "paper" ? "Abstract" : "Text supplied by the source"}</summary><p class="source-text">${item.content}</p></details></div>`}

          <div class="block"><h3>Read further</h3>
            ${(!reader || reader.status === "not_fetched") && html`<p class="hint">PIA can fetch the page to show more of it here where that is permitted. Papers on arXiv open in full; other sources show an excerpt.</p>
              <button class="button small" disabled=${readerBusy} onClick=${loadReader}>${readerBusy ? "Fetching…" : "Fetch the text"}</button>`}
            ${reader && reader.status === "unavailable" && html`<p class="hint">The text could not be shown here: ${reader.reason}. ${original && html`<a href=${original} target="_blank" rel="noopener noreferrer"><u>Open the original</u></a>.`}</p>`}
            ${reader && reader.status === "ok" && html`<details><summary>${reader.mode === "full" ? "Full text" : "Excerpt"} (${hostOf(reader.source)})</summary>
              <div class="prose">${reader.paragraphs.map((paragraph) => html`<p>${paragraph}</p>`)}</div>
              ${reader.mode === "excerpt" && html`<p class="basis">Only an excerpt is shown, out of respect for the publisher. ${original && html`<a href=${original} target="_blank" rel="noopener noreferrer"><u>Continue at the source</u></a>.`}</p>`}</details>`}
          </div>

          ${item.related.length > 0 && html`<div class="block"><h3>Connected items</h3><ul class="related">
            ${item.related.map((other) => html`<li><span class="when">${timeAgo(other.sort_at)}</span>
              <span><a href=${buildRoute(route.path, { ...route.query, item: other.id })}>${other.title}</a>
              <span class="rel">${other.direction === "later" ? "Later: " : "Earlier: "}${RELATION[other.kind]} (${other.source_name})</span></span></li>`)}
          </ul></div>`}

          <div class="block"><h3>Why you are seeing this</h3>
            <div class="signal-grid">
              <${Meter} label="Importance" value=${item.importance} note=${item.model_importance !== null ? "A model's reading of its significance in its field, plus source and popularity signals." : "From source trust and popularity only; not yet assessed by a model."} />
              <${Meter} label="Relevance to you" value=${item.relevance} note=${item.fit !== null ? "Your interest profile, plus a personal-fit judgement (Jev)." : "From your interest profile: the space's weight and the topics below."} />
            </div>
            ${item.topics.length > 0 && html`<div class="row-foot">${item.topics.map((topic) => html`<a class=${"chip" + (topic.level >= 2 ? " liked" : "") + (topic.level === -2 ? " muted" : "")}
              href=${buildRoute(["topic", String(topic.id)])} title="Open this topic to follow, mute or browse it">${topic.name}</a>`)}</div>`}
            ${item.sources.length > 1 && html`<p class="hint">Reported by ${item.sources.map((source) => source.name).join(", ")}.</p>`}
          </div>

          <div class="block"><h3>Was this worth your attention?</h3>
            <div class="feedback">${FEEDBACK.map((option) => {
              const given = item.feedback.some((entry) => entry.kind === option.kind);
              return html`<button class=${"button small" + (given ? " on" : "")} aria-pressed=${given}
                onClick=${() => (given || option.kind === "like" || option.kind === "useful" ? sendFeedback(option.kind) : setReasonFor(option.kind))}>${option.label}</button>`;
            })}</div>
            ${reasonFor && html`<form class="stack" onSubmit=${(event) => { event.preventDefault(); sendFeedback(reasonFor, reason); }}>
              <p class="hint">Optional: say why, in your own words.</p>
              <textarea class="field" rows="2" maxlength="1000" value=${reason} onInput=${(event) => setReason(event.target.value)} placeholder="A reason helps PIA tell a bad source from a topic you do not care about"></textarea>
              <div class="form-row"><button class="button small primary" type="submit">Send feedback</button><button class="button small quiet" type="button" onClick=${() => setReasonFor(null)}>Cancel</button></div>
            </form>`}
          </div>`}
      </div>
    </aside>
  </div>`;
}
