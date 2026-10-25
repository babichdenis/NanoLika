// The dsh core in the person's home instead of the app bundle (src/main.ts's dshRoot).
//
// The core is most of what an update ever changes and most of the app's size (~460 MB), and
// inside the .app it makes every core change a new build, a new asar and a new signature. Kept
// under `<userData>/payload/dsh`, it is a folder to replace: an app that ships a newer core
// copies it in on the next launch, and a core can be dropped in place without touching the app.
//
// The copy is made once, on the first run. Later runs read it and copy nothing. When the bundle
// carries a newer core (`dsh.json`'s `dsh` and `bundle`, or the installed package.json's
// version) the old copy is renamed to `dsh.previous`, the new one copied in, and the old one put
// back if that copy fails. A copy that cannot be made at all (no space, no permission) leaves
// the app on the bundle, logged, which is exactly what it did before this file existed.

import { cpSync, existsSync, mkdirSync, readFileSync, renameSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";

/** Inside a dsh root: the Host's entry point (`dsh <profile> --no-open --port …`). */
export const DSH_BIN = ["node_modules", "@deepseek-ai", "dsh", "lib", "bin.js"] as const;

/** The Host's entry point in a dsh root. */
export function dshBin(root: string): string {
  return join(root, ...DSH_BIN);
}

/** Whether `root` is a dsh root the Host can run from. */
export function hasDsh(root: string): boolean {
  return existsSync(dshBin(root));
}

/** Which of the two places the core came from, for the log line the person sends in. */
export type DshFrom = "payload" | "bundle";
export type DshPlace = { dir: string; from: DshFrom };

/** What a dsh root says it is: the core (`dsh.json`'s `dsh`, or the installed package) and the bundle (`bundle`, or the installed one). */
export type DshVersions = { core: string; bundle: string };

function readJson(file: string): Record<string, unknown> | undefined {
  try {
    return JSON.parse(readFileSync(file, "utf8")) as Record<string, unknown>;
  } catch {
    return undefined;
  }
}

/** The versions a dsh root records; `dsh.json` first, the installed packages when it is missing. */
export function versionsOf(root: string): DshVersions {
  const record = readJson(join(root, "dsh.json")) ?? {};
  let core = typeof record.dsh === "string" ? record.dsh : "";
  let bundle = typeof record.bundle === "string" ? record.bundle : "";
  if (!core) {
    const installed = readJson(join(root, "node_modules", "@deepseek-ai", "dsh", "package.json"));
    if (typeof installed?.version === "string") core = installed.version;
  }
  if (!bundle) {
    const installed = readJson(join(root, "node_modules", "dsh-nanomuse", "package.json"));
    if (typeof installed?.version === "string") bundle = installed.version;
  }
  return { core, bundle };
}

const VERSION = /^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:[-+](.*))?$/;

function parts(version: string): { nums: [number, number, number]; pre: string } | undefined {
  const m = VERSION.exec(version.trim());
  if (!m) return undefined;
  return { nums: [Number(m[1] ?? 0), Number(m[2] ?? 0), Number(m[3] ?? 0)], pre: m[4] ?? "" };
}

/** Pre-release identifiers by semver's rule: numbered ones (rc.10 > rc.9) and the rest alphabetically. */
function comparePre(a: string, b: string): number {
  const left = a.split(".");
  const right = b.split(".");
  for (let i = 0; i < Math.max(left.length, right.length); i += 1) {
    const x = left[i];
    const y = right[i];
    if (x === undefined) return -1;
    if (y === undefined) return 1;
    const nx = /^\d+$/.test(x);
    const ny = /^\d+$/.test(y);
    if (nx && ny) {
      const diff = Number(x) - Number(y);
      if (diff !== 0) return diff < 0 ? -1 : 1;
      continue;
    }
    // a numbered identifier sorts before an alphanumeric one
    if (nx !== ny) return nx ? -1 : 1;
    if (x !== y) return x < y ? -1 : 1;
  }
  return 0;
}

/** `a` against `b`: negative when `a` is older, positive when newer, 0 when the same. Undefined when either is not a version. */
export function compareVersions(a: string, b: string): number | undefined {
  if (!a || !b) return undefined;
  const left = parts(a);
  const right = parts(b);
  if (!left || !right) return undefined;
  for (let i = 0; i < 3; i += 1) {
    const diff = (left.nums[i] ?? 0) - (right.nums[i] ?? 0);
    if (diff !== 0) return diff < 0 ? -1 : 1;
  }
  if (!left.pre && !right.pre) return 0;
  // a release is newer than any of its pre-releases
  if (!left.pre) return 1;
  if (!right.pre) return -1;
  return comparePre(left.pre, right.pre);
}

/**
 * Whether the bundle's core is newer than the copy in the payload.
 *
 * The core version decides first, then the bundle's own; a record that cannot be compared falls
 * back to "a different record wins", so a payload whose `dsh.json` was lost is repaired rather
 * than kept forever.
 */
export function bundleIsNewer(payload: DshVersions, bundle: DshVersions): boolean {
  if (!bundle.core && !bundle.bundle) return false;
  if (!payload.core && !payload.bundle) return true;
  for (const key of ["core", "bundle"] as const) {
    const order = compareVersions(bundle[key], payload[key]);
    if (order !== undefined && order !== 0) return order > 0;
  }
  const same = bundle.core === payload.core && bundle.bundle === payload.bundle;
  const comparable = compareVersions(bundle.core, payload.core) !== undefined && compareVersions(bundle.bundle, payload.bundle) !== undefined;
  return !comparable && !same;
}

export type DshRootOptions = {
  /** `<userData>/payload/dsh`: the copy the app runs from. */
  payloadDir: string;
  /** `<resources>/dsh`: the core the app was built with. */
  bundleDir: string;
  log: (line: string) => void;
};

/**
 * Put the bundle's core where it can be replaced without the app: `<userData>/payload/dsh`.
 * False when the payload is left without a core the Host can start from (the caller then runs
 * from the bundle); a refresh that failed but put the old copy back is not that.
 */
function placeCore(options: DshRootOptions): boolean {
  const { payloadDir, bundleDir, log } = options;
  const previous = `${payloadDir}.previous`;
  let moved = false;
  try {
    mkdirSync(dirname(payloadDir), { recursive: true });
    if (existsSync(payloadDir)) {
      // the copy is replaced, never patched: a half-written tree from an earlier failure goes too
      rmSync(previous, { recursive: true, force: true });
      renameSync(payloadDir, previous);
      moved = true;
    }
    log(`dsh: copying ${bundleDir} → ${payloadDir}`);
    cpSync(bundleDir, payloadDir, { recursive: true, force: true, dereference: true });
    if (!hasDsh(payloadDir)) throw new Error(`the copy has no ${join(...DSH_BIN)}`);
    const { core } = versionsOf(payloadDir);
    log(moved ? `dsh: ${core || "?"} copied (the copy it replaced is at ${previous})` : `dsh: ${core || "?"} copied out of the bundle`);
    return true;
  } catch (exc) {
    log(`dsh: could not copy the core to ${payloadDir}: ${exc instanceof Error ? exc.message : String(exc)}`);
    if (moved) {
      try {
        rmSync(payloadDir, { recursive: true, force: true });
        renameSync(previous, payloadDir);
        log(`dsh: ${previous} put back`);
      } catch (restore) {
        log(`dsh: could not put ${previous} back: ${restore instanceof Error ? restore.message : String(restore)}`);
      }
    }
    // the old copy restored is a core to run; a first run that failed leaves none
    return hasDsh(payloadDir);
  }
}

/**
 * The dsh root to run from: the payload when it can be used (made now on the first run, or
 * replaced when the bundle carries a newer core), the bundle otherwise.
 */
export function resolveDshRoot(options: DshRootOptions): DshPlace {
  const { payloadDir, bundleDir, log } = options;
  const bundle = hasDsh(bundleDir);
  const payload = hasDsh(payloadDir);

  if (!payload) {
    // nothing in the payload yet, or a copy the Host cannot start from
    if (!bundle) return { dir: bundleDir, from: "bundle" };
    return placeCore(options) ? { dir: payloadDir, from: "payload" } : { dir: bundleDir, from: "bundle" };
  }
  if (!bundle) return { dir: payloadDir, from: "payload" };

  if (!bundleIsNewer(versionsOf(payloadDir), versionsOf(bundleDir))) return { dir: payloadDir, from: "payload" };
  const copy = versionsOf(payloadDir);
  const shipped = versionsOf(bundleDir);
  log(`dsh: the bundle carries ${shipped.core || "?"}/${shipped.bundle || "?"}, the payload ${copy.core || "?"}/${copy.bundle || "?"}`);
  return placeCore(options) ? { dir: payloadDir, from: "payload" } : { dir: bundleDir, from: "bundle" };
}
