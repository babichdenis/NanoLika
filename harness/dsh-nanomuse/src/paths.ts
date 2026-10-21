/**
 * Where the plugin keeps its own state: `$DSH_HOME/nanolika`.
 *
 * It was `$DSH_HOME/nanomuse` while the app was called nanoMuse. A folder of that name is
 * taken over once, on the first start after the rename, so the account, the rooms, the
 * profile and the drawn faces of an existing install carry on without anyone copying files.
 */
import { existsSync, renameSync } from "node:fs";
import { join } from "node:path";

/** `$DSH_HOME`, or `~/.dsh`: the same rule the launcher applies. */
export function dshHome(): string {
  const configured = process.env.DSH_HOME;
  if (configured) return configured;
  return join(process.env.HOME ?? process.env.USERPROFILE ?? ".", ".dsh");
}

/** The old folder's name, kept only to take it over. */
export const LEGACY_STATE_DIR = "nanomuse";

/** The folder this plugin keeps its files in. */
export const STATE_DIR = "nanolika";

let resolved: string | undefined;

/** `$DSH_HOME/nanolika`, with `$DSH_HOME/nanomuse` renamed into it the first time. */
export function stateDir(): string {
  if (resolved) return resolved;
  const home = dshHome();
  const current = join(home, STATE_DIR);
  const legacy = join(home, LEGACY_STATE_DIR);
  if (!existsSync(current) && existsSync(legacy)) {
    try {
      renameSync(legacy, current);
    } catch {
      // a read-only home or a race with another start: keep the old folder, it still works
      return (resolved = legacy);
    }
  }
  return (resolved = current);
}

/** A file of the state folder. */
export function statePath(...parts: string[]): string {
  return join(stateDir(), ...parts);
}
