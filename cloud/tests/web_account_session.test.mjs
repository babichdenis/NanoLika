import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { runInNewContext } from 'node:vm';

const source = await readFile(new URL('../nanomuse_cloud/web_client/account/account.js', import.meta.url), 'utf8');
function section(start, end) {
  const from = source.indexOf(start);
  const to = source.indexOf(end, from);
  assert.ok(from >= 0 && to > from, 'account handler must exist');
  return source.slice(from, to);
}
const handlers = [
  section('let signInRedirectStarted=', '\nfunction row('),
  section('async function load(){', "\n$('refresh').onclick=load;"),
].join('\n');

function harness(unauthorized = []) {
  const elements = {};
  const redirects = [];
  const $ = id => elements[id] ??= { hidden: false, textContent: '', disabled: false, replaceChildren() {} };
  const data = {
    '/v1/me': { account: { id: 'test-owner' } },
    '/v1/devices': { devices: [] },
    '/v1/me/sessions': { sessions: [] },
    '/v1/config': {},
    '/v1/link/channels': [],
  };
  const context = {
    $, session: { key: 'test-session', owner: 'test-owner' }, me: null,
    lang: 'ru', T: { loading: 'Загрузка', unavailable: 'Недоступно', error: 'Ошибка' },
    minPassword: 8, connectionButtons: new Map(),
    location: { href: 'https://cloud.nanolika.ru/web/account/?embed=1#api', replace: url => redirects.push(url) },
    renderMe() {}, loadUsage: async () => {}, loadApiKeys: async () => {},
    loadProviders: async () => {}, autopay: { load() {} }, empty() {}, groupWebDevices: value => value,
    fetch: async path => {
      const status = unauthorized.includes(path) ? 401 : 200;
      return { status, ok: status === 200, json: async () => data[path] || {} };
    },
  };
  runInNewContext(handlers + '\nthis.loadAccount = load; this.accountApi = api;', context);
  return { context, elements, redirects, load: () => context.loadAccount() };
}

test('expiry during parallel account loading opens sign-in with the original destination', async () => {
  const app = harness(['/v1/devices', '/v1/me/sessions']);
  await app.load();
  assert.equal(app.redirects.length, 1);
  const url = new URL(app.redirects[0], 'https://cloud.nanolika.ru');
  assert.equal(url.pathname, '/app/');
  assert.equal(url.searchParams.get('signin'), '1');
  assert.equal(url.searchParams.get('next'), app.context.location.href);
  assert.equal(app.elements['private-content'].hidden, true);
});

test('a valid account session shows the cabinet without redirecting', async () => {
  const app = harness();
  await app.load();
  assert.equal(app.redirects.length, 0);
  assert.equal(app.elements['private-content'].hidden, false);
});

test('an expired sign-out request does not preempt the sign-out flow', async () => {
  const app = harness(['/v1/auth/sign-out']);
  await assert.rejects(app.context.accountApi('/v1/auth/sign-out'), error => error.status === 401);
  assert.equal(app.redirects.length, 0);
});
