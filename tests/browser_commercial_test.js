// Test catalog observation and the worker protocol without requesting Krisha.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {test} = require('node:test');

const pageSource = fs.readFileSync('browser-extension/commercial-page.js', 'utf8');
const workerSource = fs.readFileSync('browser-extension/commercial-worker.js', 'utf8');
const catalog = 'https://krisha.kz/arenda/kommercheskaya-nedvizhimost/ekibastuz/';
const page2 = catalog + '?das%5Brent.square%5D=2&page=2';
const catalogs = [
  catalog,
  'https://krisha.kz/prodazha/kommercheskaya-nedvizhimost/ekibastuz/',
  'https://krisha.kz/prodazha/biznes/ekibastuz/',
  'https://krisha.kz/arenda/prombazy/ekibastuz/',
  'https://krisha.kz/prodazha/prombazy/ekibastuz/',
];
const listing = {url: 'https://krisha.kz/a/show/123', title: 'Офис · 100 м²', price: '150 000 ₸', description: null, published_at: null};
const clone = value => JSON.parse(JSON.stringify(value));
const flush = () => new Promise(setImmediate);

function pageEnvironment({challenge = false, ready = false, empty = false, tracked = true} = {}) {
  const env = {challenge, ready, empty, now: 0, posted: [], timers: []};
  const location = new URL(catalog);
  const card = {
    getAttribute: name => name === 'data-id' ? '123' : '',
    querySelector(selector) {
      if (selector === 'a.a-card__title[href]') return {href: listing.url, textContent: listing.title};
      if (selector === '.a-card__price') return {textContent: listing.price};
      return null;
    },
  };
  const document = {
    // The actual SafeLine response includes this title element before it gets text.
    title: '',
    body: {get innerText() { return env.empty ? 'По вашему запросу ничего не найдено' : ''; }},
    querySelector(selector) {
      if (selector.includes('title#slg-title')) return env.challenge ? {} : null;
      if (selector === '.a-search-list' && env.ready) return {querySelectorAll: () => [card]};
      return null;
    },
    querySelectorAll(selector) {
      if (selector.includes('a[href*="page="]')) return [
        {href: 'https://other.example/ekibastuz/?page=2'},
        {href: page2},
      ];
      return [];
    },
  };
  const chrome = {runtime: {async sendMessage(message) { env.posted.push(clone(message)); return {tracked}; }}};
  const VMDate = class extends Date { static now() { return env.now; } };
  vm.runInNewContext(pageSource, {
    document, location, chrome, URL, Date: VMDate,
    setTimeout(callback, delay) { env.timers.push({at: env.now + delay, callback}); },
  });
  env.advance = async time => {
    await flush();
    while (env.timers.length && env.timers[0].at <= time) {
      const timer = env.timers.shift();
      env.now = timer.at;
      await timer.callback();
    }
    env.now = time;
  };
  return env;
}

function workerEnvironment() {
  const env = {state: {}, requests: [], updates: [], created: [], handlers: {}, tab: null, now: 0, alarm: null};
  const chrome = {
    storage: {
      sync: {async get() { return {enabled: true, apiOrigin: 'http://localhost:8000'}; }},
      session: {async get() { return clone(env.state); }, async set(values) { env.state = clone(values); }},
    },
    tabs: {
      async create(options) {
        env.created.push(clone(options));
        env.tab = {id: 11, ...options};
        return clone(env.tab);
      },
      async get() { return clone(env.tab); },
      async update(id, changed) {
        assert.equal(id, env.tab.id);
        env.updates.push(clone(changed));
        Object.assign(env.tab, changed);
      },
      async remove(id) { assert.equal(id, env.tab.id); env.tab = null; },
    },
    runtime: {
      onInstalled: {addListener(callback) { env.handlers.install = callback; }},
      onStartup: {addListener(callback) { env.handlers.startup = callback; }},
      onMessage: {addListener(callback) { env.handlers.message = callback; }},
    },
    alarms: {
      async get() { return env.alarm; },
      async create(name, options) { env.alarm = {name, ...options}; },
      onAlarm: {addListener(callback) { env.handlers.alarm = callback; }},
    },
  };
  const fetch = async (url, options) => {
    assert.ok(url.startsWith('http://localhost:8000/api/v1/commercial/browser/'));
    const path = new URL(url).pathname.split('/').at(-1);
    const body = options.body ? JSON.parse(options.body) : null;
    env.requests.push({path, body});
    const response = path === 'plan'
      ? {enabled: true, catalogs, interval_minutes: 5}
      : {ok: true, catalog: body.catalog};
    return {ok: true, async json() { return response; }};
  };
  vm.runInNewContext(workerSource, {
    chrome, fetch, URL, AbortSignal, console, Date: {now: () => env.now},
    setTimeout(callback, delay) { env.now += delay; callback(); },
  });
  env.start = async () => {
    env.handlers.install();
    await flush();
  };
  env.send = (payload, overrides = {}) => new Promise(resolve => {
    const sender = {frameId: 0, tab: {id: env.tab.id}, url: env.tab.url, ...overrides};
    assert.equal(env.handlers.message({type: 'apartment-agent-commercial-page', ...payload}, sender, resolve), true);
  });
  return env;
}

test('detects a SafeLine challenge with an empty title and resumes only when the DOM shows results', async () => {
  const env = pageEnvironment({challenge: true});
  await env.advance(5000);
  assert.deepEqual(env.posted.map(event => event.status), ['verification']);
  env.challenge = false;
  env.ready = true;
  await env.advance(10000);
  assert.deepEqual(env.posted.map(event => event.status), ['verification', 'ready']);
  assert.deepEqual(env.posted[1].cards, [listing]);
  assert.equal(env.posted[1].nextPage, page2);
});

test('does not observe a challenge after the worker rejects an unowned tab', async () => {
  const env = pageEnvironment({challenge: true, tracked: false});
  await env.advance(30000);
  assert.deepEqual(env.posted.map(event => event.status), ['verification']);
  assert.equal(env.timers.length, 0);
});

test('permits delayed catalog DOM and distinguishes an explicit empty result from a load failure', async () => {
  const pending = pageEnvironment();
  await pending.advance(8000);
  assert.deepEqual(pending.posted, []);
  pending.ready = true;
  await pending.advance(9000);
  assert.equal(pending.posted[0].status, 'ready');

  const unavailable = pageEnvironment();
  await unavailable.advance(15000);
  assert.deepEqual(unavailable.posted.map(event => event.status), ['unavailable']);

  const empty = pageEnvironment({empty: true});
  await empty.advance(0);
  assert.deepEqual(empty.posted.map(event => [event.status, event.cards]), [['ready', []]]);
});

test('uses canonical page 1, pauses on verification, and follows the catalog’s actual next URL', async () => {
  const env = workerEnvironment();
  await env.start();
  assert.deepEqual(env.created, [{url: catalog, active: false}]);

  const unowned = await env.send({status: 'verification'}, {tab: {id: 55}});
  assert.equal(unowned.tracked, false);
  assert.deepEqual(env.requests.map(request => request.path), ['plan']);

  const owned = await env.send({status: 'verification'});
  assert.equal(owned.tracked, true);
  assert.deepEqual(env.updates, [{active: true}]);
  assert.deepEqual(env.requests.at(-1), {path: 'report', body: {catalog, status: 'verification'}});
  await env.send({status: 'verification'});
  assert.equal(env.requests.filter(request => request.path === 'report').length, 1);

  await env.send({status: 'ready', cards: [listing], nextPage: page2});
  assert.equal(env.tab.url, page2);
  assert.equal(env.requests.filter(request => request.path === 'scan').length, 0);
  await env.send({status: 'ready', cards: [], nextPage: null});
  assert.deepEqual(env.requests.at(-1), {path: 'scan', body: {catalog, cards: [listing]}});
  assert.equal(env.tab.url, catalogs[1]);
});

test('starts after an unpacked extension reload even when onInstalled is not dispatched', async () => {
  const env = workerEnvironment();
  await flush();
  assert.deepEqual(env.created, [{url: catalog, active: false}]);
  assert.equal(env.alarm.periodInMinutes, 1);
  await env.start();
  assert.equal(env.created.length, 1);
  assert.equal(env.requests.filter(request => request.path === 'plan').length, 1);
});

test('preserves the first verified page if the next page fails without claiming a complete scan', async () => {
  const env = workerEnvironment();
  await env.start();
  await env.send({status: 'ready', cards: [listing], nextPage: page2});
  await env.send({status: 'unavailable'});
  assert.deepEqual(env.requests.slice(1), [
    {path: 'scan', body: {catalog, cards: [listing], complete: false}},
    {path: 'report', body: {catalog, status: 'unavailable'}},
  ]);
  assert.equal(env.tab.url, catalogs[1]);
});

test('never records a failed first page as an empty successful catalog', async () => {
  const env = workerEnvironment();
  await env.start();
  await env.send({status: 'unavailable'});
  assert.deepEqual(env.requests.slice(1), [{path: 'report', body: {catalog, status: 'unavailable'}}]);
  assert.equal(env.tab.url, catalogs[1]);
});
