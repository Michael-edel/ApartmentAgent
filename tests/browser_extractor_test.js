const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function extract(file, text, data = null, mortgageField = null, bathroomField = null, comparisonCells = null, comparisonHeading = 'Цена м² в похожих квартирах', complexBlock = null, descriptionText = null, extra = {}) {
  let posted;
  const body = {innerText: text, appendChild(node) { extra.onNotice?.(node.textContent); }};
  const document = {
    title: text.startsWith('4-комнатная') ? '4-комнатная квартира 100 м²' : '3-комнатная квартира 80 м²', body,
    currentScript: {src: 'http://localhost:8000/static/browser-import.js'},
    querySelector(selector) {
      const name = selector.match(/meta\[property="([^"]+)"\]/)?.[1];
      return name && extra.meta?.[name] ? {content:extra.meta[name]} : null;
    },
    querySelectorAll(selector) {
      for (const [prefix,nodes] of Object.entries(extra.nodes || {})) if (selector.startsWith(prefix)) return typeof nodes === 'function' ? nodes(selector) : nodes;
      if (selector === 'h1' && extra.heading) return [{textContent:extra.heading}];
      if (descriptionText && selector.startsWith('.offer__description')) return [{textContent:descriptionText}];
      if (complexBlock && selector === 'h2, h3, h4') return [{textContent:complexBlock.heading, nextElementSibling:{tagName:'P', textContent:complexBlock.text, nextElementSibling:null}}];
      if (comparisonCells && selector === 'section, article, div') return [{textContent: comparisonHeading + ' ' + comparisonCells.join(' '), querySelectorAll() { return comparisonCells.map(textContent => ({textContent})); }}];
      if (selector === 'script:not([src])') return (extra.inlineScripts || []).map(textContent => ({textContent}));
      if (data && selector.startsWith('script[')) return [{textContent: JSON.stringify(extra.unscopedJson ? data : {'@type':'Apartment',url:'https://krisha.kz/a/show/123',...data})}];
      if (bathroomField && selector.startsWith('[data-testid="bathrooms"]')) {
        if (typeof bathroomField === 'string') return [{textContent: bathroomField}];
        return selector.includes(bathroomField.selector) ? bathroomField.nodes : [];
      }
      if (mortgageField && selector.startsWith('[data-testid="mortgage"]')) return [{textContent: mortgageField}];
      return [];
    },
    createElement() { return {dataset: {}, style: {}, remove() {}}; },
  };
  const window = {setTimeout() {}};
  const context = {document, window, location: {href: 'https://krisha.kz/a/show/123'}, URL,
    fetch(_url, options) {
      posted = JSON.parse(options.body);
      extra.onFetch?.(_url, posted);
      assert.equal(options.redirect, 'manual');
      return extra.response ? Promise.resolve(extra.response) : new Promise(() => {});
    }};
  vm.runInNewContext(fs.readFileSync(file, 'utf8'), context);
  const result = window.ApartmentAgentExtract ? window.ApartmentAgentExtract() : posted;
  extra.onState?.(window.ApartmentAgentImportState);
  return result;
}

for (const file of ['app/static/browser-import.js', 'browser-extension/extractor.js']) {
  const correct = extract(file, '3-комнатная квартира 80 м² Цена 45 000 000 ₸');
  assert.equal(correct.price_kzt, 45000000, file);
  for (const text of ['3-комнатная квартира 80 м² № 123456789', '3-комнатная квартира 80 м² от 45 000 000 ₸']) {
    const result = extract(file, text);
    assert.equal(result?.price_kzt ?? null, null, file);
  }
  const common = '3-комнатная квартира 80 м² Цена 45 000 000 ₸';
  {
    for (const field of ['только за наличный расчёт', 'только наличные', 'исключительно наличными', 'наличный расчет только']) {
      const item = extract(file, common, null, field);
      assert.equal(item.mortgage_supported, false, `${file}: ${field}`);
      assert.equal(item.property_details['Ипотека'], field, `${file}: preserve exact field evidence`);
    }
    for (const field of ['наличные', 'наличные или ипотека', 'не только наличные', 'только за наличный расчёт или ипотека', 'скидка только наличными']) {
      const item = extract(file, common, null, field);
      assert.equal(item.mortgage_supported, null, `${file}: ambiguous field ${field}`);
    }
    const description = 'Квартира в хорошем состоянии. Продажа только за наличный расчёт.';
    const item = extract(file, common, null, null, null, null, undefined, null, description);
    assert.equal(item.description, description, 'seller description reaches the server verbatim');
    assert.equal(item.mortgage_supported, null, 'seller prose is classified by the server, with provenance');
  }
  for (const [city, latitude, longitude] of [['Астана',51.128,71.43], ['Алматы',43.2389,76.8897], ['Шымкент',42.315,69.59], ['Караганда',49.806,73.086], ['Павлодар',52.287,76.967]]) {
    const result = extract(file, common, {address:{addressLocality:city}, geo:{latitude,longitude}});
    assert.equal(result.city, city, file);
    assert.equal(result.city_observed, true, file);
    assert.equal(result.latitude, latitude, file);
    assert.equal(result.longitude, longitude, file);
  }
  const mismatched = extract(file, common, {city:'Алматы', geo:{latitude:51.128,longitude:71.43}});
  assert.equal(mismatched.latitude, null, file);
  const unknownCity = extract(file, common, {city:'Косшы', geo:{latitude:51.128,longitude:71.43}});
  assert.equal(unknownCity.city, 'Косшы', file);
  assert.equal(unknownCity.latitude, null, file);
  assert.equal(extract(file, common).city_observed, false, file);
  const archiveText = 'В архиве. Объявление может быть неактуальным.';
  const archiveCase = node => extract(file, common, null, null, null, null, undefined, null, null,
    {nodes:{'div, p, section, aside, span':[node]}});
  assert.equal(archiveCase({textContent:archiveText}).availability_status, 'archived', file);
  assert.equal(archiveCase({textContent:archiveText, hidden:true}).availability_status, null, file);
  assert.equal(archiveCase({textContent:archiveText, parentElement:{className:'offer__description'}}).availability_status, null, file);
  assert.equal(archiveCase({textContent:archiveText, parentElement:{className:'recommendations'}}).availability_status, null, file);
  if (file === 'app/static/browser-import.js') {
    const requests = [];
    extract(file, common, null, null, null, null, undefined, null, null,
      {nodes:{'div, p, section, aside, span':[{textContent:archiveText}]}, onFetch:(url, body) => requests.push({url,body})});
    assert.equal(requests.length,1);
    assert.match(requests[0].url, /\/api\/v1\/listings\/availability$/);
    assert.equal(requests[0].body.availability_status,'archived');
    assert.equal(requests[0].body.price_kzt,undefined,'archived listing data must not overwrite existing card');
  }
  for (const cityField of ['Город Астана, р-н Байконур', 'Город Астана, Байконур р-н', 'Город Астана, район Байконур', 'Город Астана, Байконур район']) {
    const result = extract(file, `4-комнатная квартира 100 м² Цена 45 000 000 ₸ ${cityField}`);
    assert.equal(result.rooms, 4, file);
    assert.equal(result.district, 'Байконур район', `${file}: ${cityField}`);
  }
  assert.equal(extract(file, common, {addressRegion:'Астана, р-н Байконур'}).district, 'Байконур район', file);
  assert.equal(extract(file, common, {districtName:'Байконур'}).district, 'Байконур район', file);
  assert.equal(extract(file, `${common} ЖК Байконур, раздельный вход`).district, null, file);
  assert.equal(extract(file, `${common} Город Астана, Есильский р-н`).district, 'Есильский район', file);
  const unknown = extract(file, `${common} Рассчитать ипотеку`);
  assert.equal(unknown.mortgage_supported, null, file);
  assert.equal(unknown.seller_name, null, file);
  assert.equal(extract(file, common, null, 'Ипотека: нет').mortgage_supported, false, file);
  assert.equal(extract(file, common, null, 'Можно в ипотеку').mortgage_supported, true, file);
  assert.equal(extract(file, common, {mortgageSupported:false}, 'Да').mortgage_supported, true, file);
  assert.equal(extract(file, common, {mortgageSupported:true}, 'Нет').mortgage_supported, false, file);
  const explicitMortgage = extract(file, common, {property_details:{'Ипотека':'Нет'}}, 'Да');
  assert.equal(explicitMortgage.mortgage_supported,true,file);
  assert.equal(explicitMortgage.property_details['Ипотека'],'Да',file);
  assert.equal(extract(file, common, null, 'В ипотеку не проходит').mortgage_supported, false, file);
  assert.equal(extract(file, common, null, 'Не подходит под ипотеку').mortgage_supported, false, file);
  assert.equal(extract(file, common, null, 'Ипотека возможна?').mortgage_supported, null, file);
  assert.equal(extract(file, `${common} Реклама: ипотека: да`).mortgage_supported, null, file);
  for (const phrase of ['Ипотеки нет', 'Ипотека жоқ', 'Ипотека жок']) {
    assert.equal(extract(file, common, null, phrase).mortgage_supported, false, file);
    const description = extract(file, common, null, null, null, null, undefined, null, `${phrase}. Описание объявления.`);
    assert.equal(description.mortgage_supported, null, `${file}: prose must be interpreted by the server with provenance`);
    assert.equal(description.description, `${phrase}. Описание объявления.`, file);
    assert.equal(extract(file, `${common} Реклама: ${phrase}`).mortgage_supported, null, file);
  }
  for (const phrase of ['Рассчитать ипотеку', 'Квартира без ипотеки', 'С ипотекой нет проблем', 'Ипотека жоқ емес']) {
    assert.equal(extract(file, common, null, null, null, null, undefined, null, phrase).mortgage_supported, null, file);
  }
  assert.equal(extract(file, common, null, 'Можно в ипотеку и без ипотеки').mortgage_supported, true, file);
  const structured = extract(file, common, {sellerName: 'Иван', sellerType: 'Хозяин', mortgageSupported: false, latitude: 51.15, longitude: 71.45});
  assert.equal(extract(file, common, {bathroomsCount: 2}).bathrooms_count, 2, file);
  assert.equal(extract(file, common, {bathroomsCount: 'раздельный'}).bathrooms_count, 1, file);
  const lowerBathrooms = extract(file, common, null, null, 'Санузел 2 с/у и более');
  assert.equal(lowerBathrooms.bathrooms_count, 2, file);
  assert.equal(lowerBathrooms.bathrooms_count_is_lower_bound, true, file);
  const separateBathroom = extract(file, common, {bathroomsCount: 2}, null, 'Санузел раздельный');
  assert.equal(separateBathroom.bathrooms_count, 1, file);
  assert.equal(separateBathroom.bathrooms_count_is_lower_bound, false, file);
  for (const layout of ['совмещённый', 'совмещенный']) {
    assert.equal(extract(file, common, {bathroomsCount: layout}).bathrooms_count, 1, file);
    assert.equal(extract(file, common, null, null, `Санузел ${layout}`).bathrooms_count, 1, file);
  }
  assert.equal(extract(file, `${common} Раздельный санузел на рекламном баннере`).bathrooms_count, null, file);
  assert.equal(extract(file, common, {bathroomsCount: 'раздельный'}, null, 'Санузел 2 с/у и более').bathrooms_count, 2, file);
  for (const [value, count, lower] of [['2 с/у и более', 2, true], ['раздельный', 1, false], ['совмещенный', 1, false], ['совмещённый', 1, false]]) {
    const dd = {tagName:'DD', textContent:value};
    const label = {tagName:'DT', textContent:'Санузел', nextElementSibling:dd};
    const rows = [
      {selector:'.offer__info-item', nodes:[{textContent:`Санузел${value}`, children:[label, dd]}]},
      {selector:'[data-name="flat.toilet"]', nodes:[{textContent:value, getAttribute:name => name === 'data-name' ? 'flat.toilet' : null}]},
      {selector:'dt', nodes:[label]},
      // The row has extra decoration; exact label/value siblings remain readable.
      {selector:'.offer__info-item', nodes:[{textContent:`Санузел ${value} Подробнее`, children:[label, dd]}]},
    ];
    for (const row of rows) {
      const result = extract(file, common, null, null, row);
      assert.equal(result.bathrooms_count, count, `${file}: ${row.selector} ${value}`);
      assert.equal(result.bathrooms_count_is_lower_bound, lower, `${file}: ${row.selector} ${value}`);
    }
  }
  assert.equal(extract(file, common, null, null, {selector:'dt', nodes:[{tagName:'DT', textContent:'Количество комнат', nextElementSibling:{tagName:'DD', textContent:'2'}}]}).bathrooms_count, null, file);
  for (const hidden of [{hidden:true}, {style:{display:'none'}}, {parentElement:{style:{visibility:'hidden'}}}, {getAttribute:name => name === 'aria-hidden' ? 'true' : null}]) {
    const result = extract(file, common, null, null, {selector:'.offer__info-item', nodes:[
      {textContent:'Санузел 2 с/у и более', ...hidden}, {textContent:'Санузел раздельный'},
    ]});
    assert.equal(result.bathrooms_count, 1, `${file}: hidden two-plus must not override visible separate`);
    assert.equal(result.bathrooms_count_is_lower_bound, false, file);
  }
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

  const parent = {className:'offer__advertisement'};
  const property = (label, value, context = parent) => {
    const dd = {tagName:'DD',textContent:value};
    const dt = {tagName:'DT',textContent:label,nextElementSibling:dd};
    return {className:'offer__info-item',textContent:`${label} ${value}`,children:[dt,dd],parentElement:context};
  };
  const propertyRows = [
    property('Город','Астана, р-н Байконур'), property('Адрес','Кудайбердыулы 36/3'),
    property('Тип дома','кирпичный'), property('Год постройки','2011'), property('Этаж','3 из 9'),
    property('Площадь','106.4 м²'), property('Количество комнат','4'), property('Санузел','2 с/у и более'),
    property('Состояние квартиры','не новый, но аккуратный ремонт'), property('В ипотеку','Да'),
    property('Высота потолков','2.8 м'), property('Особенности планировки','изолированные комнаты'),
  ];
  const gallery = [
    {src:'https://images.example/target-1.jpg',parentElement:parent},
    {dataset:{full:'https://images.example/target-2-large.jpg'},src:'https://images.example/target-2-thumb.jpg',parentElement:parent},
    {src:'https://images.example/recommendation.jpg',parentElement:{className:'recommendations'}},
  ];
  const fullText = 'Просторная квартира с изолированными комнатами.\n\nОстаётся мебель. Возможна покупка в ипотеку.';
  const authorBlock = {textContent:'Автор объявления Хозяин недвижимости',parentElement:{tagName:'ASIDE'}};
  const full = extract(file, '4-комнатная квартира 106.4 м² Цена 45 093 093 ₸',
    {floor:9,floorsTotal:8,year:2005,sellerName:'Старое имя',sellerType:'Компания',mortgageSupported:false},
    null,null,null,undefined,null,null, {
      heading:'4-комнатная квартира · 106.4 м² · 3/9 этаж, Кудайбердыулы 36/3 — Район Встречи',
      meta:{'og:description':'Короткое описание','og:image':'https://images.example/og.jpg'},
      nodes:{
        '.offer__price':[{textContent:'45 093 093 ₸'}],
        '.offer__info-item':propertyRows,
        '.offer__description':[{textContent:fullText,parentElement:parent}],
        '[data-testid="seller"]':[authorBlock],
        '[data-testid="seller-name"]':[{textContent:'Айгуль',parentElement:authorBlock}],
        '.offer__photos img':gallery,
        'img[src]':[{src:'https://images.example/site-logo.png'}],
      },
    });
  assert.equal(full.details_extracted,true,file);
  assert.equal(full.description,fullText,file);
  assert.equal(full.seller_name,'Айгуль',file);
  assert.equal(full.seller_type,'Хозяин недвижимости',file);
  assert.equal(full.address,'Кудайбердыулы 36/3',file);
  const roleCase = (panelText, role, data = null, badges = []) => {
    const panel = {className:'owners__card', textContent:panelText, parentElement:{tagName:'ASIDE'}, querySelectorAll() { return badges; }};
    return extract(file, common, data, null,null,null,undefined,null,null, {
      nodes:{'[data-testid="seller"]':[panel], '[data-testid="seller-type"]':[{textContent:role,parentElement:panel}]},
    });
  };
  assert.equal(roleCase('Автор объявления Хозяин недвижимости Ответил риелтор? Сообщите нам','Хозяин недвижимости').seller_type,'Хозяин недвижимости',file);
  assert.equal(roleCase('Автор объявления Специалист','Специалист').seller_type,'Специалист',file);
  assert.equal(roleCase('Автор объявления Специалист Специалист подтвердил личность документами','Специалист').seller_type,'Специалист — личность подтверждена',file);
  assert.equal(roleCase('Автор объявления Специалист Крыша Агент','Специалист',{sellerType:'Хозяин'}).seller_type,'Крыша Агент',file);
  assert.equal(roleCase('Автор объявления Специалист','Специалист',null,[{getAttribute(name) { return name === 'aria-label' ? 'Крыша Агент' : null; }}]).seller_type,'Крыша Агент',file);
  assert.equal(extract(file, common, {sellerType:'Крыша Агент'}).seller_type,'Крыша Агент',file);
  assert.equal(extract(file, `${common} Крыша Агент`, {sellerType:'Хозяин'}).seller_type,'Хозяин',`${file}: listing text must not supply seller role`);
  // Krisha's public author panel may render a seller name and role as siblings,
  // without a dedicated seller-type attribute on the role element.
  const nestedAuthor = (panelText, roleText) => {
    const panel = {className:'owners__card', textContent:panelText, parentElement:{tagName:'ASIDE'}};
    const role = {tagName:'SPAN',textContent:roleText,parentElement:panel};
    panel.querySelectorAll = selector => selector.startsWith('span') ? [role] : [];
    return extract(file,common,null,null,null,null,undefined,null,null,{nodes:{'[data-testid="seller"]':[panel]}});
  };
  assert.equal(nestedAuthor('Автор объявления Капарова Дана Специалист Специалист подтвердил личность документами','Специалист').seller_type,
    'Специалист — личность подтверждена', `${file}: role in visible author descendants`);
  assert.equal(nestedAuthor('Автор объявления Хозяин недвижимости Ответил риелтор? Сообщите нам','Хозяин недвижимости').seller_type,
    'Хозяин недвижимости', `${file}: owner must not become realtor from complaint text`);
  const employerNode = {className:'seller-address',textContent:'Работает в компании Royal Invest'};
  const exactHeadline = '4-комнатная квартира · 106.4 м² · 3/9 этаж, Кудайбердыулы 36/3 — Район Встречи';
  const employerBeforeHeadline = extract(file, common,null,null,null,null,undefined,null,null,{
    heading:exactHeadline,nodes:{'[itemprop="streetAddress"]':[employerNode]},
  });
  assert.equal(employerBeforeHeadline.address,'Кудайбердыулы 36/3',`${file}: seller employer must not override the apartment address`);
  const officeAddress = {className:'address',textContent:'Астана, Нура р-н, пр. Улы Дала, 13',parentElement:{className:'owners__card'}};
  const actualNoFloorHeadline = '4-комнатная квартира · 95.66 м², Айтматова 21';
  const noFloorHeadline = extract(file,common,null,null,null,null,undefined,null,null,{
    heading:actualNoFloorHeadline,nodes:{'[itemprop="streetAddress"]':[officeAddress]},
  });
  assert.equal(noFloorHeadline.address,'Айтматова 21',`${file}: own h1 without floor supplies the apartment address before the author's office`);
  const corpusList = 'Е-321 17/3,17/2, 17/1, 15/1, 15/2, 15/3';
  const multipleBuildings = extract(file,common,null,null,null,null,undefined,null,null,{
    heading:`4-комнатная квартира · 104.4 м², ${corpusList}`,nodes:{'[itemprop="streetAddress"]':[officeAddress]},
  });
  assert.equal(multipleBuildings.address,corpusList,`${file}: preserve every advertised building instead of substituting the agent's office`);
  const officeBeforeFullHeadline = extract(file,common,null,null,null,null,undefined,null,null,{
    heading:exactHeadline,nodes:{'[itemprop="streetAddress"]':[officeAddress]},
  });
  assert.equal(officeBeforeFullHeadline.address,'Кудайбердыулы 36/3',file);
  for (const context of ['owners__card','seller-card','offer__contacts','author__profile','agent-address']) {
    const officeOnly = extract(file,common,null,null,null,null,undefined,null,null,{
      nodes:{'[itemprop="streetAddress"]':[{className:'address',textContent:'пр. Улы Дала, 13',parentElement:{className:context}}]},
    });
    assert.equal(officeOnly.address,null,`${file}: ${context} is not the apartment's address`);
  }
  const officeInBody = {className:'address',textContent:'улица Офисная 99',parentElement:{className:'owners__card'}};
  const noBodyOffice = extract(file,`${common} улица Офисная 99`,null,null,null,null,undefined,null,null,{
    nodes:{'[itemprop="streetAddress"]':[officeInBody]},
  });
  assert.equal(noBodyOffice.address,null,`${file}: do not reintroduce the author's address through the page-text fallback`);
  const incompleteHeadline = extract(file,common,{address:'Айтматова 21'},null,null,null,undefined,null,null,{
    heading:'4-комнатная квартира · 95.66 м², Айтматова',nodes:{'[itemprop="streetAddress"]':[officeAddress]},
  });
  assert.equal(incompleteHeadline.address,'Айтматова 21',`${file}: scoped JSON supplies the address when h1 does not contain a house number`);
  const streetOnly = extract(file,common,null,null,null,null,undefined,null,null,{heading:'4-комнатная квартира · 95.66 м², Айтматова'});
  assert.equal(streetOnly.address,null,`${file}: never invent a house number for an incomplete h1 address`);
  const explicitNoFloorAddress = extract(file,common,null,null,null,null,undefined,null,null,{
    heading:actualNoFloorHeadline,nodes:{'.offer__info-item':[property('Адрес','Кенен Азербаев 6б')],'[itemprop="streetAddress"]':[officeAddress]},
  });
  assert.equal(explicitNoFloorAddress.address,'Кенен Азербаев 6б',file);
  const employerOnly = extract(file, common,{address:'Работает в компании Royal Invest'},null,null,null,undefined,null,null,{
    nodes:{'[itemprop="streetAddress"]':[employerNode]},
  });
  assert.equal(employerOnly.address,null,`${file}: employer alone is not an apartment address`);
  const explicitAddress = extract(file, common,null,null,null,null,undefined,null,null,{
    heading:exactHeadline,nodes:{'.offer__info-item':[property('Адрес','Кенен Азербаев 6б')],'[itemprop="streetAddress"]':[employerNode]},
  });
  assert.equal(explicitAddress.address,'Кенен Азербаев 6б',`${file}: an explicit apartment address is retained`);
  const structuredAddress = extract(file, common,{seller:{address:'Работает в компании Royal Invest'},listing:{streetAddress:'Кудайбердыулы 36/3'}},null,null,null,undefined,null,null,{
    nodes:{'[itemprop="streetAddress"]':[employerNode]},
  });
  assert.equal(structuredAddress.address,'Кудайбердыулы 36/3',`${file}: skip the seller employer and keep looking in structured data`);
  assert.equal(full.district,'Байконур район',file);
  assert.equal(full.area_m2,106.4,file);
  assert.equal(full.rooms,4,file);
  assert.equal(full.floor,3,file);
  assert.equal(full.floors_total,9,file);
  assert.equal(full.building_year,2011,file);
  assert.equal(full.building_type,'brick',file);
  assert.equal(full.mortgage_supported,true,file);
  assert.equal(full.repair_condition,'не новый, но аккуратный ремонт',file);
  assert.equal(full.property_details['Высота потолков'],'2.8 м',file);
  assert.equal(full.property_details['Особенности планировки'],'изолированные комнаты',file);
  assert.deepEqual(Array.from(full.photo_urls), ['https://images.example/target-1.jpg','https://images.example/target-2-large.jpg','https://images.example/og.jpg'],file);
  const partial = extract(file, common, null,null,null,null,undefined,null,null,{meta:{'og:description':'Короткая meta без DOM'}});
  assert.equal(partial.details_extracted,false,file);
  assert.equal(Object.keys(partial.property_details).length,0,file);
  assert.equal(partial.seller_name,null,file);
  assert.equal(partial.repair_condition,null,file);
  assert.equal(extract(file,common,{sellerName:'Хозяин недвижимости'}).seller_name,null,file);
  assert.equal(extract(file,common,{ownerName:'+77001234567'}).seller_name,null,file);
  const sidebars = extract(file, common,null,null,null,null,undefined,null,null,{nodes:{
    '.offer__info-item':[property('Ремонт','евроремонт',{tagName:'ASIDE'}),property('Санузел','2 с/у и более',{className:'recommendations'})],
    '.offer__description':[{textContent:'Ипотеки нет. Купите другую квартиру.',parentElement:{className:'advertisement'}}],
    '[data-testid="seller-name"]':[{textContent:'Рекламный агент',parentElement:{className:'recommendations'}}],
  }});
  assert.equal(sidebars.details_extracted,false,file);
  assert.equal(sidebars.bathrooms_count,null,file);
  assert.equal(sidebars.mortgage_supported,null,file);
  assert.equal(sidebars.seller_name,null,file);
  const ownSidebar = {tagName:'ASIDE',className:'offer__sidebar'};
  const foreignSidebar = {tagName:'ASIDE',className:'offer__sidebar',parentElement:{className:'recommendations'}};
  const sidebarAuthor = {className:'offer__contacts',parentElement:ownSidebar};
  const adInSidebar = {className:'ad-banner',parentElement:ownSidebar};
  const sidebarDescription = 'В квартире сделан свежий ремонт, окна выходят во двор.';
  const sidebarDetails = extract(file,common,null,null,null,null,undefined,null,null,{nodes:{
    '.offer__info-item':[
      property('Ремонт','чужой ремонт',foreignSidebar),
      property('Ремонт','офисный ремонт',sidebarAuthor),
      property('Ремонт','хороший ремонт',ownSidebar),
      property('Ипотека','Да',adInSidebar),
      property('Ипотека','Нет',ownSidebar),
    ],
    '.offer__description':[
      {textContent:'Длинное описание похожего объявления, которое нельзя брать для этой квартиры.',parentElement:foreignSidebar},
      {textContent:'Длинное описание агентства недвижимости, которое нельзя сохранять в карточку.',parentElement:sidebarAuthor},
      {textContent:sidebarDescription,parentElement:ownSidebar},
    ],
  }});
  assert.equal(sidebarDetails.repair_condition,'хороший ремонт',file);
  assert.equal(sidebarDetails.mortgage_supported,false,file);
  assert.equal(sidebarDetails.description,sidebarDescription,file);
  const sidebarBadge = extract(file,common,null,null,null,null,undefined,null,null,{nodes:{
    '[data-testid="mortgage"]':[
      {textContent:'Нет',parentElement:{tagName:'ASIDE'}},
      {textContent:'Нет',parentElement:foreignSidebar},
      {textContent:'Нет',parentElement:sidebarAuthor},
      {textContent:'Да',parentElement:ownSidebar},
    ],
  }});
  assert.equal(sidebarBadge.mortgage_supported,true,file);
  assert.equal(sidebarBadge.details_extracted,true,file);
  const sidebarInfo = {className:'offer__info',parentElement:ownSidebar};
  const splitInfo = (label,value,valueClass = 'offer__info-value') => ({
    className:'offer__info-title',textContent:label,parentElement:sidebarInfo,
    nextElementSibling:{className:valueClass,textContent:value,parentElement:sidebarInfo},
  });
  const splitDetails = extract(file,common,null,null,null,null,undefined,null,null,{nodes:{
    '.offer__info-item':selector => selector.includes('.offer__info .offer__info-title')
      ? [splitInfo('Ремонт','офисный ремонт','company-caption'),splitInfo('Ремонт','свежий ремонт'),splitInfo('В ипотеку','проходит')] : [],
  }});
  assert.equal(splitDetails.repair_condition,'свежий ремонт',file);
  assert.equal(splitDetails.mortgage_supported,true,file);
  const exactCondition = 'не новый, но аккуратный ремонт';
  const plainConditionLabel = (context, text = exactCondition) => {
    const row = {tagName:'DIV',parentElement:context};
    const value = {tagName:'DIV',textContent:text,parentElement:row};
    const label = {tagName:'SPAN',textContent:'Состояние квартиры',parentElement:row,nextElementSibling:value};
    row.children = [label,value];
    return label;
  };
  const plainInfo = {className:'offer__info',parentElement:{tagName:'ASIDE'}};
  const exactConditionSelector = ':is(.offer__info, .offer__sidebar)';
  const hiddenCondition = plainConditionLabel(plainInfo,'скрытая заглушка ремонта');
  hiddenCondition.nextElementSibling.hidden = true;
  const unrelatedNeighbor = plainConditionLabel(plainInfo,'посторонний текст');
  unrelatedNeighbor.parentElement.children.push({});
  const notFromConditionRow = [
    plainConditionLabel({className:'offer__info',parentElement:{tagName:'ASIDE',className:'nearby-adverts'}},'ремонт другой квартиры'),
    plainConditionLabel(sidebarAuthor,'ремонт офиса'),
    plainConditionLabel(adInSidebar,'ремонт из рекламы'),
    plainConditionLabel({tagName:'ASIDE'},'ремонт из сторонней колонки'),
    hiddenCondition,
    unrelatedNeighbor,
    plainConditionLabel(plainInfo,'Год постройки'),
    plainConditionLabel(plainInfo,'2'),
  ];
  const falseCondition = extract(file,common,null,null,null,null,undefined,null,'Состояние квартиры описано в свободном тексте.',{
    nodes:{[exactConditionSelector]:notFromConditionRow},
  });
  assert.equal(falseCondition.repair_condition,null,file);
  for (const context of [plainInfo,ownSidebar]) {
    const rows = [...notFromConditionRow,plainConditionLabel(context)];
    const fallback = extract(file,common,{rooms:3,area:80,price:45000000,repairCondition:'устаревшее значение'},null,null,null,undefined,null,null,{
      nodes:{[exactConditionSelector]:rows},
    });
    assert.equal(fallback.property_details['Состояние квартиры'],exactCondition,file);
    assert.equal(fallback.repair_condition,exactCondition,file);
    assert.equal(fallback.details_extracted,true,file);
    const directWins = extract(file,common,null,null,null,null,undefined,null,null,{
      nodes:{'.offer__info-item':[property('Состояние квартиры','новый ремонт')],[exactConditionSelector]:rows},
    });
    assert.equal(directWins.repair_condition,'новый ремонт',file);
  }
  const visibleSpecs = [
    ['Город','Астана, Алматы р-н'], ['Тип дома','кирпичный'], ['Год постройки','2011'],
    ['Этаж','3 из 10'], ['Площадь','106.4 м²'], ['Состояние квартиры',exactCondition], ['Санузел','2 с/у и более'],
  ];
  const specElement = (tagName, textContent, children = []) => {
    const node = {tagName,textContent,children};
    children.forEach((child,index) => {
      child.parentElement = node;
      child.nextElementSibling = children[index + 1] || null;
    });
    return node;
  };
  const specBlock = (values, layout = 'wrapped', context = {tagName:'ASIDE',className:'unknown-sidebar'}) => {
    const group = {tagName:'DIV',className:'unknown-specs',children:[],parentElement:context};
    const repairLabels = [];
    for (const [name,value] of values) {
      const label = specElement(layout === 'definition' ? 'DT' : 'SPAN',name);
      if (name === 'Состояние квартиры') repairLabels.push(label);
      const renderedValue = specElement(layout === 'definition' ? 'DD' : layout === 'table' ? 'TD' : 'DIV',value);
      renderedValue.innerText = value.replace('аккуратный ремонт','аккуратный\nремонт');
      if (layout === 'definition') {
        group.tagName = 'DL';
        group.children.push(label,renderedValue);
        label.parentElement = renderedValue.parentElement = group;
        label.nextElementSibling = renderedValue;
      } else {
        const labelCell = specElement(layout === 'table' ? 'TH' : 'DIV',name,[label]);
        const row = specElement(layout === 'table' ? 'TR' : 'DIV',`${name} ${value}`,[labelCell,renderedValue]);
        const siblingRow = layout === 'table' ? row : specElement('DIV',row.textContent,[row]);
        siblingRow.parentElement = group;
        group.children.push(siblingRow);
      }
    }
    return {group,repairLabels};
  };
  const structureSelector = 'dt, th, td, label, div, span, p, b, strong';
  const structuralResult = blocks => extract(file,common,null,null,null,null,undefined,null,null,{
    nodes:{[structureSelector]:blocks.flatMap(block => block.repairLabels)},
  });
  for (const layout of ['wrapped','definition','table']) {
    const result = structuralResult([specBlock(visibleSpecs,layout)]);
    assert.equal(result.repair_condition,exactCondition,`${file}: ${layout} exact condition without CSS classes`);
    assert.equal(result.property_details['Состояние квартиры'],exactCondition,file);
    assert.equal(result.details_extracted,true,file);
  }
  const shortSpecs = specBlock(visibleSpecs.filter(([label]) => ['Состояние квартиры','Город','Тип дома'].includes(label)));
  const hiddenSpecs = specBlock(visibleSpecs,'wrapped',{hidden:true});
  const foreignSpecs = specBlock(visibleSpecs,'wrapped',{className:'similar-listings'});
  const promoSpecs = specBlock(visibleSpecs,'wrapped',{className:'promotion-widget'});
  const officeSpecs = specBlock(visibleSpecs,'wrapped',{className:'offer__contacts'});
  const linkedSpecs = specBlock(visibleSpecs);
  linkedSpecs.group.querySelectorAll = () => [{href:'https://krisha.kz/a/show/999'}];
  const rejectedSpecs = [shortSpecs,hiddenSpecs,foreignSpecs,promoSpecs,officeSpecs,linkedSpecs];
  assert.equal(structuralResult(rejectedSpecs).repair_condition,null,file);
  assert.equal(structuralResult([...rejectedSpecs,specBlock(visibleSpecs)]).repair_condition,exactCondition,file);
  const conflictingSpecs = specBlock(visibleSpecs.map(([label,value]) => [label,label === 'Состояние квартиры' ? 'требуется ремонт' : value]));
  assert.equal(structuralResult([specBlock(visibleSpecs),conflictingSpecs]).repair_condition,null,file);
  const nativeTerm = splitInfo('Ремонт','аккуратный ремонт','');
  nativeTerm.tagName = 'DT';
  nativeTerm.nextElementSibling.tagName = 'DD';
  const nativeDetails = extract(file,common,null,null,null,null,undefined,null,null,{nodes:{'.offer__info-item':[nativeTerm]}});
  assert.equal(nativeDetails.repair_condition,'аккуратный ремонт',file);
  for (const [textContent,expected] of [
    ['Ипотека возможна',true],['Ипотека недоступна',false],['Ипотека возможна?',null],
    ['Ипотека недоступна?',null],['Ипотека возможна в другом ЖК',null],['Ипотека — рассчитать платёж',null],
  ]) {
    const inline = extract(file,common,null,null,null,null,undefined,null,null,{nodes:{
      '.offer__info-item':[{className:'offer__info-item',textContent}],
    }});
    assert.equal(inline.mortgage_supported,expected,`${file}: ${textContent}`);
    if (expected !== null) assert.equal(inline.property_details['Ипотека'],textContent,file);
  }
  const badCalc = extract(file, common,null,null,null,null,undefined,null,null,{nodes:{'.offer__info-item':[property('Ипотека','Рассчитать ипотеку')]}});
  assert.equal(badCalc.mortgage_supported,null,file);
  const noMortgage = extract(file, common,null,null,null,null,undefined,null,null,{nodes:{'.offer__info-item':[property('Ипотека','Нет'),property('Ремонт','хороший')]}});
  assert.equal(noMortgage.mortgage_supported,false,file);
  assert.equal(noMortgage.repair_condition,'хороший',file);
  const explicitPledge = extract(file, common,null,null,null,null,undefined,null,null,{nodes:{'.offer__info-item':[property('В залоге','Да')]}});
  assert.equal(explicitPledge.property_details['В залоге'],'Да',file);
  assert.equal(explicitPledge.mortgage_supported,null,file);
  const prosePledge = extract(file, common,null,null,null,null,undefined,null,'Квартира находится в залоге.');
  assert.equal(prosePledge.property_details['В залоге'],undefined,file);
  const inconsistent = extract(file, common,{floor:9,floorsTotal:8,year:2150});
  assert.equal(inconsistent.floor,null,file);
  assert.equal(inconsistent.floors_total,null,file);
  assert.equal(inconsistent.building_year,null,file);
  const oldYear = extract(file, common,null,null,null,null,undefined,null,null,{nodes:{'.offer__info-item':[property('Год постройки','1880')]}});
  assert.equal(oldYear.building_year,null,file);
  assert.equal(oldYear.property_details['Год постройки'],'1880',file);
  const manyPhotos = extract(file,common,null,null,null,null,undefined,null,null,{nodes:{'.offer__photos img':Array.from({length:35},(_,index) => ({src:`https://images.example/photo-${index}.jpg`}))}});
  assert.equal(manyPhotos.photo_urls.length,30,file);
  const placeholderPhotos = extract(file,common,null,null,null,null,undefined,null,null,{nodes:{'.offer__photos img':[
    {src:'https://krisha.kz/static/frontend/images/empty-photo-750.png'},
    {src:'https://images.example/apartment-real.jpg'},
  ]}});
  assert.deepEqual(Array.from(placeholderPhotos.photo_urls),['https://images.example/apartment-real.jpg'],file);

  // JSON fixtures below model contracts only, not a captured Krisha response.
  const richJson = {
    '@type':'Apartment',id:123,url:'https://krisha.kz/a/show/123',name:'Квартира из JSON',
    offers:{'@type':'Offer',price:45093093,priceCurrency:'KZT'},floorSize:{value:106.4,unitCode:'MTK'},numberOfRooms:4,
    description:'Просторная квартира с отдельной кухней.\n\nДва санузла и окна во двор.',
    address:{'@type':'PostalAddress',streetAddress:'Кудайбердыулы 36/3',addressLocality:'Астана',addressRegion:'Алматы р-н'},
    geo:{'@type':'GeoCoordinates',latitude:51.162,longitude:71.4675},
    seller:{'@type':'Person',id:123,name:'Автор текущей квартиры',sellerType:'Хозяин недвижимости'},
    image:['https://images.example/json-1.jpg',{url:'https://images.example/json-2.jpg'}],
    additionalProperty:[
      {name:'Санузел',value:'2 с/у и более'}, {name:'Ремонт',value:'хороший ремонт'},
      {name:'Ипотека',value:false}, {name:'Этаж',value:'3 из 10'},
      {name:'Год постройки',value:2011}, {name:'Тип дома',value:'кирпичный'},
      {name:'Высота потолков',value:'2.8 м'},
    ],
    krisha_city_price_per_m2:792420,krisha_district_price_per_m2:845700,krisha_comparison_district:'Алматы р-н',
  };
  let state;
  const rich = extract(file,'Загрузка объявления',richJson,null,null,null,undefined,null,null,{unscopedJson:true,onState:value=>state=value});
  assert.equal(state.matched_json_id,'123',file);
  assert.equal(rich.price_kzt,45093093,file);
  assert.equal(rich.area_m2,106.4,file);
  assert.equal(rich.rooms,4,file);
  assert.equal(rich.description,richJson.description,file);
  assert.equal(rich.seller_name,'Автор текущей квартиры',file);
  assert.equal(rich.seller_type,'Хозяин недвижимости',file);
  assert.equal(rich.address,'Кудайбердыулы 36/3',file);
  assert.equal(rich.city,'Астана',file);
  assert.equal(rich.district,'Алматы район',file);
  assert.equal(rich.repair_condition,'хороший ремонт',file);
  assert.equal(rich.bathrooms_count,2,file);
  assert.equal(rich.bathrooms_count_is_lower_bound,true,file);
  assert.equal(rich.mortgage_supported,false,file);
  assert.equal(rich.latitude,51.162,file);
  assert.equal(rich.floor,3,file);
  assert.equal(rich.floors_total,10,file);
  assert.equal(rich.building_year,2011,file);
  assert.equal(rich.building_type,'brick',file);
  assert.equal(rich.krisha_city_price_per_m2,792420,file);
  assert.equal(rich.krisha_district_price_per_m2,845700,file);
  assert.equal(rich.details_extracted,true,file);
  assert.deepEqual(Array.from(rich.photo_urls),['https://images.example/json-1.jpg','https://images.example/json-2.jpg'],file);
  const jsonPlaceholder = extract(file,'Загрузка объявления',{...richJson,image:['https://krisha.kz/static/frontend/images/empty-photo-750.png']},null,null,null,undefined,null,null,{unscopedJson:true,onState:value=>state=value});
  assert.equal(jsonPlaceholder.photo_urls.length,0,file);
  assert.equal(state.has_json_photos,false,file);

  const other = {...richJson,id:999,url:'https://krisha.kz/a/show/999',offers:{price:99999999},seller:{name:'Чужой автор'},address:{streetAddress:'Чужой адрес 99'}};
  const selected = extract(file,'Загрузка объявления',{adverts:[other,{...richJson,recommendations:[other],similarListings:[other]}]},null,null,null,undefined,null,null,{unscopedJson:true});
  assert.equal(selected.price_kzt,45093093,file);
  assert.equal(selected.seller_name,'Автор текущей квартиры',file);
  assert.equal(selected.address,'Кудайбердыулы 36/3',file);
  const foreignOnly = extract(file,'Загрузка объявления',{adverts:[other]},null,null,null,undefined,null,null,{unscopedJson:true,onState:value=>state=value});
  assert.equal(state.matched_json_id,null,file);
  assert.equal(foreignOnly.price_kzt,null,file);
  assert.equal(foreignOnly.seller_name,null,file);
  assert.equal(foreignOnly.photo_urls.length,0,file);
  assert.equal(foreignOnly.details_extracted,false,file);
  const recommendationOnly = extract(file,'Загрузка объявления',{recommendations:[richJson]},null,null,null,undefined,null,null,{unscopedJson:true});
  assert.equal(recommendationOnly.price_kzt,null,file);
  const sellerId = extract(file,'Загрузка объявления',{'@type':'Person',id:123,name:'Не квартира',price:99999999},null,null,null,undefined,null,null,{unscopedJson:true});
  assert.equal(sellerId.price_kzt,null,file);
  const sellerTypeArray = extract(file,'Загрузка объявления',{'@type':['Person','Thing'],id:123,price:99999999},null,null,null,undefined,null,null,{unscopedJson:true});
  assert.equal(sellerTypeArray.price_kzt,null,file);
  const sellerOffice = extract(file,'Загрузка объявления',{id:123,rooms:4,area:100,seller:{'@type':'Person',name:'Автор',price:99999999,address:{streetAddress:'Офисная улица 99'},geo:{latitude:51.18,longitude:71.42}}},null,null,null,undefined,null,null,{unscopedJson:true});
  assert.equal(sellerOffice.price_kzt,null,file);
  assert.equal(sellerOffice.address,null,file);
  assert.equal(sellerOffice.latitude,null,file);
  const authorOffice = extract(file,'Загрузка объявления',{id:123,rooms:4,area:100,author:{'@type':'Person',name:'Автор объявления',price:99999999,address:{streetAddress:'Адрес офиса 99'},geo:{latitude:51.18,longitude:71.42}}},null,null,null,undefined,null,null,{unscopedJson:true});
  assert.equal(authorOffice.price_kzt,null,file);
  assert.equal(authorOffice.address,null,file);
  assert.equal(authorOffice.latitude,null,file);
  const typedOffice = extract(file,'Загрузка объявления',{id:123,rooms:4,area:100,broker:{'@type':'RealEstateAgent',price:99999999,address:{streetAddress:'Адрес офиса 99'},geo:{latitude:51.18,longitude:71.42}}},null,null,null,undefined,null,null,{unscopedJson:true});
  assert.equal(typedOffice.price_kzt,null,file);
  assert.equal(typedOffice.address,null,file);
  assert.equal(typedOffice.latitude,null,file);
  const separateCoordinates = extract(file,'Загрузка объявления',[
    {id:123,rooms:4,area:100,price:40000000,geo:{latitude:51.11}},
    {id:123,rooms:4,area:100,price:40000000,geo:{longitude:71.49}},
  ],null,null,null,undefined,null,null,{unscopedJson:true});
  assert.equal(separateCoordinates.latitude,null,file);
  assert.equal(separateCoordinates.longitude,null,file);
  const sparseJson = extract(file,'Загрузка объявления',{id:123,price:40000000,rooms:4,area:100},null,null,null,undefined,null,null,{unscopedJson:true});
  assert.equal(sparseJson.price_kzt,40000000,file);
  assert.equal(sparseJson.details_extracted,false,file);
  const similarBody = 'Похожие квартиры: 3-комнатная квартира 80 м² Цена 99 000 000 ₸ 8/9 этаж Год постройки 2001 Город Астана, Нура р-н ул. Чужая 99';
  const partialWithSimilar = extract(file,similarBody,{id:123,rooms:4,area:100},null,null,null,undefined,null,null,{unscopedJson:true});
  assert.equal(partialWithSimilar.price_kzt,null,`${file}: missing current JSON price must not come from a recommendation`);
  assert.equal(partialWithSimilar.area_m2,100,file);
  assert.equal(partialWithSimilar.rooms,4,file);
  assert.equal(partialWithSimilar.floor,null,file);
  assert.equal(partialWithSimilar.building_year,null,file);
  assert.equal(partialWithSimilar.address,null,file);
  assert.equal(partialWithSimilar.district,null,file);
  const partialNumbers = extract(file,similarBody,{id:123,price:40000000},null,null,null,undefined,null,null,{unscopedJson:true});
  assert.equal(partialNumbers.area_m2,null,file);
  assert.equal(partialNumbers.rooms,null,file);
  const explicitPrice = extract(file,similarBody,{id:123,rooms:4,area:100},null,null,null,undefined,null,null,{
    unscopedJson:true,heading:'4-комнатная квартира · 100 м² · 3/10 этаж, Кудайбердыулы 36/3 — Район Встречи',
    nodes:{'.offer__price':[{textContent:'45 000 000 ₸'}]},
  });
  assert.equal(explicitPrice.price_kzt,45000000,file);
  assert.equal(explicitPrice.floor,3,file);
  assert.equal(explicitPrice.floors_total,10,file);
  assert.equal(explicitPrice.address,'Кудайбердыулы 36/3',file);
  const scriptJson = extract(file,'Загрузка объявления',null,null,null,null,undefined,null,null,{inlineScripts:[`window.data = ${JSON.stringify({advert:richJson,recommendations:[other]})};`]});
  assert.equal(scriptJson.price_kzt,45093093,file);
  assert.equal(scriptJson.details_extracted,true,file);
  const apiNames = extract(file,'Загрузка объявления',{
    id:123,price_kzt:40000000,area_m2:100,rooms:4,seller_name:'Автор со snake_case',seller_type:'Хозяин',
    floors_total:10,floor:3,building_year:2011,building_type:'brick',photo_urls:['https://images.example/flat-name.jpg'],
  },null,null,null,undefined,null,null,{unscopedJson:true});
  assert.equal(apiNames.seller_name,'Автор со snake_case',file);
  assert.equal(apiNames.seller_type,'Хозяин',file);
  assert.equal(apiNames.floors_total,10,file);
  assert.equal(apiNames.building_year,2011,file);
  assert.equal(apiNames.building_type,'brick',file);
  assert.equal(apiNames.photo_urls[0],'https://images.example/flat-name.jpg',file);
  const flatRich = extract(file,'Загрузка объявления',{
    id:123,price_kzt:40000000,area_m2:100,rooms:4,description:'Описание текущей квартиры с отдельной кухней и окнами во двор.',
    seller_name:'Автор со snake_case',repair_condition:'хороший ремонт',property_details:{'Санузел':'2 с/у и более','Ипотека':'Нет'},
  },null,null,null,undefined,null,null,{unscopedJson:true});
  assert.equal(flatRich.details_extracted,true,file);
  assert.equal(flatRich.bathrooms_count,2,file);
  assert.equal(flatRich.mortgage_supported,false,file);
  const structuredDetails = extract(file,'Загрузка объявления',{
    id:123,price_kzt:40000000,area_m2:100,rooms:4,seller:{},
    owner:{seller_name:'Айгуль',seller_type:'Риелтор'},
    data:{characteristics:{'flat.is_in_pledge':{value:false},'flat.mortgage':{label:'Да'}},
      additionalProperty:[{name:'Состояние',propertyID:'flat.renovation',value:{value:2,label:'хороший ремонт'}}]},
  },null,null,null,undefined,null,null,{unscopedJson:true});
  assert.equal(structuredDetails.seller_name,'Айгуль',file);
  assert.equal(structuredDetails.seller_type,'Риелтор',file);
  assert.equal(structuredDetails.repair_condition,'хороший ремонт',file);
  assert.equal(structuredDetails.property_details['В залоге'],'Нет',file);
  assert.equal(structuredDetails.mortgage_supported,true,file);
  const pledgeOnly = extract(file,common,{property_details:{'В залоге':true}},null,null,null,undefined,null,null);
  assert.equal(pledgeOnly.property_details['В залоге'],'Да',file);
  assert.equal(pledgeOnly.mortgage_supported,null,file);
  const profile = {className:'owners',textContent:'Автор объявления Айгуль Работает в компании House'};
  const verifiedSeller = extract(file,common,{seller:{name:'Прежний продавец',role:'Риелтор'}},null,null,null,undefined,null,null,{nodes:{
    '[data-testid="seller"]':[profile],
    '[data-testid="seller-name"]':[
      {textContent:'Чужой автор',parentElement:{className:'a-card'}},
      {textContent:'Работает в компании House',parentElement:profile},
      {textContent:'Айгуль',parentElement:profile},
    ],
  }});
  assert.equal(verifiedSeller.seller_name,'Айгуль',file);
  assert.equal(verifiedSeller.seller_type,null,`${file}: the old seller role and employer caption do not describe the visible author`);
  const foreignDetails = {seller_name:'Чужой автор',seller_type:'Компания',repair_condition:'евроремонт',mortgage_supported:true};
  const scopedDetails = extract(file,common,{
    broker:{...foreignDetails,address:{streetAddress:'Офисная улица 99'}},
    similar_listings:[{id:999,...foreignDetails}],nearby:[{id:998,...foreignDetails}],
    unlabelledWidget:{id:997,...foreignDetails,description:'Объявление другой квартиры'},
  });
  assert.equal(scopedDetails.seller_name,null,file);
  assert.equal(scopedDetails.seller_type,null,file);
  assert.equal(scopedDetails.repair_condition,null,file);
  assert.equal(scopedDetails.mortgage_supported,null,file);
  assert.equal(scopedDetails.address,null,file);
  assert.equal(scopedDetails.description,null,file);
  const brokerOnly = extract(file,'Загрузка объявления',{
    broker:{id:123,price:40000000,rooms:4,area:100,...foreignDetails,address:{streetAddress:'Офисная улица 99'}},
  },null,null,null,undefined,null,null,{unscopedJson:true});
  assert.equal(brokerOnly.price_kzt,null,file);
  assert.equal(brokerOnly.address,null,file);
  assert.equal(brokerOnly.seller_name,null,file);
  for (const source of [
    `/*\nwindow.data = ${JSON.stringify(richJson)};\n*/`,
    `// window.data = ${JSON.stringify(richJson)};`,
    'const note = `\nwindow.data = ' + JSON.stringify(richJson) + ';\n`;',
    "const note = ';window.data = " + JSON.stringify(richJson) + ";';",
    `const note = ${JSON.stringify(';window.data = ' + JSON.stringify(richJson) + ';')};`,
    'const pattern = /;window.data = {"id":123,"price":40000000}/;',
    `function deferred() {\nwindow.data = ${JSON.stringify(richJson)};\n}`,
  ]) {
    const quoted = extract(file,'Загрузка объявления',null,null,null,null,undefined,null,null,{inlineScripts:[source],onState:value=>state=value});
    assert.equal(state.matched_json_id,null,`${file}: JSON-looking text in comments and strings is not page data`);
    assert.equal(quoted.price_kzt,null,file);
    assert.equal(quoted.details_extracted,false,file);
  }
  for (const branch of ['otherAdverts','recommendedAdverts']) {
    const notCurrent = extract(file,'Загрузка объявления',{[branch]:[richJson]},null,null,null,undefined,null,null,{unscopedJson:true});
    assert.equal(notCurrent.price_kzt,null,`${file}: ${branch} is not the current advert`);
  }
  for (const source of [
    `window.data = (() => { throw new Error('must never execute'); })();`,
    `window.data = {"id":123,"price":(() => 99999999)()};`,
    'window.data = {"id":123,"price":40000000',
  ]) {
    const malformed = extract(file,common,null,null,null,null,undefined,null,null,{inlineScripts:[source]});
    assert.equal(malformed.price_kzt,45000000,file);
    assert.equal(malformed.details_extracted,false,file);
  }
  const domWins = extract(file,'4-комнатная квартира 106.4 м² Цена 46 000 000 ₸',richJson,null,null,
    ['416 667 ₸ Цена м² в этом объявлении','555 000 ₸ Похожие квартиры в Алматы р-н','600 000 ₸ Похожие квартиры в Астане'],undefined,null,null,{
      unscopedJson:true,heading:'4-комнатная квартира 110 м²',nodes:{'.offer__price':[{textContent:'46 000 000 ₸'}],'.offer__info-item':[property('Ремонт','новый ремонт'),property('Ипотека','Да'),property('Санузел','раздельный')]},
    });
  assert.equal(domWins.price_kzt,46000000,file);
  assert.equal(domWins.area_m2,110,file);
  assert.equal(domWins.repair_condition,'новый ремонт',file);
  assert.equal(domWins.mortgage_supported,true,file);
  assert.equal(domWins.bathrooms_count,1,file);
  assert.equal(domWins.krisha_city_price_per_m2,600000,file);
}
assert.ok(fs.readFileSync('app/static/browser-import.js','utf8').startsWith(fs.readFileSync('browser-extension/extractor.js','utf8')), 'bookmarklet and extension share exactly the same extraction implementation');

(async () => {
  const saved = {id:1,source:'browser',source_url:'https://krisha.kz/a/show/123',assessment:{score:95}};
  const json = body => new Response(JSON.stringify(body), {status:201,headers:{'Content-Type':'application/json'}});
  const cases = [
    [json(saved), /сохранено объявление №123 в карточку №1\. Рейтинг 95\/100/],
    [new Response('<html>Login</html>'), /не подтвердил сохранение/],
    [new Response('not json', {status:201,headers:{'Content-Type':'application/json'}}), /не подтвердил сохранение/],
    [json({...saved, source_url:'https://krisha.kz/a/show/999'}), /не подтвердил сохранение/],
    [{type:'opaqueredirect',status:0}, /перенаправил запрос/],
  ];
  for (const [response, expected] of cases) {
    const notices = [];
    extract('app/static/browser-import.js','3-комнатная квартира 80 м² Цена 45 000 000 ₸',
      null,null,null,null,undefined,null,null,{response,onNotice:message => notices.push(message)});
    await new Promise(setImmediate);
    assert.match(notices.at(-1), expected);
  }
  console.log('Browser extraction checks passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
