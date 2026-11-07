// All requests are local fixtures; this test never makes a network connection.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('app/static/service-worker.js', 'utf8');

function fixture() {
  const handlers = {};
  const network = [];
  const stored = [];
  const removed = [];
  const cache = new Map([['/', new Response('<html>offline panel</html>')]]);
  let offline = false;
  let status = 200;
  const context = {
    URL, Response, Request,
    self: {location:{origin:'http://panel.test'}, addEventListener(name, fn) {handlers[name] = fn;}, skipWaiting() {}, clients:{claim() {}}},
    caches: {
      async open() {return {async put(request, response) {stored.push(request.url); cache.set(request.url, response);}, async addAll() {}};},
      async match(request) {const found = cache.get(typeof request === 'string' ? request : request.url); return found?.clone();},
      async keys() {return ['apartment-agent-v1','another-app-cache','apartment-agent-v49','apartment-agent-v50','apartment-agent-v51'];},
      async delete(key) {removed.push(key);},
    },
    async fetch(request, options) {
      network.push({url:request.url,options});
      if (offline) throw Error('Offline fixture');
      return new Response('fresh response', {status});
    },
  };
  vm.runInNewContext(source, context);
  async function request(path, mode='cors') {
    const waits=[];
    let result;
    handlers.fetch({request:{method:'GET',url:new URL(path,'http://panel.test').href,mode},
      waitUntil(promise) {waits.push(promise);}, respondWith(promise) {result=promise;}});
    try {return result === undefined ? undefined : await result;}
    finally {await Promise.all(waits);}
  }
  return {handlers,network,stored,removed,request, set offline(value) {offline=value;}, set status(value) {status=value;}};
}

(async () => {
  const env = fixture();
  assert.equal((await env.request('/?release=current','navigate')).status,200);
  assert.equal(env.network[0].options.cache,'no-store');
  assert.equal(env.stored.length,1);
  env.offline=true;
  assert.match(await (await env.request('/?release=another','navigate')).text(),/offline panel/);
  await assert.rejects(env.request('/api/v1/listings'),/Offline fixture/);
  await assert.rejects(env.request('/health'),/Offline fixture/);
  assert.equal(env.network.at(-1).options.cache,'no-store');
  assert.equal((await env.request('/static/missing.js')).type,'error');
  assert.equal(await env.request('https://external.test/picture.jpg'),undefined);
  env.offline=false;
  env.status=500;
  const stored=env.stored.length;
  assert.equal((await env.request('/static/app.js?v=155')).status,500);
  assert.equal(env.stored.length,stored);
  const waits=[];
  env.handlers.activate({waitUntil(promise) {waits.push(promise);}});
  await Promise.all(waits);
  assert.deepEqual(env.removed,['apartment-agent-v1','apartment-agent-v49','apartment-agent-v50']);
  console.log('Panel service worker checks passed');
})().catch(error => {console.error(error); process.exitCode=1;});
