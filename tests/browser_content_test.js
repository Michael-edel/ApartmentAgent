// Local fixtures only: no website access or network requests.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const extractorSource = fs.readFileSync('browser-extension/extractor.js', 'utf8');
const contentSource = fs.readFileSync('browser-extension/content.js', 'utf8');

function fixture({fieldAt = () => 'Санузел 2 с/у и более', readyAt = 0, failures = 0, respond = true, failureMessage = 'Сервер недоступен', bodySuffix = '', mainHeading = null, captchaForm = null, extraAt = () => ({}), detailsMode = false} = {}) {
  let now = 0;
  let sequence = 0;
  const tasks = [];
  const posted = [];
  const outcomes = [];
  const reasons = [];
  const errors = [];
  const events = [];
  const messages = [];
  const notices = [];
  const setTimeout = (callback, delay) => tasks.push({at: now + delay, sequence:sequence++, callback});
  const document = {
    title:'4-комнатная квартира 100 м²',
    body:{get innerText() { return (now >= readyAt ? '4-комнатная квартира 100 м² Цена 45 000 000 ₸' : 'Загрузка объявления') + ' ' + bodySuffix + ' ' + notices.filter(node => !node.removed).map(node => node.textContent).join(' '); }, appendChild(node) { messages.push(node.textContent); notices.push(node); }},
    querySelector(selector) {return selector.startsWith('[data-apartment-agent-extension-message]') ? notices.find(node => !node.removed) || null : null;},
    querySelectorAll(selector) {
      const extra = extraAt(now);
      if (selector.startsWith('[data-apartment-agent-extension-message]')) return notices.filter(node => !node.removed);
      if (selector === 'h1, [role="heading"][aria-level="1"]') return mainHeading ? [typeof mainHeading === 'string' ? {textContent:mainHeading} : mainHeading] : [];
      if (selector.startsWith('form[action*="captcha"')) return captchaForm ? [captchaForm] : [];
      if (selector.startsWith('.offer__info-item')) return extra.properties || [];
      if (selector.startsWith('.offer__description')) return extra.description ? [{textContent:extra.description}] : [];
      if (selector.startsWith('[data-testid="seller"]')) return extra.author ? [{textContent:extra.author}] : [];
      if (selector.startsWith('[data-testid="seller-name"]')) return extra.name ? [{textContent:extra.name}] : [];
      if (selector.startsWith('.offer__photos img')) return extra.photos || [];
      const value = fieldAt(now);
      if (value && selector.startsWith('[data-testid="bathrooms"]')) return [{textContent:value}];
      return [];
    },
    createElement() {return {dataset:{}, style:{}, remove(){this.removed = true;}};},
  };
  const chrome = {runtime:{sendMessage(message, callback) {
    if (message.type === 'apartment-agent-queue-finished') { outcomes.push(message.outcome); reasons.push(message.reason || ''); errors.push(message.error || ''); events.push({at:now,outcome:message.outcome}); callback?.({ok:true}); return; }
    const sent = JSON.parse(JSON.stringify(message.payload));
    posted.push(sent);
    if (respond) setTimeout(() => callback(posted.length <= failures
      ? {ok:false, error:failureMessage}
      : {ok:true, waiting_details:detailsMode && !sent.details_extracted, observing:true, listing:{assessment:{score:95}}}), 50);
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
  return {run, advance, extract, posted, messages, outcomes, reasons, errors, events, document};
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
assert.deepEqual(stable.outcomes,['imported']);
assert.equal(stable.events[0].at,12000,'a queue tab remains open for the full observation interval');

const lateDetails = fixture({detailsMode:true,extraAt:time => time < 3000 ? {} : {
  properties:[{className:'offer__info-item',textContent:'Состояние квартиры хороший ремонт'}],
  description:'Полное описание владельца, появившееся после загрузки страницы.',
  author:'Автор объявления Хозяин недвижимости',name:'Айгуль',
  photos:[{src:'https://images.example/late-photo.jpg'}],
}});
lateDetails.run();
lateDetails.advance(11000);
assert.equal(lateDetails.posted.length,2);
assert.equal(lateDetails.posted[0].details_extracted,false);
assert.equal(lateDetails.posted[1].details_extracted,true);
assert.equal(lateDetails.posted[1].repair_condition,'хороший ремонт');
assert.equal(lateDetails.posted[1].seller_name,'Айгуль');
assert.equal(lateDetails.posted[1].photo_urls[0],'https://images.example/late-photo.jpg');
assert.equal(lateDetails.outcomes.length,0,'do not close the queued page on its first valid save');
lateDetails.advance(14000);
assert.deepEqual(lateDetails.outcomes,['imported']);
assert.equal(lateDetails.events[0].at,12000);

const metadataOnlyDetails = fixture({detailsMode:true});
metadataOnlyDetails.run();
metadataOnlyDetails.advance(14000);
assert.deepEqual(metadataOnlyDetails.outcomes,['unknown']);
assert.deepEqual(metadataOnlyDetails.reasons,['unreadable'],'metadata-only payload is not a completed details check');

const footer = fixture({bodySuffix:'Сайт защищён reCAPTCHA. Политика конфиденциальности.'});
footer.run();
footer.advance(14000);
assert.equal(footer.posted.length, 1, 'a reCAPTCHA footer on a valid listing is not a browser challenge');
assert.equal(footer.outcomes.includes('blocked'), false);

const address403 = fixture({bodySuffix:'Адрес: улица 403, дом 5. Сайт защищён reCAPTCHA.'});
address403.run();
address403.advance(14000);
assert.equal(address403.posted.length, 1, 'an address number 403 is not an HTTP error');

const blankWithFooter = fixture({readyAt:20000,bodySuffix:'Сайт защищён reCAPTCHA.'});
blankWithFooter.run();
blankWithFooter.advance(14000);
assert.deepEqual(blankWithFooter.outcomes, ['unknown'], 'a footer alone does not turn an unreadable page into a CAPTCHA');
assert.deepEqual(blankWithFooter.reasons, ['unreadable']);

for (const heading of ['CAPTCHA', 'Krisha.kz — CAPTCHA', '403 Forbidden', '429 Too Many Requests', '468', 'Проверка браузера']) {
  const challengeHeading = fixture({mainHeading:heading});
  challengeHeading.run();
  challengeHeading.advance(14000);
  assert.equal(challengeHeading.posted.length, 0, 'an explicit main heading overrides otherwise valid advertising metadata');
  assert.deepEqual(challengeHeading.outcomes, ['blocked']);
}

const bodyHttpError = fixture({bodySuffix:'HTTP Error 468'});
bodyHttpError.run();
bodyHttpError.advance(14000);
assert.equal(bodyHttpError.posted.length, 0, 'an explicit HTTP error must not import unrelated listing metadata');
assert.deepEqual(bodyHttpError.outcomes, ['blocked']);

const visibleForm = fixture({readyAt:20000,captchaForm:{style:{display:'block'}}});
visibleForm.run();
visibleForm.advance(14000);
assert.deepEqual(visibleForm.outcomes, ['blocked']);

const hiddenHeading = fixture({mainHeading:{textContent:'403 Forbidden', hidden:true}});
hiddenHeading.run();
hiddenHeading.advance(14000);
assert.equal(hiddenHeading.posted.length, 1);

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

const apiDenied = fixture({failures:100,failureMessage:'API HTTP 403'});
apiDenied.run();
apiDenied.advance(14000);
assert.deepEqual(apiDenied.outcomes, ['error'], 'our own API error banner must not be mistaken for a site CAPTCHA');
assert.match(apiDenied.messages.at(-1), /API HTTP 403/);

const missing = fixture({readyAt:20000});
missing.run();
missing.advance(14000);
assert.equal(missing.posted.length, 0);
assert.match(missing.messages.at(-1), /не удалось определить price_kzt/);
assert.deepEqual(missing.outcomes, ['unknown']);
assert.deepEqual(missing.reasons, ['unreadable']);
assert.match(missing.errors[0], /Не удалось прочитать: price_kzt/);
assert.match(missing.errors[0], /Заголовок: 4-комнатная квартира 100 м²/);

const noResponse = fixture({respond:false});
noResponse.run();
noResponse.advance(14000);
assert.equal(noResponse.posted.length, 1, 'an in-flight request is not duplicated');
assert.match(noResponse.messages.at(-1), /подтверждение сохранения не получено/);

for (const title of ['Проверка безопасности', 'Captcha', 'Are you a robot?', '403 Forbidden', 'HTTP 429 Too Many Requests', '468', 'Access denied']) {
  const challenge = fixture();
  challenge.document.title = title;
  challenge.run();
  challenge.advance(14000);
  assert.equal(challenge.posted.length, 0, 'never import or solve a browser challenge');
  assert.deepEqual(challenge.outcomes, ['blocked']);
  assert.match(challenge.messages.at(-1), /самостоятельно/);
}

for (const title of ['Объявление неактуально', 'Это объявление было удалено', 'Объявление в архиве', 'Объявление больше не актуально', 'Объявление уже неактивно', 'Архивное объявление']) {
  const unavailable = fixture();
  unavailable.document.title = title;
  unavailable.run();
  unavailable.advance(14000);
  assert.equal(unavailable.posted.length, 0);
  assert.deepEqual(unavailable.outcomes, ['unknown']);
  assert.deepEqual(unavailable.reasons, ['unavailable']);
}

console.log('Browser content lifecycle checks passed');
