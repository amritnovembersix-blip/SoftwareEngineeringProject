import { describe, it, expect, vi, afterEach } from "vitest";
import { api, shortId, API_URL } from "./api.js";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("Frontend API tests", () => {
  it("returns classroom data after a successful response", async () => {
    const data = [{ id: "class-1", name: "Class 1" }];

    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => data,
    }));

    await expect(api.classrooms()).resolves.toEqual(data);
    expect(fetch).toHaveBeenCalledWith(
      `${API_URL}/classrooms`,
      { method: "GET" }
    );
  });

  it("handles HTTP 404 errors", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: false,
      status: 404,
      json: async () => ({ detail: "Not found" }),
    }));

    await expect(api.classrooms()).rejects.toThrow("Not found");
  });

  it("handles HTTP 503 errors", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: false,
      status: 503,
      json: async () => ({ detail: "Service unavailable" }),
    }));

    await expect(api.classrooms()).rejects.toThrow(
      "Service unavailable"
    );
  });

  it("handles an unavailable backend", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(
      new Error("Network failure")
    ));

    await expect(api.classrooms()).rejects.toThrow(
      "Can't reach the backend"
    );
  });

  it("returns null when a successful response contains invalid JSON", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => {
        throw new Error("Invalid JSON");
      },
    }));

    await expect(api.classrooms()).resolves.toBeNull();
  });

  it("encodes special characters in classroom IDs", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [],
    }));

    await api.students("class 1");

    expect(fetch).toHaveBeenCalledWith(
      `${API_URL}/classrooms/class%201/students`,
      { method: "GET" }
    );
  });

  it("shortens student IDs to 12 characters", () => {
    expect(shortId("1234567890123456")).toBe("123456789012");
  });

  it("returns an empty string for a missing student ID", () => {
    expect(shortId("")).toBe("");
    expect(shortId(null)).toBe("");
  });
});
