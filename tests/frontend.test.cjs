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
  showModal() { this.open = true; }
  close() { this.open = false; }
}

function setup(responses) {
  const ids = ['facebook-token', 'connect', 'disconnect', 'fanpage', 'page-count', 'availability', 'operation-status',
    'load-posts', 'max-posts', 'content-list', 'select-all', 'selected-count', 'load-status', 'audit-warning', 'local-pagination', 'load-progress',
    'fanpage-search', 'fanpage-search-status', 'source-diagnostics', 'stop-load', 'dry-run', 'dry-status', 'dry-results', 'dry-pagination',
    'delete-one', 'delete-dialog-title', 'delete-warning', 'delete-manual-text', 'delete-help', 'delete-dialog', 'delete-details', 'delete-manual-check', 'delete-cancel', 'delete-confirm', 'delete-status',
    'delete-diagnosis', 'delete-read-last', 'delete-selected', 'bulk-dialog', 'bulk-details', 'bulk-preview-pagination', 'bulk-pagination', 'bulk-manual-check', 'bulk-cancel', 'bulk-confirm', 'bulk-status', 'bulk-technical', 'bulk-technical-content', 'bulk-results', 'bulk-read', 'access-post', 'access-post-result'];
  const elements = Object.fromEntries(ids.map(id => [id, new Element(id === 'fanpage' ? 'select' : 'div')]));
  elements['max-posts'].value = '100';
  const requests = [];
  const timers = [];
  const clockNow = Date.now();
  class TestDate extends Date { static now() { return clockNow; } }
  const context = {
    document: {getElementById: id => elements[id], createElement: tag => new Element(tag)},
    fetch: async (path, options) => {
      requests.push({path, options});
      const data = responses.shift();
      if (typeof data === 'function') return data();
      return {ok: true, json: async () => data};
    },
    setTimeout: (fn, delay) => { timers.push({fn, delay}); return timers.length; },
    clearTimeout: () => {}, Date: TestDate, URL,
  };
  vm.runInNewContext(fs.readFileSync('static/app.js', 'utf8'), context);
  return {elements, requests, timers};
}

const disconnected = {connected: false, state: 'disconnected', pages: [], count: 0, message: 'Belum terhubung'};
const connected = {connected: true, pages: [{id: '11', name: '<img onerror=alert(1)>'}], count: 1,
  message: 'Fanpage berhasil dimuat. Load Posts belum tersedia pada tahap ini.'};
const tick = () => new Promise(resolve => setImmediate(resolve));

test('current audit Post access probe needs no failed delete or confirmation',async()=>{
  const loaded=audit(1);loaded.items[0].sources=[{source:'published_posts',object_id:'11_0'}];
  const evidence={request_count:5,request_budget:5,permissions:{pages_manage_posts:'granted'},checks:[],target:{page_name:'Page',target_id:'11_0'},message:'Memeriksa sesi saat ini, bukan token lama.'};
  const {elements,requests}=setup([connected,loaded,evidence]);
  await tick();await elements['load-posts'].events.click();choose(elements);
  assert.equal(elements['access-post'].disabled,false);
  await elements['access-post'].events.click();
  assert.equal(requests.at(-1).path,'/api/delete-diagnosis');
  assert.deepEqual(JSON.parse(requests.at(-1).options.body),{page_id:'11',keys:['published_posts:11_0']});
  assert.match(JSON.stringify(elements['access-post-result'].children),/saat ini/);
  assert.equal(requests.filter(r=>r.path.includes('prepare')||r.path.includes('execute')||r.path.includes('confirm')).length,0);
});

test('audit access double click and stale Page/disconnect response are protected',async()=>{
  for (const disconnect of [false,true]) {
    let release;const pending=new Promise(resolve=>{release=resolve;});
    const loaded=audit(1);loaded.items[0].sources=[{source:'published_posts',object_id:'11_0'}];
    const status={...connected,pages:[{id:'11',name:'First'},{id:'22',name:'Second'}],count:2};
    const {elements,requests}=setup([status,loaded,()=>pending,disconnected]);
    await tick();await elements['load-posts'].events.click();choose(elements);
    const work=elements['access-post'].events.click();await elements['access-post'].events.click();
    assert.equal(requests.filter(r=>r.path==='/api/delete-diagnosis').length,1);
    if (disconnect) await elements.disconnect.events.click();
    else {elements.fanpage.value='22';elements.fanpage.events.change();}
    release({request_count:5,request_budget:5,message:'Old Page diagnosis'});await work;
    assert.equal(elements['access-post-result'].children.length,0);
    assert.equal(elements['access-post'].disabled,true);
  }
});

test('audit access requires one selected candidate with published Post provenance',async()=>{
  const loaded=audit(2);loaded.items[0].sources=[{source:'video_reels',object_id:'99'}];
  loaded.items[1].sources=[{source:'published_posts',object_id:'11_1'}];
  const {elements,requests}=setup([connected,loaded]);
  await tick();await elements['load-posts'].events.click();choose(elements);
  assert.equal(elements['access-post'].disabled,true);
  elements['select-all'].checked=true;elements['select-all'].events.change();
  assert.equal(elements['access-post'].disabled,true);
  await elements['access-post'].events.click();assert.equal(requests.length,2);
});

test('safe deletion diagnosis distinguishes uncertain causes and hides old dry report', async () => {
  for (const reason of ['timeout_after_send','network_after_send','response_unverified','interrupted','internal_error']) {
    const result={outcome:'uncertain',reason,target:preparedPost.target,message:'Hasil belum dapat dipastikan',
      diagnostic:{reason,http_status:200,error_code:0,error_subcode:77,duration_ms:123,content_type:'application/json',
        json_parsed:true,success_type:'string',has_error_field:false,response_issue:'success_not_boolean',
        internal_code:reason==='internal_error'?'response_headers.runtime_error':null,phase:'response_headers',
        raw_body:'dummy-page-secret',authorization:'dummy-page-secret'}};
    const {elements,requests}=setup([connected,onePostAudit(),preparedPost,result]);
    await tick();
    await elements['load-posts'].events.click();
    choose(elements);
    elements['dry-status'].textContent='Pemeriksaan selesai. Tidak ada konten dihapus.';
    elements['dry-results'].appendChild(new Element());
    await elements['delete-one'].events.click();
    elements['delete-manual-check'].checked=true;
    await elements['delete-confirm'].events.click();
    assert.equal(elements['dry-status'].hidden,true);
    assert.equal(elements['dry-results'].children.length,0);
    const text=elements['delete-diagnosis'].children.map(child=>child.textContent).join(' ');
    assert.ok(text.includes(reason));
    assert.match(text,/HTTP: 200/);
    assert.match(text,/123 ms/);
    assert.match(text,/success: string/);
    assert.doesNotMatch(text,/dummy-page-secret/);
    assert.equal(elements['content-list'].children.length,1);
    assert.equal(requests.length,4);
  }
});

test('last attempt is read locally without repeating delete and unavailable restart is explicit', async () => {
  const result={outcome:'uncertain',reason:'network_after_send',target:preparedPost.target,message:'Hasil belum dapat dipastikan',
    diagnostic:{http_status:null,duration_ms:200,reason:'network_after_send'}};
  const {elements,requests}=setup([connected,{state:'available',result},
    {state:'not_available',message:'Hasil tidak tersedia setelah restart; jangan ulang DELETE untuk diagnosis.'}]);
  await tick();
  await elements['delete-read-last'].events.click();
  assert.equal(requests[1].path,'/api/posts/delete/last');
  const text=elements['delete-diagnosis'].children.map(child=>child.textContent).join(' ');
  assert.match(text,/network_after_send/);
  await elements['delete-read-last'].events.click();
  assert.match(elements['delete-status'].textContent,/restart/);
  assert.equal(elements['delete-diagnosis'].children.length,0);
  assert.equal(requests.length,3);
  assert.ok(requests.every(request=>!request.options.method || request.options.method==='GET'));
});

test('delayed local read cannot replace diagnosis of a newly started attempt', async () => {
  let releaseRead, releaseDelete;
  const {elements,requests}=setup([connected,onePostAudit(),preparedPost,
    ()=>new Promise(resolve=>{releaseRead=()=>resolve({ok:true,json:async()=>({state:'available',
      result:{outcome:'uncertain',reason:'timeout_after_send',message:'Diagnosis lama',target:preparedPost.target}})});}),
    ()=>new Promise(resolve=>{releaseDelete=()=>resolve({ok:true,json:async()=>({outcome:'uncertain',
      reason:'network_after_send',message:'Diagnosis baru',target:preparedPost.target})});})]);
  await tick();
  await elements['load-posts'].events.click();
  choose(elements);
  await elements['delete-one'].events.click();
  const read=elements['delete-read-last'].events.click();
  elements['delete-manual-check'].checked=true;
  const execute=elements['delete-confirm'].events.click();
  releaseRead();
  await read;
  assert.doesNotMatch(elements['delete-status'].textContent,/Diagnosis lama/);
  assert.equal(elements['delete-diagnosis'].children.length,0);
  releaseDelete();
  await execute;
  assert.match(elements['delete-status'].textContent,/Proses selesai.*belum pasti 1/);
  assert.match(elements['delete-diagnosis'].children.map(child=>child.textContent).join(' '),/network_after_send/);
  assert.equal(requests.length,5);
});

function onePostAudit(size = 1) {
  const data = audit(size);
  for (const item of data.items) {
    item.kind = 'Postingan';
    item.post_status_type = 'mobile_status_update';
    item.sources = [{source:'published_posts', object_id:item.object_id}];
  }
  return data;
}

function choose(elements, index = 0, checked = true) {
  const checkbox = elements['content-list'].children[index].children.find(child => child.type === 'checkbox');
  checkbox.checked = checked;
  checkbox.events.change();
}

const preparedPost = {confirmation_id:'random-single-use', expires_in_seconds:60, story_automatically_verified:false,
  target:{key:'published_posts:11_0', target_id:'11_0', page_id:'11', page_name:'<script>Fanpage</script>',
    kind:'Postingan', text:'<script>teks</script>', published_wib:'12/10/2026 07:00:00 WIB', permalink:'https://www.facebook.com/11/posts/0'}};

test('single Post requires exact one selection, prepare, manual check and explicit confirmation', async () => {
  const {elements, requests} = setup([connected, onePostAudit(2), preparedPost,
    {outcome:'succeeded', target:preparedPost.target, message:'Post berhasil dihapus'}]);
  await tick();
  await elements['load-posts'].events.click();
  assert.equal(elements['delete-one'].disabled,true);
  choose(elements,0);
  assert.equal(elements['delete-one'].disabled,false);
  choose(elements,1);
  assert.equal(elements['delete-one'].disabled,true);
  await elements['delete-one'].events.click();
  assert.equal(requests.length,2);
  choose(elements,1,false);
  await elements['delete-one'].events.click();
  assert.equal(requests[2].path,'/api/posts/delete/prepare');
  assert.deepEqual(JSON.parse(requests[2].options.body),{page_id:'11',keys:['published_posts:11_0']});
  assert.equal(elements['delete-dialog'].open,true);
  assert.match(elements['delete-details'].children.map(child=>child.textContent).join(' '), /11_0/);
  assert.equal(elements['delete-confirm'].disabled,true);
  await elements['delete-confirm'].events.click();
  assert.equal(requests.length,3);
  elements['delete-manual-check'].checked=true;
  elements['delete-manual-check'].events.change();
  await elements['delete-confirm'].events.click();
  assert.equal(requests[3].path,'/api/posts/delete/execute');
  assert.deepEqual(JSON.parse(requests[3].options.body),{confirmation_id:'random-single-use',manual_target_confirmed:true});
  assert.equal(elements['content-list'].children.length,1);
  assert.match(elements['selected-count'].textContent,/Terpilih 0/);
  assert.equal(elements['delete-dialog'].open,false);
});

test('cancel sends no DELETE execution and unlinked Reel shows backend rejection', async () => {
  const data=onePostAudit();
  const {elements,requests}=setup([connected,data,preparedPost]);
  await tick();
  await elements['load-posts'].events.click();
  choose(elements);
  await elements['delete-one'].events.click();
  elements['delete-cancel'].events.click();
  elements['delete-manual-check'].checked=true;
  await elements['delete-confirm'].events.click();
  assert.equal(requests.length,3);
  assert.equal(elements['delete-dialog'].open,false);
  const reelData=onePostAudit();
  reelData.items[0].kind='Reel';
  reelData.items[0].sources=[{source:'video_reels',object_id:'987'}];
  const second=setup([connected,reelData,()=>({ok:false,json:async()=>({error:{code:'page_post_missing',message:'Tidak memiliki ID Page Post'}})})]);
  await tick();
  await second.elements['load-posts'].events.click();
  choose(second.elements);
  assert.equal(second.elements['delete-one'].disabled,false);
  await second.elements['delete-one'].events.click();
  assert.match(second.elements['delete-status'].textContent,/Tidak memiliki ID Page Post/);
  assert.notEqual(second.elements['delete-dialog'].open,true);
  assert.equal(second.requests.length,3);
});

test('frontend has no subtype rule and expired dialog cannot execute', async () => {
  const data=onePostAudit();
  delete data.items[0].post_status_type;
  const unknown=setup([connected,data,preparedPost]);
  await tick();
  await unknown.elements['load-posts'].events.click();
  choose(unknown.elements);
  assert.equal(unknown.elements['delete-one'].disabled,false);
  await unknown.elements['delete-one'].events.click();
  assert.equal(unknown.requests.length,3);
  assert.equal(unknown.elements['delete-dialog'].open,true);
  const {elements,requests,timers}=setup([connected,onePostAudit(),preparedPost]);
  await tick();
  await elements['load-posts'].events.click();
  choose(elements);
  await elements['delete-one'].events.click();
  const expiry=timers.find(timer=>timer.delay===60000);
  expiry.fn();
  elements['delete-manual-check'].checked=true;
  await elements['delete-confirm'].events.click();
  assert.equal(requests.length,3);
  assert.equal(elements['delete-dialog'].open,false);
  assert.match(elements['delete-status'].textContent,/kedaluwarsa/);
});

test('photo and link labels defer to backend Page Post preview', async () => {
  for (const kind of ['Foto','Link','Status']) {
    const data=onePostAudit();
    data.items[0].kind=kind;
    data.items[0].post_status_type='anything';
    const preview={...preparedPost,target:{...preparedPost.target,kind,operation_scope:'page_post',media_deletion_guaranteed:false}};
    const {elements,requests}=setup([connected,data,preview]);
    await tick();
    await elements['load-posts'].events.click();
    choose(elements);
    assert.equal(elements['delete-one'].disabled,false,kind);
    await elements['delete-one'].events.click();
    assert.equal(elements['delete-dialog'].open,true,kind);
    const text=elements['delete-details'].children.map(child=>child.textContent).join(' ');
    assert.match(text,/ID Page Post asli: 11_0/);
    assert.match(text,/menargetkan ID Page Post asli/);
    assert.equal(requests.length,3);
  }
});

test('uncertain retains audit and never repeats DELETE after network loss', async () => {
  const {elements,requests}=setup([connected,onePostAudit(),preparedPost,()=>{throw new Error('lost');}]);
  await tick();
  await elements['load-posts'].events.click();
  choose(elements);
  await elements['delete-one'].events.click();
  elements['delete-manual-check'].checked=true;
  elements['delete-manual-check'].events.change();
  await elements['delete-confirm'].events.click();
  await elements['delete-confirm'].events.click();
  assert.equal(requests.length,4);
  assert.equal(elements['content-list'].children.length,1);
  assert.match(elements['delete-status'].textContent,/belum dapat dipastikan/);
});

test('double confirmation suppressed and late delete result ignored after Page switch', async () => {
  let release;
  const {elements,requests}=setup([connected,onePostAudit(),preparedPost,()=>new Promise(resolve=>{
    release=()=>resolve({ok:true,json:async()=>({outcome:'succeeded',target:preparedPost.target,message:'Post dihapus'})});
  })]);
  await tick();
  await elements['load-posts'].events.click();
  choose(elements);
  await elements['delete-one'].events.click();
  elements['delete-manual-check'].checked=true;
  elements['delete-manual-check'].events.change();
  const first=elements['delete-confirm'].events.click();
  await elements['delete-confirm'].events.click();
  assert.equal(requests.length,4);
  elements.fanpage.value='22';
  elements.fanpage.events.change();
  release();
  await first;
  assert.equal(elements['content-list'].children.length,0);
  assert.doesNotMatch(elements['delete-status'].textContent,/Post dihapus/);
});

test('disconnect suppresses late delete success and never rebuilds cleared audit', async () => {
  let release;
  const {elements,requests}=setup([connected,onePostAudit(),preparedPost,()=>new Promise(resolve=>{
    release=()=>resolve({ok:true,json:async()=>({outcome:'succeeded',target:preparedPost.target,message:'Post dihapus'})});
  }),disconnected]);
  await tick();
  await elements['load-posts'].events.click();
  choose(elements);
  await elements['delete-one'].events.click();
  elements['delete-manual-check'].checked=true;
  const first=elements['delete-confirm'].events.click();
  await elements.disconnect.events.click();
  release();
  await first;
  assert.equal(requests.length,5);
  assert.equal(elements['content-list'].children.length,0);
  assert.equal(elements['delete-one'].disabled,true);
  assert.doesNotMatch(elements['delete-status'].textContent,/Post dihapus/);
});

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
  assert.equal(requests[1].path, '/api/posts/jobs');
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

test('Fanpage search is local, case insensitive, and pins unmatched active selection', async () => {
  const session = {...connected, count: 2, pages: [{id: '11', name: 'Alpha'}, {id: '22', name: 'BETA'}]};
  const {elements, requests} = setup([session, audit(1)]);
  await tick();
  await elements['load-posts'].events.click();
  elements['fanpage-search'].value = 'beta';
  elements['fanpage-search'].events.input();
  assert.equal(elements.fanpage.value, '11');
  assert.equal(elements.fanpage.options.length, 2);
  assert.match(elements.fanpage.options[0].textContent, /aktif.*luar pencarian/i);
  assert.match(elements['fanpage-search-status'].textContent, /1 hasil/);
  assert.equal(elements['content-list'].children.length, 1);
  assert.equal(requests.length, 2);
  elements['fanpage-search'].value = 'absent';
  elements['fanpage-search'].events.input();
  assert.match(elements['fanpage-search-status'].textContent, /tidak ditemukan/i);
  assert.equal(elements.fanpage.value, '11');
  elements['fanpage-search'].value = '22';
  elements['fanpage-search'].events.input();
  elements.fanpage.value = '22';
  elements.fanpage.events.change();
  assert.equal(elements['content-list'].children.length, 0);
  assert.equal(requests.length, 2);
});

test('session status reconciliation preserves active Page still present', async () => {
  const session = {...connected, count: 2, pages: [{id: '11', name: 'Alpha'}, {id: '22', name: 'Beta'}]};
  const data = audit(1, {page_id: '22'});
  const {elements} = setup([session, data, () => { throw new Error('disconnect response lost'); }, session]);
  await tick();
  elements.fanpage.value = '22';
  elements.fanpage.events.change();
  await elements['load-posts'].events.click();
  await elements.disconnect.events.click();
  // Reconciliation reads status: Page22 remains available even though ordered second.
  assert.equal(elements.fanpage.value, '22');
});

test('source diagnosis displays request and item counts plus precise stop reason', async () => {
  const data = audit(1, {state: 'partial', bulk_selectable: false, error: {code: 'permission_denied', source: 'video_reels', message: 'Akses ditolak'},
    sources: [{source: 'published_posts', requests: 2, pages: 1, received: 20, status: 'limit_reached', stop_reason: 'unique_limit'},
              {source: 'video_reels', requests: 1, pages: 0, received: 0, status: 'failed', stop_reason: 'permission_denied'}]});
  const {elements} = setup([connected, data]);
  await tick();
  await elements['load-posts'].events.click();
  assert.equal(elements['source-diagnostics'].children.length, 2);
  assert.match(elements['source-diagnostics'].children[0].textContent, /2 request.*1 halaman.*20 item/);
  assert.match(elements['source-diagnostics'].children[1].textContent, /Reels.*Gagal.*izin/i);
});

test('cooldown from a later source error is honored even when first error is permission', async () => {
  const first = {source: 'published_posts', code: 'permission_denied', message: 'Izin ditolak'};
  const second = {source: 'video_reels', code: 'rate_limited', message: 'Rate limit', retry_after: 30};
  const {elements} = setup([connected, audit(0, {state: 'partial', bulk_selectable: false, error: first, errors: [first, second]})]);
  await tick();
  await elements['load-posts'].events.click();
  assert.equal(elements['load-posts'].disabled, true);
});

function jobState(revision, overrides = {}) {
  return {...audit(1), job_id: 'opaque-job', revision, terminal: false, state: 'running',
    request_count: revision * 2, dated_count: 1, undated_count: 0, resume_after_seconds: 0, ...overrides};
}

test('job advances same id/revision automatically and preserves audit selection', async () => {
  let release;
  const {elements, requests} = setup([connected, jobState(0, {items: [], displayed: 0}), jobState(1),
    () => new Promise(resolve => {release = () => resolve({ok: true, json: async () => jobState(2, {terminal: true, state: 'source_exhausted'})});})]);
  await tick();
  const work = elements['load-posts'].events.click();
  await tick();
  assert.equal(elements['stop-load'].disabled, false);
  elements['select-all'].checked = true;
  elements['select-all'].events.change();
  release();
  await work;
  assert.deepEqual(requests.slice(1).map(r => r.path), ['/api/posts/jobs', '/api/posts/jobs/opaque-job/step', '/api/posts/jobs/opaque-job/step']);
  assert.deepEqual(requests.slice(2).map(r => JSON.parse(r.options.body).revision), [0,1]);
  assert.match(elements['selected-count'].textContent, /Terpilih 1/);
  assert.match(elements['load-status'].textContent, /bertanggal/);
  assert.equal(elements['stop-load'].disabled, true);
});

test('lost step response retries identical revision without starting a new job', async () => {
  const {elements, requests} = setup([connected, jobState(0), () => {throw Error('lost');}, jobState(1, {terminal: true})]);
  await tick();
  await elements['load-posts'].events.click();
  assert.equal(requests.filter(r => r.path === '/api/posts/jobs').length, 1);
  assert.deepEqual(requests.slice(2).map(r => JSON.parse(r.options.body).revision), [0,0]);
});

test('cooldown waits on local timer and stop cancels continuation', async () => {
  const {elements, requests, timers} = setup([connected, jobState(0), jobState(1, {state:'cooldown', resume_after_seconds:30}),
    jobState(2, {terminal:true, state:'cancelled'})]);
  await tick();
  const work = elements['load-posts'].events.click();
  await tick();
  assert.equal(requests.length,3);
  assert.ok(timers.some(t => t.delay >= 30000));
  await elements['stop-load'].events.click();
  await work;
  assert.equal(requests[3].path,'/api/posts/jobs/opaque-job/stop');
  assert.equal(requests.filter(r => r.path.endsWith('/step')).length,1);
  assert.match(elements['load-status'].textContent,/dihentikan|dibatalkan/i);
});

test('Page change cancels backend job and late step cannot render old data', async () => {
  let release;
  const session={...connected,count:2,pages:[{id:'11',name:'A'},{id:'22',name:'B'}]};
  const {elements, requests}=setup([session,jobState(0),()=>new Promise(resolve=>{release=()=>resolve({ok:true,json:async()=>jobState(1)});}),jobState(2,{terminal:true,state:'cancelled'})]);
  await tick();
  const work=elements['load-posts'].events.click();
  await tick();
  elements.fanpage.value='22';
  elements.fanpage.events.change();
  await tick();
  release();
  await work;
  assert.ok(requests.some(r=>r.path.endsWith('/stop')));
  assert.equal(elements['content-list'].children.length,0);
});


test('stop during failed step prevents frontend retry and later Meta continuation', async () => {
  let rejectStep;
  const {elements, requests}=setup([connected,jobState(0),()=>new Promise((resolve,reject)=>{rejectStep=reject;}),jobState(1,{terminal:true,state:'cancelled'})]);
  await tick();
  const work=elements['load-posts'].events.click();
  await tick();
  await elements['stop-load'].events.click();
  rejectStep(Error('lost response after stop'));
  await work;
  assert.equal(requests.filter(r=>r.path.endsWith('/step')).length,1);
  assert.equal(requests.filter(r=>r.path.endsWith('/stop')).length,1);
});

test('disconnect while job step pending clears audit and prevents following steps', async () => {
  let release;
  const {elements,requests}=setup([connected,jobState(0),()=>new Promise(resolve=>{release=()=>resolve({ok:true,json:async()=>jobState(1)});}),jobState(2,{terminal:true,state:'cancelled'}),disconnected]);
  await tick();
  const work=elements['load-posts'].events.click();
  await tick();
  await elements.disconnect.events.click();
  release();
  await work;
  assert.equal(requests.filter(r=>r.path.endsWith('/step')).length,1);
  assert.equal(elements['content-list'].children.length,0);
  assert.equal(elements['load-posts'].disabled,true);
});

test('cooldown wait is capped by job deadline for local expiry reconciliation', async () => {
  const {elements, requests, timers}=setup([connected,jobState(0),
    jobState(1,{state:'cooldown',resume_after_seconds:300,job_remaining_seconds:2}),
    jobState(2,{state:'partial',terminal:true,step_stop_reason:'job_timeout'})]);
  await tick();
  const work=elements['load-posts'].events.click();
  await tick();
  const wait=timers.find(t=>t.delay===2050);
  assert.ok(wait);
  assert.equal(requests.length,3);
  wait.fn();
  await work;
  assert.equal(requests.length,4);
  assert.match(elements['load-status'].textContent,/parsial/i);
});

test('dry run submits only selected keys and renders concrete ineligible target safely', async () => {
  const dry={job_id:'a'.repeat(32),terminal:true,state:'completed',selected:1,valid:0,ambiguous:1,duplicates:0,checked:1,
    pages:[{page_id:'11',name:'<script>Page</script>'}],safe_for_delete:false,
    items:[{key:'k0',object_id:'11_1',delete_target:'11_1',status:'dry_run_checked',eligible:false,reasons:['story_exclusion_unverified']}]};
  const {elements,requests}=setup([connected,audit(1),dry]);
  await tick();
  await elements['load-posts'].events.click();
  elements['select-all'].checked=true;
  elements['select-all'].events.change();
  await elements['dry-run'].events.click();
  const sent=requests.find(r=>r.path==='/api/dry-run/jobs');
  assert.ok(sent);
  assert.deepEqual(JSON.parse(sent.options.body),{selections:[{page_id:'11',keys:[audit(1).items[0].key]}]});
  assert.match(elements['dry-status'].textContent,/layak 0/i);
  assert.match(elements['dry-results'].children[0].textContent,/11_1/);
  assert.match(elements['dry-results'].children[0].textContent,/Story/);
});

test('dry run stop cancels polling and uses distinct Stop label', async () => {
  const dry={job_id:'a'.repeat(32),terminal:false,state:'running',selected:1,valid:0,ambiguous:1,duplicates:0,checked:0,pages:[],items:[]};
  const {elements,requests}=setup([connected,audit(1),dry,{...dry,terminal:true,state:'cancelled'}]);
  await tick();await elements['load-posts'].events.click();
  elements['select-all'].checked=true;elements['select-all'].events.change();
  const work=elements['dry-run'].events.click();
  await tick();
  assert.equal(elements['stop-load'].textContent,'Berhenti dry run');
  await elements['stop-load'].events.click();
  await work;
  assert.equal(requests[3].path,'/api/dry-run/jobs/'+dry.job_id+'/stop');
  assert.equal(requests.filter(r=>r.path.includes('/api/dry-run/') && r.options.method!== 'POST').length,0);
});

test('late dry run result from old Page is hidden and cancelled', async () => {
  let release;
  const session={...connected,count:2,pages:[{id:'11',name:'A'},{id:'22',name:'B'}]};
  const dry={job_id:'a'.repeat(32),terminal:false,state:'running',pages:[],items:[]};
  const {elements,requests}=setup([session,audit(1),()=>new Promise(resolve=>{release=()=>resolve({ok:true,json:async()=>dry});}),{...dry,terminal:true}]);
  await tick();await elements['load-posts'].events.click();
  elements['select-all'].checked=true;elements['select-all'].events.change();
  const work=elements['dry-run'].events.click();
  await tick();
  elements.fanpage.value='22';elements.fanpage.events.change();
  release();await work;await tick();
  assert.equal(elements['dry-results'].children.length,0);
  assert.ok(requests.some(r=>r.path.endsWith('/stop')));
});

const bulkPreview = {confirmation_id:'bulk-cid',expires_in_seconds:60,selected:2,supported:1,unsupported:1,duplicates:0,max_targets:20,
  items:[{supported:true,operation_scope:'page_post',page_id:'11',page_name:'Original Page',key:'published_posts:11_0',target_id:'11_0',kind:'Foto',text:'<script>safe</script>',permalink:'https://www.facebook.com/11/posts/0'},
    {supported:false,page_id:'11',page_name:'Original Page',key:'published_posts:11_1',kind:'Story',reason:'identified_story',message:'Story ditolak'}]};
const bulkJob = {job_id:'bulk-job',terminal:true,state:'completed',selected:2,total:1,duplicates:0,completed:1,succeeded:1,pages:[{page_id:'11',name:'Original Page'}],
  counts:{queued:0,processing:0,succeeded:1,failed:0,uncertain:0,blocked:0,cancelled:0},items:[{page_id:'11',key:'published_posts:11_0',delete_target:'11_0',status:'succeeded',reasons:['success_boolean_true'],details:bulkPreview.items[0],diagnostic:{http_status:200,raw_body:'secret-token'}}]};
test('queue distinguishes local usage saturation from Meta cooldown with safe collapsed details',async()=>{
  for (const [reason,source,policy,label] of [
    ['local_usage_saturated','x-app-usage','local','Penggunaan API tinggi (jeda lokal)'],
    ['retry_after','retry-after','meta','Meta meminta menunggu'],
    ['meta_http_429','http_status','meta','Meta meminta menunggu'],
    ['meta_rate_limit','error_code','meta','Meta meminta menunggu'],
    ['local_policy','local_policy','local','Jeda kebijakan lokal'],
    ['local_scope_capacity','local_policy','local','Menunggu kapasitas lokal'],
  ]) {
    const cooldown={reason,source,policy,scope:'token',expires_at:'2026-10-14T10:00:00Z',remaining_seconds:7,usage:100,error_code:4,
      raw_headers:'unsafe-secret',token_key:'unsafe-secret'};
    const running={...bulkJob,terminal:false,state:'cooldown',cooldown_seconds:7,cooldown,pacing:{interval_seconds:3,usage_known:false},items:[]};
    const {elements}=setup([connected,{jobs:[running]}]);await tick();await elements['bulk-read'].events.click();
    assert.ok(elements['bulk-status'].textContent.includes(label));
    assert.match(elements['bulk-status'].textContent,/Jeda API 7 detik/);
    assert.equal(elements['bulk-technical'].hidden,false);assert.notEqual(elements['bulk-technical'].open,true);
    const detail=JSON.stringify(elements['bulk-technical-content'].children);
    for (const value of [source,reason,'token','2026-10-14T10:00:00Z','100%','Usage terbaru tidak tersedia']) assert.ok(detail.includes(value),value);
    assert.doesNotMatch(detail,/unsafe-secret|raw_headers|token_key/);
    assert.equal(elements['bulk-results'].children.length,0);
  }
});

test('expired cooldown stays in collapsed history without showing an active wait',async()=>{
  const running={...bulkJob,terminal:false,state:'running',cooldown_seconds:0,cooldown:null,last_cooldown:{reason:'retry_after',source:'retry-after',scope:'global',policy:'meta',expires_at:'2026-10-14T09:00:00Z',remaining_seconds:0,active:false,usage:null,error_code:4,raw_headers:'unsafe-secret'},items:[]};
  const {elements}=setup([connected,{jobs:[running]}]);await tick();await elements['bulk-read'].events.click();
  assert.doesNotMatch(elements['bulk-status'].textContent,/Meta meminta menunggu|Jeda API|jeda lokal/);
  assert.equal(elements['bulk-technical'].hidden,false);assert.notEqual(elements['bulk-technical'].open,true);
  const detail=JSON.stringify(elements['bulk-technical-content'].children);
  for(const value of ['Jeda terakhir (sudah berakhir)','retry_after','retry-after','global','2026-10-14T09:00:00Z','sisa: 0 detik']) assert.ok(detail.includes(value),value);
  assert.doesNotMatch(detail,/unsafe-secret|raw_headers/);
});

test('high usage shows adaptive pacing without implying Meta cooldown',async()=>{
  const running={...bulkJob,terminal:false,state:'running',cooldown_seconds:0,cooldown:null,pacing:{interval_seconds:2.5,usage_known:true,
    last_usage:{source:'x-business-use-case-usage',percent:99,metrics:{call_count:99,total_time:70,total_cputime:60},observed_at:'2026-10-14T09:59:00Z',raw_headers:'unsafe-secret'}},items:[]};
  const {elements,timers}=setup([connected,{jobs:[running]}, {...running,pacing:{interval_seconds:1,usage_known:false,last_usage:null}}]);
  await tick();await elements['bulk-read'].events.click();
  assert.match(elements['bulk-status'].textContent,/Penggunaan API tinggi; interval request 2.5 detik/);
  assert.doesNotMatch(elements['bulk-status'].textContent,/Meta meminta menunggu|jeda lokal|Jeda API/);
  const detail=JSON.stringify(elements['bulk-technical-content'].children);
  assert.match(detail,/x-business-use-case-usage/);assert.match(detail,/99%/);assert.match(detail,/call_count: 99/);assert.doesNotMatch(detail,/unsafe-secret|raw_headers/);
  await timers.filter(t=>t.delay===1000).at(-1).fn();
  assert.doesNotMatch(elements['bulk-status'].textContent,/Penggunaan API tinggi|Usage terbaru/);
  assert.match(JSON.stringify(elements['bulk-technical-content'].children),/Usage terbaru tidak tersedia/);
});
test('large confirmation preview pages fifty local targets without another request',async()=>{
  const preview={...bulkPreview,selected:5000,supported:5000,unsupported:0,max_targets:5000,expires_in_seconds:600,
    items:Array.from({length:5000},(_,i)=>({...bulkPreview.items[0],key:`target:${i}`}))};
  const {elements,requests}=setup([connected,audit(1),preview]);
  await tick();await elements['load-posts'].events.click();choose(elements);
  await elements['delete-selected'].events.click();
  assert.equal(elements['bulk-details'].children.length,51);
  assert.match(elements['bulk-details'].children[0].textContent,/5000/);
  const before=requests.length;
  elements['bulk-preview-pagination'].children.at(-1).events.click();
  assert.equal(requests.length,before);
  assert.match(elements['bulk-details'].children[1].children[0].textContent,/target:50/);
});
test('terminal queue drains compact changes and removes successes outside visible result page',async()=>{
  const initial={...bulkJob,items:[],revision:101,next_revision:100,has_more_changes:true,
    changes:[{...bulkJob.items[0],key:'published_posts:11_1',delete_target:'11_1'}]};
  const final={...initial,next_revision:101,has_more_changes:false,changes:[bulkJob.items[0]]};
  const {elements,requests,timers}=setup([connected,audit(2),bulkPreview,initial,final]);
  await tick();await elements['load-posts'].events.click();choose(elements);
  await elements['delete-selected'].events.click();elements['bulk-manual-check'].checked=true;
  await elements['bulk-confirm'].events.click();
  assert.equal(elements['content-list'].children.length,1);
  await timers.filter(t=>t.delay===1000).at(-1).fn();
  assert.equal(requests.at(-1).path,'/api/bulk-delete/jobs/bulk-job?offset=0&since=100');
  assert.equal(elements['content-list'].children.length,0);
});
test('result pagination reads local offset and revision while keeping fifty visible rows',async()=>{
  const first={...bulkJob,selected:5000,total:5000,revision:5000,next_revision:5000,changes:[],items:Array.from({length:50},(_,i)=>({...bulkJob.items[0],key:`target:${i}`,status:'uncertain'}))};
  const next={...first,offset:50,items:[{...bulkJob.items[0],key:'target:50',status:'failed'}]};
  const {elements,requests}=setup([connected,{jobs:[],last_job:first},next]);
  await tick();await elements['bulk-read'].events.click();
  assert.equal(elements['bulk-results'].children.length,50);
  await elements['bulk-pagination'].children.at(-1).events.click();
  assert.equal(requests.at(-1).path,'/api/bulk-delete/jobs/bulk-job?offset=50&since=5000');
  assert.match(elements['bulk-results'].children[0].children[0].textContent,/Gagal/);
  assert.equal(requests.at(-1).options.method,undefined);
});
test('reset snapshot applies compact successes and resumes at current revision',async()=>{
  const first={...bulkJob,terminal:false,state:'running',revision:5,next_revision:2,changes:[],items:[]};
  const reset={...bulkJob,terminal:false,state:'running',reset_required:true,revision:10,next_revision:3,changes:[bulkJob.items[0]],items:[]};
  const last={...bulkJob,revision:10,next_revision:10,changes:[],items:[]};
  const {elements,requests,timers}=setup([connected,audit(1),bulkPreview,first,reset,last]);
  await tick();await elements['load-posts'].events.click();choose(elements);
  await elements['delete-selected'].events.click();elements['bulk-manual-check'].checked=true;await elements['bulk-confirm'].events.click();
  await timers.filter(t=>t.delay===1000).at(-1).fn();
  assert.equal(elements['content-list'].children.length,0);
  await timers.filter(t=>t.delay===1000).at(-1).fn();
  assert.equal(requests.at(-1).path,'/api/bulk-delete/jobs/bulk-job?offset=0&since=10');
});
test('Stop stays hidden while idle and appears only during an active queue',async()=>{
  const running={...bulkJob,terminal:false,state:'running',items:[]};
  const {elements}=setup([connected,{jobs:[running]}, { ...bulkJob,items:[] }]);
  await tick();assert.equal(elements['stop-load'].hidden,true);
  await elements['bulk-read'].events.click();assert.equal(elements['stop-load'].hidden,false);
  await elements['stop-load'].events.click();assert.equal(elements['stop-load'].hidden,true);
});
test('queue cooldown and failed or uncertain alias outcomes remain clear on loaded audit rows',async()=>{
  const loaded=onePostAudit(3);
  loaded.items[1].sources=[{source:'video_reels',object_id:'99'}];
  const changes=[{...bulkJob.items[0],status:'failed'},
    {...bulkJob.items[0],key:'reel:99',delete_target:'99',status:'uncertain',details:{operation_scope:'reel_video',related_video_ids:['99']}}];
  const running={...bulkJob,terminal:false,state:'cooldown',cooldown_seconds:7,items:[],changes,revision:2,next_revision:2};
  const reset={...bulkJob,reset_required:true,revision:3,changes:[{...bulkJob.items[0],key:loaded.items[2].key,delete_target:'11_2'}],items:[]};
  const {elements,timers}=setup([connected,loaded,bulkPreview,running,reset]);
  await tick();await elements['load-posts'].events.click();choose(elements);
  await elements['delete-selected'].events.click();elements['bulk-manual-check'].checked=true;await elements['bulk-confirm'].events.click();
  assert.match(elements['bulk-status'].textContent,/Jeda API 7 detik/);
  assert.match(JSON.stringify(elements['content-list'].children[0]),/Penghapusan: Gagal/);
  assert.match(JSON.stringify(elements['content-list'].children[1]),/Penghapusan: Belum pasti/);
  await timers.filter(t=>t.delay===1000).at(-1).fn();
  assert.equal(elements['content-list'].children.length,2);
  assert.match(JSON.stringify(elements['content-list'].children[0]),/Penghapusan: Gagal/);
  assert.match(JSON.stringify(elements['content-list'].children[1]),/Penghapusan: Belum pasti/);
});
test('expired session during local queue polling clears results and asks for reconnect',async()=>{
  const running={...bulkJob,terminal:false,state:'running',items:[]};
  const {elements,requests,timers}=setup([connected,{jobs:[running]},()=>({ok:false,json:async()=>({error:{code:'session_expired',message:'Sesi kedaluwarsa.'}})}),disconnected]);
  await tick();await elements['bulk-read'].events.click();
  await timers.filter(t=>t.delay===1000).at(-1).fn();
  assert.equal(requests.at(-1).path,'/api/session');
  assert.equal(elements['bulk-results'].children.length,0);
  assert.equal(elements['stop-load'].hidden,true);
  assert.match(elements['operation-status'].textContent,/Hubungkan kembali/);
});
test('template puts optional tools in closed additional section and leaves main queue status empty',()=>{
  const html=fs.readFileSync('templates/index.html','utf8');
  const additional=html.indexOf('<details><summary>Alat tambahan</summary>');
  const end=html.indexOf('</section>',additional);
  assert.ok(additional>0);
  for (const id of ['bulk-read','access-post','delete-one','delete-read-last','dry-run']) {
    const pos=html.indexOf(`id="${id}"`);assert.ok(pos>additional && pos<end,id);
  }
  assert.match(html,/<p id="bulk-status"[^>]*><\/p>/);
  assert.match(html,/Maksimal 5000 target/);
});
test('bulk rejects a supported video scope before confirmation',async()=>{
  const preview={...bulkPreview,items:[{...bulkPreview.items[0],operation_scope:'reel_video'}]};
  const {elements,requests}=setup([connected,audit(1),preview]);
  await tick();await elements['load-posts'].events.click();choose(elements);
  await elements['delete-selected'].events.click();
  assert.notEqual(elements['bulk-dialog'].open,true);
  assert.match(elements['bulk-status'].textContent,/Load Posts kembali/);
  assert.equal(requests.filter(r=>r.path.includes('confirm')).length,0);
});
test('bulk success invalidates aliases only through original published Post identity',async()=>{
  const loaded=audit(3);
  loaded.items[1].sources=[{source:'published_posts',object_id:'11_0'}, {source:'video_reels',object_id:'99',post_id:'11_0'}];
  const {elements}=setup([connected,loaded,bulkPreview,bulkJob]);
  await tick();await elements['load-posts'].events.click();choose(elements);
  await elements['delete-selected'].events.click();elements['bulk-manual-check'].checked=true;
  await elements['bulk-confirm'].events.click();
  assert.equal(elements['content-list'].children.length,1);
  assert.match(elements['bulk-status'].textContent,/selesai 1\/1/);
});
test('bulk previews supported subset once and confirms immutable keys without retry', async()=>{
  const {elements,requests}=setup([connected,audit(2),bulkPreview,bulkJob]);
  await tick(); await elements['load-posts'].events.click();
  elements['select-all'].checked=true;elements['select-all'].events.change();
  assert.equal(elements['delete-selected'].disabled,false);
  await elements['delete-selected'].events.click();
  assert.equal(elements['bulk-dialog'].open,true);
  assert.match(elements['bulk-details'].children[0].textContent,/dipilih 2.*didukung 1.*ditolak 1/i);
  elements['select-all'].checked=false;elements['select-all'].events.change();
  elements['bulk-manual-check'].checked=true;
  await elements['bulk-confirm'].events.click();await elements['bulk-confirm'].events.click();
  assert.deepEqual(JSON.parse(requests[2].options.body),{selections:[{page_id:'11',keys:['published_posts:11_0','published_posts:11_1']}]});
  assert.deepEqual(JSON.parse(requests[3].options.body),{confirmation_id:'bulk-cid',manual_targets_confirmed:true});
  assert.equal(requests.length,4);assert.equal(elements['content-list'].children.length,1);
  assert.equal(elements['dry-status'].hidden,true);
  assert.doesNotMatch(JSON.stringify(elements['bulk-results'].children),/secret-token/);
});

test('bulk active job survives Page change and stop drains processing through local polling',async()=>{
  const running={...bulkJob,terminal:false,state:'running',counts:{...bulkJob.counts,succeeded:0,queued:0,processing:1},items:[{...bulkJob.items[0],status:'processing'}]};
  const {elements,requests,timers}=setup([{...connected,count:2,pages:[{id:'11',name:'Original Page'},{id:'22',name:'Other Page'}]},audit(2),bulkPreview,running,
    {...running,state:'stopping'},bulkJob]);
  await tick();await elements['load-posts'].events.click();choose(elements);
  await elements['delete-selected'].events.click();elements['bulk-manual-check'].checked=true;await elements['bulk-confirm'].events.click();
  assert.equal(elements['stop-load'].textContent,'Berhenti penghapusan');
  assert.equal(elements['load-posts'].disabled,true);assert.equal(elements['dry-run'].disabled,true);assert.equal(elements['delete-one'].disabled,true);
  elements.fanpage.value='22';elements.fanpage.events.change();
  assert.match(elements['bulk-status'].textContent,/Original Page/);
  assert.equal(requests.filter(r=>r.path.endsWith('/stop')).length,0);
  await elements['stop-load'].events.click();assert.equal(elements['stop-load'].disabled,false);
  const timer=timers.filter(t=>t.delay===1000).at(-1);await timer.fn();
  assert.equal(requests.at(-1).path,'/api/bulk-delete/jobs/bulk-job?offset=0&since=0');
  assert.equal(elements['stop-load'].disabled,true);assert.equal(elements['content-list'].children.length,0);
});
test('lost bulk confirmation is never retried and recovers real job with local active read',async()=>{
  const {elements,requests}=setup([connected,audit(1),bulkPreview,()=>{throw Error('lost confirmation response')},{jobs:[],last_job:bulkJob}]);
  await tick();await elements['load-posts'].events.click();choose(elements);
  await elements['delete-selected'].events.click();elements['bulk-manual-check'].checked=true;await elements['bulk-confirm'].events.click();
  await elements['bulk-confirm'].events.click();
  assert.match(elements['bulk-status'].textContent,/belum pasti/);
  await elements['bulk-read'].events.click();
  assert.equal(requests.filter(r=>r.path==='/api/bulk-delete/confirm').length,1);
  assert.equal(requests.at(-1).path,'/api/bulk-delete/jobs/active');assert.match(elements['bulk-status'].textContent,/berhasil 1/i);
});
test('bulk unsupported preview cannot confirm; changing Page closes it without enqueue',async()=>{
  const preview={...bulkPreview,supported:0,unsupported:2,items:bulkPreview.items.map(item=>({...item,supported:false}))};
  const {elements,requests}=setup([connected,audit(2),preview]);await tick();await elements['load-posts'].events.click();choose(elements);
  await elements['delete-selected'].events.click();elements['bulk-manual-check'].checked=true;elements['bulk-manual-check'].events.change();
  assert.equal(elements['bulk-confirm'].disabled,true);await elements['bulk-confirm'].events.click();
  elements.fanpage.value='22';elements.fanpage.events.change();assert.equal(elements['bulk-dialog'].open,false);
  assert.equal(requests.length,3);
});
test('session expiry cancels bulk polling and clears diagnostics without more requests',async()=>{
  const running={...bulkJob,terminal:false,state:'running',items:[{...bulkJob.items[0],status:'uncertain'}]};
  const {elements,requests,timers}=setup([{...connected,session_remaining_seconds:5},audit(1),bulkPreview,running]);
  await tick();await elements['load-posts'].events.click();choose(elements);await elements['delete-selected'].events.click();
  elements['bulk-manual-check'].checked=true;await elements['bulk-confirm'].events.click();
  assert.equal(elements['content-list'].children.length,1);
  timers.find(t=>t.delay===5000).fn();await timers.find(t=>t.delay===1000).fn();
  assert.equal(elements['bulk-results'].children.length,0);assert.equal(elements['load-posts'].disabled,true);assert.equal(requests.length,4);
});


test('completed bulk failures remain visible in neutral summary and safe collapsed diagnostics', async()=>{
  const mixed={...bulkJob,selected:6,total:5,completed:5,unsupported:1,
    counts:{queued:0,processing:0,succeeded:1,failed:1,uncertain:1,blocked:1,cancelled:1},
    items:[{...bulkJob.items[0],status:'failed',diagnostic:{error_message:'<script>alert(1)</script>',error_user_msg:'Izin diperlukan',raw_body:'hidden-secret'}}]};
  const {elements,requests}=setup([connected,{jobs:[],last_job:mixed}]);
  await tick();await elements['bulk-read'].events.click();
  assert.match(elements['bulk-status'].textContent,/Proses selesai.*Berhasil 1, gagal 1, belum pasti 1, dihentikan 2, tidak didukung 1/);
  assert.doesNotMatch(elements['bulk-status'].textContent,/semua.*berhasil|completed|uncertain|blocked/i);
  const row=elements['bulk-results'].children[0];
  assert.equal(row.tag,'details');assert.notEqual(row.open,true);
  assert.match(row.children.map(child=>child.textContent).join(' '),/<script>alert\(1\)<\/script>/);
  assert.doesNotMatch(JSON.stringify(row.children),/hidden-secret/);
  assert.equal(row.children.filter(child=>child.tag==='script').length,0);
  assert.equal(requests.length,2);
});

test('failed Post read-only probe sends backend job/key and displays safe evidence',async()=>{
  const failed={...bulkJob,counts:{...bulkJob.counts,succeeded:0,failed:1},items:[{...bulkJob.items[0],status:'failed'}]};
  const evidence={request_count:5,request_budget:5,permissions:{pages_manage_posts:'granted',pages_read_engagement:'unknown'},
    tasks:['CREATE_CONTENT'],owner:{id:'11',name:'<script>text</script>',matches_page:true},post_id:'11_0',status_type:'added_photos',
    application:null,media_object_id:'555',photo_ids:['555'],relations:[{type:'photo',id:'555'}],checks:[{source:'post_media',status:'unavailable',reason:'upstream_error',failed_fields:['application']}],message:'GET sukses bukan izin DELETE.'};
  const {elements,requests}=setup([connected,{jobs:[],last_job:failed},evidence]);
  await tick();await elements['bulk-read'].events.click();
  const row=elements['bulk-results'].children[0];
  const button=row.children.find(child=>child.tag==='button' && child.textContent==='Periksa Penyebab Gagal');
  assert.ok(button);
  await button.events.click();
  assert.equal(requests.at(-1).path,'/api/delete-diagnosis');
  assert.deepEqual(JSON.parse(requests.at(-1).options.body),{job_id:'bulk-job',key:'published_posts:11_0'});
  assert.match(JSON.stringify(row.children),/granted|Diberikan/);
  assert.match(JSON.stringify(row.children),/555/);
  assert.equal(row.children.filter(child=>child.tag==='script').length,0);
  assert.equal(requests.filter(r=>r.path.includes('/confirm') || r.path.includes('/execute')).length,0);
});

test('read-only failure probe ignores response after disconnect and prevents duplicate clicks',async()=>{
  let release;
  const pending=new Promise(resolve=>{release=resolve;});
  const failed={...bulkJob,items:[{...bulkJob.items[0],status:'failed'}]};
  const {elements,requests}=setup([connected,{jobs:[],last_job:failed},()=>pending,disconnected]);
  await tick();await elements['bulk-read'].events.click();
  const row=elements['bulk-results'].children[0];
  const button=row.children.find(child=>child.tag==='button');
  const work=button.events.click();await button.events.click();
  assert.equal(requests.filter(r=>r.path==='/api/delete-diagnosis').length,1);
  await elements.disconnect.events.click();
  release({request_count:5,request_budget:5,message:'old evidence must not appear'});await work;
  assert.equal(elements['bulk-results'].children.length,0);
});

test('successful Post or failed Reel has no read-only Post failure action',async()=>{
  for (const item of [bulkJob.items[0],{...bulkJob.items[0],status:'failed',details:{...bulkJob.items[0].details,operation_scope:'reel_video'}}]) {
    const {elements}=setup([connected,{jobs:[],last_job:{...bulkJob,items:[item]}}]);
    await tick();await elements['bulk-read'].events.click();
    assert.equal(elements['bulk-results'].children[0].children.filter(child=>child.tag==='button').length,0);
  }
});

test('single delete safe Meta messages appear only in diagnostic text', async()=>{
  const result={outcome:'failed',reason:'permission_denied',message:'Long technical error',
    diagnostic:{error_message:'<img src=x onerror=alert(1)>',error_user_msg:'Periksa izin'}};
  const {elements}=setup([connected,{result}]);await tick();await elements['delete-read-last'].events.click();
  assert.match(elements['delete-status'].textContent,/Proses selesai.*gagal 1/);
  assert.doesNotMatch(elements['delete-status'].textContent,/technical|onerror/);
  assert.match(elements['delete-diagnosis'].children.map(child=>child.textContent).join(' '),/error_user_msg: Periksa izin/);
  assert.equal(elements['delete-diagnosis'].children.filter(child=>child.tag==='img').length,0);
});

test('template separates dry run and collapses detailed diagnosis',()=>{
  const html=fs.readFileSync('templates/index.html','utf8');
  assert.match(html,/<details><summary>Detail hasil penghapusan<\/summary>/);
  assert.match(html,/<details><summary>Pemeriksaan lokal \(Dry Run\)<\/summary>/);
  assert.doesNotMatch(html,/Penghapusan massal tetap nonaktif|Daftar hanya untuk audit/);
});

test('Reel controls and endpoints are absent without null element failures',async()=>{
  const html=fs.readFileSync('templates/index.html','utf8');
  const script=fs.readFileSync('static/app.js','utf8');
  assert.doesNotMatch(html,/id="delete-one-reel"|Uji Hapus 1 Reel/);
  assert.doesNotMatch(script,/deleteReelButton|\/api\/reels\/delete|kandidat postingan, foto, video dan Reels|memeriksa ID Story/);
  const {elements}=setup([connected]);await tick();
  assert.equal(elements['load-posts'].disabled,false);
});

test('Post preview refuses legacy Reel operation and sends no execution',async()=>{
  const preview={...preparedPost,target:{...preparedPost.target,operation_scope:'reel_video'}};
  const {elements,requests}=setup([connected,onePostAudit(),preview]);
  await tick();await elements['load-posts'].events.click();choose(elements);
  await elements['delete-one'].events.click();
  assert.notEqual(elements['delete-dialog'].open,true);
  assert.match(elements['delete-status'].textContent,/cakupan/);
  assert.equal(requests[2].path,'/api/posts/delete/prepare');
  assert.equal(requests.filter(r=>r.path.includes('execute')).length,0);
});

test('legacy audit rejection requests a fresh Load Posts',async()=>{
  const preview={...bulkPreview,supported:0,items:[{...bulkPreview.items[0],supported:false,reason:'audit_scope_incompatible',operation_scope:'page_post'}]};
  const {elements}=setup([connected,audit(1),preview]);
  await tick();await elements['load-posts'].events.click();choose(elements);
  await elements['delete-selected'].events.click();
  assert.match(JSON.stringify(elements['bulk-details'].children),/Load Posts kembali/);
  assert.equal(elements['bulk-confirm'].disabled,true);
});

test('published source diagnosis reports metadata-filtered and skipped counts',async()=>{
  const loaded=audit(1,{sources:[{source:'published_posts',requests:1,pages:1,received:5,eligible:1,skipped:4,status:'source_exhausted',stop_reason:'source_exhausted'}]});
  const {elements}=setup([connected,loaded]);await tick();await elements['load-posts'].events.click();
  assert.match(elements['source-diagnostics'].children[0].textContent,/1 lolos filter; 4 dilewati/);
});

test('source skip reasons use fixed labels and safe positive counts only',async()=>{
  const source={source:'published_posts',requests:1,pages:1,received:5,eligible:1,skipped:4,status:'source_exhausted',stop_reason:'source_exhausted',
    skip_reasons:{unknown_scope:2,unknown_attachments:1,truncated_attachments:1,unsupported_status_type:3,unsupported_attachment_type:4,ambiguous_provenance:5,identified_story:6,
      '<script>secret</script>':99,other_secret:100}};
  const {elements}=setup([connected,audit(1,{sources:[source]})]);await tick();await elements['load-posts'].events.click();
  const details=elements['source-diagnostics'].children[1];
  assert.match(details.textContent,/jenis konten tidak pasti: 2/);
  assert.match(details.textContent,/metadata attachment tidak pasti: 1/);
  assert.match(details.textContent,/attachment tidak lengkap: 1/);
  assert.match(details.textContent,/jenis status di luar cakupan: 3/);
  assert.match(details.textContent,/jenis attachment di luar cakupan: 4/);
  assert.match(details.textContent,/asal identitas ambigu: 5/);
  assert.match(details.textContent,/Story teridentifikasi: 6/);
  assert.doesNotMatch(details.textContent,/secret|script|99|100/);
  const invalid={...source,skip_reasons:{unknown_scope:0,unknown_attachments:-1,truncated_attachments:'2',unsupported_status_type:Infinity,identified_story:0.5}};
  const second=setup([connected,audit(1,{sources:[invalid]})]);await tick();await second.elements['load-posts'].events.click();
  assert.equal(second.elements['source-diagnostics'].children.length,1);
});
