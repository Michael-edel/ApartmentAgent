const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

async function main() {
  const events = new Map();
  const values = {
    searchCity:'astana', searchMarketType:'secondary', searchRooms:'3', searchMinPrice:'', searchMaxPrice:'45000000', searchMinArea:'75', searchMaxArea:'110',
    conditionFirstFloor:'off', conditionLastFloor:'off', conditionMinYear:'', conditionMinYearMode:'off', conditionMaxYear:'', conditionMaxYearMode:'off', conditionKitchen:'', conditionKitchenMode:'off',
    profileSelect:'', profileName:'Семья', profileEnabled:'', profileMessage:'',
  };
  const elements = new Map();
  function element(id) {
    if (!elements.has(id)) elements.set(id, {
      value:values[id] ?? '', checked:false, disabled:false, hidden:false, textContent:'',
      addEventListener(type, callback) { events.set(`${id}:${type}`, callback); },
      checkValidity() { return true; }, replaceChildren() {}, add() {},
    });
    return elements.get(id);
  }
  const requests=[];
  const context={
    document:{getElementById:element,addEventListener() {}},
    window:{confirm:() => true}, location:{reload() {},hash:'',pathname:'/',search:''}, history:{replaceState() {}},
    Option:function(label,value) { this.label=label; this.value=value; },
    fetch:async (url,options={}) => {
      if (options.method === 'POST') requests.push({url,body:JSON.parse(options.body)});
      return {ok:true,status:200,json:async () => options.method === 'POST' ? {id:1} : {items:[],active_profile_id:null}};
    },
  };
  vm.runInNewContext(fs.readFileSync('app/static/search-profiles.js','utf8'),context);
  await new Promise(resolve => setImmediate(resolve));
  element('profileName').value='Семья';
  events.get('profileCreate:click')();
  await new Promise(resolve => setTimeout(resolve,10));
  assert.equal(requests.length,1);
  assert.equal(requests[0].body.criteria.market_type,'secondary');
  assert.equal(requests[0].body.criteria.city,'astana');

  const source=fs.readFileSync('app/static/app.js','utf8');
  const badge=source.match(/function marketBadge\(item\) \{[\s\S]*?\n\}/)?.[0];
  assert.ok(badge, 'market badge function exists');
  const render=vm.runInNewContext(`(${badge})`);
  assert.match(render({market_type:'new'}),/Новостройка/);
  assert.match(render({market_type:'secondary'}),/Вторичное жильё/);
  assert.equal(render({market_type:null}),'');
  assert.equal(render({market_type:'unknown'}),'');
  console.log('Market UI profile and badges passed');
}
main().catch(error => { console.error(error); process.exitCode=1; });
