const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function extract(file, text, data = null, mortgageField = null, bathroomField = null, comparisonCells = null, comparisonHeading = 'Цена м² в похожих квартирах', complexBlock = null) {
  let posted;
  const body = {innerText: text, appendChild() {}};
  const document = {
    title: '3-комнатная квартира 80 м²', body,
    currentScript: {src: 'http://localhost:8000/static/browser-import.js'},
    querySelector() { return null; },
    querySelectorAll(selector) {
      if (complexBlock && selector === 'h2, h3, h4') return [{textContent:complexBlock.heading, nextElementSibling:{tagName:'P', textContent:complexBlock.text, nextElementSibling:null}}];
      if (comparisonCells && selector === 'section, article, div') return [{textContent: comparisonHeading + ' ' + comparisonCells.join(' '), querySelectorAll() { return comparisonCells.map(textContent => ({textContent})); }}];
      if (data && selector.startsWith('script[')) return [{textContent: JSON.stringify(data)}];
      if (bathroomField && selector.startsWith('[data-testid="bathrooms"]')) return [{textContent: bathroomField}];
      if (mortgageField && selector.startsWith('[data-testid="mortgage"]')) return [{textContent: mortgageField}];
      return [];
    },
    createElement() { return {dataset: {}, style: {}, remove() {}}; },
  };
  const window = {setTimeout() {}};
  const context = {document, window, location: {href: 'https://krisha.kz/a/show/123'}, URL,
    fetch(_url, options) { posted = JSON.parse(options.body); return new Promise(() => {}); }};
  vm.runInNewContext(fs.readFileSync(file, 'utf8'), context);
  return window.ApartmentAgentExtract ? window.ApartmentAgentExtract() : posted;
}

for (const file of ['app/static/browser-import.js', 'browser-extension/extractor.js']) {
  const correct = extract(file, '3-комнатная квартира 80 м² Цена 45 000 000 ₸');
  assert.equal(correct.price_kzt, 45000000, file);
  for (const text of ['3-комнатная квартира 80 м² № 123456789', '3-комнатная квартира 80 м² от 45 000 000 ₸']) {
    const result = extract(file, text);
    assert.equal(result?.price_kzt ?? null, null, file);
  }
  const common = '3-комнатная квартира 80 м² Цена 45 000 000 ₸';
  const unknown = extract(file, `${common} Рассчитать ипотеку`);
  assert.equal(unknown.mortgage_supported, null, file);
  assert.equal(unknown.seller_name, null, file);
  assert.equal(extract(file, common, null, 'Ипотека: нет').mortgage_supported, false, file);
  assert.equal(extract(file, common, null, 'Можно в ипотеку').mortgage_supported, true, file);
  assert.equal(extract(file, `${common} Реклама: ипотека: да`).mortgage_supported, null, file);
  const structured = extract(file, common, {sellerName: 'Иван', sellerType: 'Хозяин', mortgageSupported: false, latitude: 51.15, longitude: 71.45});
  assert.equal(extract(file, common, {bathroomsCount: 2}).bathrooms_count, 2, file);
  assert.equal(extract(file, common, {bathroomsCount: 'раздельный'}).bathrooms_count, null, file);
  const lowerBathrooms = extract(file, common, null, null, 'Санузел 2 с/у и более');
  assert.equal(lowerBathrooms.bathrooms_count, 2, file);
  assert.equal(lowerBathrooms.bathrooms_count_is_lower_bound, true, file);
  assert.equal(extract(file, common, null, null, 'Санузел раздельный').bathrooms_count, null, file);
  const comparison = extract(file, common, null, null, null, ['416 667 ₸ Цена м² в этом объявлении', '845 700 ₸ Похожие квартиры в Есильском р-н', '792 420 ₸ Похожие квартиры в Астане']);
  assert.equal(comparison.krisha_district_price_per_m2, 845700, file);
  assert.equal(comparison.krisha_city_price_per_m2, 792420, file);
  assert.equal(comparison.krisha_comparison_district, 'Есильском р-н', file);
  assert.equal(unknown.krisha_city_price_per_m2, null, file);
  const unrelated = extract(file, common, null, null, null, ['845 700 ₸ Похожие квартиры в Есильском р-н', '792 420 ₸ Похожие квартиры в Астане'], 'Рекламный блок');
  assert.equal(unrelated.krisha_city_price_per_m2, null, file);
  const noDistrict = extract(file, common, null, null, null, ['792 420 ₸ Похожие квартиры в Астане']);
  assert.equal(noDistrict.krisha_district_price_per_m2, null, file);
  const complexText = 'Жилой комплекс включает благоустроенный двор и помещения для жителей.';
  const complex = extract(file, common, null, null, null, null, undefined, {heading:'Описание ЖК', text:complexText});
  assert.equal(complex.complex_description, complexText, file);
  assert.equal(complex.complex_description_source_kind, 'listing', file);
  assert.equal(complex.complex_description_source_url, 'https://krisha.kz/a/show/123', file);
  assert.equal(unknown.complex_description, null, file);
  const apartmentText = extract(file, common, null, null, null, null, undefined, {heading:'Описание квартиры', text:complexText});
  assert.equal(apartmentText.complex_description, null, file);
  assert.equal(structured.seller_name, 'Иван', file);
  assert.equal(structured.seller_type, 'Хозяин', file);
  assert.equal(structured.mortgage_supported, false, file);
  assert.equal(structured.latitude, 51.15, file);
  assert.equal(structured.longitude, 71.45, file);
}
console.log('Browser extraction checks passed');
