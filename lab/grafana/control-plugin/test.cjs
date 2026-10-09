const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { test, afterEach } = require('node:test');
const { JSDOM } = require('jsdom');
const React = require('react');
const { createRoot } = require('react-dom/client');

const dom = new JSDOM('<!doctype html><div id="root"></div>');
global.window = dom.window;
global.document = dom.window.document;
global.IS_REACT_ACT_ENVIRONMENT = true;
const intervals = new Map();
let timerId = 0;
let root, selected, role, current, failure, calls, postImplementation, readImplementation;
const copy = value => JSON.parse(JSON.stringify(value));
const api = {
  get: async url => { calls.push({ method: 'GET', url }); if (failure) { throw failure; } return readImplementation ? readImplementation() : copy(current); },
  post: async (url, body) => {
    calls.push({ method: 'POST', url, body: copy(body) });
    if (postImplementation) { return postImplementation(url, body); }
    if (url.endsWith('/configuration')) { Object.assign(current, body); }
    else { current.pending = null; }
    return copy(current);
  },
};
const modules = {
  react: React,
  '@grafana/data': { PanelPlugin: class { constructor(component) { this.component = component; } } },
  '@grafana/runtime': {
    config: { bootData: { user: { get orgRole() { return role; } } } },
    getBackendSrv: () => api,
    getTemplateSrv: () => ({ replace: () => selected }),
  },
};
let exported;
vm.runInNewContext(fs.readFileSync(__dirname + '/dist/panel/module.js', 'utf8'), {
  define: (names, factory) => {
    exported = {};
    factory(...names.map(name => name === 'exports' ? exported : name === 'require' ? () => {} : modules[name]));
  },
  setInterval: (callback, delay) => { intervals.set(++timerId, { callback, delay }); return timerId; },
  clearInterval: id => intervals.delete(id),
  Date,
});
const props = { width: 1000, height: 280, data: { series: [{ fields: [{ name: 'live_stream', values: ['live-active'] }] }] } };
async function mount(overrides = {}) {
  selected = '__latest_live__'; role = 'Admin'; failure = null; calls = []; postImplementation = null; readImplementation = null;
  current = { mode: 'manual', profile: 'voice', confirmed_path: 'wan1', observing_native_qbr: false,
    network_available: true, apply_inflight: null, pending: null, ...overrides };
  root = createRoot(document.getElementById('root'));
  await React.act(async () => root.render(React.createElement(exported.Controls, props)));
}
async function rerender() { await React.act(async () => root.render(React.createElement(exported.Controls, { ...props }))); }
async function click(label) {
  const button = [...document.querySelectorAll('button')].find(item => item.textContent === label);
  assert.ok(button, 'Button exists: ' + label);
  await React.act(async () => button.click());
}
function disabled(label) { return [...document.querySelectorAll('button')].find(item => item.textContent === label).disabled; }
function writes() { return calls.filter(item => item.method === 'POST'); }
const recommendation = () => ({ id: 'safe-id', source: 'wan1', target: 'wan2', reason: 'SLA violation', expires_at: Date.now() / 1000 + 30 });
afterEach(async () => { if (root) { await React.act(async () => root.unmount()); root = null; } assert.equal(intervals.size, 0); });

test('Auto and Manual buttons use authenticated same-origin proxy configuration', async () => {
  await mount(); await click('Auto');
  assert.deepEqual(writes(), [{ method: 'POST', url: '/api/plugin-proxy/aisdwan-controls-app/configuration', body: { mode: 'auto' } }]);
  assert.match(document.body.textContent, /Automatic decisions; no approval required/);
  await click('Manual'); assert.deepEqual(writes()[1].body, { mode: 'manual' });
});
test('profile selection changes only the profile', async () => {
  await mount();
  await React.act(async () => {
    const select = document.querySelector('select'); select.value = 'bulk';
    select.dispatchEvent(new window.Event('change', { bubbles: true }));
  });
  await click('Apply profile'); assert.deepEqual(writes()[0].body, { profile: 'bulk' });
});
test('Approve and Reject send the displayed pending identifier and never replay it', async () => {
  for (const action of ['Approve', 'Reject']) {
    if (root) { await React.act(async () => root.unmount()); root = null; }
    await mount({ pending: recommendation() }); assert.equal(disabled(action), false);
    await click(action); await click(action);
    assert.equal(writes().length, 1);
    assert.equal(writes()[0].url, '/api/plugin-proxy/aisdwan-controls-app/recommendations/safe-id');
    assert.deepEqual(writes()[0].body, { action: action.toLowerCase() });
    assert.equal(disabled('Approve'), true); assert.equal(disabled('Reject'), true);
  }
});
test('empty, expired, automatic and inflight recommendations cannot be reviewed', async () => {
  for (const overrides of [{}, { pending: { ...recommendation(), expires_at: Date.now() / 1000 - 1 } },
    { pending: recommendation(), mode: 'auto' }, { pending: recommendation(), apply_inflight: { target: 'wan2' } }]) {
    if (root) { await React.act(async () => root.unmount()); root = null; }
    await mount(overrides); await click('Approve'); await click('Reject'); assert.equal(writes().length, 0);
  }
});
test('archive, Viewer and observer views cannot mutate settings', async () => {
  await mount(); selected = 'archived-run'; await rerender(); await click('Auto'); assert.equal(writes().length, 0);
  selected = 'live-active'; await rerender(); assert.equal(disabled('Auto'), false);
  role = 'Viewer'; await rerender(); await click('Auto'); assert.equal(writes().length, 0);
  role = 'Admin'; current.observing_native_qbr = true;
  await React.act(async () => { await [...intervals.values()].find(item => item.delay === 5000).callback(); });
  await click('Auto'); assert.equal(writes().length, 0);
});
test('API authentication failures pause controls and display the error', async () => {
  await mount(); failure = { status: 401, data: { detail: 'Sign in required' } };
  await React.act(async () => { await [...intervals.values()].find(item => item.delay === 5000).callback(); });
  assert.match(document.querySelector('[role="alert"]').textContent, /Sign in required/);
  await click('Auto'); assert.equal(writes().length, 0);
});
test('rapid duplicate clicks submit one request and asynchronous apply waits for readback', async () => {
  await mount({ pending: recommendation() });
  let release;
  postImplementation = () => new Promise(resolve => { release = resolve; });
  const button = [...document.querySelectorAll('button')].find(item => item.textContent === 'Approve');
  await React.act(async () => { button.click(); button.click(); }); assert.equal(writes().length, 1);
  await React.act(async () => release({ ...current, pending: null, apply_inflight: { target: 'wan2' } }));
  assert.match(document.body.textContent, /waiting for controller verification/); assert.equal(disabled('Auto'), true);
});
test('an older state poll cannot overwrite a completed configuration change', async () => {
  await mount();
  const oldState = copy(current);
  let releaseRead;
  readImplementation = () => new Promise(resolve => { releaseRead = resolve; });
  let reading;
  await React.act(async () => { reading = [...intervals.values()].find(item => item.delay === 5000).callback(); });
  await click('Auto');
  await React.act(async () => { releaseRead(oldState); await reading; });
  assert.equal(disabled('Auto'), true);
  assert.match(document.body.textContent, /Automatic decisions; no approval required/);
});
