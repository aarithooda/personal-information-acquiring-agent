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

export const KIND_LABELS = {
  paper: "Research paper", repo: "Repository", news: "News", article: "Article", post: "Discussion",
  release: "Release", opportunity: "Opportunity", announcement: "Announcement",
};
// Plural names for filter chips and facets.
export const KIND_SHORT = {
  paper: "Papers", repo: "Code", news: "News", article: "Articles", post: "Discussions",
  release: "Releases", opportunity: "Opportunities", announcement: "Announcements",
};

// A space's weight in words (the rail, the Spaces index and the weight menu share these).
export function weightLabel(weight) {
  if (weight === 0) return "Hidden";
  if (weight < 0.8) return "Less";
  if (weight >= 1.3) return "Priority";
  return "Normal";
}

// The spaces the owner marked Priority, strongest first: the rail shows at most six.
export function priorityDomains(domains) {
  return domains.filter((domain) => domain.weight >= 1.3).sort((a, b) => b.weight - a.weight).slice(0, 6);
}

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
  { value: "interested", label: "Interested" }, { value: "planning", label: "Planning" }, { value: "applied", label: "Applied" },
  { value: "accepted", label: "Accepted" }, { value: "rejected", label: "Not selected" },
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

// ---------- the redesign's presentation rules (pure; unit-tested in tests/test_platform_misc.py) ----------

const group = (n) => Number(n || 0).toLocaleString("en-GB");

export function andList(names) {
  return names.length <= 1 ? names.join("") : names.slice(0, -1).join(", ") + " and " + names[names.length - 1];
}

// Why PIA put this item in front of the owner: at most two clauses, composed from fields the API already returns.
export function reasons(item, opportunity) {  // eslint-disable-line no-unused-vars
  const out = [];
  const topics = item.topics || [];
  const priority = topics.filter((topic) => topic.level === 3).map((topic) => topic.name);
  const following = topics.filter((topic) => topic.level === 2).map((topic) => topic.name);
  if (priority.length) out.push(`Your priority: ${andList(priority.slice(0, 2))}.`);
  else if (following.length) out.push(`You follow ${andList(following.slice(0, 2))}.`);
  const signals = item.signals || {};
  if (signals.points >= 250) out.push(`${group(signals.points)} points and ${group(signals.comments)} comments on Hacker News.`);
  else if (signals.stars) out.push(`${group(signals.stars)} stars on GitHub.`);
  else if (signals.upvotes >= 30) out.push(`${signals.upvotes} upvotes on Hugging Face Papers.`);
  if (item.resurfaced && out.length < 2) out.push("Newer work builds on this.");
  if (item.importance >= 0.68 && out.length < 2) out.push("Assessed as significant in its field.");
  if (!out.length) out.push(`Ranks near the top of ${item.domain_short || "its space"}.`);
  return out.slice(0, 2);
}

// The daily briefing: a presentation rule over /api/home `featured` (already ranked, capped per space, one per story).
export function composeBriefing(featured) {
  const out = [];
  let repos = 0;
  for (const item of featured || []) {
    if (!item || item.spam || item.dismissed) continue;
    if (item.kind === "repo") {
      if (repos >= 1 || !((item.signals && item.signals.stars) >= 1000)) continue;
      repos += 1;
    }
    out.push(item);
    if (out.length === 6) break;
  }
  const lead = out.findIndex((item) => item.kind !== "repo" && item.kind !== "post");
  if (lead > 0) out.unshift(out.splice(lead, 1)[0]);
  return out;
}

// Two postings are the same opportunity when their names start alike and they share a deadline.
export function oppKey(opportunity) {
  const name = (opportunity.programme || opportunity.title || "").toLowerCase().replace(/[^a-z0-9]+/g, "").slice(0, 12);
  return name + "|" + (opportunity.deadline || "");
}

export function foldOpportunities(list) {
  const folded = new Map();
  for (const opportunity of list) {
    const key = oppKey(opportunity);
    if (folded.has(key)) folded.get(key).dup += 1;
    else folded.set(key, { ...opportunity, dup: 0 });
  }
  return [...folded.values()];
}

export function urgencyGroup(daysLeft) {
  if (daysLeft === null || daysLeft === undefined) return "undated";
  if (daysLeft <= 7) return "week";
  if (daysLeft <= 31) return "month";
  return "later";
}

export function dueText(days) {
  if (days === null || days === undefined) return "No deadline given";
  if (days < 0) return days === -1 ? "Closed yesterday" : `Closed ${-days} days ago`;
  if (days === 0) return "Closes today";
  if (days === 1) return "Closes tomorrow";
  return `Closes in ${days} days`;
}

// "hot" up to two days out, "soon" up to two weeks, "none" when undated.
export function dueTone(days) {
  if (days === null || days === undefined) return "none";
  if (days < 0) return "";
  if (days <= 2) return "hot";
  if (days <= 14) return "soon";
  return "";
}

const localDay = (date) => new Date(date).toLocaleDateString("en-CA");

export function dayLabel(iso, now = new Date()) {
  const day = localDay(iso);
  if (day === localDay(now)) return "Today";
  if (day === localDay(new Date(new Date(now).getTime() - 86400000))) return "Yesterday";
  return new Date(iso).toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" });
}

export function groupByDay(items, now = new Date()) {
  const groups = [];
  for (const item of items) {
    const key = localDay(item.sort_at);
    const last = groups[groups.length - 1];
    if (last && last.key === key) last.items.push(item);
    else groups.push({ key, label: dayLabel(item.sort_at, now), items: [item] });
  }
  return groups;
}

// The masthead sentence as parts, so the page can set the numbers in bold and the deadline as a button.
// `urgent` is the folded list of open opportunities; only those closing within three days are mentioned.
export function standfirst(newCount, chosen, urgent) {
  const soon = (urgent || []).filter((o) => o.days_left !== null && o.days_left !== undefined && o.days_left >= 0 && o.days_left <= 3)
    .sort((a, b) => a.days_left - b.days_left);
  let link = null;
  if (soon.length) {
    const more = soon.length === 2 ? ", and one more deadline falls in the next three days"
      : soon.length > 2 ? `, and ${soon.length - 1} more deadlines fall in the next three days` : "";
    link = { id: soon[0].item_id, label: soon[0].programme || soon[0].title, tail: dueText(soon[0].days_left).toLowerCase() + more + "." };
  }
  return { count: group(newCount), noun: newCount === 1 ? "new item" : "new items", chosen, link };
}
