import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, clearSession, getToken, getUser, saveSession, shortId } from "../api.js";

function mockFetch(status, body) {
  global.fetch = vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  });
}

beforeEach(() => {
  clearSession();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("login", () => {
  it("maps the backend's access_token and user into a session", async () => {
    mockFetch(200, {
      access_token: "tok-123",
      token_type: "bearer",
      expires_in: 28800,
      user: { id: 7, email: "t@sass.test", role: "teacher", teacher_id: "demo-teacher", display_name: "Ms. Rivera" },
    });
    const session = await api.login("t@sass.test", "pw");
    expect(session).toEqual({
      token: "tok-123",
      id: 7,
      email: "t@sass.test",
      name: "Ms. Rivera",
      role: "teacher",
      teacher_id: "demo-teacher",
    });
    // login is sent without an Authorization header
    const [, opts] = global.fetch.mock.calls[0];
    expect(opts.headers.Authorization).toBeUndefined();
    expect(JSON.parse(opts.body)).toEqual({ email: "t@sass.test", password: "pw" });
  });

  it("stores the token and user, and clears them on logout", () => {
    saveSession({ token: "abc", name: "Admin", role: "admin" }, false);
    expect(getToken()).toBe("abc");
    expect(getUser()).toEqual({ name: "Admin", role: "admin" });
    clearSession();
    expect(getToken()).toBeNull();
  });

  it("refuses to save a session without a token", () => {
    expect(() => saveSession({ name: "x" })).toThrow(/token/);
  });
});

describe("requests", () => {
  it("sends the token on data requests", async () => {
    saveSession({ token: "abc", role: "teacher" });
    mockFetch(200, []);
    await api.classrooms();
    expect(global.fetch.mock.calls[0][1].headers.Authorization).toBe("Bearer abc");
  });

  it("keeps the HTTP status and the backend's message on errors", async () => {
    saveSession({ token: "abc", role: "teacher" });
    mockFetch(403, { detail: "this classroom is not in your classes" });
    const err = await api.students("c1").catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(403);
    expect(err.message).toBe("this classroom is not in your classes");
  });

  it("turns FastAPI validation errors into readable text", async () => {
    saveSession({ token: "abc", role: "teacher" });
    mockFetch(422, { detail: [{ msg: "give exactly two different classroom IDs" }] });
    const err = await api.comparison("c1", "c1").catch((e) => e);
    expect(err.message).toBe("give exactly two different classroom IDs");
  });

  it("logs out on 401", async () => {
    saveSession({ token: "expired", role: "teacher" });
    mockFetch(401, { detail: "login expired" });
    const assign = vi.fn();
    Object.defineProperty(window, "location", { value: { ...window.location, pathname: "/", assign }, writable: true });
    const err = await api.dashboard().catch((e) => e);
    expect(err.status).toBe(401);
    expect(getToken()).toBeNull();
    expect(assign).toHaveBeenCalledWith("/login");
  });

  it("builds query strings and skips empty values", async () => {
    saveSession({ token: "abc", role: "teacher" });
    mockFetch(200, []);
    await api.findings("stu 1", undefined);
    expect(global.fetch.mock.calls[0][0]).toMatch(/\/students\/stu%201\/findings$/);
    await api.findings("s", "a@2.00-control");
    expect(global.fetch.mock.calls[1][0]).toMatch(/\?assignment_id=a%402.00-control$/);
  });

  it("reports an unreachable backend clearly", async () => {
    global.fetch = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
    const err = await api.classrooms().catch((e) => e);
    expect(err.message).toMatch(/Can't reach the backend/);
  });
});

it("hides peer-pair notifications (left out of this release)", async () => {
  saveSession({ token: "abc", role: "teacher" });
  mockFetch(200, [
    { id: "needs_attention:c1", kind: "needs_attention" },
    { id: "pending_pairs:c1", kind: "pending_pairs" },
  ]);
  const rows = await api.notifications();
  expect(rows.map((n) => n.kind)).toEqual(["needs_attention"]);
});

it("shortens anonymous student ids", () => {
  expect(shortId("5660cf806c38b591345c")).toBe("5660cf80");
});