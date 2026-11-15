// Pure helpers: no DOM, no network. Unit-tested with Node (tests/test_platform_web_logic.py).

export function parseRoute(hash) {
  // "#/space/ai?view=important&item=12" -> { path: ["space", "ai"], query: { view: "important", item: "12" } }
  const raw = (hash || "").replace(/^#\/?/, "");
  const [pathPart, queryPart = ""] = raw.split("?");
  const path = pathPart.split("/").filter(Boolean).map(decodeURIComponent);
  const query = {};
  for (const pair of queryPart.split("&")) {
    if (!pair) continue;
    const [key, value = ""] = pair.split("=");
    query[decodeURIComponent(key)] = decodeURIComponent(value.replace(/\+/g, " "));
  }
  return { path, query };
}

export function buildRoute(path, query = {}) {
  const parts = Object.entries(query)
    .filter(([, value]) => value !== undefined && value !== null && value !== "" && value !== false)
    .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(value)}`);
  return "#/" + path.map(encodeURIComponent).join("/") + (parts.length ? "?" + parts.join("&") : "");
}

// Only http(s) links are ever made clickable: item URLs come from the web and could be "javascript:".
export function safeUrl(url) {
  return typeof url === "string" && /^https?:\/\//i.test(url) ? url : null;
}

export function hostOf(url) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
}

export function timeAgo(iso, now = new Date()) {
  if (!iso) return "";
  const then = new Date(iso);
  const seconds = Math.max(0, (now - then) / 1000);
  if (seconds < 90) return "just now";
  const minutes = seconds / 60;
  if (minutes < 60) return `${Math.round(minutes)} min ago`;
  const hours = minutes / 60;
  if (hours < 24) return `${Math.round(hours)} h ago`;
  const days = hours / 24;
  if (days < 7) return `${Math.round(days)} d ago`;
  const sameYear = then.getFullYear() === now.getFullYear();
  return then.toLocaleDateString("en-GB", { day: "numeric", month: "short", ...(sameYear ? {} : { year: "numeric" }) });
}

export function fullDate(iso) {
  if (!iso) return "";
  return new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" });
}

export function dateTime(iso) {
  if (!iso) return "";
  return new Date(iso).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

// "closes today", "2 days left", "closed 3 days ago"; null when there is no deadline.
export function deadlineText(daysLeft) {
  if (daysLeft === null || daysLeft === undefined) return null;
  if (daysLeft < 0) return daysLeft === -1 ? "closed yesterday" : `closed ${-daysLeft} days ago`;
  if (daysLeft === 0) return "closes today";
  if (daysLeft === 1) return "closes tomorrow";
  return `${daysLeft} days left`;
}

// Importance in three typographic tiers; the thresholds mirror the notification defaults.
export function tier(item) {
  const score = item.score ?? 0;
  if (score >= 0.72) return "lead";
  if (score >= 0.56) return "standard";
  return "minor";
}

// 0-1 -> how many of four signal marks are lit.
export function marks(value) {
  return Math.max(0, Math.min(4, Math.round((value ?? 0) * 4)));
}

export const KIND_LABELS = {
  paper: "Paper", release: "Release", repo: "Repository", article: "Article", news: "News", post: "Discussion",
  opportunity: "Opportunity", announcement: "Announcement",
};

export const LEVELS = [
  { value: 3, label: "Priority", hint: "One of the handful that matter most" },
  { value: 2, label: "Highly interesting", hint: "" },
  { value: 1, label: "Somewhat", hint: "" },
  { value: -1, label: "Not interesting", hint: "" },
  { value: -2, label: "Muted", hint: "Keep it off Home and out of notifications" },
];

export function levelLabel(level) {
  const found = LEVELS.find((entry) => entry.value === level);
  return found ? found.label : "Not rated";
}

export const OPPORTUNITY_TYPES = {
  internship: "Internship", research_programme: "Research programme", fellowship: "Fellowship", summer_school: "Summer school",
  hackathon: "Hackathon", competition: "Competition", developer_programme: "Developer programme", scholarship: "Scholarship",
  job: "Job", announcement: "Announcement", other: "Opportunity",
};

export const TRACK_STATUSES = [
  { value: "interested", label: "Interested" }, { value: "planning", label: "Planning to apply" }, { value: "applied", label: "Applied" },
  { value: "accepted", label: "Accepted" }, { value: "rejected", label: "Rejected" },
];

export function plural(count, one, many = one + "s") {
  return `${count.toLocaleString("en-GB")} ${count === 1 ? one : many}`;
}

export function bytes(n) {
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export function usd(n) {
  if (!n) return "$0";
  return n < 0.01 ? `$${n.toFixed(4)}` : `$${n.toFixed(2)}`;
}

// What an empty feed should say. An empty state is a claim about the data, so it must be true:
// "nothing here" and "your filters hide everything" are different situations.
export function emptyMessage({ total, filtered, state, q }) {
  if (q) return { title: `Nothing matches “${q}”`, hint: "Try fewer or different words. Search covers titles, source text and summaries." };
  if (filtered) return { title: "Nothing matches these filters", hint: "Clear the filters to see everything in this space.", clear: true };
  if (state === "unread" && total > 0) return { title: "You are caught up", hint: "Everything here has been read. Switch to All to look back." };
  if (state === "saved") return { title: "Nothing saved yet", hint: "Save an item to keep it here, across sessions and devices." };
  return { title: "Nothing here yet", hint: "PIA has not acquired anything for this space. Check Sources to see what is being monitored." };
}
