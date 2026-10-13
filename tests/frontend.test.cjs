const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class Element {
  constructor(tag = 'div') {
    this.tag = tag;
    this.value = ''; this.disabled = false; this.textContent = '';
    this.dataset = {}; this.options = []; this.events = {};
    this.children = this.options; this.checked = false; this.attributes = {};
  }
  addEventListener(event, handler) { this.events[event] = handler; }
  setAttribute(name, value) { this.attributes[name] = value; }
  removeAttribute(name) { delete this.attributes[name]; }
  replaceChildren(...items) { this.options = []; this.children = this.options; for (const item of items) this.appendChild(item); }
  appendChild(child) { this.options.push(child); if (this.tag === 'select' && this.options.length === 1) this.value = child.value; }
  focus() { this.focused = true; }
}

function setup(responses) {
  const ids = ['facebook-token', 'connect', 'disconnect', 'fanpage', 'page-count', 'availability', 'operation-status',
    'load-posts', 'max-posts', 'content-list', 'select-all', 'selected-count', 'load-status', 'audit-warning', 'local-pagination', 'load-progress'];
  const elements = Object.fromEntries(ids.map(id => [id, new Element(id === 'fanpage' ? 'select' : 'div')]));
  elements['max-posts'].value = '100';
  const requests = [];
  const timers = [];
  const context = {
    document: {getElementById: id => elements[id], createElement: tag => new Element(tag)},
    fetch: async (path, options) => {
      requests.push({path, options});
      const data = responses.shift();
      if (typeof data === 'function') return data();
      return {ok: true, json: async () => data};
    },
    setTimeout: (fn, delay) => { timers.push({fn, delay}); return timers.length; },
    clearTimeout: () => {}, Date, URL,
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

function audit(size = 120, extra = {}) {
  return {page_id: '11', state: 'limit_reached', found: size, displayed: size, bulk_selectable: true,
    warning: 'Audit kandidat; N terbaru global belum terjamin', cache_ttl_seconds: 300,
    items: Array.from({length: size}, (_, i) => ({key: 'published_posts:11_' + i, object_id: '11_' + i,
      kind: 'Foto', published_at: '2026-10-12T00:00:00+00:00', published_wib: '12/10/2026 07:00:00 WIB',
      text: '<script>tidak dijalankan</script>', thumbnail: null, permalink: 'https://www.facebook.com/11/posts/' + i, selectable: true})), ...extra};
}

test('load active only with selected Page and valid limit; submits no token', async () => {
  const {elements, requests} = setup([connected, audit(20)]);
  await tick();
  assert.equal(elements['load-posts'].disabled, false);
  elements['max-posts'].value = '0';
  await elements['load-posts'].events.click();
  assert.equal(requests.length, 1);
  elements['max-posts'].value = '20';
  await elements['load-posts'].events.click();
  assert.equal(requests[1].path, '/api/posts/load');
  assert.deepEqual(JSON.parse(requests[1].options.body), {page_id: '11', limit: 20});
  assert.equal(elements['content-list'].children.length, 20);
});

test('local display paging does not fetch; select all covers all loaded results', async () => {
  const {elements, requests} = setup([connected, audit()]);
  await tick();
  await elements['load-posts'].events.click();
  assert.equal(elements['content-list'].children.length, 50);
  elements['select-all'].checked = true;
  elements['select-all'].events.change();
  assert.match(elements['selected-count'].textContent, /120/);
  const nextButton = elements['local-pagination'].children.find(e => e.textContent === 'Berikutnya');
  nextButton.events.click();
  assert.equal(elements['content-list'].children.length, 50);
  assert.equal(requests.length, 2);
  assert.equal(elements['select-all'].checked, true);
});

test('double load suppressed and old Page response ignored after switch', async () => {
  let release;
  const {elements, requests} = setup([connected, () => new Promise(resolve => {
    release = () => resolve({ok: true, json: async () => audit(20)});
  })]);
  await tick();
  const first = elements['load-posts'].events.click();
  await elements['load-posts'].events.click();
  assert.equal(requests.length, 2);
  elements.fanpage.value = '22';
  elements.fanpage.events.change();
  release();
  await first;
  assert.equal(elements['content-list'].children.length, 0);
  assert.equal(elements['select-all'].disabled, true);
});

test('disconnect invalidates late load response', async () => {
  let release;
  const {elements} = setup([connected, () => new Promise(resolve => {
    release = () => resolve({ok: true, json: async () => audit(20)});
  }), disconnected]);
  await tick();
  const first = elements['load-posts'].events.click();
  assert.equal(elements.disconnect.disabled, false);
  await elements.disconnect.events.click();
  release();
  await first;
  assert.equal(elements['content-list'].children.length, 0);
  assert.equal(elements['load-posts'].disabled, true);
});

test('partial disables bulk selection; invalid URL never rendered', async () => {
  const data = audit(1, {state: 'partial', bulk_selectable: false, error: {message: 'Timeout'}});
  data.items[0].thumbnail = 'javascript:alert(1)';
  data.items[0].permalink = 'https://user:pass@www.facebook.com/11';
  const {elements} = setup([connected, data]);
  await tick();
  await elements['load-posts'].events.click();
  assert.equal(elements['select-all'].disabled, true);
  assert.match(elements['load-status'].textContent, /tidak lengkap/i);
  const row = elements['content-list'].children[0];
  assert.equal(row.children.some(child => child.tag === 'img' || child.tag === 'a'), false);
  assert.equal(row.children.some(child => child.textContent === '<script>tidak dijalankan</script>'), true);
});

test('partial rate limit applies cooldown without automatic retry', async () => {
  const {elements, requests} = setup([connected, audit(1, {state: 'partial', bulk_selectable: false,
    error: {message: 'Rate limit', code: 'rate_limited', retry_after: 30}})]);
  await tick();
  await elements['load-posts'].events.click();
  assert.equal(elements['load-posts'].disabled, true);
  assert.equal(elements.connect.disabled, true);
  await elements['load-posts'].events.click();
  assert.equal(requests.length, 2);
});

test('valid Facebook permalink and CDN thumbnail rendered with safety attributes', async () => {
  const data = audit(1);
  data.items[0].thumbnail = 'https://scontent.example.fbcdn.net/photo.jpg';
  const {elements} = setup([connected, data]);
  await tick();
  await elements['load-posts'].events.click();
  const row = elements['content-list'].children[0];
  const image = row.children.find(child => child.tag === 'img');
  const link = row.children.find(child => child.tag === 'a');
  assert.equal(image.loading, 'lazy');
  assert.equal(image.referrerPolicy, 'no-referrer');
  assert.equal(link.href, 'https://www.facebook.com/11/posts/0');
  assert.equal(link.rel, 'noopener noreferrer');
});

test('local session expiry clears audit and disables load without Meta polling', async () => {
  const {elements, requests, timers} = setup([{...connected, session_remaining_seconds: 5}, audit(1)]);
  await tick();
  await elements['load-posts'].events.click();
  timers[0].fn();
  assert.equal(elements['load-posts'].disabled, true);
  assert.equal(elements['content-list'].children.length, 0);
  assert.equal(requests.length, 2);
});
