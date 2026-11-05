import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import { test } from "node:test";

const desktop = fileURLToPath(new URL("../", import.meta.url));

function findFile(root, name, accept = () => true) {
  if (!existsSync(root)) return undefined;
  for (const entry of readdirSync(root, { withFileTypes: true })) {
    const path = join(root, entry.name);
    if (entry.isFile() && entry.name === name && accept(path)) return path;
    if (entry.isDirectory()) {
      const found = findFile(path, name, accept);
      if (found) return found;
    }
  }
  return undefined;
}

// Paths are supplied by the fixture, never by an installer command line.
const nsisString = (s) => s.replace(/\$/g, "$$$$").replace(/"/g, '$\\"');

test("the real NSIS cleanup retains credentials during replacement and signs out on uninstall", {
  skip: process.platform !== "win32" ? "runs the Windows NSIS binary" : false,
}, (t) => {
  const cache = join(process.env.LOCALAPPDATA, "electron-builder", "Cache");
  const compiler = findFile(join(cache, "nsis-3.0.4.1"), "makensis.exe");
  const plugin = findFile(join(cache, "nsis-resources-3.4.1"), "StdUtils.dll", (path) => path.includes("x86-unicode"));
  if (!compiler || !plugin) {
    if (process.env.NANOLIKA_REQUIRE_NSIS_TESTS === "1") assert.fail("NSIS must be cached by the package step");
    t.skip("package once to cache NSIS, then rerun this test");
    return;
  }
  const root = mkdtempSync(join(process.env.NANOLIKA_NSIS_TEST_ROOT || tmpdir(), "nanolika-profile-fixture-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const profile = join(root, "profile");
  const source = join(root, "fixture.nsi");
  const exe = join(root, "fixture.exe");
  const header = resolve(desktop, "node_modules/app-builder-lib/templates/nsis/include/StdUtils.nsh");
  // Use NSIS's real command-line parser, matching electron-builder's generated
  // isUpdated adapter. The production cleanup macro is included and executed.
  writeFileSync(source, `Unicode true
RequestExecutionLevel user
SilentInstall silent
Name "NanoLika profile fixture"
OutFile "${nsisString(exe)}"
!include "LogicLib.nsh"
!addplugindir /x86-unicode "${nsisString(dirname(plugin))}"
!include "${nsisString(header)}"
!macro _isUpdated _a _b _t _f
  \${StdUtils.TestParameter} $R9 "updated"
  StrCmp "$R9" "true" \`\${_t}\` \`\${_f}\`
!macroend
!define isUpdated \`"" isUpdated ""\`
!include "${nsisString(join(desktop, "resources/installer.nsh"))}"
Section
  !insertmacro nanoLikaRemoveAccountFiles "${nsisString(profile)}"
  SetErrorLevel 0
SectionEnd
`, "utf8");
  const built = spawnSync(compiler, ["/V2", "/INPUTCHARSET", "UTF8", source], { encoding: "utf8", timeout: 30_000 });
  assert.equal(built.status, 0, `${built.error || ""}\n${built.stdout}\n${built.stderr}`);
  const account = join(profile, ".nanolika/desktop/nanolika/cloud.json");
  const keys = join(profile, ".nanolika/desktop/.credentials.yaml");
  const chat = join(profile, ".nanolika/desktop/nanolika/chats/keep.txt");
  const fixtures = new Map([[account, '{"fixture":"session"}\r\n'], [keys, "fixture: ключ\r\n"], [chat, "fixture chat"]]);
  for (const args of [["--updated"], ["--updated", "/S"], ["--updated", "--keep-shortcuts"], []]) {
    for (const [path, data] of fixtures) { mkdirSync(dirname(path), { recursive: true }); writeFileSync(path, data); }
    const result = spawnSync(exe, args, { encoding: "utf8", timeout: 30_000 });
    assert.equal(result.status, 0, `${args}: ${result.error || ""} ${result.stderr}`);
    assert.equal(readFileSync(chat, "utf8"), fixtures.get(chat), "chat data survives both operations");
    for (const path of [account, keys]) {
      if (args.includes("--updated")) assert.equal(readFileSync(path, "utf8"), fixtures.get(path), `${args}: credentials remain byte-for-byte`);
      else assert.equal(existsSync(path), false, "a real uninstall signs out");
    }
  }
  assert.equal(spawnSync(exe, [], { timeout: 30_000 }).status, 0, "missing credential files are harmless");
});
