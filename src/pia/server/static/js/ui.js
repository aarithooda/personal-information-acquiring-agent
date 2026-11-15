// Shared components. Text from the web is only ever rendered as text nodes (never as HTML), and links are only
// made from http(s) URLs, so nothing an item contains can run in the page.
import { html, useEffect, useState, useCallback } from "../vendor/preact-htm.js";
import { KIND_LABELS, buildRoute, marks, safeUrl, tier, timeAgo } from "./util.js";

const PATHS = {
  search: "M11 19a8 8 0 1 1 0-16 8 8 0 0 1 0 16Zm10 2-4.3-4.3",
  bookmark: "M6 3h12v18l-6-4.5L6 21V3Z",
  check: "M4 12.5 9.5 18 20 6.5",
  x: "M6 6l12 12M18 6 6 18",
  external: "M14 4h6v6M20 4l-9 9M18 14v6H4V6h6",
  bell: "M6 9a6 6 0 1 1 12 0c0 6 2.5 7 2.5 7h-17S6 15 6 9Zm4 10a2 2 0 0 0 4 0",
  home: "M4 11 12 4l8 7v9h-5v-6H9v6H4v-9Z",
  target: "M12 21a9 9 0 1 1 0-18 9 9 0 0 1 0 18Zm0-4a5 5 0 1 0 0-10 5 5 0 0 0 0 10Zm0-4a1 1 0 1 0 0-2",
  rss: "M5 19a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3ZM4 10a10 10 0 0 1 10 10M4 4a16 16 0 0 1 16 16",
  sliders: "M4 7h10M18 7h2M4 17h4M12 17h8M16 4v6M10 14v6",
  pulse: "M3 12h4l3-8 4 16 3-8h4",
  menu: "M4 7h16M4 12h16M4 17h16",
  refresh: "M20 11a8 8 0 1 0-2.3 6M20 5v6h-6",
  plus: "M12 5v14M5 12h14",
  back: "M15 5l-7 7 7 7",
  eyeoff: "M3 3l18 18M10.6 6.2A9.8 9.8 0 0 1 12 6c6 0 9.5 6 9.5 6a16 16 0 0 1-3 3.6M6.3 7.6A16 16 0 0 0 2.5 12S6 18 12 18c1.3 0 2.5-.3 3.6-.7",
};

export function Icon({ name, size = 18, fill = false }) {
  return html`<svg width=${size} height=${size} viewBox="0 0 24 24" fill=${fill ? "currentColor" : "none"} stroke="currentColor"
    stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d=${PATHS[name]} /></svg>`;
}

export function Marks({ value, label = "Importance" }) {
  const lit = marks(value);
  return html`<span class="marks" role="img" aria-label=${`${label} ${lit} of 4`} title=${`${label} ${lit} of 4`}>
    ${[1, 2, 3, 4].map((n) => html`<i class=${n <= lit ? "lit" : ""}></i>`)}
  </span>`;
}

// Load something, track loading and error, and allow a reload.
export function useLoad(loader, deps) {
  const [state, setState] = useState({ loading: true, data: null, error: null });
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let live = true;
    setState((previous) => ({ ...previous, loading: true, error: null }));
    loader().then(
      (data) => live && setState({ loading: false, data, error: null }),
      (error) => live && setState({ loading: false, data: null, error: error.message || "Something went wrong." }),
    );
    return () => { live = false; };
  }, [...deps, tick]);
  const reload = useCallback(() => setTick((n) => n + 1), []);
  const patch = useCallback((change) => setState((previous) => ({ ...previous, data: typeof change === "function" ? change(previous.data) : change })), []);
  return { ...state, reload, patch };
}

export function Loading({ lines = 4 }) {
  return html`<div aria-busy="true" aria-label="Loading">
    <div class="skeleton title"></div>
    ${Array.from({ length: lines }, () => html`<div class="skeleton line"></div>`)}
  </div>`;
}

export function ErrorNote({ message, retry }) {
  return html`<div class="notice error" role="alert">${message}
    ${retry && html` <button class="button small quiet" onClick=${retry}>Try again</button>`}</div>`;
}

export function Empty({ title, hint, action }) {
  return html`<div class="empty"><h2>${title}</h2><p>${hint}</p>
    ${action && html`<button class="button" onClick=${action.run}>${action.label}</button>`}</div>`;
}

export function hueStyle(hue) {
  return { "--hue": hue ?? 225 };
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

// ---------- toasts ----------
let pushToast = () => {};
export function toast(message, options = {}) {
  pushToast({ id: Math.random(), message, ...options });
}

export function Toasts() {
  const [list, setList] = useState([]);
  useEffect(() => {
    pushToast = (entry) => {
      setList((current) => [...current.slice(-2), entry]);
      setTimeout(() => setList((current) => current.filter((t) => t.id !== entry.id)), entry.undo ? 7000 : 4000);
    };
  }, []);
  return html`<div class="toasts" role="status" aria-live="polite">
    ${list.map((entry) => html`<div class=${"toast" + (entry.error ? " error" : "")} key=${entry.id}>
      <span>${entry.message}</span>
      ${entry.undo && html`<button onClick=${() => { entry.undo(); setList((current) => current.filter((t) => t.id !== entry.id)); }}>Undo</button>`}
    </div>`)}
  </div>`;
}

export function Tabs({ tabs, value, onChange }) {
  return html`<div class="tabs" role="tablist">
    ${tabs.map((tab) => html`<button role="tab" aria-selected=${tab.value === value} onClick=${() => onChange(tab.value)}>
      ${tab.label}${tab.count !== undefined && html`<span class="n">${tab.count}</span>`}</button>`)}
  </div>`;
}

export function Segmented({ options, value, onChange, label }) {
  return html`<div class="seg" role="group" aria-label=${label}>
    ${options.map((option) => html`<button aria-pressed=${option.value === value} onClick=${() => onChange(option.value)}>${option.label}</button>`)}
  </div>`;
}
