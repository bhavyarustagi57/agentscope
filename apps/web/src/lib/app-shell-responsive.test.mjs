import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const source = (path) => readFile(new URL(path, import.meta.url), "utf8");

test("mobile and desktop navigation occupy distinct responsive shell regions", async () => {
  const text = await source("../components/app-shell.tsx");
  assert.match(text, /<header[^>]*className="[^"]*lg:hidden/);
  assert.match(text, /<aside[^>]*className="[^"]*hidden[^"]*lg:block/);
  assert.match(text, /<main[\s\S]*className="[^"]*w-full[^"]*min-w-0/);
});

test("the mobile navigation remains fully visible without a drawer or keyboard trap", async () => {
  const text = await source("../components/app-shell.tsx");
  assert.match(text, /function NavigationGroups/);
  assert.match(text, /grid-cols-2/);
  assert.doesNotMatch(text, /role="dialog"|aria-modal|fixed inset/);
});

test("all seven product destinations remain in the shared grouped navigation", async () => {
  const text = await source("../lib/navigation.ts");
  for (const destination of ["Overview", "Traces", "Monitoring", "Evaluations", "Calibration", "Experiments", "Regressions"]) {
    assert.match(text, new RegExp(`label: "${destination}"`));
  }
});

test("current-page and skip-link semantics survive the responsive split", async () => {
  const text = await source("../components/app-shell.tsx");
  assert.match(text, /href="#main-content"/);
  assert.match(text, /id="main-content"[\s\S]*tabIndex=\{-1\}/);
  assert.match(text, /aria-current=\{item\.label === active \? "page" : undefined\}/);
  assert.match(text, /focus-visible:ring-2/);
});

test("the accepted desktop grid and grouped sidebar remain intact", async () => {
  const text = await source("../components/app-shell.tsx");
  assert.match(text, /lg:grid-cols-\[15rem_1fr\]/);
  assert.match(text, /Local-first control plane/);
  assert.match(text, /NAVIGATION_GROUPS\.map/);
});
