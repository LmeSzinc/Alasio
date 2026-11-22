/**
 * Unit tests for the remembered route of the embedded web app.
 *
 * The module is pure: it validates the route reported by the frontend and
 * builds the URL the iframe is mounted at. The store in the main process and
 * the postMessage plumbing live next to it, which needs electron to run.
 */
import { describe, expect, it } from "vitest";
import { embeddedUrl, isEmbeddedRoute } from "./embeddedRoute";

/** Backend port of the loaded config, the one the iframe points at */
const PORT = 22267;

describe("TestIsEmbeddedRoute", () => {
  it("accepts a path of the frontend", () => {
    expect(isEmbeddedRoute("/")).toBe(true);
    expect(isEmbeddedRoute("/app")).toBe(true);
    expect(isEmbeddedRoute("/config/cfg_a/overview")).toBe(true);
    expect(isEmbeddedRoute("/config/cfg_a/arg?nav=main&card=stop")).toBe(true);
    expect(isEmbeddedRoute("/dev/setting#top")).toBe(true);
  });

  it("rejects anything that is not a path", () => {
    expect(isEmbeddedRoute("")).toBe(false);
    expect(isEmbeddedRoute("config/cfg_a/overview")).toBe(false);
    expect(isEmbeddedRoute("https://example.com/")).toBe(false);
    expect(isEmbeddedRoute(undefined)).toBe(false);
    expect(isEmbeddedRoute(null)).toBe(false);
    expect(isEmbeddedRoute(42)).toBe(false);
    expect(isEmbeddedRoute({ route: "/" })).toBe(false);
  });

  it("rejects paths that address another origin", () => {
    // `//host` and `/\host` are URLs, not paths: mounting one would point the
    // iframe at a foreign page
    expect(isEmbeddedRoute("//example.com/")).toBe(false);
    expect(isEmbeddedRoute("/\\example.com/")).toBe(false);
  });

  it("rejects a route with a control character or an absurd length", () => {
    expect(isEmbeddedRoute("/config/line\nbreak")).toBe(false);
    expect(isEmbeddedRoute("/config/\u0000")).toBe(false);
    expect(isEmbeddedRoute("/" + "a".repeat(2048))).toBe(false);
    // the limit itself is still fine
    expect(isEmbeddedRoute("/" + "a".repeat(2047))).toBe(true);
  });
});

describe("TestEmbeddedUrl", () => {
  const params = { embedded: "electron", theme: "dark" };

  it("mounts the app root when nothing is remembered", () => {
    expect(embeddedUrl(PORT, "/", params)).toBe(`http://127.0.0.1:${PORT}/?embedded=electron&theme=dark`);
  });

  it("mounts the remembered page", () => {
    expect(embeddedUrl(PORT, "/config/cfg_a/overview", params)).toBe(
      `http://127.0.0.1:${PORT}/config/cfg_a/overview?embedded=electron&theme=dark`,
    );
  });

  it("keeps the query and the hash of the route", () => {
    expect(embeddedUrl(PORT, "/dev/setting?tab=display#top", params)).toBe(
      `http://127.0.0.1:${PORT}/dev/setting?tab=display&embedded=electron&theme=dark#top`,
    );
  });

  it("encodes the parameter values", () => {
    expect(embeddedUrl(PORT, "/", { lang: "zh-CN", note: "a&b c" })).toBe(
      `http://127.0.0.1:${PORT}/?lang=zh-CN&note=a%26b+c`,
    );
  });

  it("falls back to the app root for a route it would not mount", () => {
    expect(embeddedUrl(PORT, "//example.com/", params)).toBe(`http://127.0.0.1:${PORT}/?embedded=electron&theme=dark`);
    expect(embeddedUrl(PORT, "", params)).toBe(`http://127.0.0.1:${PORT}/?embedded=electron&theme=dark`);
  });
});
