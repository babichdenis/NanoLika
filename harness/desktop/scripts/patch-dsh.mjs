#!/usr/bin/env node
// Prepares the staged dsh tree for our build, after `prepare-dsh` and before electron-builder:
//
//  1. Vendors the community Russian pack (`harness/vendor/dsh-locale-ru`) into
//     `dsh/node_modules/@absolutemikhail/dsh-locale-ru`, so the profile can link to it and the
//     Loader finds it next to the harness's own packages. The desktop profile lists it as a
//     bundle (`LOCALE_BUNDLE` in src/main.ts) and links it in on every start.
//  2. Keeps the built-in locale list at its stock `["zh", "en"]`: the pack registers `ru`
//     through `locale.addLanguage()`, and a second registration of the same id throws
//     ("locale \"ru\" is already registered"). An earlier build patched that list instead;
//     the line is normalised here so such a tree still builds.
//
// Idempotent: a second run copies the same files and finds nothing to normalise.

import { cpSync, existsSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const appDir = resolve(here, "..");
const args = process.argv.slice(2);
const at = args.indexOf("--dir");
const root = resolve(appDir, at >= 0 ? args[at + 1] : "dsh");
const vendor = resolve(appDir, "..", "vendor", "dsh-locale-ru");

if (!existsSync(join(root, "node_modules"))) {
  console.error(`patch-dsh: ${root} has no node_modules — run prepare-dsh first`);
  process.exit(1);
}

// 1. The pack, next to the harness's own packages.
if (!existsSync(join(vendor, "package.json"))) {
  console.error(`patch-dsh: the vendored pack is missing at ${vendor}`);
  process.exit(1);
}
const dest = join(root, "node_modules", "@absolutemikhail", "dsh-locale-ru");
rmSync(dest, { recursive: true, force: true });
cpSync(vendor, dest, { recursive: true });
console.log(`patch-dsh: locale pack → ${dest}`);

// 2. Stock locale ids (the pack adds ru itself).
const lib = join(root, "node_modules", "@deepseek-ai", "dsh-client-locale", "lib");
const PATCHED = 'const LOCALE_IDS = ["zh", "en", "ru"];';
const STOCK = 'const LOCALE_IDS = ["zh", "en"];';
let normalised = 0;
for (const name of ["index.js", "client.js"]) {
  const path = join(lib, name);
  if (!existsSync(path)) continue;
  const text = readFileSync(path, "utf8");
  if (!text.includes(PATCHED)) continue;
  writeFileSync(path, text.split(PATCHED).join(STOCK));
  normalised += 1;
}
if (normalised > 0) console.log(`patch-dsh: locale ids normalised in ${normalised} file(s)`);
