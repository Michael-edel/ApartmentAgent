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
    runtime:{getURL:path => `chrome-extension://fixture/${path}`,getManifest:() => ({version:'0.2.0'})},
    alarms:{
      async create(name, value) {env.alarms.set(name, clone(value));},
      async clear(name) {return env.alarms.delete(name);},
      onAlarm:{addListener:handler => {env.handlers.alarm = handler;}},
    },
    tabs:{
      async get(id) {
        if (!env.tabs.has(id)) throw new Error('No tab');
        const tab = env.tabs.get(id);
        if (env.now < (env.addressReadyAt || 0)) {
          if (env.permissionReadError) throw new Error('Missing permission to read tab URL');
          return {id,...(env.temporaryBlank ? {url:'about:blank'} : {})};
        }
        if (env.placeholderAddress && ['', 'about:blank'].includes(tab.url || '')) tab.url = env.placeholderAddress;
        return clone(tab);
      },
      async query(info) {assert.equal(info.url, 'chrome-extension://fixture/queue-tab.html', 'only query our own placeholder URL'); return [...env.tabs.values()].filter(tab => (tab.pendingUrl || tab.url || '').split('#')[0] === info.url).map(clone);},
      async create(value) {const tab = {id:env.nextTab++,...value}; if (env.pendingCreate) {tab.pendingUrl = tab.url; tab.url = 'about:blank';} if (env.addressReadyAt) {env.placeholderAddress = value.url; tab.url = env.temporaryBlank ? 'about:blank' : ''; delete tab.pendingUrl;} env.tabs.set(tab.id,tab); env.created.push(clone(tab)); return clone(tab);},
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
    let result = {job_id:env.jobId,status:env.status,current_url:env.lease?.url || null,mode:env.mode || 'bathrooms'};
    if (path === '/claim') {
      if (!env.lease && env.status === 'running') env.lease = env.tasks.shift() || null;
      result = {...result,task:env.lease,wait_seconds:15};
    } else if (path === '/complete') {
      assert.equal(body.task_id, env.lease.id);
      assert.equal(body.lease_token, env.lease.lease_token);
      env.outcomes.push(body.outcome);
      env.lease = null;
      if (['blocked', 'error'].includes(body.outcome)) env.status = 'paused';
      result.status = env.status;
    } else if (path === '/pause') {env.status = 'paused'; result.status = 'paused';}
    return {ok:true,async json() {return result;}};
  };
  env.worker = () => {
    env.handlers = {};
    const context = {chrome,fetch,crypto:{randomUUID:() => 'persistent-worker'},URL,Date:{now:() => env.now},setTimeout(callback,delay) {env.now += delay; callback();}};
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

  for (const mode of ['missing URL', 'temporary about:blank', 'permission read error']) {
    const transient = environment();
    transient.addressReadyAt = transient.now + 750;
    transient.temporaryBlank = mode === 'temporary about:blank';
    transient.permissionReadError = mode === 'permission read error';
    const transientWorker = transient.worker();
    await transientWorker.start();
    assert.equal(transient.created.length, 1, mode);
    assert.equal(transient.outcomes.length, 0, `${mode}: wait for the owned placeholder address`);
    assert.equal(transient.tabs.get(transient.state().task.tab_id).url, 'https://krisha.kz/a/show/100', mode);
  }
  for (const permissionReadError of [false,true]) {
    const noAddress = environment();
    noAddress.addressReadyAt = Infinity;
    noAddress.permissionReadError = permissionReadError;
    const noAddressWorker = noAddress.worker();
    await noAddressWorker.start();
    assert.equal(noAddress.now, 105000, 'wait no longer than five seconds for placeholder URL access');
    assert.deepEqual(noAddress.outcomes, ['blocked']);
    assert.equal(noAddress.status, 'paused');
    assert.match(noAddress.requests.find(item => item.path === '/complete').body.error, /Нет доступа к адресу служебной вкладки/);
    assert.equal(noAddress.tabs.size, 1, 'keep the placeholder for diagnostics');
  }

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
  assert.equal(env.outcomes.length, 0, 'even a known bathroom waits for late author/description/gallery');
  assert.ok(env.tabs.has(tabId));
  await worker.finished({tab:{id:tabId}}, 'imported');
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
  await pauseWorker.finished({tab:{id:pausedId}}, 'imported');
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
  assert.deepEqual(malicious.outcomes, ['error']);
  assert.equal(malicious.requests.some(item => item.path === '/pause'), false, 'invalid URL preserves its real cause through complete(error)');
  for (const url of ['http://krisha.kz/a/show/1','https://krisha.kz/a/show/nope','https://krisha.kz:444/a/show/1','https://user:password@krisha.kz/a/show/1','https://example.com/a/show/1']) assert.equal(invalidWorker.canonical(url), null);
  assert.equal(invalidWorker.canonical('https://www.krisha.kz/a/show/123/?from=search#photos'), 'https://krisha.kz/a/show/123');

  const unreadable = environment(['https://krisha.kz/a/show/100','https://krisha.kz/a/show/200','https://krisha.kz/a/show/300','https://krisha.kz/a/show/400']);
  let unreadableWorker = unreadable.worker();
  await unreadableWorker.start();
  for (let index = 0; index < 3; index++) {
    await unreadableWorker.finished({tab:{id:unreadable.state().task.tab_id}}, 'unknown', 'Не удалось прочитать обязательные данные объявления', 'unreadable');
    assert.equal(unreadable.state().unreadable_streak, index + 1);
    if (index < 2) {
      assert.equal(unreadable.status, 'running', 'one unreadable page must not pause the queue');
      unreadableWorker = unreadable.worker(); // The streak survives an MV3 restart.
      await unreadableWorker.start();
      unreadable.now += 30000;
      await unreadableWorker.kick(appSender);
    }
  }
  assert.deepEqual(unreadable.outcomes, ['unknown','unknown','error']);
  assert.equal(unreadable.status, 'paused');
  assert.equal(unreadable.created.length, 3);
  assert.equal(unreadable.requests.some(item => item.path === '/pause'), false, 'do not overwrite the automatic error cause with a user-pause message');
  assert.match(unreadable.requests.filter(item => item.path === '/complete').at(-1).body.error, /Три страницы подряд/);
  assert.match(unreadable.state().message, /Три страницы подряд/);

  for (const oldStatus of ['running','paused']) {
    const staleHalted = environment();
    staleHalted.storage[KEY] = {worker_id:'persistent-worker',job_id:1,task:null,next_wake:0,halted:true,message:'Old error',unreadable_streak:3};
    staleHalted.status = oldStatus;
    const sameWorker = staleHalted.worker();
    await sameWorker.start();
    assert.equal(staleHalted.requests.filter(item => item.path === '/claim').length, 0, 'never automatically resume the same halted/paused job');
    assert.equal(staleHalted.created.length, 0);
    staleHalted.jobId = 2;
    staleHalted.status = 'running'; // New job explicitly started by the user.
    const newJobWorker = staleHalted.worker();
    await newJobWorker.start();
    assert.equal(staleHalted.requests.filter(item => item.path === '/claim').length, 1, 'extension reload discovers the newly started job despite old local halt');
    assert.equal(staleHalted.created.length, 1);
    assert.equal(staleHalted.state().job_id, 2);
    assert.equal(staleHalted.state().halted, false);
    assert.equal(staleHalted.state().unreadable_streak, 0);
    assert.equal(staleHalted.state().message, '');
  }

  const detailed = environment(['https://krisha.kz/a/show/100']);
  detailed.storage[KEY] = {worker_id:'persistent-worker',job_id:1,task:null,next_wake:0,halted:false,unreadable_streak:2};
  const detailedWorker = detailed.worker();
  await detailedWorker.start();
  await detailedWorker.finished({tab:{id:detailed.state().task.tab_id}}, 'unknown', 'Не удалось прочитать: price_kzt, area_m2. Заголовок: Продажа - №1010095181', 'unreadable');
  const diagnostic = detailed.requests.filter(item => item.path === '/complete').at(-1).body.error;
  assert.match(diagnostic, /price_kzt, area_m2/);
  assert.match(diagnostic, /Продажа - №1010095181/);

  for (const resetReason of ['unavailable','']) {
    const reset = environment(['https://krisha.kz/a/show/100','https://krisha.kz/a/show/200','https://krisha.kz/a/show/300']);
    const resetWorker = reset.worker();
    await resetWorker.start();
    await resetWorker.finished({tab:{id:reset.state().task.tab_id}}, 'unknown', '', 'unreadable');
    reset.now += 30000;
    await resetWorker.kick(appSender);
    await resetWorker.finished({tab:{id:reset.state().task.tab_id}}, 'unknown', '', resetReason);
    assert.equal(reset.state().unreadable_streak, 0, 'unavailable/complete unknown pages reset the unreadable streak');
    reset.now += 30000;
    await resetWorker.kick(appSender);
    reset.storage[KEY].unreadable_streak = 2;
    // Reload stored state to exercise reset by a successful known import.
    const knownWorker = reset.worker();
    await knownWorker.start();
    await knownWorker.imported({tab:{id:reset.state().task.tab_id}}, {bathrooms_count:2});
    await knownWorker.finished({tab:{id:reset.state().task.tab_id}}, 'imported');
    assert.equal(reset.state().unreadable_streak, 0);
  }

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
  let rejectImport = false;
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
      return rejectImport
        ? {ok:false,status:422,async json() {return {detail:[{loc:['body','floor'],msg:'floor cannot exceed floors_total',input:'sensitive-test-input'}]};}}
        : {ok:true, async json() {return {id:7,bathrooms_count:2,assessment:{score:95}};}};
    },
  });
  const ordinaryResponse = await new Promise(resolve => backgroundListener({type:'apartment-agent-import',payload:{bathrooms_count:2}}, {tab:{id:77}}, resolve));
  assert.equal(ordinaryResponse.ok, true);
  assert.equal(ordinaryResponse.listing.id, 7);
  assert.equal(importHooks[0].sender.tab.id, 77);
  assert.equal(importHooks[0].payload.bathrooms_count, 2);
  rejectImport = true;
  const badResponse = await new Promise(resolve => backgroundListener({type:'apartment-agent-import',payload:{bathrooms_count:2}}, {tab:{id:77}}, resolve));
  assert.equal(badResponse.ok,false);
  assert.match(badResponse.error,/floor: floor cannot exceed floors_total/);
  assert.doesNotMatch(badResponse.error,/object Object|sensitive-test-input/);

  const details = environment();
  details.mode = 'details';
  const detailsWorker = details.worker();
  await detailsWorker.start();
  assert.equal(details.requests.find(item => item.path === '/claim').body.extension_version,'0.2.0');
  const detailId = details.state().task.tab_id;
  const pendingDetail = await detailsWorker.imported({tab:{id:detailId}}, {bathrooms_count:2,details_extracted:false});
  assert.equal(pendingDetail.waiting_details,true);
  assert.equal(details.outcomes.length,0);
  await detailsWorker.imported({tab:{id:detailId}}, {bathrooms_count:2,details_extracted:true});
  assert.equal(details.outcomes.length,0,'full details still wait for the 12 second observation finish');
  await detailsWorker.finished({tab:{id:detailId}},'imported');
  assert.deepEqual(details.outcomes,['imported']);
  assert.equal(details.tabs.has(detailId),false);

  const detailsUnknownBath = environment();
  detailsUnknownBath.mode = 'details';
  const unknownBathDetailsWorker = detailsUnknownBath.worker();
  await unknownBathDetailsWorker.start();
  const unknownBathId = detailsUnknownBath.state().task.tab_id;
  await unknownBathDetailsWorker.imported({tab:{id:unknownBathId}}, {bathrooms_count:null,details_extracted:true});
  await unknownBathDetailsWorker.finished({tab:{id:unknownBathId}},'imported');
  assert.deepEqual(detailsUnknownBath.outcomes,['imported'],'details mode accepts a fully read page even when bath count is genuinely absent');

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
