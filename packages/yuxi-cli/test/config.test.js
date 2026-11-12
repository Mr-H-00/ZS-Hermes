import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { getRemote, loadConfig, normalizeUrl, resolveVerificationUrl, saveConfig } from "../dist/config.js";

test("normalizes API suffix and preserves remote selection", async () => {
  assert.equal(normalizeUrl("example.com/api/"), "http://example.com");
  assert.equal(resolveVerificationUrl("https://example.com/yuxi", "/auth/cli/authorize").toString(), "https://example.com/yuxi/auth/cli/authorize");
  const path = join(await mkdtemp(join(tmpdir(), "yuxi-")), "config.json");
  await saveConfig({ current: "prod", remotes: { prod: { name: "prod", url: "https://example.com/api", apiKey: "yxkey_test" } } }, path);
  const config = await loadConfig(path);
  assert.equal(getRemote(config).apiKey, "yxkey_test");
  assert.match(await readFile(path, "utf8"), /\"prod\"/);
});
