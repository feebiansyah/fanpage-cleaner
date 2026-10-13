const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class Element {
  constructor() {
    this.value = ''; this.disabled = false; this.textContent = '';
    this.dataset = {}; this.options = []; this.events = {};
  }
  addEventListener(event, handler) { this.events[event] = handler; }
  setAttribute() {}
  replaceChildren() { this.options = []; }
  appendChild(child) { this.options.push(child); }
  focus() { this.focused = true; }
}

function setup(responses) {
  const ids = ['facebook-token', 'connect', 'disconnect', 'fanpage', 'page-count', 'availability', 'operation-status'];
  const elements = Object.fromEntries(ids.map(id => [id, new Element()]));
  const requests = [];
  const timers = [];
  const context = {
    document: {getElementById: id => elements[id], createElement: () => new Element()},
    fetch: async (path, options) => {
      requests.push({path, options});
      const data = responses.shift();
      if (typeof data === 'function') return data();
      return {ok: true, json: async () => data};
    },
    setTimeout: (fn, delay) => { timers.push({fn, delay}); return timers.length; },
    clearTimeout: () => {}, Date,
  };
  vm.runInNewContext(fs.readFileSync('static/app.js', 'utf8'), context);
  return {elements, requests, timers};
}

const disconnected = {connected: false, state: 'disconnected', pages: [], count: 0, message: 'Belum terhubung'};
const connected = {connected: true, pages: [{id: '11', name: '<img onerror=alert(1)>'}], count: 1,
  message: 'Fanpage berhasil dimuat. Load Posts belum tersedia pada tahap ini.'};
const tick = () => new Promise(resolve => setImmediate(resolve));

test('bootstrap enables connect but keeps empty Fanpage disabled', async () => {
  const {elements, requests} = setup([disconnected]);
  await tick();
  assert.equal(elements.connect.disabled, false);
  assert.equal(elements.fanpage.disabled, true);
  assert.equal(elements.disconnect.disabled, true);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].path, '/api/session');
});

test('empty successful result keeps dropdown disabled after loading finishes', async () => {
  const {elements} = setup([disconnected, {connected: true, pages: [], count: 0, message: 'Tidak ada Fanpage'}]);
  await tick();
  elements['facebook-token'].value = 'dummy';
  await elements.connect.events.click();
  assert.equal(elements.fanpage.disabled, true);
  assert.equal(elements['availability'].textContent, 'Tidak ada Fanpage');
  assert.equal(elements.disconnect.disabled, false);
});

test('connect submits JSON only, suppresses double clicks and clears token', async () => {
  let release;
  const {elements, requests} = setup([disconnected, () => new Promise(resolve => {
    release = () => resolve({ok: true, json: async () => connected});
  })]);
  await tick();
  elements['facebook-token'].value = 'dummy-user-secret';
  const first = elements.connect.events.click();
  await elements.connect.events.click();
  assert.equal(requests.length, 2);
  assert.equal(elements.connect.disabled, true);
  assert.equal(elements['facebook-token'].value, '');
  release();
  await first;
  assert.equal(requests[1].path, '/api/connect');
  assert.deepEqual(JSON.parse(requests[1].options.body), {token: 'dummy-user-secret'});
  assert.equal(elements.fanpage.options[0].textContent, '<img onerror=alert(1)> (11)');
  assert.equal(elements.connect.disabled, false);
  assert.equal(elements.disconnect.disabled, false);
});

test('refresh uses local status and disconnect clears Fanpage', async () => {
  const {elements, requests} = setup([connected, disconnected]);
  await tick();
  assert.equal(elements.fanpage.options[0].value, '11');
  await elements.disconnect.events.click();
  assert.equal(requests.length, 2);
  assert.deepEqual(requests.map(r => r.path), ['/api/session', '/api/disconnect']);
  assert.equal(elements.fanpage.options[0].value, '');
  assert.equal(elements.fanpage.disabled, true);
  assert.equal(elements.disconnect.disabled, true);
});

test('failed reconnect restores cached session and button without reflecting token', async () => {
  const {elements, requests} = setup([connected,
    () => ({ok: false, json: async () => ({error: {message: 'Token tidak valid', code: 'token_invalid'}})}), connected]);
  await tick();
  elements['facebook-token'].value = 'dummy-secret';
  await elements.connect.events.click();
  assert.deepEqual(requests.map(r => r.path), ['/api/session', '/api/connect', '/api/session']);
  assert.equal(elements.fanpage.options[0].value, '11');
  assert.equal(elements.connect.disabled, false);
  assert.equal(elements['facebook-token'].value, '');
  assert.equal(elements.availability.textContent, 'Token tidak valid');
});

test('network failures use Indonesian status and preserve local cached session', async () => {
  const {elements} = setup([connected, () => { throw new Error('Failed to fetch'); }, connected]);
  await tick();
  elements['facebook-token'].value = 'dummy';
  await elements.connect.events.click();
  assert.equal(elements.availability.textContent, 'Gangguan jaringan ke backend lokal. Muat ulang atau coba lagi nanti.');
  assert.equal(elements.connect.disabled, false);
  assert.equal(elements.fanpage.options[0].value, '11');
});

test('cooldown disables connect without polling or automatically reconnecting', async () => {
  const {elements, requests, timers} = setup([disconnected,
    () => ({ok: false, json: async () => ({error: {message: 'Cooldown', code: 'cooldown', retry_after: 30}})}), disconnected]);
  await tick();
  elements['facebook-token'].value = 'dummy';
  await elements.connect.events.click();
  assert.equal(elements.connect.disabled, true);
  const before = requests.length;
  await elements.connect.events.click();
  timers[0].fn();
  assert.equal(requests.length, before);
  assert.equal(timers[0].delay, 30050);
});

test('bootstrap failure keeps connection disabled', async () => {
  const {elements} = setup([() => { throw new Error('Network'); }]);
  await tick();
  assert.equal(elements.connect.disabled, true);
  assert.equal(elements.disconnect.disabled, true);
  assert.match(elements.availability.textContent, /Muat ulang/);
});

test('lost disconnect response reconciles backend state locally', async () => {
  const {elements, requests} = setup([connected, () => { throw new Error('Connection lost after server deleted session'); }, disconnected]);
  await tick();
  await elements.disconnect.events.click();
  assert.deepEqual(requests.map(r => r.path), ['/api/session', '/api/disconnect', '/api/session']);
  assert.equal(elements.fanpage.options[0].value, '');
  assert.equal(elements.fanpage.disabled, true);
  assert.equal(elements.disconnect.disabled, true);
  assert.equal(elements.availability.textContent, 'Gangguan jaringan ke backend lokal. Muat ulang atau coba lagi nanti.');
});
