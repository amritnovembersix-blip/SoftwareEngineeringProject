
/**
 * SASS - API Configuration
 *
 * All frontend API requests go through this file.
 * The frontend communicates with the FastAPI backend,
 * never directly with the Agentic AI service.
 */

export const API_URL = (
  import.meta.env.VITE_API_URL || "http://127.0.0.1:8001"
).replace(/\/$/, "");

// ------------------------------------
// Authentication session management
// ------------------------------------

const TOKEN_KEY = "sassToken";
const USER_KEY = "sassUser";

// Retrieve authentication token
export function getToken() {
  return (
    sessionStorage.getItem(TOKEN_KEY) ||
    localStorage.getItem(TOKEN_KEY)
  );
}

// Retrieve logged-in user
export function getUser() {
  const raw =
    sessionStorage.getItem(USER_KEY) ||
    localStorage.getItem(USER_KEY);

  try {
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

// Save login session
export function saveSession(session, remember = false) {
  const { token, name, role, ...user } = session || {};

  if (!token) {
    throw new Error(
      "Authentication token was not returned by the backend."
    );
  }

  clearSession();

  const storage = remember ? localStorage : sessionStorage;

  storage.setItem(TOKEN_KEY, token);
  storage.setItem(
    USER_KEY,
    JSON.stringify({ name, role, ...user })
  );
}

// Clear login session
export function clearSession() {
  for (const storage of [sessionStorage, localStorage]) {
    storage.removeItem(TOKEN_KEY);
    storage.removeItem(USER_KEY);
  }
}

// ------------------------------------
// Common API request handler
// ------------------------------------

async function request(method, path, { body, auth = true } = {}) {
  const headers = {};

  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
  }

  const token = getToken();

  if (auth && token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  let response;

  try {
    response = await fetch(`${API_URL}${path}`, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new Error(
      `Can't reach the backend at ${API_URL}. Is it running?`
    );
  }

  // Handle expired or invalid authentication sessions.
  if (response.status === 401 && auth) {
    clearSession();
    window.location.assign("/login");

    throw new Error(
      "Your session has ended. Please sign in again."
    );
  }

  const data = await response.json().catch(() => null);

  if (!response.ok) {
    throw new Error(
      (data && data.detail) ||
      `The backend returned ${response.status}.`
    );
  }

  return data;
}

// ------------------------------------
// SASS API endpoints
// ------------------------------------

export const api = {
  // Authentication
  login: (email, password) =>
    request("POST", "/auth/login", {
      body: { email, password },
      auth: false,
    }),

  me: () =>
    request("GET", "/auth/me"),

  // Classrooms
  classrooms: () =>
    request("GET", "/classrooms"),

  // Students
  students: (classroomId) =>
    request(
      "GET",
      `/classrooms/${encodeURIComponent(classroomId)}/students`
    ),

  // Student findings
  findings: (studentId) =>
    request(
      "GET",
      `/students/${encodeURIComponent(studentId)}/findings`
    ),

  // Start review
  startReview: (classroomId) =>
    request(
      "POST",
      `/classrooms/${encodeURIComponent(classroomId)}/reviews`
    ),

  // Review progress
  reviewProgress: (runId) =>
    request(
      "GET",
      `/reviews/${encodeURIComponent(runId)}`
    ),
};

// ------------------------------------
// Utility functions
// ------------------------------------

// Student IDs are long anonymous hashes.
// First 12 characters are sufficient for display.
export const shortId = (id) => (id || "").slice(0, 12);
