#!/usr/bin/env node
/**
 * The Node the office skills run their scripts with.
 *
 * The desktop app is Electron, and Electron is not Node: `process.execPath` there is the
 * app itself, so a skill that spawns `node script.py`-style helpers has nothing to spawn.
 * This stages the official build for the current platform under `node/`, which
 * electron-builder copies into the app's resources and src/main.ts hands to the harness as
 * `NANOMUSE_NODE` (the preset row `skill-office` reads it).
 *
 *   node scripts/prepare-node.mjs               # fetch once, keep it
 *   node scripts/prepare-node.mjs --force       # fetch again
 *   node scripts/prepare-node.mjs --out node    # where to stage (default: ./node)
 *   node scripts/prepare-node.mjs --version 22.19.0
 *
 * Nothing is fetched when the binary is already there, so `npm run dist` stays offline after
 * the first build. The version is the one the harness's own CI uses (Node 22).
 */
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { chmodSync, copyFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, "..");

function arg(name, fallback) {
  const at = process.argv.indexOf(`--${name}`);
  return at === -1 ? fallback : (process.argv[at + 1] ?? fallback);
}

const VERSION = arg("version", process.env.NODE_VERSION || "22.19.0");
const OUT = resolve(ROOT, arg("out", "node"));
const FORCE = process.argv.includes("--force");

/** nodejs.org's names for our platforms, or undefined where there is no build to fetch. */
function distName() {
  const os = { darwin: "darwin", linux: "linux", win32: "win" }[process.platform];
  const arch = { x64: "x64", arm64: "arm64" }[process.arch];
  if (!os || !arch) return undefined;
  return { id: `node-v${VERSION}-${os}-${arch}`, ext: process.platform === "win32" ? "zip" : "tar.gz", exe: process.platform === "win32" ? "node.exe" : "node" };
}

const target = () => join(OUT, "bin", process.platform === "win32" ? "node.exe" : "node");

function stage(from, exe) {
  mkdirSync(join(OUT, "bin"), { recursive: true });
  copyFileSync(from, target());
  if (process.platform !== "win32") chmodSync(target(), 0o755);
  const size = Math.round(statSync(target()).size / (1024 * 1024));
  console.log(`prepare-node: ${target()} (${size} MB) from ${exe}`);
}

function main() {
  if (existsSync(target()) && !FORCE) {
    console.log(`prepare-node: ${target()} is already there (--force to fetch again)`);
    return;
  }
  const dist = distName();
  if (!dist) {
    console.log(`prepare-node: no official Node build for ${process.platform}/${process.arch}; the office skills keep the Node they find on PATH`);
    return;
  }
  const url = `https://nodejs.org/dist/v${VERSION}/${dist.id}.${dist.ext}`;
  const work = mkdtempSync(join(tmpdir(), "prepare-node-"));
  const archive = join(work, `${dist.id}.${dist.ext}`);
  try {
    console.log(`prepare-node: ${url}`);
    // curl ships with macOS, Windows 10 and every Linux we build on; fetch() would hold the
    // whole archive in memory and give no progress.
    execFileSync("curl", ["-fL", "--retry", "2", "-o", archive, url], { stdio: ["ignore", "ignore", "inherit"] });
    // The archive is a binary we hand to the agent's skills: take the published digest,
    // never the download alone.
    const shasums = join(work, "SHASUMS256.txt");
    execFileSync("curl", ["-fL", "--retry", "2", "-o", shasums, `https://nodejs.org/dist/v${VERSION}/SHASUMS256.txt`], { stdio: ["ignore", "ignore", "inherit"] });
    const wanted = readFileSync(shasums, "utf8")
      .split("\n")
      .find((line) => line.endsWith(`  ${dist.id}.${dist.ext}`))
      ?.split(/\s+/)[0];
    const got = createHash("sha256").update(readFileSync(archive)).digest("hex");
    if (!wanted || wanted !== got) throw new Error(`prepare-node: ${dist.id}.${dist.ext} is ${got}, the published digest is ${wanted ?? "absent"}`);
    if (dist.ext === "zip") execFileSync("powershell", ["-NoProfile", "-Command", `Expand-Archive -LiteralPath '${archive}' -DestinationPath '${work}' -Force`], { stdio: "inherit" });
    else execFileSync("tar", ["-xzf", archive, "-C", work], { stdio: "inherit" });
    const dir = join(work, dist.id);
    // Windows archives keep node.exe at the root, the unix ones under bin/.
    const exe = process.platform === "win32" ? join(dir, dist.exe) : join(dir, "bin", dist.exe);
    stage(exe, dist.id);
  } finally {
    rmSync(work, { recursive: true, force: true });
  }
}

main();
