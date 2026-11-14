const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const code = fs.readFileSync('app/static/search-profiles.js', 'utf8');
function fixture() {
  const elements = new Map();
  const get = id => {
    if (!elements.has(id)) elements.set(id, {value:'', checked:false, disabled:false, listeners:{},
      addEventListener(name, fn) {this.listeners[name] = fn;}, checkValidity() {return true;}});
    return elements.get(id);
  };
  const document = {getElementById:get, addEventListener() {}};
  const window = {confirm() {return true;}};
  vm.runInNewContext(code, {document, window, fetch:() => new Promise(() => {}), Set, Number, Error});
  return {get, api:window.ApartmentAgentProfiles};
}
{
  const {get, api} = fixture(), control=get('searchSellerFilter');
  api.applySeller('owner'); assert.equal(control.value, 'owner');
  control.value='realtor'; control.listeners.change();
  api.applySeller('all'); assert.equal(control.value, 'realtor');
}
{
  const {get, api} = fixture(), control=get('searchSellerFilter');
  control.value='verified_agent'; control.listeners.change();
  api.applySeller('owner'); assert.equal(control.value, 'verified_agent');
}
{
  const {get, api} = fixture(), control=get('searchSellerFilter');
  api.applySeller(undefined); api.applySeller('all'); assert.equal(control.value, 'all');
}
console.log('Seller profile restoration: passed');
