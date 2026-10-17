#!/usr/bin/env node
// The dsh client ships exactly two locales — `LOCALE_IDS = ["zh", "en"]` — and our bundle
// registers a third (ru) for its own screens. Without this patch the Language row never offers
// Russian, and the runtime refuses a `locale.preference` of `ru`.
//
// Run after `prepare-dsh` (which stages `dsh/`) and before electron-builder:
//
//   node scripts/patch-locale.mjs            # patches ./dsh
//   node scripts/patch-locale.mjs --dir dsh  # another staged directory
//
// Idempotent: a second run finds the patched text and does nothing.

import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const appDir = resolve(here, "..");
const args = process.argv.slice(2);
const at = args.indexOf("--dir");
const root = resolve(appDir, at >= 0 ? args[at + 1] : "dsh");
const lib = join(root, "node_modules", "@deepseek-ai", "dsh-client-locale", "lib");

if (!existsSync(lib)) {
  console.error(`patch-locale: ${lib} is not there — run prepare-dsh first`);
  process.exit(1);
}

const OLD_IDS = 'const LOCALE_IDS = ["zh", "en"];';
const NEW_IDS = 'const LOCALE_IDS = ["zh", "en", "ru"];';
const OLD_META = `		const BUILT_IN_LOCALE_METADATA = {
			zh: {
				label: "中文",
				fallback: "en"
			},
			en: { label: "English" }
		};`;
const NEW_META = `		const BUILT_IN_LOCALE_METADATA = {
			zh: {
				label: "中文",
				fallback: "en"
			},
			en: { label: "English" },
			ru: {
				label: "Русский",
				fallback: "en"
			}
		};`;

let touched = 0;
for (const name of ["index.js", "client.js"]) {
  const path = join(lib, name);
  if (!existsSync(path)) continue;
  const before = readFileSync(path, "utf8");
  let text = before.replace(OLD_IDS, NEW_IDS);
  if (name === "client.js") text = text.replace(OLD_META, NEW_META);
  if (text !== before) {
    writeFileSync(path, text);
    touched += 1;
    console.log(`patch-locale: ${name} patched`);
  }
}

if (touched === 0) console.log("patch-locale: already patched");
