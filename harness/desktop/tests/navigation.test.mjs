// Where the main window may navigate (src/navigation.ts): the Host's origin compared as a
// parsed URL, the shell's own pages from disk, http(s) to the browser, the rest dropped.
import assert from "node:assert/strict";
import { test } from "node:test";
import { HEAL_INTERVAL_MS, mayHeal, navigationVerdict, needsHostUrl, ownPage, sameOrigin } from "../out/navigation.js";

const HOST = "http://127.0.0.1:38421/?token=abc";
const OWN = "file:///opt/NanoLika/resources/app.asar/resources";

test("the Host's origin is a parsed origin, not a prefix of the address", () => {
  assert.equal(sameOrigin("http://127.0.0.1:38421/", HOST), true);
  assert.equal(sameOrigin("http://127.0.0.1:38421/sessions/1#x", HOST), true);
  // the Host's origin as the user-info part of another host passed the old prefix test
  assert.equal(sameOrigin("http://127.0.0.1:38421@evil.example/", HOST), false);
  assert.equal(sameOrigin("http://127.0.0.1:384210/", HOST), false);
  assert.equal(sameOrigin("https://127.0.0.1:38421/", HOST), false);
  assert.equal(sameOrigin("http://localhost:38421/", HOST), false);
  assert.equal(sameOrigin("not a url", HOST), false);
  assert.equal(sameOrigin("http://127.0.0.1:38421/", null), false, "nothing is the Host's before it is up");
});

test("only the shell's own pages come from disk", () => {
  assert.equal(ownPage(`${OWN}/loading.html?lang=zh`, OWN), true);
  assert.equal(ownPage("file:///etc/passwd", OWN), false);
  assert.equal(ownPage(`${OWN}-other/x.html`, OWN), false, "a sibling directory with the same prefix is not ours");
  assert.equal(ownPage("http://127.0.0.1:38421/loading.html", OWN), false);
});

test("the verdict: stay, open outside, or drop", () => {
  assert.equal(navigationVerdict("http://127.0.0.1:38421/x", HOST, OWN), "allow");
  assert.equal(navigationVerdict(`${OWN}/loading.html`, HOST, OWN), "allow");
  assert.equal(navigationVerdict("https://nanomuse.cn/docs/desktop", HOST, OWN), "external");
  assert.equal(navigationVerdict("http://127.0.0.1:38421@evil.example/", HOST, OWN), "external", "to the browser, never into the window");
  assert.equal(navigationVerdict("javascript:alert(1)", HOST, OWN), "deny");
  assert.equal(navigationVerdict("file:///etc/passwd", HOST, OWN), "deny");
  assert.equal(navigationVerdict("data:text/html,<b>x</b>", HOST, OWN), "deny");
});

// A window that lost its token (src/navigation.ts): the Host's 401 sentence on the Host's
// origin is repaired by loading the URL that carries the token again.
test("a page without the token is recognised, and the shell's own pages never are", () => {
  const sentence = "dsh web authentication required; reopen the URL printed by dsh web.";
  assert.equal(needsHostUrl("http://127.0.0.1:38421/", HOST, sentence), true);
  // an empty document is the web app before it mounts, never a reason to reload it
  assert.equal(needsHostUrl("http://127.0.0.1:38421/sessions/1", HOST, "  \n "), false);
  assert.equal(needsHostUrl("http://127.0.0.1:38421/", HOST, ""), false);
  // a page that did come up is left alone
  assert.equal(needsHostUrl("http://127.0.0.1:38421/c/abc", HOST, "NanoLika\nNew chat"), false);
  // the shell's own splash is a file: URL, and another origin is somebody else's page
  assert.equal(needsHostUrl(`${OWN}/loading.html`, HOST, ""), false);
  assert.equal(needsHostUrl("https://cloud.nanolika.ru/app/", HOST, ""), false);
  assert.equal(needsHostUrl("http://127.0.0.1:38421/", null, ""), false, "nothing is the Host's before it is up");
});

test("one repair in ten seconds, so a refusing Host cannot set the window spinning", () => {
  assert.equal(mayHeal(0, HEAL_INTERVAL_MS), true);
  assert.equal(mayHeal(0, 1), false);
  assert.equal(mayHeal(1_000, 1_000 + HEAL_INTERVAL_MS - 1), false);
  assert.equal(mayHeal(1_000, 1_000 + HEAL_INTERVAL_MS), true);
});
