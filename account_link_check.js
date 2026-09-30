// Run with node account_link_check.js. No browser, framework or external service.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('static/account.html', 'utf8').match(/<script>([\s\S]*?)<\/script>/)[1];

async function check(status) {
  const saved = new Map(), elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, {classList: {add() {}, toggle() {}}});
    return elements.get(id);
  };
  vm.runInNewContext(source, {
    document: {getElementById: element},
    location: {hash: '#token=invalid-link-check'}, history: {replaceState() {}},
    sessionStorage: {getItem: k => saved.get(k), setItem: (k,v) => saved.set(k,v), removeItem: k => saved.delete(k)},
    URLSearchParams, FormData,
    fetch: async () => ({ok: false, status, json: async () => ({detail: 'link check failed'})}),
  });
  await new Promise(setImmediate);
  assert.equal(saved.has('donna-account-link'), ![400, 410].includes(status));
  assert.equal(element('message').textContent, 'link check failed');
}

(async () => {
  await check(400);
  await check(410);
  await check(503);
  console.log('account link ok: expired link releases redirect; temporary failure retains link for retry');
})().catch(error => {console.error(error); process.exitCode = 1;});
