// Local UI fixture. Fetch, the DOM and timeout signals are mocked.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const app = fs.readFileSync('app/static/app.js', 'utf8');
const queueControls = app.split('function normalizeUrl(')[0];
const importStart = app.indexOf("importForm?.addEventListener('submit'");
const importEnd = app.indexOf("\nform?.addEventListener('submit'", importStart);
const queuePath = '/api/v1/search/bathroom-check';
const running = {job_id:15, status:'running', mode:'details', checked:21, total:83};
const paused = {...running, status:'paused'};
const tick = () => new Promise(setImmediate);

function fixture() {
  const nodes = new Map();
  const requests = [];
  let poll;
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, {
      events:{},
      addEventListener(event, handler) { this.events[event] = handler; },
      querySelectorAll() { return []; },
    });
    return nodes.get(id);
  };
  const context = {
    document:{querySelector:id => /^#(?:bathroomCheck|startBathroomCheck|startDetailsCheck|pauseBathroomCheck|resumeBathroomCheck|cancelBathroomCheck|importForm|importMessage)/.test(id) ? node(id) : null},
    window:{setInterval(handler) { poll = handler; }, setTimeout() {}, postMessage() {}},
    location:{origin:'http://127.0.0.1:8000'}, URL,
    FormData:class { get() { return '1012345678'; } },
    AbortSignal:{timeout(ms) {
      assert.equal(ms,10000);
      const controller = new AbortController();
      controller.signal.expire = () => controller.abort(new DOMException('Mock timeout','TimeoutError'));
      return controller.signal;
    }},
    fetch(path, options) {
      return new Promise((resolve,reject) => {
        options.signal?.addEventListener('abort', () => reject(options.signal.reason));
        requests.push({path,options,reply(state,ok = true) { resolve({ok,text:async () => JSON.stringify(state)}); }});
      });
    },
    loadListings:async () => {}, loadSearchResults:async () => {}, loadSearchStatus:async () => {},
  };
  vm.runInNewContext(queueControls + app.slice(importStart,importEnd),context);
  return {node,requests,poll:() => poll(),lastRequest:() => requests.at(-1)};
}

(async () => {
  const ui = fixture();
  const pause = ui.node('#pauseBathroomCheck');
  const resume = ui.node('#resumeBathroomCheck');
  const message = ui.node('#bathroomCheckMessage');
  ui.lastRequest().reply(running);
  await tick();

  pause.events.click();
  assert.equal(ui.lastRequest().path,`${queuePath}/pause`);
  assert.match(message.textContent,/Отправляю команду «Пауза»/);
  assert.equal(pause.disabled,true);
  ui.lastRequest().options.signal.expire();
  await tick();
  assert.equal(pause.disabled,false);
  assert.match(message.textContent,/Сервер не подтвердил паузу\. Истекло время ожидания/);

  ui.poll();
  ui.lastRequest().reply({...running,checked:22});
  await tick();
  assert.match(message.textContent,/Сервер не подтвердил паузу/, 'routine polling must not erase the failure');

  pause.events.click();
  ui.lastRequest().reply({detail:'Служба временно недоступна.'},false);
  await tick();
  assert.match(message.textContent,/Служба временно недоступна/);
  ui.poll();
  ui.lastRequest().reply(paused);
  await tick();
  assert.equal(message.textContent,'Проверка на паузе.');
  assert.equal(resume.hidden,false);

  resume.events.click();
  assert.equal(ui.lastRequest().path,`${queuePath}/resume`);
  assert.match(message.textContent,/Возобновляю/);
  ui.lastRequest().reply(running);
  await tick();
  assert.equal(message.textContent,'Идёт дополнение карточек.');

  const submission = ui.node('#importForm').events.submit({preventDefault() {}});
  assert.equal(ui.lastRequest().path,`${queuePath}/start`);
  assert.equal(pause.disabled,true, 'pending single import visibly disables competing queue commands');
  assert.match(message.textContent,/Ставлю объявление в очередь импорта/);
  ui.lastRequest().options.signal.expire();
  await submission;
  assert.equal(pause.disabled,false, 'an unresponsive single import cannot lock the Pause button');
  pause.events.click();
  ui.lastRequest().reply(paused);
  await tick();
  assert.equal(message.textContent,'Проверка на паузе.');
  assert.equal(resume.hidden,false);
  console.log('Browser queue controls checks passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
