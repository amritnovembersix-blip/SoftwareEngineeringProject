/**
 * SASS - every call the frontend makes to the backend.
 *
 * The frontend talks only to the FastAPI backend (Backend/, port 8001), never to the
 * agentic service, so the service token stays out of the browser.
 *
 * Endpoints marked "PENDING BACKEND" are not in the backend yet (see docs/BACKEND_GAPS.md).
 * Their pages show a "waiting on the backend" notice until Amrit adds them.
 */

export const API_URL = (
  import.meta.env.VITE_API_URL || "http://127.0.0.1:8001"
).replace(/\/$/, "");

// ------------------------------------
// Login session (token + user) in browser storage
// ------------------------------------

const TOKEN_KEY = "sassToken";
const USER_KEY = "sassUser";

function safeStorage(kind) {
  try {
    return kind === "local" ? window.localStorage : window.sessionStorage;
  } catch {
    return null;
  }
}

export function getToken() {
  return (
    safeStorage("session")?.getItem(TOKEN_KEY) ||
    safeStorage("local")?.getItem(TOKEN_KEY) ||
    null
  );
}

export function getUser() {
  const raw =
    safeStorage("session")?.getItem(USER_KEY) ||
    safeStorage("local")?.getItem(USER_KEY);
  try {
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

/** session = { token, name, role, email, id, teacher_id } (what api.login returns). */
export function saveSession(session, remember = false) {
  const { token, ...user } = session || {};
  if (!token) {
    throw new Error("Authentication token was not returned by the backend.");
  }
  clearSession();
  const storage = safeStorage(remember ? "local" : "session");
  storage?.setItem(TOKEN_KEY, token);
  storage?.setItem(USER_KEY, JSON.stringify(user));
}

/** Update the stored user (after GET /auth/me) without touching the token. */
export function updateStoredUser(user) {
  for (const kind of ["session", "local"]) {
    const s = safeStorage(kind);
    if (s?.getItem(TOKEN_KEY)) s.setItem(USER_KEY, JSON.stringify(user));
  }
}

export function clearSession() {
  for (const kind of ["session", "local"]) {
    safeStorage(kind)?.removeItem(TOKEN_KEY);
    safeStorage(kind)?.removeItem(USER_KEY);
  }
}

/** The backend's user object -> what the app stores. */
export function toSessionUser(u) {
  return {
    id: u?.id,
    email: u?.email,
    name: u?.display_name,
    role: (u?.role || "").toLowerCase(),
    teacher_id: u?.teacher_id ?? null,
  };
}

// ------------------------------------
// Request helper
// ------------------------------------

/** An error from the backend that keeps its HTTP status (404, 409, 422 ...). */
export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

function detailText(detail) {
  if (!detail) return null;
  if (typeof detail === "string") return detail;
  // FastAPI validation errors: [{loc, msg}, ...]
  if (Array.isArray(detail)) return detail.map((d) => d.msg || String(d)).join("; ");
  if (typeof detail === "object") return detail.message || JSON.stringify(detail);
  return String(detail);
}

async function request(method, path, { body, auth = true } = {}) {
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const token = getToken();
  if (auth && token) headers.Authorization = `Bearer ${token}`;

  let response;
  try {
    response = await fetch(`${API_URL}${path}`, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError(`Can't reach the backend at ${API_URL}. Is it running?`, 0);
  }

  // Expired or invalid login: back to the login page.
  if (response.status === 401 && auth) {
    clearSession();
    if (window.location.pathname !== "/login") window.location.assign("/login");
    throw new ApiError("Your session has ended. Please sign in again.", 401);
  }

  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const fallback =
      response.status === 429
        ? "The AI service is busy right now; try again in a minute."
        : `The backend returned ${response.status}.`;
    throw new ApiError(detailText(data && data.detail) || fallback, response.status);
  }
  return data;
}

const enc = encodeURIComponent;

function qs(params) {
  const parts = Object.entries(params || {})
    .filter(([, v]) => v !== undefined && v !== null && v !== "")
    .map(([k, v]) => `${enc(k)}=${enc(v)}`);
  return parts.length ? `?${parts.join("&")}` : "";
}

// ------------------------------------
// Endpoints (Amrit's backend, branch backend/cleanup)
// ------------------------------------

export const api = {
  // Login
  login: async (email, password) => {
    const data = await request("POST", "/auth/login", {
      body: { email, password },
      auth: false,
    });
    return { token: data.access_token, ...toSessionUser(data.user) };
  },
  me: () => request("GET", "/auth/me"),

  // Home
  dashboard: () => request("GET", "/dashboard"),
  notifications: () => request("GET", "/notifications"),

  // Classrooms
  classrooms: () => request("GET", "/classrooms"),
  students: (classroomId) => request("GET", `/classrooms/${enc(classroomId)}/students`),
  conceptWeeks: (classroomId, strand) =>
    request("GET", `/classrooms/${enc(classroomId)}/concept-weeks${qs({ strand })}`),
  reteachGroups: (classroomId) => request("GET", `/classrooms/${enc(classroomId)}/reteach-groups`),
  comparison: (a, b) => request("GET", `/comparison${qs({ classrooms: `${a},${b}` })}`),

  // Peer pairs (FR7)
  peerPairs: (classroomId, status) =>
    request("GET", `/classrooms/${enc(classroomId)}/peer-pairs${qs({ status })}`),
  decidePeerPair: (pairId, status) =>
    request("PATCH", `/peer-pairs/${enc(pairId)}`, { body: { status } }),

  // One student
  concepts: (studentId, classroomId) =>
    request("GET", `/students/${enc(studentId)}/concepts${qs({ classroom_id: classroomId })}`),
  conceptHistory: (studentId, { classroomId, conceptCode } = {}) =>
    request(
      "GET",
      `/students/${enc(studentId)}/concept-history${qs({ classroom_id: classroomId, concept_code: conceptCode })}`
    ),
  findings: (studentId, assignmentId) =>
    request("GET", `/students/${enc(studentId)}/findings${qs({ assignment_id: assignmentId })}`),
  strengths: (studentId, assignmentId) =>
    request("GET", `/students/${enc(studentId)}/strengths${qs({ assignment_id: assignmentId })}`),
  respondToFinding: (findingId, stance, reason) =>
    request("POST", `/findings/${enc(findingId)}/responses`, {
      body: { stance, reason: reason && reason.trim() ? reason.trim() : null },
    }),

  // Review class
  startReview: (classroomId) => request("POST", `/classrooms/${enc(classroomId)}/reviews`),
  reviewProgress: (runId) => request("GET", `/reviews/${enc(runId)}`),

  // Admin - PENDING BACKEND. Same shapes as the agentic service's own endpoints
  // (AgenticAI/src/agentic/service/app.py), forwarded by the backend for admins only.
  reviewPolicy: () => request("GET", "/admin/review-policy"),
  saveReviewPolicy: (policy) => request("PUT", "/admin/review-policy", { body: policy }),
  settings: () => request("GET", "/admin/settings"),
  saveSetting: (key, value) => request("PUT", `/admin/settings/${enc(key)}`, { body: { value } }),
  settingHistory: (key) => request("GET", `/admin/settings/${enc(key)}/history`),
};

// ------------------------------------
// Small display helpers
// ------------------------------------

/** Student IDs are long anonymous hashes; the first 8 characters are enough to tell them apart. */
export const shortId = (id) => (id || "").slice(0, 8);

export function formatDate(value, { withTime = false } = {}) {
  if (!value) return "—";
  // A bare date ("2027-01-11") is a calendar day: don't shift it by the time zone.
  const d = /^\d{4}-\d{2}-\d{2}$/.test(value) ? new Date(`${value}T12:00:00`) : new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  return d.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    ...(withTime ? { hour: "numeric", minute: "2-digit" } : {}),
  });
}

/** "Jan 11" for week columns. */
export function formatWeek(value) {
  if (!value) return "";
  const d = new Date(`${value}T12:00:00`);
  return Number.isNaN(d.getTime())
    ? value
    : d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;