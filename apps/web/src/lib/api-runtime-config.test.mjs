import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { resolveApiBaseUrl } from "./api-base-url.ts";

test("development keeps the explicit local API convenience default", () => {
  assert.equal(resolveApiBaseUrl(undefined, "development"), "http://localhost:8000");
});

test("production requires an explicit browser-visible API URL", () => {
  assert.throws(() => resolveApiBaseUrl(undefined, "production"), /NEXT_PUBLIC_API_URL/);
});

test("API configuration accepts only a credential-free HTTP origin", () => {
  assert.equal(
    resolveApiBaseUrl("https://api.agentscope.example.test/", "production"),
    "https://api.agentscope.example.test",
  );
  for (const value of [
    "ftp://api.example.test",
    "https://user:secret@api.example.test",
    "https://api.example.test/path",
    "not-a-url",
  ]) {
    assert.throws(() => resolveApiBaseUrl(value, "production"), /NEXT_PUBLIC_API_URL/);
  }
});

test("Next.js applies baseline response security headers", async () => {
  const source = await readFile(new URL("../../next.config.ts", import.meta.url), "utf8");
  assert.match(source, /X-Content-Type-Options[\s\S]*nosniff/);
  assert.match(source, /X-Frame-Options[\s\S]*DENY/);
  assert.match(source, /Referrer-Policy[\s\S]*no-referrer/);
});
