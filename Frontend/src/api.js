// Every call to the backend goes through here. The frontend only talks to the backend
// (port 8001), never to the agentic service: its secret token must not reach the browser.

export const API_URL = (import.meta.env.VITE_API_URL || "http://localhost:8001").replace(/\/$/, "");

async function request(method, path) {
  let resp;
  try {
    resp = await fetch(API_URL + path, { method });
  } catch {
    throw new Error(`Can't reach the backend at ${API_URL}. Is it running?`);
  }
  const body = await resp.json().catch(() => null);
  if (!resp.ok) {
    throw new Error((body && body.detail) || `The backend returned ${resp.status}.`);
  }
  return body;
}

export const api = {
  classrooms: () => request("GET", "/classrooms"),
  students: (classroomId) => request("GET", `/classrooms/${encodeURIComponent(classroomId)}/students`),
  findings: (studentId) => request("GET", `/students/${encodeURIComponent(studentId)}/findings`),
  startReview: (classroomId) => request("POST", `/classrooms/${encodeURIComponent(classroomId)}/reviews`),
  reviewProgress: (runId) => request("GET", `/reviews/${encodeURIComponent(runId)}`),
};

// Student IDs are long anonymous hashes; the first 12 characters are enough to tell them apart.
export const shortId = (id) => (id || "").slice(0, 12);
