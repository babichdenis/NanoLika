#!/usr/bin/env node
/**
 * The provider catalogue as the browser may see it.
 *
 * `nanomuse/llm/providers.json` is the runtime's own description of every supplier: key
 * environment names, catalog and balance endpoints, relay addresses, roles, prices. None of
 * that belongs in a bundle a stranger downloads. This script copies only the display fields
 * the client actually renders into `web/src/data/providers.client.json`, so the boundary is
 * one generated file instead of a pile of cross-package imports.
 *
 *   node scripts/client-providers.mjs          # write the file
 *   node scripts/client-providers.mjs --check  # fail when it is stale (CI)
 */
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const source = join(root, "nanomuse/llm/providers.json");
const target = join(root, "web/src/data/providers.client.json");

// Everything the UI draws: names, where to sign up, which models it defaults to, what it can
// do, and the two explanatory notes. Anything else stays on the server.
const FIELDS = [
  "id",
  "name",
  "name_zh",
  "protocol",
  "base_url",
  "base_url_global",
  "key_url",
  "key_url_global",
  "key_hint",
  "auth",
  "auth_capabilities",
  "regions",
  "capabilities",
  "defaults",
  "note",
  "note_zh",
  "session_header",
  "verified",
  "reasoning",
  "user_capabilities",
];

function build() {
  const raw = JSON.parse(readFileSync(source, "utf8"));
  const providers = (raw.providers ?? []).map((entry) => {
    const out = {};
    for (const field of FIELDS) {
      if (entry[field] !== undefined) out[field] = entry[field];
    }
    return out;
  });
  return {
    version: raw.version ?? 1,
    updated: raw.updated ?? "",
    capabilities: raw.capabilities ?? [],
    auth_kinds: raw.auth_kinds ?? [],
    providers,
  };
}

const text = JSON.stringify(build(), null, 2) + "\n";
if (process.argv.includes("--check")) {
  let current = "";
  try {
    current = readFileSync(target, "utf8");
  } catch {
    current = "";
  }
  if (current !== text) {
    console.error("web/src/data/providers.client.json is stale: run node scripts/client-providers.mjs");
    process.exit(1);
  }
  console.log("client providers: up to date");
} else {
  writeFileSync(target, text);
  console.log(`client providers: ${text.length} bytes -> web/src/data/providers.client.json`);
}
