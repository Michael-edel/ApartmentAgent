// In-memory DOM, map and API fixtures. No external requests or database access.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.listeners = {}; this.hidden = false; this.value = ''; this.open = false; }
  append(...nodes) { for (const n of nodes) { this.children.push(n); if (this.tag === 'select' && this.children.length === 1) this.value = n.value; } }
  replaceChildren(...nodes) { this.children = nodes; }
  setAttribute() {}
  addEventListener(type, fn) { this.listeners[type] = fn; }
  showModal() { this.open = true; }
  close() { this.open = false; this.listeners.close?.(); }
  focus() {}
  remove() {}
}

(async () => {
  const body = new Element('body');
  const document = {body, createElement: tag => new Element(tag), createTextNode: text => ({textContent:text})};
  let requests = 0, fits = 0, layers = [];
  const map = {setView() { fits++; return this; }, fitBounds() { fits++; return this; },
    getBounds() { return {contains:() => true}; }, on() {}, remove() {}, invalidateSize() {}};
  const group = {addTo() { return this; }, clearLayers() { layers = []; }};
  const L = {map:() => map, tileLayer:() => ({on() {}, addTo() {}}), layerGroup:() => group,
    divIcon: value => value, marker: (_position, options) => ({bindPopup(popup) {this.popup = popup; return this;},
      addTo() {layers.push({title:options.title, icon:options.icon, popup:this.popup}); return this;}})};
  const response = {city:'astana', items:[
    {source_url:'https://krisha.kz/a/show/1016547402', title:'Активная метка', latitude:51.1, longitude:71.4, platform:'krisha'},
    {source_url:'https://krisha.kz/a/show/22', title:'Вторая метка', latitude:51.2, longitude:71.4, platform:'krisha'},
  ], without_coordinates_items:[{source_url:'https://krisha.kz/a/show/33', title:'Объект тридцать три', platform:'krisha'}]};
  const window = {L, setTimeout, clearTimeout};
  vm.runInNewContext(fs.readFileSync('app/static/listing-map.js','utf8'), {
    window, document, L, URL, URLSearchParams, AbortController, Intl, Number, Set, Map,
    requestAnimationFrame() {}, fetch:async () => { requests++; return {ok:true,json:async () => response}; },
  });
  window.ApartmentAgentMap.open();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(requests, 1);
  assert.equal(layers.length, 2);
  const all = node => [node, ...(node.children || []).flatMap(all)];
  const select = all(body).find(node => node.tag === 'select');
  select.value = 'krisha';
  select.listeners.change();
  const missing = all(body).find(node => node.children?.some(child => child.textContent === 'Объект тридцать три'));
  assert(missing, 'unlocated item is initially present');
  const beforeFits = fits;
  window.ApartmentAgentMap.setUnavailableUrls(['https://www.krisha.kz/a/show/1016547402/?x=1','https://krisha.kz/a/show/33']);
  assert.equal(requests, 1, 'no new request');
  assert.equal(fits, beforeFits, 'viewport is not reset');
  assert.equal(select.value, 'krisha', 'source selection is preserved');
  assert.deepEqual(layers.map(x => x.title), ['Вторая метка']);
  const visibleText = node => node.hidden ? '' : [node.textContent || '', ...(node.children || []).map(visibleText)].join(' ');
  const content = visibleText(body);
  assert(!content.includes('Активная метка'));
  assert(!content.includes('Объект тридцать три'));
  assert(content.includes('Вторая метка'));
  window.ApartmentAgentMap.setUnavailableUrls([]);
  assert.equal(layers.length, 2, 'a reactivated item can reappear');
  assert.equal(fits, beforeFits);

  response.items.push(
    {source_url:'https://astana.etagi.com/realty/object/', title:'Квартира Этажей', latitude:51.21, longitude:71.4, platform:'etagi'},
    {source_url:'https://www.olx.kz/d/obyavlenie/duplicate/', title:'Дубль Krisha и OLX', latitude:51.22, longitude:71.4, platform:'olx', cross_platform:true},
  );
  window.ApartmentAgentMap.open();
  await new Promise(resolve => setImmediate(resolve));
  const sourceSelect = all(body.children.at(-1)).find(node => node.tag === 'select');
  sourceSelect.value = 'etagi';
  sourceSelect.listeners.change();
  assert.deepEqual(layers.map(x => x.title), ['Квартира Этажей']);
  assert.match(layers[0].icon.html, /listing-map-pin etagi/);
  assert.match(visibleText(layers[0].popup), /Источник: Этажи/);
  sourceSelect.value = 'cross';
  sourceSelect.listeners.change();
  assert.deepEqual(layers.map(x => x.title), ['Дубль Krisha и OLX']);
  console.log('Map availability checks passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
