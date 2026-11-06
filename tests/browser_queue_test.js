// Local-only Chrome/API mocks. No request is made to a real website.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('browser-extension/queue-worker.js', 'utf8');
const appSender = {url:'http://localhost:8000/'};
const KEY = 'apartmentAgentBathroomQueue';
const clone = value => value === undefined ? undefined : JSON.parse(JSON.stringify(value));

function environment(urls = ['https://krisha.kz/a/show/100', 'https://krisha.kz/a/show/200']) {
  const env = {now:100000, storage:{}, tabs:new Map(), alarms:new Map(), created:[], removed:[], reloaded:[], requests:[], tasks:urls.map((url,index) => ({id:index + 1,url,lease_token:`lease-${index + 1}`})), status:'running', jobId:1, lease:null, outcomes:[], failApi:false, handlers:{}, nextTab:1};
  const chrome = {
    storage:{
      local:{async get(key) {return {[key]:clone(env.storage[key])};}, async set(values) {Object.assign(env.storage,clone(values));}},
      sync:{async get() {return {enabled:true,apiOrigin:'http://localhost:8000'};}},
    },
    runtime:{getURL:path => `chrome-extension://fixture/${path}`},
    alarms:{
      async create(name, value) {env.alarms.set(name, clone(value));},
      async clear(name) {return env.alarms.delete(name);},
      onAlarm:{addListener:handler => {env.handlers.alarm = handler;}},
    },
    tabs:{
      async get(id) {if (!env.tabs.has(id)) throw new Error('No tab'); return clone(env.tabs.get(id));},
      async query() {return [...env.tabs.values()].map(clone);},
      async create(value) {const tab = {id:env.nextTab++,...value}; if (env.pendingCreate) {tab.pendingUrl = tab.url; tab.url = 'about:blank';} env.tabs.set(tab.id,tab); env.created.push(clone(tab)); return clone(tab);},
      async update(id, value) {const tab = env.tabs.get(id); if (!tab) throw new Error('No tab'); Object.assign(tab,value); delete tab.pendingUrl; return clone(tab);},
      async remove(id) {env.tabs.delete(id); env.removed.push(id); env.handlers.removed?.(id);},
      async reload(id) {env.reloaded.push(id);},
      onRemoved:{addListener:handler => {env.handlers.removed = handler;}},
      onUpdated:{addListener:handler => {env.handlers.updated = handler;}},
    },
  };
  const fetch = async (url, options) => {
    assert.ok(url.startsWith('http://localhost:8000/api/v1/search/bathroom-check'), 'only local application API is fetched');
    if (env.failApi) throw new Error('API unavailable');
    const path = url.slice('http://localhost:8000/api/v1/search/bathroom-check'.length);
    const body = options.body ? JSON.parse(options.body) : null;
    env.requests.push({path,body});
    let result = {job_id:env.jobId,status:env.status,current_url:env.lease?.url || null};
    if (path === '/claim') {
      if (!env.lease && env.status === 'running') env.lease = env.tasks.shift() || null;
      result = {...result,task:env.lease,wait_seconds:15};
    } else if (path === '/complete') {
      assert.equal(body.task_id, env.lease.id);
      assert.equal(body.lease_token, env.lease.lease_token);
      env.outcomes.push(body.outcome);
      env.lease = null;
      if (body.outcome === 'blocked') env.status = 'paused';
      result.status = env.status;
    } else if (path === '/pause') {env.status = 'paused'; result.status = 'paused';}
    return {ok:true,async json() {return result;}};
  };
  env.worker = () => {
    env.handlers = {};
    const context = {chrome,fetch,crypto:{randomUUID:() => 'persistent-worker'},URL,Date:{now:() => env.now}};
    vm.runInNewContext(source, context);
    return context.ApartmentAgentBathroomQueue;
  };
  env.state = () => env.storage[KEY];
  return env;
}

(async () => {
  const env = environment();
  let worker = env.worker();
  await worker.start();
  assert.equal(env.created.length, 1);
  assert.equal(env.created[0].active, false);
  assert.match(env.created[0].url, /^chrome-extension:/, 'persist tab ownership before navigating to the listing');
  const tabId = env.state().task.tab_id;
  assert.equal(env.tabs.get(tabId).url, 'https://krisha.kz/a/show/100');
  assert.equal(env.state().worker_id, 'persistent-worker');
  assert.ok(env.alarms.has('apartment-agent-bathroom-heartbeat'));

  const pendingCreation = environment();
  pendingCreation.pendingCreate = true;
  const pendingWorker = pendingCreation.worker();
  await pendingWorker.start();
  assert.equal(pendingCreation.created[0].url, 'about:blank');
  assert.match(pendingCreation.created[0].pendingUrl, /^chrome-extension:/);
  assert.equal(pendingCreation.outcomes.length, 0, 'pending placeholder is owned, not a redirect');
  assert.equal(pendingCreation.tabs.get(pendingCreation.state().task.tab_id).url, 'https://krisha.kz/a/show/100');

  // A worker restart recovers the same lease and existing tab.
  worker = env.worker();
  await worker.start();
  assert.equal(env.created.length, 1);
  assert.equal(env.state().task.tab_id, tabId);
  assert.equal(env.requests.filter(item => item.path === '/claim').length, 1);

  // Imports from a user-owned tab cannot complete the queue task.
  await worker.imported({tab:{id:999}}, {bathrooms_count:2});
  assert.equal(env.outcomes.length, 0);
  await worker.imported({tab:{id:tabId}}, {bathrooms_count:null});
  assert.equal(env.outcomes.length, 0, 'unknown fields wait for the content deadline');
  await worker.imported({tab:{id:tabId}}, {bathrooms_count:2});
  assert.deepEqual(env.outcomes, ['imported']);
  assert.deepEqual(env.removed, [tabId]);
  assert.equal(env.state().task, null);
  await worker.kick(appSender);
  assert.equal(env.created.length, 1, 'UI polling cannot bypass the inter-task gap');
  env.now += 30000;
  await worker.kick(appSender);
  assert.equal(env.created.length, 2);

  // Manual pause lets the leased page finish and stops the next claim.
  const paused = environment();
  const pauseWorker = paused.worker();
  await pauseWorker.start();
  const pausedId = paused.state().task.tab_id;
  paused.status = 'paused';
  await pauseWorker.kick(appSender);
  assert.ok(paused.tabs.has(pausedId), 'pause must not close the current leased page');
  await pauseWorker.imported({tab:{id:pausedId}}, {bathrooms_count:1});
  assert.deepEqual(paused.outcomes, ['imported']);
  paused.now += 30000;
  await pauseWorker.kick(appSender);
  assert.equal(paused.created.length, 1);
  paused.status = 'running';
  await pauseWorker.kick(appSender);
  assert.equal(paused.created.length, 2, 'resume opens exactly one pending task');

  // Unknown count is completed only on the bounded content observation result.
  const unknown = environment();
  const unknownWorker = unknown.worker();
  await unknownWorker.start();
  await unknownWorker.finished({tab:{id:unknown.state().task.tab_id}}, 'unknown');
  assert.deepEqual(unknown.outcomes, ['unknown']);
  assert.equal(unknown.created.length, 1);

  // CAPTCHA/redirect is a human handoff. No automatic continuation or tab close.
  for (const reason of ['content challenge', 'redirect', 'timeout', 'closed']) {
    const blocked = environment();
    const blockedWorker = blocked.worker();
    await blockedWorker.start();
    const id = blocked.state().task.tab_id;
    if (reason === 'content challenge') await blockedWorker.finished({tab:{id}}, 'blocked', 'Проверка браузера');
    if (reason === 'redirect') {blocked.tabs.get(id).url = 'https://krisha.kz/security-check'; await blockedWorker.updated(id, {url:'https://krisha.kz/security-check'});}
    if (reason === 'timeout') {blocked.now += 60001; await blockedWorker.start();}
    if (reason === 'closed') {blocked.tabs.delete(id); await blockedWorker.removed(id);}
    assert.equal(blocked.status, 'paused', reason);
    assert.deepEqual(blocked.outcomes, ['blocked'], reason);
    assert.equal(blocked.created.length, 1, reason);
    assert.equal(blocked.state().halted, true, reason);
    if (reason !== 'closed') assert.ok(blocked.tabs.has(id), `${reason}: keep page for the user`);
    blocked.now += 120000;
    await blockedWorker.start();
    assert.equal(blocked.created.length, 1, `${reason}: a heartbeat never retries`);
  }

  // Cancel can close only the extension's unchanged tab.
  for (const userNavigated of [false,true]) {
    const cancelled = environment();
    const cancelWorker = cancelled.worker();
    await cancelWorker.start();
    const id = cancelled.state().task.tab_id;
    if (userNavigated) cancelled.tabs.get(id).url = 'https://example.com/user-page';
    cancelled.status = 'cancelled';
    await cancelWorker.kick(appSender);
    assert.equal(cancelled.tabs.has(id), userNavigated);
    assert.equal(cancelled.state().task, null);
  }

  const pendingUserNavigation = environment();
  const pendingNavigationWorker = pendingUserNavigation.worker();
  await pendingNavigationWorker.start();
  const pendingUserId = pendingUserNavigation.state().task.tab_id;
  pendingUserNavigation.tabs.get(pendingUserId).pendingUrl = 'https://example.com/user-page';
  pendingUserNavigation.status = 'cancelled';
  await pendingNavigationWorker.kick(appSender);
  assert.ok(pendingUserNavigation.tabs.has(pendingUserId), 'never close a tab with a user-initiated pending navigation');

  const pendingDuringImport = environment();
  const importPendingWorker = pendingDuringImport.worker();
  await importPendingWorker.start();
  const pendingImportId = pendingDuringImport.state().task.tab_id;
  pendingDuringImport.tabs.get(pendingImportId).pendingUrl = 'https://example.com/user-page';
  await importPendingWorker.imported({tab:{id:pendingImportId}}, {bathrooms_count:2});
  assert.deepEqual(pendingDuringImport.outcomes, ['blocked']);
  assert.ok(pendingDuringImport.tabs.has(pendingImportId), 'preserve a pending user navigation even when import just succeeded');

  const offline = environment();
  const offlineWorker = offline.worker();
  await offlineWorker.start();
  offline.failApi = true;
  await offlineWorker.finished({tab:{id:offline.state().task.tab_id}}, 'unknown');
  assert.equal(offline.state().halted, true);
  assert.ok(offline.state().task, 'retain the lease for recovery after local API failure');
  offline.now += 120000;
  await offlineWorker.start();
  assert.equal(offline.created.length, 1, 'no next task after an API failure');

  const resumed = environment(['https://krisha.kz/a/show/100']);
  const resumeWorker = resumed.worker();
  await resumeWorker.start();
  const preservedId = resumed.state().task.tab_id;
  await resumeWorker.finished({tab:{id:preservedId}}, 'blocked', 'Проверка браузера');
  resumed.tasks.push({id:1,url:'https://krisha.kz/a/show/100',lease_token:'retry-lease'});
  resumed.status = 'running'; // Explicit application resume, not a heartbeat.
  await resumeWorker.kick(appSender);
  assert.equal(resumed.created.length, 1, 'explicit resume reuses an unchanged preserved tab');
  assert.deepEqual(resumed.reloaded, [preservedId]);
  assert.equal(resumed.state().task.lease_token, 'retry-lease');

  const malicious = environment(['https://krisha.kz.attacker.test/a/show/123']);
  const invalidWorker = malicious.worker();
  await invalidWorker.start();
  assert.equal(malicious.created.length, 0);
  assert.equal(malicious.status, 'paused');
  for (const url of ['http://krisha.kz/a/show/1','https://krisha.kz/a/show/nope','https://krisha.kz:444/a/show/1','https://user:password@krisha.kz/a/show/1','https://example.com/a/show/1']) assert.equal(invalidWorker.canonical(url), null);
  assert.equal(invalidWorker.canonical('https://www.krisha.kz/a/show/123/?from=search#photos'), 'https://krisha.kz/a/show/123');

  const recovered = environment();
  recovered.storage[KEY] = {worker_id:'persistent-worker',job_id:1,task:{id:1,url:'https://krisha.kz/a/show/100',lease_token:'lease-1',tab_id:null,started_at:recovered.now},next_wake:0,halted:false};
  recovered.lease = recovered.tasks.shift();
  recovered.tabs.set(99,{id:99,url:'chrome-extension://fixture/queue-tab.html#persistent-worker'});
  const recoveredWorker = recovered.worker();
  await recoveredWorker.start();
  assert.equal(recovered.created.length, 0, 'recover the persisted placeholder after a worker restart');
  assert.equal(recovered.state().task.tab_id, 99);
  assert.equal(recovered.tabs.get(99).url, 'https://krisha.kz/a/show/100');

  const bridgeSource = fs.readFileSync('browser-extension/queue-bridge.js', 'utf8');
  let receive;
  const bridgeMessages = [];
  const page = {addEventListener(_type, handler) {receive = handler;}, postMessage() {}};
  vm.runInNewContext(bridgeSource, {window:page,location:{origin:'http://localhost:8000'},chrome:{runtime:{sendMessage(message,callback) {bridgeMessages.push(message); callback({ok:true});}}}});
  receive({source:{},origin:'http://localhost:8000',data:{type:'apartment-agent-bathroom-queue'}});
  receive({source:page,origin:'https://example.com',data:{type:'apartment-agent-bathroom-queue'}});
  assert.equal(bridgeMessages.length, 0, 'the bridge rejects other windows and origins');
  receive({source:page,origin:'http://localhost:8000',data:{type:'apartment-agent-bathroom-queue'}});
  assert.equal(bridgeMessages.length, 1);
  assert.equal(bridgeMessages[0].type, 'apartment-agent-bathroom-queue-poll');

  // Existing ordinary import still saves public fields and returns its card.
  let backgroundListener;
  const importHooks = [];
  vm.runInNewContext(fs.readFileSync('browser-extension/background.js', 'utf8'), {
    importScripts() {},
    ApartmentAgentBathroomQueue:{async imported(sender,payload) {importHooks.push({sender,payload});},async kick(){},async finished(){}},
    chrome:{
      runtime:{onInstalled:{addListener(){}},onMessage:{addListener(handler){backgroundListener = handler;}}},
      action:{onClicked:{addListener(){}}},
      storage:{sync:{get(_defaults, callback){callback({enabled:true,apiOrigin:'http://localhost:8000'});}}},
    },
    async fetch(url, options) {
      assert.equal(url, 'http://localhost:8000/api/v1/listings/browser-import');
      assert.equal(JSON.parse(options.body).bathrooms_count, 2);
      return {ok:true, async json() {return {id:7,bathrooms_count:2,assessment:{score:95}};}};
    },
  });
  const ordinaryResponse = await new Promise(resolve => backgroundListener({type:'apartment-agent-import',payload:{bathrooms_count:2}}, {tab:{id:77}}, resolve));
  assert.equal(ordinaryResponse.ok, true);
  assert.equal(ordinaryResponse.listing.id, 7);
  assert.equal(importHooks[0].sender.tab.id, 77);
  assert.equal(importHooks[0].payload.bathrooms_count, 2);

  // Server complete committed, then MV3 died before persisting task=null.
  for (const pausedAfterComplete of [false,true]) {
    const crashed = environment();
    let crashWorker = crashed.worker();
    await crashWorker.start();
    const id = crashed.state().task.tab_id;
    crashed.lease = null; // Server no longer owns the persisted local lease.
    if (pausedAfterComplete) crashed.status = 'paused';
    crashWorker = crashed.worker();
    await crashWorker.start();
    assert.equal(crashed.state().task, null, 'reconcile a completed server task instead of reporting a timeout');
    assert.equal(crashed.outcomes.length, 0, 'never complete an already finished lease again');
    assert.equal(crashed.tabs.has(id), pausedAfterComplete, 'keep paused pages for the user; otherwise close only unchanged own tab');
    assert.equal(crashed.created.length, 1);
  }

  console.log('Browser queue worker checks passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
