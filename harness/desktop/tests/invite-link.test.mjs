// The share link's code (src/invite-link.ts): what nanolika.ru's "Открыть в Лике" button
// hands the app, and what the app is right to refuse.
import assert from "node:assert/strict";
import { test } from "node:test";
import { inviteCode, inviteFromArgv, inviteFromUrl } from "../out/invite-link.js";

test("the code is the last part of the path", () => {
  assert.equal(inviteFromUrl("nanolika://invite/ABCD2345"), "ABCD2345");
  assert.equal(inviteFromUrl("nanolika://invite/ABCD2345/"), "ABCD2345");
  assert.equal(inviteFromUrl("NANOLIKA://invite/ABCD2345"), "ABCD2345", "the scheme's case is not the code's");
  assert.equal(inviteFromUrl("nanolika://invite/abcd2345"), "ABCD2345", "the app uppercases as it types");
});

test("a query parameter counts too, as the site may send it", () => {
  assert.equal(inviteFromUrl("nanolika://invite?code=ABCD2345"), "ABCD2345");
  assert.equal(inviteFromUrl("nanolika://invite?invite=ABCD2345"), "ABCD2345");
  assert.equal(inviteFromUrl("nanolika://?code=ABCD2345"), "ABCD2345", "no host at all");
});

test("the short form without slashes is read, not dropped", () => {
  assert.equal(inviteFromUrl("nanolika:ABCD2345"), "ABCD2345");
  assert.equal(inviteFromUrl("nanolika://ABCD2345"), "ABCD2345");
});

test("another scheme, another address: nothing", () => {
  assert.equal(inviteFromUrl("https://nanolika.ru/?invite=ABCD2345"), "");
  assert.equal(inviteFromUrl("nanolika://settings/ABCD2345"), "ABCD2345", "the code is still the path's last part");
  assert.equal(inviteFromUrl("nanolika://invite/"), "");
  assert.equal(inviteFromUrl(""), "");
  assert.equal(inviteFromUrl("   "), "");
});

test("what is not a code is refused rather than guessed at", () => {
  assert.equal(inviteCode("ABCD"), "ABCD", "four is the shortest the relay's own codes are not, a friend may type one");
  assert.equal(inviteCode("ABC"), "", "three is too short");
  assert.equal(inviteCode("ABCDEFGHJKMNPQRSTUVWXYZ2345678901"), "", "33 is too long");
  assert.equal(inviteCode("ABCD 2345"), "", "a space means it is a sentence");
  assert.equal(inviteCode("../../../etc/passwd"), "", "a path is not a code");
  assert.equal(inviteCode("ABCD2345\n"), "ABCD2345", "the end of a line is not part of it");
  assert.equal(inviteCode("#ABCD2345"), "ABCD2345", "a fragment marker is not part of it");
});

test("the command line carries one link among its arguments", () => {
  assert.equal(inviteFromArgv(["/Applications/NanoLika.app/Contents/MacOS/NanoLika"]), "");
  assert.equal(
    inviteFromArgv(["/opt/NanoLika", "--flag", "nanolika://invite/ABCD2345"]),
    "ABCD2345",
    "Windows and Linux hand the link over as an argument",
  );
  assert.equal(inviteFromArgv(["nanolika://invite/ABCD2345", "nanolika://invite/ZZZZ9999"]), "ABCD2345", "the first one wins");
});
