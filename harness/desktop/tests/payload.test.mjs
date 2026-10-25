// The dsh core in the person's home (src/payload.ts): the copy is made once and never again, a
// newer core in the bundle replaces it (the old one kept as dsh.previous), an older one does
// not, and a copy that fails leaves the app on the bundle. Temporary directories throughout, no
// Electron and no 460 MB. Then main.ts's wiring: startHost runs from dshRoot(), which names the
// payload under userData and logs where the core came from. Run after `tsc -p tsconfig.json`
// (`npm test` does both).
import assert from "node:assert/strict";
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { after, test } from "node:test";
import { bundleIsNewer, compareVersions, dshBin, hasDsh, resolveDshRoot, versionsOf } from "../out/payload.js";

const roots = [];
// the whole test run's copies go: a failed assertion must not leave a tree behind
after(() => {
  for (const dir of roots) rmSync(dir, { recursive: true, force: true });
});

function scratch() {
  const dir = mkdtempSync(join(tmpdir(), "nanolika-payload-"));
  roots.push(dir);
  return dir;
}

/**
 * A dsh root shaped like the one scripts/prepare-dsh.mjs stages: the Host's entry point where
 * src/main.ts looks for it, the record About reads, and one file that tells the copies apart.
 */
function stageDsh(dir, { core, bundle, marker }) {
  mkdirSync(join(dir, "node_modules", "@deepseek-ai", "dsh", "lib"), { recursive: true });
  writeFileSync(join(dir, "node_modules", "@deepseek-ai", "dsh", "lib", "bin.js"), "// the Host\n");
  writeFileSync(join(dir, "dsh.json"), `${JSON.stringify({ dsh: core, bundle, platform: process.platform, arch: process.arch }, null, 2)}\n`);
  writeFileSync(join(dir, "marker.txt"), marker);
  return dir;
}

/** Resolve as main.ts's dshRoot does, keeping the lines the log would have received. */
function resolve({ payloadDir, bundleDir }) {
  const lines = [];
  return { place: resolveDshRoot({ payloadDir, bundleDir, log: (line) => lines.push(line) }), lines };
}

const marker = (dir) => readFileSync(join(dir, "marker.txt"), "utf8");

test("the Host's entry point is node_modules/@deepseek-ai/dsh/lib/bin.js inside the root", () => {
  assert.equal(dshBin("/a/dsh"), join("/a/dsh", "node_modules", "@deepseek-ai", "dsh", "lib", "bin.js"));
});

test("the first run copies the core out of the bundle once", () => {
  const root = scratch();
  const payloadDir = join(root, "userData", "payload", "dsh");
  const bundleDir = stageDsh(join(root, "Resources", "dsh"), { core: "0.2.0-rc.2", bundle: "1.0.5", marker: "from the bundle" });
  const { place, lines } = resolve({ payloadDir, bundleDir });
  assert.equal(place.from, "payload");
  assert.equal(place.dir, payloadDir);
  assert.ok(hasDsh(payloadDir), "bin.js is in the payload");
  assert.equal(marker(payloadDir), "from the bundle");
  assert.equal(readFileSync(join(payloadDir, "dsh.json"), "utf8"), readFileSync(join(bundleDir, "dsh.json"), "utf8"));
  assert.deepEqual(versionsOf(payloadDir), { core: "0.2.0-rc.2", bundle: "1.0.5" });
  assert.ok(lines.some((line) => line.includes("copying")), lines.join("\n"));
});

test("later runs copy nothing, not even a byte", () => {
  const root = scratch();
  const payloadDir = join(root, "userData", "payload", "dsh");
  const bundleDir = stageDsh(join(root, "Resources", "dsh"), { core: "0.2.0-rc.2", bundle: "1.0.5", marker: "from the bundle" });
  assert.equal(resolve({ payloadDir, bundleDir }).place.from, "payload");
  // a file only the copy has: a second copy would not carry it over
  writeFileSync(join(payloadDir, "local-edit.txt"), "kept");
  const before = statSync(dshBin(payloadDir));
  const second = resolve({ payloadDir, bundleDir });
  assert.equal(second.place.from, "payload");
  assert.equal(second.place.dir, payloadDir);
  assert.equal(readFileSync(join(payloadDir, "local-edit.txt"), "utf8"), "kept", "the copy was not replaced");
  assert.equal(statSync(dshBin(payloadDir)).ino, before.ino, "bin.js is the same file");
  assert.deepEqual(second.lines, [], "nothing was copied, so the log says nothing");
});

test("a newer core in the bundle replaces the copy, the old one kept as dsh.previous", () => {
  const root = scratch();
  const payloadDir = join(root, "userData", "payload", "dsh");
  const bundleDir = join(root, "Resources", "dsh");
  stageDsh(bundleDir, { core: "0.2.0", bundle: "1.0.5", marker: "the old core" });
  resolve({ payloadDir, bundleDir });
  stageDsh(bundleDir, { core: "0.3.0", bundle: "1.0.5", marker: "the new core" });
  const { place, lines } = resolve({ payloadDir, bundleDir });
  assert.equal(place.from, "payload");
  assert.equal(marker(payloadDir), "the new core");
  assert.deepEqual(versionsOf(payloadDir), { core: "0.3.0", bundle: "1.0.5" });
  assert.equal(marker(`${payloadDir}.previous`), "the old core", "the copy it replaced is still there");
  assert.ok(lines.some((line) => line.includes("0.3.0")), lines.join("\n"));
});

test("a newer plugin in the bundle refreshes the copy too", () => {
  const root = scratch();
  const payloadDir = join(root, "userData", "payload", "dsh");
  const bundleDir = join(root, "Resources", "dsh");
  stageDsh(bundleDir, { core: "0.2.0", bundle: "1.0.5", marker: "bundle 1.0.5" });
  resolve({ payloadDir, bundleDir });
  stageDsh(bundleDir, { core: "0.2.0", bundle: "1.0.6", marker: "bundle 1.0.6" });
  assert.equal(resolve({ payloadDir, bundleDir }).place.dir, payloadDir);
  assert.equal(marker(payloadDir), "bundle 1.0.6");
  assert.deepEqual(versionsOf(payloadDir), { core: "0.2.0", bundle: "1.0.6" });
});

test("an older core in the bundle leaves the copy alone", () => {
  const root = scratch();
  const payloadDir = stageDsh(join(root, "userData", "payload", "dsh"), { core: "0.3.0", bundle: "1.2.0", marker: "the newer copy" });
  const bundleDir = stageDsh(join(root, "Resources", "dsh"), { core: "0.2.0", bundle: "1.2.0", marker: "an older app" });
  const { place, lines } = resolve({ payloadDir, bundleDir });
  assert.equal(place.from, "payload");
  assert.equal(marker(payloadDir), "the newer copy");
  assert.deepEqual(lines, [], "nothing copied, nothing said");
  assert.ok(!existsSync(`${payloadDir}.previous`));
});

test("a copy that cannot be made leaves the app on the bundle, logged", () => {
  const root = scratch();
  const dataDir = join(root, "userData");
  mkdirSync(dataDir, { recursive: true });
  const bundleDir = stageDsh(join(root, "Resources", "dsh"), { core: "0.2.0", bundle: "1.0.5", marker: "from the bundle" });
  // nowhere to copy to: the folder the payload would sit in cannot be written
  chmodSync(dataDir, 0o500);
  try {
    const { place, lines } = resolve({ payloadDir: join(dataDir, "payload", "dsh"), bundleDir });
    assert.equal(place.from, "bundle");
    assert.equal(place.dir, bundleDir);
    assert.ok(!existsSync(join(dataDir, "payload", "dsh")), "no half-made payload");
    assert.ok(lines.some((line) => line.includes("could not copy")), lines.join("\n"));
  } finally {
    chmodSync(dataDir, 0o700);
  }
});

test("a failed refresh puts the old copy back", () => {
  const root = scratch();
  const payloadDir = stageDsh(join(root, "userData", "payload", "dsh"), { core: "0.2.0", bundle: "1.0.5", marker: "the trusted copy" });
  const bundleDir = stageDsh(join(root, "Resources", "dsh"), { core: "0.3.0", bundle: "1.0.5", marker: "the new core" });
  // a file in the new bundle that the copy cannot read: the copy stops partway
  const locked = join(bundleDir, "locked.bin");
  writeFileSync(locked, "cannot be read\n");
  chmodSync(locked, 0o000);
  try {
    const { place, lines } = resolve({ payloadDir, bundleDir });
    assert.equal(place.from, "payload", "the app keeps a core it can start");
    assert.equal(marker(payloadDir), "the trusted copy", "the old copy is back in place");
    assert.ok(hasDsh(payloadDir));
    assert.ok(!existsSync(`${payloadDir}.previous`), "dsh.previous became the payload again");
    assert.ok(lines.some((line) => line.includes("put back")), lines.join("\n"));
  } finally {
    chmodSync(locked, 0o644);
  }
});

test("a core in the payload and none in the bundle is used as it is", () => {
  const root = scratch();
  const payloadDir = stageDsh(join(root, "userData", "payload", "dsh"), { core: "0.2.0", bundle: "1.0.5", marker: "the copy" });
  const { place } = resolve({ payloadDir, bundleDir: join(root, "Resources", "no-dsh-here") });
  assert.equal(place.from, "payload");
  assert.equal(place.dir, payloadDir);
});

test("no core anywhere is reported as missing, never copied", () => {
  const root = scratch();
  const payloadDir = join(root, "userData", "payload", "dsh");
  const bundleDir = join(root, "Resources", "no-dsh-here");
  const { place, lines } = resolve({ payloadDir, bundleDir });
  assert.deepEqual(place, { dir: bundleDir, from: "bundle" });
  assert.ok(!existsSync(payloadDir));
  assert.deepEqual(lines, []);
});

test("the record comes from the installed packages when dsh.json is missing", () => {
  const root = scratch();
  const dir = stageDsh(join(root, "dsh"), { core: "0.2.0", bundle: "1.0.5", marker: "" });
  rmSync(join(dir, "dsh.json"));
  mkdirSync(join(dir, "node_modules", "dsh-nanomuse"), { recursive: true });
  writeFileSync(join(dir, "node_modules", "@deepseek-ai", "dsh", "package.json"), `${JSON.stringify({ name: "@deepseek-ai/dsh", version: "0.1.9" })}\n`);
  writeFileSync(join(dir, "node_modules", "dsh-nanomuse", "package.json"), `${JSON.stringify({ name: "dsh-nanomuse", version: "1.0.4" })}\n`);
  assert.deepEqual(versionsOf(dir), { core: "0.1.9", bundle: "1.0.4" });
});

test("versions compare the way semver does", () => {
  assert.equal(compareVersions("0.2.0", "0.2.0"), 0);
  assert.equal(compareVersions("0.2.1", "0.2.0"), 1);
  assert.equal(compareVersions("0.10.0", "0.9.9"), 1);
  assert.equal(compareVersions("0.2.0", "0.2.0-rc.2"), 1, "a release is newer than its pre-release");
  assert.equal(compareVersions("0.2.0-rc.10", "0.2.0-rc.9"), 1, "numbered pre-release parts count as numbers");
  assert.equal(compareVersions("v1.0.0", "1.0.0"), 0);
  assert.equal(compareVersions("", "1.0.0"), undefined);
  assert.equal(compareVersions("the bundle", "1.0.0"), undefined);
});

test("the bundle replaces the copy only when it is newer", () => {
  assert.equal(bundleIsNewer({ core: "0.2.0", bundle: "1.0.5" }, { core: "0.3.0", bundle: "1.0.5" }), true);
  assert.equal(bundleIsNewer({ core: "0.3.0", bundle: "1.0.5" }, { core: "0.2.0", bundle: "1.0.5" }), false);
  assert.equal(bundleIsNewer({ core: "0.2.0", bundle: "1.0.5" }, { core: "0.2.0", bundle: "1.0.6" }), true);
  assert.equal(bundleIsNewer({ core: "0.2.0", bundle: "1.0.6" }, { core: "0.2.0", bundle: "1.0.5" }), false);
  assert.equal(bundleIsNewer({ core: "0.2.0", bundle: "1.0.5" }, { core: "0.2.0", bundle: "1.0.5" }), false);
  assert.equal(bundleIsNewer({ core: "0.2.0", bundle: "1.0.5" }, { core: "", bundle: "" }), false, "a bundle with no record changes nothing");
  assert.equal(bundleIsNewer({ core: "", bundle: "" }, { core: "0.2.0", bundle: "1.0.5" }), true, "a copy with no record is repaired");
});

// ---- the wiring in main.ts (read as source: importing it would need Electron) --------------

test("the Host runs from dshRoot(), which names the payload under userData and says where it came from", () => {
  const main = readFileSync(new URL("../src/main.ts", import.meta.url), "utf8");
  const at = main.indexOf("function startHost");
  assert.notEqual(at, -1);
  const start = main.slice(at, at + 900);
  assert.match(start, /const dshDir = dshRoot\(\);/, "startHost takes the root from dshRoot()");
  assert.doesNotMatch(start, /join\(resourcesDir\(\), "dsh"\)/, "and no longer from the bundle's path");
  assert.match(start, /const bin = join\(dshDir, \.\.\.DSH_BIN\);/, "bin.js is still where it was inside the root");
  // the prompt path: <userData>/payload/dsh, resolved once, logged in one line
  assert.match(main, /payloadDir: join\(app\.getPath\("userData"\), "payload", "dsh"\)/);
  assert.match(main, /bundleDir: join\(resourcesDir\(\), "dsh"\)/);
  assert.match(main, /if \(!dshPlace\) \{/, "resolved once, so a restart does not copy again");
  assert.match(main, /log\(`dsh: \$\{dshPlace\.dir\} \(\$\{dshPlace\.from\}\)`\)/, "dsh: /path (payload|bundle)");
  // the plugin and the profile are given the same root
  assert.match(start, /ensurePluginOutsideBundle\(dshDir\);/);
  assert.match(start, /ensureProfile\(dshDir\);/);
});
