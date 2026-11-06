// Local fixtures only: no website access or network requests.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const extractorSource = fs.readFileSync('browser-extension/extractor.js', 'utf8');
const contentSource = fs.readFileSync('browser-extension/content.js', 'utf8');

function fixture({fieldAt = () => 'Санузел 2 с/у и более', readyAt = 0, failures = 0, respond = true} = {}) {
  let now = 0;
  let sequence = 0;
  const tasks = [];
  const posted = [];
  const outcomes = [];
  const messages = [];
  const notices = [];
  const setTimeout = (callback, delay) => tasks.push({at: now + delay, sequence:sequence++, callback});
  const document = {
    title:'4-комнатная квартира 100 м²',
    body:{get innerText() { return (now >= readyAt ? '4-комнатная квартира 100 м² Цена 45 000 000 ₸' : 'Загрузка объявления') + ' ' + notices.filter(node => !node.removed).map(node => node.textContent).join(' '); }, appendChild(node) { messages.push(node.textContent); notices.push(node); }},
    querySelector(selector) {return selector.startsWith('[data-apartment-agent-extension-message]') ? notices.find(node => !node.removed) || null : null;},
    querySelectorAll(selector) {
      if (selector.startsWith('[data-apartment-agent-extension-message]')) return notices.filter(node => !node.removed);
      const value = fieldAt(now);
      if (value && selector.startsWith('[data-testid="bathrooms"]')) return [{textContent:value}];
      return [];
    },
    createElement() {return {dataset:{}, style:{}, remove(){this.removed = true;}};},
  };
  const chrome = {runtime:{sendMessage(message, callback) {
    if (message.type === 'apartment-agent-queue-finished') { outcomes.push(message.outcome); callback?.({ok:true}); return; }
    posted.push(JSON.parse(JSON.stringify(message.payload)));
    if (respond) setTimeout(() => callback(posted.length <= failures
      ? {ok:false, error:'Сервер недоступен'}
      : {ok:true, listing:{assessment:{score:95}}}), 50);
  }}};
  const context = {
    document, chrome, location:{href:'https://krisha.kz/a/show/fixture'}, URL,
    window:{setTimeout}, Date:{now:() => now},
    // An old successful URL marker must never suppress a new page load.
    sessionStorage:{getItem(){throw new Error('Legacy session marker must not be read');}, setItem(){throw new Error('Legacy session marker must not be written');}},
  };
  vm.runInNewContext(extractorSource, context);
  const extract = () => context.window.ApartmentAgentExtract();
  const run = () => vm.runInNewContext(contentSource, context);
  const advance = target => {
    for (;;) {
      tasks.sort((a,b) => a.at-b.at || a.sequence-b.sequence);
      if (!tasks.length || tasks[0].at > target) break;
      const task = tasks.shift();
      now = task.at;
      task.callback();
    }
    now = target;
  };
  return {run, advance, extract, posted, messages, outcomes, document};
}

// Repeated extractor calls read the DOM at call time without reinjecting code.
const live = fixture({fieldAt:time => time >= 3000 ? 'Санузел 2 с/у и более' : null});
assert.equal(live.extract().bathrooms_count, null);
live.advance(3000);
assert.equal(live.extract().bathrooms_count, 2);

const stable = fixture();
stable.run();
stable.advance(14000);
assert.equal(stable.posted.length, 1, 'one successful POST for an unchanged payload despite polling');
assert.equal(stable.posted[0].floor, null, 'the rating 95/100 in our own banner must not become floor 95 of 100');
assert.match(stable.messages.at(-1), /Санузлов: не менее 2/);

const late = fixture({fieldAt:time => time >= 3000 ? 'Санузел 2 с/у и более' : null});
late.run();
late.advance(14000);
assert.deepEqual(late.posted.map(payload => payload.bathrooms_count), [null, 2]);
assert.equal(late.posted[1].bathrooms_count_is_lower_bound, true);
assert.match(late.messages.at(-1), /Санузлов: не менее 2/);

const delayedRequired = fixture({readyAt:3000});
delayedRequired.run();
delayedRequired.advance(14000);
assert.equal(delayedRequired.posted.length, 1, 'required fields appearing late are retried');

for (const layout of ['раздельный', 'совмещенный', 'совмещённый']) {
  const one = fixture({fieldAt:() => `Санузел ${layout}`});
  one.run();
  one.advance(14000);
  assert.equal(one.posted.length, 1);
  assert.equal(one.posted[0].bathrooms_count, 1);
  assert.match(one.messages.at(-1), /Санузлов: 1\./);
}

const unknown = fixture({fieldAt:() => null});
unknown.run();
unknown.advance(14000);
assert.equal(unknown.posted.length, 1);
assert.match(unknown.messages.at(-1), /количество не определено/);
assert.deepEqual(unknown.outcomes, ['unknown']);

const transientError = fixture({failures:1});
transientError.run();
transientError.advance(14000);
assert.equal(transientError.posted.length, 2);
assert.match(transientError.messages.at(-1), /сохранено/);

const failed = fixture({failures:100});
failed.run();
failed.advance(14000);
assert.equal(failed.posted.length, 3, 'network failures have a bounded retry count');
assert.match(failed.messages.at(-1), /Сервер недоступен/);
assert.deepEqual(failed.outcomes, ['error']);

const missing = fixture({readyAt:20000});
missing.run();
missing.advance(14000);
assert.equal(missing.posted.length, 0);
assert.match(missing.messages.at(-1), /не удалось определить price_kzt/);

const noResponse = fixture({respond:false});
noResponse.run();
noResponse.advance(14000);
assert.equal(noResponse.posted.length, 1, 'an in-flight request is not duplicated');
assert.match(noResponse.messages.at(-1), /подтверждение сохранения не получено/);

for (const title of ['Проверка безопасности', 'Captcha', 'Are you a robot?']) {
  const challenge = fixture();
  challenge.document.title = title;
  challenge.run();
  challenge.advance(14000);
  assert.equal(challenge.posted.length, 0, 'never import or solve a browser challenge');
  assert.deepEqual(challenge.outcomes, ['blocked']);
  assert.match(challenge.messages.at(-1), /самостоятельно/);
}

console.log('Browser content lifecycle checks passed');
