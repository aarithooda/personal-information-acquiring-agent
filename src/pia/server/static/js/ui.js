// Shared components. Text from the web is only ever rendered as text nodes (never as HTML), and links are only
// made from http(s) URLs, so nothing an item contains can run in the page.
import { html, useCallback, useEffect, useRef, useState } from "../vendor/preact-htm.js";
import { api } from "./api.js";
import { KIND_LABELS, buildRoute, dueText, parseRoute, reasons, safeUrl, timeAgo } from "./util.js";

// ---------- icons (24px grid, 1.6 stroke) ----------

const PATHS = {
  today: "M4 5h16v15H4zM4 9h16M8 3v4M16 3v4",
  spaces: "M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z",
  flag: "M5 21V4M5 4h11l-2 4 2 4H5",
  library: "M6 3h12v18l-6-4.5L6 21z",
  search: "M11 18a7 7 0 1 1 0-14 7 7 0 0 1 0 14zm9 3-4.2-4.2",
  bell: "M6 9a6 6 0 1 1 12 0c0 6 2.5 7 2.5 7h-17S6 15 6 9zm4 10a2 2 0 0 0 4 0",
  sliders: "M4 7h10M18 7h2M4 17h4M12 17h8M16 4v6M10 14v6",
  rss: "M5 19a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3zM4 10a10 10 0 0 1 10 10M4 4a16 16 0 0 1 16 16",
  gear: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z",
  check: "M5 12.5 10 17.5 19.5 7",
  x: "M6 6l12 12M18 6 6 18",
  bookmark: "M6 3h12v18l-6-4.5L6 21z",
  external: "M14 4h6v6M20 4l-9 9M18 14v6H4V6h6",
  up: "M6 15l6-6 6 6",
  down: "M6 9l6 6 6-6",
  expand: "M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5",
  more: "M5 12h.01M12 12h.01M19 12h.01",
  plus: "M12 5v14M5 12h14",
  menu: "M4 7h16M4 12h16M4 17h16",
  paper: "M7 3h7l4 4v14H7zM14 3v4h4M9.5 12h6M9.5 15.5h6M9.5 9h2",
  repo: "M7 4a2 2 0 1 0 0 4 2 2 0 0 0 0-4zM7 8v8M7 16a2 2 0 1 0 0 4 2 2 0 0 0 0-4zM17 8a2 2 0 1 0 0-4 2 2 0 0 0 0 4zM17 8c0 5-10 3-10 8",
  news: "M4 5h13v14H6a2 2 0 0 1-2-2zM17 9h3v8a2 2 0 0 1-2 2M7.5 9h6M7.5 12.5h6M7.5 16h4",
  post: "M5 5h14v10H10l-5 4z",
  release: "M3.5 12.5 11 5h8v8l-7.5 7.5zM15 9h.01",
  opportunity: "M5 21V4M5 4h11l-2 4 2 4H5",
  announcement: "M4 10v4h3l6 4V6L7 10zM16.5 9a4 4 0 0 1 0 6",
  keyboard: "M3 6h18v12H3zM7 10h.01M11 10h.01M15 10h.01M7 14h10",
  back: "M15 5l-7 7 7 7",
  refresh: "M20 11a8 8 0 1 0-2.3 6M20 5v6h-6",
  eyeoff: "M3 3l18 18M10.6 6.2A9.8 9.8 0 0 1 12 6c6 0 9.5 6 9.5 6a16 16 0 0 1-3 3.6M6.3 7.6A16 16 0 0 0 2.5 12S6 18 12 18c1.3 0 2.5-.3 3.6-.7",
};
PATHS.article = PATHS.news;

export function Icon({ name, size = 18, fill = false }) {
  return html`<svg width=${size} height=${size} viewBox="0 0 24 24" fill=${fill ? "currentColor" : "none"} stroke="currentColor"
    stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d=${PATHS[name] || PATHS.news} /></svg>`;
}

export function hueStyle(hue) {
  return { "--hue": hue ?? 225 };
}

// Theme: System (default), Paper or Night desk. Stored in localStorage as "pia.theme" and applied as data-theme on <html>.
export function applyTheme(choice) {
  try {
    if (choice === "light" || choice === "dark") document.documentElement.dataset.theme = choice;
    else delete document.documentElement.dataset.theme;
  } catch { /* no DOM */ }
}

// ---------- navigation helpers ----------

export function go(path, query) {
  location.hash = buildRoute(path, query);
}

// Link to an item over the current page (the reader opens over whatever page the owner is on).
export function itemHref(route, id) {
  return buildRoute(route.path, { ...route.query, item: id });
}

// The ids of the list the owner opened an item from: the reader steps through it with J and K.
export const readingContext = { ids: [], label: "" };
export function setReadingContext(ids, label = "") {
  readingContext.ids = ids;
  readingContext.label = label;
}

// The reader and the pages behind it tell each other about state changes, so a list is never stale after the sheet closes.
const itemListeners = new Set();
export function publishItem(id, change) {
  itemListeners.forEach((listener) => listener(id, change));
}
export function useItemSync(patchItems) {
  useEffect(() => {
    itemListeners.add(patchItems);
    return () => itemListeners.delete(patchItems);
  }, [patchItems]);
}

// The top unread headlines of each space, kept for a minute (Today and the Spaces index share them).
const spaceTops = new Map();
export function loadSpaceTops(slugs) {
  return Promise.all(slugs.map((slug) => {
    const cached = spaceTops.get(slug);
    if (!cached || Date.now() - cached.at > 60000) {
      const promise = api.items({ domain: slug, view: "important", state: "unread", limit: 3 }).then((page) => page.items, () => { spaceTops.delete(slug); return []; });
      spaceTops.set(slug, { at: Date.now(), promise });
    }
    return spaceTops.get(slug).promise.then((items) => [slug, items]);
  })).then((pairs) => Object.fromEntries(pairs));
}

// ---------- loading ----------

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

function useAfter(ms) {
  const [on, setOn] = useState(false);
  useEffect(() => {
    const timer = setTimeout(() => setOn(true), ms);
    return () => clearTimeout(timer);
  }, []);
  return on;
}

// Skeleton rows shaped like the content. They appear only after 150 ms, so a fast answer never flashes.
export function Skeleton({ rows = 4 }) {
  const visible = useAfter(150);
  return html`<div class="skel" aria-busy="true" aria-label="Loading">
    ${visible && Array.from({ length: rows }, () => html`<div><i class="s"></i><i class="h"></i><i></i><i class="w"></i></div>`)}
  </div>`;
}

export function Empty({ title, children, hint, action }) {
  const button = action && action.run ? html`<button class="btn" onClick=${action.run}>${action.label}</button>` : action;
  return html`<div class="empty"><h2>${title}</h2><p>${children || hint}</p>${button}</div>`;
}

export function ErrorNote({ message, retry }) {
  return html`<div class="callout warn" role="alert">${message}
    ${retry && html` <button class="btn small" onClick=${retry}>Try again</button>`}</div>`;
}

// ---------- small controls ----------

export function IconButton({ icon, label, pressed, onClick, fill = false, size = 17, className = "" }) {
  return html`<button class=${"icon-btn" + (pressed ? " on" : "") + (className ? " " + className : "")} title=${label} aria-label=${label}
    aria-pressed=${pressed === undefined ? undefined : pressed} onClick=${onClick}><${Icon} name=${icon} size=${size} fill=${fill} /></button>`;
}

export function Chip({ pressed, onClick, children }) {
  return html`<button class="chip" aria-pressed=${pressed} onClick=${onClick}>${children}</button>`;
}

export function ChipGroup({ options, value, onChange, label }) {
  return html`<div class="chips" role="group" aria-label=${label}>
    ${options.map((option) => html`<${Chip} pressed=${option.value === value} onClick=${() => onChange(option.value === value && option.value !== "" ? "" : option.value)}>${option.label}<//>`)}
  </div>`;
}

export function Tabs({ tabs, value, onChange }) {
  return html`<div class="tabs" role="tablist">
    ${tabs.map((tab) => html`<button role="tab" aria-selected=${tab.value === value} onClick=${() => onChange(tab.value)}>
      ${tab.label}${tab.count !== undefined && tab.count !== null && html`<span class="n">${tab.count}</span>`}</button>`)}
  </div>`;
}

export function Segmented({ options, value, onChange, label }) {
  return html`<div class="seg" role="group" aria-label=${label}>
    ${options.map((option) => html`<button aria-pressed=${option.value === value} onClick=${() => onChange(option.value)}>${option.label}</button>`)}
  </div>`;
}

export function Toggle({ checked, onChange, label }) {
  return html`<label class="toggle"><input type="checkbox" checked=${checked} onChange=${(event) => onChange(event.target.checked)} />${label}</label>`;
}

// A button that opens a small popover of choices. Esc and an outside click close it; arrow keys move between choices.
export function Menu({ label, items, value, onSelect, after = [], className = "", align = "right", active = false }) {
  const [open, setOpen] = useState(false);
  const wrap = useRef(null);
  const button = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    const outside = (event) => wrap.current && !wrap.current.contains(event.target) && setOpen(false);
    document.addEventListener("pointerdown", outside);
    wrap.current?.querySelector("[role^=menuitem]")?.focus();
    return () => document.removeEventListener("pointerdown", outside);
  }, [open]);
  const key = (event) => {
    const entries = [...wrap.current.querySelectorAll("[role^=menuitem]")];
    const at = entries.indexOf(document.activeElement);
    if (event.key === "Escape") { event.stopPropagation(); setOpen(false); button.current?.focus(); }
    else if (event.key === "ArrowDown") entries[(at + 1) % entries.length]?.focus();
    else if (event.key === "ArrowUp") entries[(at - 1 + entries.length) % entries.length]?.focus();
    else return;
    event.preventDefault();
  };
  const choose = (entry) => { setOpen(false); button.current?.focus(); onSelect(entry.value); };
  return html`<div class=${"menu-wrap " + className} ref=${wrap} onKeyDown=${key}>
    <button class=${"btn small" + (active ? " on" : "")} ref=${button} aria-haspopup="menu" aria-expanded=${open} onClick=${() => setOpen(!open)}>${label}<${Icon} name="down" size=${14} /></button>
    ${open && html`<div class=${"menu" + (align === "left" ? " left" : "")} role="menu">
      ${items.map((entry) => html`<button role="menuitemradio" aria-checked=${value === entry.value} title=${entry.hint || undefined} onClick=${() => choose(entry)}>
        ${entry.label}${value === entry.value && html`<${Icon} name="check" size=${15} />`}</button>`)}
      ${after.length > 0 && html`<hr />${after.map((entry) => html`<button role="menuitem" onClick=${() => choose(entry)}>${entry.label}</button>`)}`}
    </div>`}
  </div>`;
}

// Replaces window.confirm: the question appears in place, with the consequence stated.
export function InlineConfirm({ message, confirmLabel = "Remove", onConfirm, onCancel }) {
  const ref = useRef(null);
  useEffect(() => { ref.current?.querySelector("button")?.focus(); }, []);
  return html`<div class="confirm" role="alertdialog" aria-label=${message} ref=${ref}
    onKeyDown=${(event) => event.key === "Escape" && (event.stopPropagation(), onCancel())}>
    <span>${message}</span>
    <button class="btn small danger" onClick=${onConfirm}>${confirmLabel}</button>
    <button class="btn small quiet" onClick=${onCancel}>Cancel</button>
  </div>`;
}

// Words in a result that match the query, as <mark> elements. Always text nodes, never HTML.
export function Highlight({ text, q }) {
  const words = (q || "").toLowerCase().split(/\s+/).filter((word) => word.length > 1).map((word) => word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  if (!words.length || !text) return text || "";
  return text.split(new RegExp(`(${words.join("|")})`, "ig")).map((part, index) => (index % 2 ? html`<mark>${part}</mark>` : part));
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
      setList((current) => [...current.slice(-1), entry]);
      setTimeout(() => setList((current) => current.filter((t) => t.id !== entry.id)), entry.undo ? 6000 : entry.error ? 6000 : 3000);
    };
  }, []);
  return html`<div class="toasts" role="status" aria-live="polite">
    ${list.map((entry) => html`<div class=${"toast" + (entry.error ? " error" : "")} key=${entry.id}>
      <span>${entry.message}</span>
      ${entry.undo && html`<button onClick=${() => { entry.undo(); setList((current) => current.filter((t) => t.id !== entry.id)); }}>Undo</button>`}
    </div>`)}
  </div>`;
}

// ---------- item display ----------

export function Meta({ item, showSpace = true, extra }) {
  const signals = item.signals || {};
  return html`<div class="meta">
    <span class="kind"><${Icon} name=${item.kind} size=${14} />${KIND_LABELS[item.kind] || "Item"}</span>
    <span class="src">${item.source}</span>
    ${showSpace && item.domain_short && html`<span>${item.domain_short}</span>`}
    <time datetime=${item.sort_at}>${timeAgo(item.sort_at)}</time>
    ${signals.points ? html`<span class="num">${signals.points.toLocaleString("en-GB")} points</span>` : null}
    ${signals.stars ? html`<span class="num">${signals.stars.toLocaleString("en-GB")} stars</span>` : null}
    ${signals.upvotes ? html`<span class="num">${signals.upvotes} upvotes</span>` : null}
    ${item.spam ? html`<span class="warn" title="The same repository name appears under many owners, a common pattern for copied or spam projects">Likely clone</span>` : null}
    ${extra}
  </div>`;
}

// PIA's reasons for choosing an item, in blue annotation ink.
export function MarginNote({ reasons: lines }) {
  return html`<p class="note">${lines.map((line) => html`<span>${line}</span>`)}</p>`;
}

export function RowActions({ item, act, keep = false }) {
  const original = safeUrl(item.url);
  return html`<div class=${"acts" + (keep ? " keep" : "")}>
    <button class=${"icon-btn" + (item.saved ? " on" : "")} title=${item.saved ? "Remove from Library (S)" : "Save to Library (S)"} aria-label=${item.saved ? "Remove from Library" : "Save to Library"}
      aria-pressed=${item.saved} onClick=${() => act(item, { saved: !item.saved })}><${Icon} name="bookmark" fill=${item.saved} size=${17} /></button>
    <button class="icon-btn" title=${item.read ? "Mark unread (E)" : "Mark read (E)"} aria-label=${item.read ? "Mark unread" : "Mark read"}
      onClick=${() => act(item, { read: !item.read })}><${Icon} name="check" size=${17} /></button>
    ${item.dismissed
      ? html`<button class="icon-btn" title="Restore (X)" aria-label="Restore" onClick=${() => act(item, { dismissed: false })}><${Icon} name="refresh" size=${17} /></button>`
      : html`<button class="icon-btn" title="Dismiss (X)" aria-label="Dismiss" onClick=${() => act(item, { dismissed: true })}><${Icon} name="x" size=${17} /></button>`}
    ${original && html`<a class="icon-btn" href=${original} target="_blank" rel="noopener noreferrer" title="Open the original (V)" aria-label="Open the original"><${Icon} name="external" size=${17} /></a>`}
  </div>`;
}

// An Important row. `leading` rows are larger and carry PIA's margin note.
export function Row({ item, href, act, cursor = false, leading = false, showSpace = true, opp, highlight, topics = true }) {
  const classes = ["row", leading ? "leading" : (item.score ?? 1) < 0.56 ? "minor" : "", item.read ? "read" : "", cursor ? "cursor" : "", item.dismissed ? "dismissed" : ""].join(" ");
  const days = opp && opp.days_left;
  const due = opp && days !== null && days !== undefined && days >= 0 ? html`<span class=${"due" + (days <= 2 ? " hot" : "")}>${dueText(days)}</span>` : null;
  const shown = (item.topics || []).slice(0, 4);
  return html`<li class=${classes} data-id=${item.id}>
    <div class="body">
      <${Meta} item=${item} showSpace=${showSpace} extra=${due} />
      <h3><a href=${href}>${highlight ? html`<${Highlight} text=${item.title} q=${highlight} />` : item.title}</a></h3>
      ${item.summary && html`<p class="sum">${highlight ? html`<${Highlight} text=${item.summary} q=${highlight} />` : item.summary}</p>`}
      ${!leading && topics && shown.length > 0 && html`<div class="topics">${shown.map((topic) => html`<a href=${buildRoute(["topic", String(topic.id)])} class=${topic.level >= 2 ? "pri" : ""}>${topic.name}</a>`)}</div>`}
    </div>
    ${leading && html`<${MarginNote} reasons=${reasons(item, opp)} />`}
    <${RowActions} item=${item} act=${act} />
  </li>`;
}

// A Latest line: time, kind glyph, headline, kind and source.
export function Line({ item, href, act, cursor = false, summaries = false }) {
  const clock = new Date(item.sort_at).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  const points = item.signals && item.signals.points;
  return html`<li class=${"line" + (item.read ? " read" : "") + (cursor ? " cursor" : "")} data-id=${item.id}>
    <time datetime=${item.sort_at}>${clock}</time>
    <span class="k" title=${KIND_LABELS[item.kind]}><${Icon} name=${item.kind} size=${15} /></span>
    <div class="t"><a href=${href}>${item.title}</a>${(item.score ?? 0) >= 0.72 ? html`<span class="sig" title="Scores in the top tier for you">Significant</span>` : null}
      <small>${KIND_LABELS[item.kind]}  ·  ${item.source}${points ? `  ·  ${points.toLocaleString("en-GB")} points` : ""}</small>
      ${summaries && item.summary && html`<p>${item.summary}</p>`}</div>
    <${RowActions} item=${item} act=${act} />
  </li>`;
}

// Apply a state change to one item everywhere: optimistically in the list, then on the server.
export function useItemActions(patchItems, refreshBoot) {
  return useCallback(async (item, change) => {
    const before = { read: item.read, saved: item.saved, dismissed: item.dismissed };
    const after = { ...change };
    if (change.saved || change.dismissed) after.read = true;
    patchItems(item.id, after);
    const undo = (back) => () => api.setState(item.id, back).then(() => { patchItems(item.id, back); refreshBoot(); }, (error) => toast(error.message, { error: true }));
    try {
      await api.setState(item.id, change);
      refreshBoot();
      if (change.dismissed) toast("Dismissed. It won't appear on Today again.", { undo: undo({ dismissed: false }) });
      else if (change.dismissed === false) toast("Restored");
      else if (change.saved === true) toast("Saved to Library");
      else if (change.saved === false) toast("Removed from Library", { undo: undo({ saved: true }) });
    } catch (error) {
      patchItems(item.id, before);
      toast(error.message, { error: true });
    }
  }, [patchItems, refreshBoot]);
}

// Keyboard triage over a visible list: j and k move, o or Enter opens, s saves, e marks read, x dismisses, v opens the original.
export function useTriage(items, act, href, enabled = true) {
  const [cursor, setCursor] = useState(-1);
  const state = useRef({});
  state.current = { items, cursor, act, href };
  useEffect(() => {
    if (!enabled) return undefined;
    const onKey = (event) => {
      if (document.body.dataset.overlay || event.metaKey || event.ctrlKey || event.altKey || /INPUT|TEXTAREA|SELECT/.test(event.target.tagName)) return;
      const { items: rows, cursor: at, act: apply, href: link } = state.current;
      const current = rows[at];
      if (event.key === "j") setCursor(Math.min(rows.length - 1, at + 1));
      else if (event.key === "k") setCursor(Math.max(0, at - 1));
      else if (!current) return;
      else if (event.key === "o" || event.key === "Enter") {
        document.querySelector(".cursor a")?.focus();  // so closing the reader returns focus to this row
        location.hash = link(current);
      } else if (event.key === "s") apply(current, { saved: !current.saved });
      else if (event.key === "e") apply(current, { read: !current.read });
      else if (event.key === "x") apply(current, { dismissed: !current.dismissed });
      else if (event.key === "v") { const url = safeUrl(current.url); if (url) window.open(url, "_blank", "noopener,noreferrer"); }
      else return;
      event.preventDefault();
    };
    addEventListener("keydown", onKey);
    return () => removeEventListener("keydown", onKey);
  }, [enabled]);
  useEffect(() => { if (cursor >= 0) document.querySelector(".cursor")?.scrollIntoView({ block: "nearest" }); }, [cursor]);
  return [cursor, setCursor];
}

// ---------- overlays ----------

// Overlays (reader, drawer, palette, shortcuts) share one rule set: the top one owns Esc, Tab stays inside it,
// and focus returns to whatever had it before the overlay opened.
const overlays = [];
export function useOverlay(name, onClose, container) {
  useEffect(() => {
    const opener = document.activeElement;
    overlays.push(name);
    document.body.dataset.overlay = name;
    const onKey = (event) => {
      if (overlays[overlays.length - 1] !== name) return;
      if (event.key === "Escape") {
        if (/INPUT|TEXTAREA|SELECT/.test(event.target.tagName) && name !== "palette") { event.target.blur(); return; }
        event.preventDefault();
        onClose();
      } else if (event.key === "Tab" && container.current) {
        const focusable = [...container.current.querySelectorAll("a[href], button:not([disabled]), input, select, textarea, summary, [tabindex]:not([tabindex='-1'])")]
          .filter((element) => element.offsetParent !== null);
        if (!focusable.length) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (!container.current.contains(document.activeElement)) { first.focus(); event.preventDefault(); }
        else if (event.shiftKey && document.activeElement === first) { last.focus(); event.preventDefault(); }
        else if (!event.shiftKey && document.activeElement === last) { first.focus(); event.preventDefault(); }
      }
    };
    addEventListener("keydown", onKey);
    return () => {
      removeEventListener("keydown", onKey);
      overlays.splice(overlays.lastIndexOf(name), 1);
      if (overlays.length) document.body.dataset.overlay = overlays[overlays.length - 1];
      else delete document.body.dataset.overlay;
      if (opener && document.contains(opener) && opener.focus) opener.focus();
    };
  }, []);
}

const PAGES = [
  ["Today", [], "today"], ["Spaces", ["spaces"], "spaces"], ["Opportunities", ["opportunities"], "flag"], ["Library", ["library"], "library"],
  ["Interests", ["interests"], "sliders"], ["Sources", ["sources"], "rss"], ["Notification settings", ["settings"], "bell"],
  ["System", ["settings"], "gear", { tab: "system" }],
];

// Search and go: pages, spaces and the best matching items. Enter on the first row runs the full search.
export function Palette({ onClose, domains }) {
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const [found, setFound] = useState({ items: [], busy: false });
  const input = useRef(null);
  const box = useRef(null);
  useOverlay("palette", onClose, box);
  useEffect(() => { input.current?.focus(); }, []);
  const ql = q.toLowerCase().trim();

  useEffect(() => {
    if (ql.length < 2) { setFound({ items: [], busy: false }); return undefined; }
    let live = true;
    setFound((now) => ({ ...now, busy: true }));
    const timer = setTimeout(() => {
      api.items({ q: ql, limit: 6, view: "relevance" }).then(
        (page) => live && setFound({ items: page.items, busy: false }),
        () => live && setFound({ items: [], busy: false }),
      );
    }, 200);
    return () => { live = false; clearTimeout(timer); };
  }, [ql]);

  const options = [];
  if (ql) options.push({ group: "Search", label: html`Search everything for “${q.trim()}”`, icon: "search", run: () => go(["search"], { q: q.trim() }) });
  for (const [label, path, icon, query] of PAGES) if (!ql || label.toLowerCase().includes(ql)) options.push({ group: "Go to", label, icon, run: () => go(path, query) });
  for (const domain of domains) {
    if (ql && (domain.name.toLowerCase().includes(ql) || domain.short.toLowerCase().includes(ql))) options.push({ group: "Spaces", label: domain.name, icon: "spaces", run: () => go(["space", domain.slug]) });
  }
  for (const item of found.items) {
    options.push({
      group: "Items", label: html`<${Highlight} text=${item.title} q=${q} />`, sub: `${KIND_LABELS[item.kind] || "Item"}, ${item.source}`, serif: true, icon: item.kind,
      run: () => { const route = parseRoute(location.hash); location.hash = itemHref(route, item.id); },
    });
  }
  const shown = ql ? options : options.slice(0, 9);
  useEffect(() => setSel(0), [q]);
  const run = (option) => { onClose(); option.run(); };
  const key = (event) => {
    if (event.key === "ArrowDown") setSel(Math.min(shown.length - 1, sel + 1));
    else if (event.key === "ArrowUp") setSel(Math.max(0, sel - 1));
    else if (event.key === "Enter" && shown[sel]) run(shown[sel]);
    else return;
    event.preventDefault();
  };
  let last = null;
  return html`<div class="palette-wrap" onClick=${(event) => event.target === event.currentTarget && onClose()}>
    <div class="palette" role="dialog" aria-modal="true" aria-label="Search and go" ref=${box}>
      <div class="in"><${Icon} name="search" />
        <input ref=${input} value=${q} onInput=${(event) => setQ(event.target.value)} onKeyDown=${key} placeholder="Search items, spaces and pages" aria-label="Search"
          role="combobox" aria-expanded="true" aria-controls="pal-list" aria-activedescendant=${shown[sel] ? "pal-" + sel : undefined} autocomplete="off" /><kbd>Esc</kbd></div>
      <div class="results" id="pal-list" role="listbox">
        ${shown.map((option, index) => {
          const head = option.group !== last ? html`<div class="grp" role="presentation">${option.group}</div>` : null;
          last = option.group;
          return html`${head}<button class="opt" id=${"pal-" + index} role="option" aria-selected=${index === sel} onMouseEnter=${() => setSel(index)} onClick=${() => run(option)}>
            <${Icon} name=${option.icon} size=${16} /><span><span class=${"t block" + (option.serif ? " serif" : "")}>${option.label}</span>${option.sub && html`<span class="s">${option.sub}</span>`}</span></button>`;
        })}
        ${found.busy && html`<p class="grp pad">Searching…</p>`}
        ${ql && !found.busy && shown.length <= 1 && html`<p class="grp pad">No pages or spaces match. Press Enter to search every item.</p>`}
      </div>
      <div class="foot"><span><kbd>↑</kbd> <kbd>↓</kbd> to move</span><span><kbd>Enter</kbd> to open</span><span><kbd>/</kbd> or <kbd>Ctrl</kbd> <kbd>K</kbd> opens this anywhere</span></div>
    </div>
  </div>`;
}

const LEVEL_NAMES = { critical: "Critical", important: "Important", interesting: "Interesting" };

export function NotificationsDrawer({ onClose, refreshBoot }) {
  const { data, error, loading, reload } = useLoad(() => api.notifications(), []);
  const box = useRef(null);
  useOverlay("drawer", onClose, box);
  useEffect(() => { box.current?.querySelector("button")?.focus(); }, []);
  const list = data ? data.notifications : [];
  const unseen = data ? data.unseen : 0;
  const today = new Date().toLocaleDateString("en-CA");
  const groups = [["Today", list.filter((n) => new Date(n.created_at).toLocaleDateString("en-CA") === today)], ["Earlier", list.filter((n) => new Date(n.created_at).toLocaleDateString("en-CA") !== today)]].filter(([, l]) => l.length);
  const markAll = () => api.markSeen(null).then(() => { reload(); refreshBoot(); }, (failure) => toast(failure.message, { error: true }));
  const openItem = (n) => {
    if (!n.seen_at) api.markSeen([n.id]).then(refreshBoot, () => {});
    onClose();
    location.hash = buildRoute(parseRoute(location.hash).path, { item: n.item_id });
  };
  return html`<div>
    <div class="scrim scrim-drawer" onClick=${onClose}></div>
    <aside class="drawer" role="dialog" aria-modal="true" aria-label="Notifications" ref=${box}>
      <div class="drawer-head"><h2>Notifications</h2>
        ${unseen > 0 && html`<button class="btn small quiet" onClick=${markAll}>Mark all seen</button>`}
        <${IconButton} icon="x" label="Close (Esc)" onClick=${onClose} /></div>
      <div class="list">
        ${loading && !data && html`<${Skeleton} rows=${4} />`}
        ${error && html`<div class="pad"><${ErrorNote} message=${error} retry=${reload} /></div>`}
        ${data && list.length === 0 && html`<div class="pad"><${Empty} title="No notifications yet">When something clears your thresholds it appears here, and on your desktop if the Windows companion is set up.<//></div>`}
        ${groups.map(([name, entries]) => html`<div class="grp-head">${name}</div>${entries.map((n) => html`<button class=${"notif " + (n.seen_at ? "seen" : "unseen")} key=${n.id} onClick=${() => openItem(n)}>
          <span class="lvl"></span><span class="t">${n.title}</span>
          <span class="r"><span class=${"lvl-tag " + n.level}>${LEVEL_NAMES[n.level] || n.level}</span>  ·  ${n.reason}  ·  ${timeAgo(n.created_at)}</span></button>`)}`)}
      </div>
      <div class="drawer-foot"><span class="meta">${unseen} unseen</span><a class="textlink" href="#/settings" onClick=${onClose}>Notification settings</a></div>
    </aside>
  </div>`;
}

export const SHORTCUTS = [
  ["/  or  Ctrl K", "Search and go"], ["G then T, S, O, L, I", "Today, Spaces, Opportunities, Library, Interests"], ["J  /  K", "Next and previous item (also inside the reader)"],
  ["O  or  Enter", "Open the selected item"], ["S", "Save or unsave"], ["E", "Mark read or unread"], ["X", "Dismiss"], ["V", "Open the original in a new tab"],
  ["F", "Focus mode in the reader"], ["Esc", "Close the reader, drawer or search"], ["?", "Show these shortcuts"],
];

export function ShortcutTable() {
  return html`<div class="table-wrap"><table class="grid"><tbody>${SHORTCUTS.map(([keys, what]) => html`<tr><td class="keys"><kbd>${keys}</kbd></td><td>${what}</td></tr>`)}</tbody></table></div>`;
}

export function Shortcuts({ onClose }) {
  const box = useRef(null);
  useOverlay("shortcuts", onClose, box);
  useEffect(() => { box.current?.focus(); }, []);
  return html`<div class="palette-wrap" onClick=${(event) => event.target === event.currentTarget && onClose()}>
    <div class="palette shortcuts" role="dialog" aria-modal="true" aria-label="Keyboard shortcuts" tabindex="-1" ref=${box}>
      <h2>Keyboard shortcuts</h2><${ShortcutTable} /></div>
  </div>`;
}
