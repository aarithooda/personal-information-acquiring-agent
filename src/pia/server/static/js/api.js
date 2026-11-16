// The only module that talks to the server.

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

let onUnauthorised = () => {};
export function setUnauthorisedHandler(handler) {
  onUnauthorised = handler;
}

// The shell shows a banner while the server cannot be reached (a fetch that never gets an answer).
let onReachability = () => {};
export function setReachabilityHandler(handler) {
  onReachability = handler;
}

async function request(method, path, body) {
  let response;
  try {
    response = await fetch(path, {
      method,
      credentials: "same-origin",
      headers: body === undefined ? {} : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    onReachability(false);
    throw new ApiError(0, "PIA is not reachable. Check that it is running, then try again.");
  }
  onReachability(true);
  if (response.status === 401 && path !== "/api/login") {
    onUnauthorised();
    throw new ApiError(401, "Sign in to continue.");
  }
  let data = null;
  try {
    data = await response.json();
  } catch {
    /* no body */
  }
  if (!response.ok) {
    const detail = data && data.detail;
    const message = typeof detail === "string" ? detail : Array.isArray(detail) ? "That value was not accepted." : `Request failed (${response.status}).`;
    throw new ApiError(response.status, message);
  }
  return data;
}

function query(params) {
  const parts = Object.entries(params || {})
    .filter(([, value]) => value !== undefined && value !== null && value !== "" && value !== false)
    .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(value)}`);
  return parts.length ? "?" + parts.join("&") : "";
}

export const api = {
  session: () => request("GET", "/api/session"),
  login: (password) => request("POST", "/api/login", { password }),
  logout: () => request("POST", "/api/logout"),
  bootstrap: () => request("GET", "/api/bootstrap"),
  home: () => request("GET", "/api/home"),
  items: (params) => request("GET", "/api/items" + query(params)),
  item: (id) => request("GET", `/api/items/${id}`),
  open: (id) => request("POST", `/api/items/${id}/open`),
  setState: (id, change) => request("POST", `/api/items/${id}/state`, change),
  markRead: (ids) => request("POST", "/api/items/read", { ids }),
  feedback: (id, kind, reason = "") => request("POST", `/api/items/${id}/feedback`, { kind, reason }),
  undoFeedback: (id, kind) => request("DELETE", `/api/items/${id}/feedback/${kind}`),
  brief: (id) => request("POST", `/api/items/${id}/brief`),
  reader: (id) => request("POST", `/api/items/${id}/reader`),
  profile: () => request("GET", "/api/profile"),
  rateTopic: (id, level) => request("PUT", `/api/topics/${id}`, { level }),
  addTopic: (name, domain, level = 2) => request("POST", "/api/topics", { name, domain, level }),
  resetTopic: (id) => request("POST", `/api/topics/${id}/reset`),
  topic: (id) => request("GET", `/api/topics/${id}`),
  weighDomain: (slug, weight) => request("PUT", `/api/domains/${slug}`, { weight }),
  dismissOnboarding: () => request("POST", "/api/onboarding/dismiss"),
  sources: () => request("GET", "/api/sources"),
  inspectSource: (url) => request("POST", "/api/sources/inspect", { url }),
  addSource: (payload) => request("POST", "/api/sources", payload),
  patchSource: (id, change) => request("PATCH", `/api/sources/${id}`, change),
  deleteSource: (id) => request("DELETE", `/api/sources/${id}`),
  discover: () => request("POST", "/api/sources/discover"),
  addOrganisation: (payload) => request("POST", "/api/organisations", payload),
  deleteOrganisation: (id) => request("DELETE", `/api/organisations/${id}`),
  opportunities: (params) => request("GET", "/api/opportunities" + query(params)),
  track: (id, status, notes) => request("PATCH", `/api/opportunities/${id}`, notes === undefined ? { status } : { status, notes }),
  notifications: () => request("GET", "/api/notifications"),
  markSeen: (ids) => request("POST", "/api/notifications/seen", { ids: ids || null }),
  saveNotificationSettings: (settings) => request("PUT", "/api/notifications/settings", settings),
  addDevice: (name) => request("POST", "/api/devices", { name }),
  removeDevice: (id) => request("DELETE", `/api/devices/${id}`),
  system: () => request("GET", "/api/system"),
  runNow: (force = false) => request("POST", "/api/system/run", { force }),
};
