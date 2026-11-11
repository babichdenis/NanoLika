import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { runInNewContext } from 'node:vm';

// Execute the real handlers in isolation so no browser session or real API key is used.
const source = await readFile(new URL('../nanomuse_cloud/web_client/account/account.js', import.meta.url), 'utf8');
function section(start, end) {
  const from = source.indexOf(start);
  const to = source.indexOf(end, from);
  assert.ok(from >= 0 && to > from, 'account handler must exist');
  return source.slice(from, to);
}
const handlers = [
  section('async function api(', '\nfunction row('),
  section('async function loadApiKeys(', '\nasync function loadProviders('),
  section("if($('create-api-key'))$('create-api-key').onclick=", "\nif($('add-provider'))"),
].join('\n');

async function createKey(postStatus, listStatus) {
  const elements = {};
  const element = id => elements[id] ??= { value: '', hidden: false, textContent: '', disabled: false };
  const requests = [];
  const context = {
    $: element,
    lang: 'ru',
    session: { key: 'test-session' },
    T: { apiCreated: 'Ключ создан.', apiError: 'Ошибка', apiBalance: 'Баланс', unlimited: 'Не ограничен' },
    rub: () => '',
    renderApiKeys: () => {},
    fetch: async (path, options) => {
      requests.push({ path, options });
      const status = options.method === 'POST' ? postStatus : listStatus;
      return {
        status,
        ok: status < 400,
        json: async () => status >= 400 ? { error: {} }
          : options.method === 'POST' ? { api_key: 'test-key' } : { keys: [], balance: {} },
      };
    },
  };
  runInNewContext(handlers, context);
  await element('create-api-key').onclick();
  return { elements, requests };
}

for (const status of [401, 500]) {
  test(`a created key remains visible when refreshing the list returns ${status}`, async () => {
    const { elements, requests } = await createKey(200, status);
    assert.equal(elements['new-api-key'].hidden, false);
    assert.equal(elements['new-api-key-value'].textContent, 'test-key');
    assert.match(elements['api-key-status'].textContent, /Ключ создан/);
    assert.match(elements['api-key-status'].textContent, /после копирования/);
    assert.notEqual(elements['private-content']?.hidden, true);
    assert.equal(requests.filter(request => request.options.method === 'POST').length, 1);
    assert.equal('preserveContentOnUnauthorized' in requests[1].options, false);
  });
}

test('a successful creation and refresh shows the key and success message', async () => {
  const { elements } = await createKey(200, 200);
  assert.equal(elements['new-api-key-value'].textContent, 'test-key');
  assert.equal(elements['api-key-status'].textContent, 'Ключ создан.');
});

test('a failed creation reports failure without trying to refresh the list', async () => {
  const { elements, requests } = await createKey(500, 200);
  assert.equal(elements['api-key-status'].textContent, 'Ошибка');
  assert.equal(requests.length, 1);
});
