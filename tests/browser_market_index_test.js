const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

async function main() {
  const inputs = new Map();
  const handlers = new Map();
  const requests = [];
  const panel = {
    addEventListener(type, handler) { handlers.set(type, handler); },
    querySelector(selector) {
      if (!inputs.has(selector)) {
        const name = selector.match(/\[name="(.+)"\]/)?.[1];
        inputs.set(selector, {
          name, value: {scope:'city', days:'90'}[name] || '',
          textContent:'', innerHTML:'', hidden:true, selectedOptions:[], firstElementChild:{},
          replaceChildren() { this.innerHTML = this.value = ''; },
          addEventListener(type, handler) { handlers.set(`retry:${type}`, handler); },
        });
      }
      return inputs.get(selector);
    },
  };
  let mounted = false;
  const window = {};
  vm.runInNewContext(fs.readFileSync('app/static/listing-market-index.js', 'utf8'), {
    document:{
      addEventListener() {},
      createElement() { return panel; },
      getElementById(id) {
        assert.equal(id, 'savedListings');
        return {before(element) { mounted = element === panel; }};
      },
    },
    window, AbortController, URLSearchParams, setTimeout, clearTimeout,
    fetch(url, options) {
      return new Promise(resolve => requests.push({
        url:new URL(url, 'http://localhost'),
        signal:options.signal,
        respond(data, ok = true) { resolve({ok, json:async () => data}); },
      }));
    },
  });
  const tick = () => new Promise(resolve => setImmediate(resolve));
  const input = name => panel.querySelector(`[name="${name}"]`);
  const data = {
    locations:[], area_bands:[{key:'80_99',label:'80–99 м²'}],
    building_types:[{key:'brick',label:'Кирпичный'}], room_options:[3,4], min_sample:5,
  };
  const body = panel.querySelector('[data-market-series]');
  const status = panel.querySelector('[data-market-status]');
  const retry = panel.querySelector('[data-market-retry]');

  window.ApartmentAgentMarketIndex.bind();
  assert.ok(mounted);
  assert.equal(requests.length, 0);
  panel.open = true;
  handlers.get('toggle')();
  assert.equal(requests[0].url.pathname, '/api/v1/market-index');
  assert.equal(requests[0].url.searchParams.get('scope'), 'city');
  requests[0].respond({...data, label:'Астана', series:[
    {day:'2026-11-12',sample_size:7,median:400000,p25:350000,p75:450000},
    {day:'2026-11-13',sample_size:3,median:null,p25:null,p75:null},
  ]});
  await tick();
  assert.match(body.innerHTML, /13\.11\.2026/);
  assert.match(body.innerHTML, /12\.11\.2026/);
  assert.match(body.innerHTML, /нет данных/);
  assert.match(body.innerHTML, /Квартир в выборке на эту дату<\/span><strong>3/);

  input('scope').value = 'district';
  handlers.get('change')({target:input('scope')});
  assert.equal(requests[1].url.searchParams.get('location'), null);
  requests[1].respond({...data, locations:[{key:'есиль',label:'Есиль'}], series:[]});
  await tick();
  assert.match(input('location').innerHTML, /Есиль/);
  assert.equal(status.textContent, 'Выберите район.');

  input('location').value = 'есиль';
  handlers.get('change')({target:input('location')});
  input('building_type').value = 'brick';
  input('rooms').value = '4';
  input('area_band').value = '80_99';
  input('days').value = '30';
  handlers.get('change')({target:input('days')});
  assert.ok(requests[2].signal.aborted);
  assert.deepEqual(Object.fromEntries(requests[3].url.searchParams), {
    scope:'district', days:'30', building_type:'brick', rooms:'4', area_band:'80_99', location:'есиль',
  });
  requests[3].respond({...data, locations:[{key:'есиль',label:'Есиль'}], label:'Есиль', series:[]});
  await tick();
  requests[2].respond({...data, label:'Старый ответ', series:[]});
  await tick();
  assert.equal(status.textContent, 'Есиль');
  assert.match(body.innerHTML, /пока нет наблюдений/);

  input('scope').value = 'complex';
  handlers.get('change')({target:input('scope')});
  assert.equal(requests[4].url.searchParams.get('scope'), 'complex');
  assert.equal(requests[4].url.searchParams.get('location'), null);
  requests[4].respond({}, false);
  await tick();
  assert.equal(retry.hidden, false);
  handlers.get('retry:click')();
  requests[5].respond({...data, locations:[], series:[]});
  await tick();
  assert.equal(retry.hidden, true);
  assert.match(status.textContent, /нет наблюдений/);
  console.log('Market index filters, observations and request races passed');
}

main().catch(error => { console.error(error); process.exitCode = 1; });
