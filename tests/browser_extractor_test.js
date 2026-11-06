const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function extract(file, text, data = null, mortgageField = null, bathroomField = null, comparisonCells = null, comparisonHeading = 'Цена м² в похожих квартирах', complexBlock = null, descriptionText = null, extra = {}) {
  let posted;
  const body = {innerText: text, appendChild() {}};
  const document = {
    title: text.startsWith('4-комнатная') ? '4-комнатная квартира 100 м²' : '3-комнатная квартира 80 м²', body,
    currentScript: {src: 'http://localhost:8000/static/browser-import.js'},
    querySelector(selector) {
      const name = selector.match(/meta\[property="([^"]+)"\]/)?.[1];
      return name && extra.meta?.[name] ? {content:extra.meta[name]} : null;
    },
    querySelectorAll(selector) {
      for (const [prefix,nodes] of Object.entries(extra.nodes || {})) if (selector.startsWith(prefix)) return typeof nodes === 'function' ? nodes() : nodes;
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
    fetch(_url, options) { posted = JSON.parse(options.body); return new Promise(() => {}); }};
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
  assert.equal(extract(file, `${common} Реклама: ипотека: да`).mortgage_supported, null, file);
  for (const phrase of ['Ипотеки нет', 'Ипотека жоқ', 'Ипотека жок']) {
    assert.equal(extract(file, common, null, phrase).mortgage_supported, false, file);
    assert.equal(extract(file, common, null, null, null, null, undefined, null, phrase).mortgage_supported, false, file);
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
  const full = extract(file, '4-комнатная квартира 106.4 м² Цена 45 093 093 ₸',
    {floor:9,floorsTotal:8,year:2005,sellerName:'Старое имя',sellerType:'Компания',mortgageSupported:false},
    null,null,null,undefined,null,null, {
      heading:'4-комнатная квартира · 106.4 м² · 3/9 этаж, Кудайбердыулы 36/3 — Район Встречи',
      meta:{'og:description':'Короткое описание','og:image':'https://images.example/og.jpg'},
      nodes:{
        '.offer__price':[{textContent:'45 093 093 ₸'}],
        '.offer__info-item':propertyRows,
        '.offer__description':[{textContent:fullText,parentElement:parent}],
        '[data-testid="seller"]':[{textContent:'Автор объявления Хозяин недвижимости',parentElement:{tagName:'ASIDE'}}],
        '[data-testid="seller-name"]':[{textContent:'Айгуль',parentElement:{tagName:'ASIDE'}}],
        '.offer__photos img':gallery,
        'img[src]':[{src:'https://images.example/site-logo.png'}],
      },
    });
  assert.equal(full.details_extracted,true,file);
  assert.equal(full.description,fullText,file);
  assert.equal(full.seller_name,'Айгуль',file);
  assert.equal(full.seller_type,'Хозяин недвижимости',file);
  assert.equal(full.address,'Кудайбердыулы 36/3',file);
  const employerNode = {className:'seller-address',textContent:'Работает в компании Royal Invest'};
  const exactHeadline = '4-комнатная квартира · 106.4 м² · 3/9 этаж, Кудайбердыулы 36/3 — Район Встречи';
  const employerBeforeHeadline = extract(file, common,null,null,null,null,undefined,null,null,{
    heading:exactHeadline,nodes:{'[itemprop="streetAddress"]':[employerNode]},
  });
  assert.equal(employerBeforeHeadline.address,'Кудайбердыулы 36/3',`${file}: seller employer must not override the apartment address`);
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
  const badCalc = extract(file, common,null,null,null,null,undefined,null,null,{nodes:{'.offer__info-item':[property('Ипотека','Рассчитать ипотеку')]}});
  assert.equal(badCalc.mortgage_supported,null,file);
  const noMortgage = extract(file, common,null,null,null,null,undefined,null,null,{nodes:{'.offer__info-item':[property('Ипотека','Нет'),property('Ремонт','хороший')]}});
  assert.equal(noMortgage.mortgage_supported,false,file);
  assert.equal(noMortgage.repair_condition,'хороший',file);
  const inconsistent = extract(file, common,{floor:9,floorsTotal:8,year:2150});
  assert.equal(inconsistent.floor,null,file);
  assert.equal(inconsistent.floors_total,null,file);
  assert.equal(inconsistent.building_year,null,file);
  const oldYear = extract(file, common,null,null,null,null,undefined,null,null,{nodes:{'.offer__info-item':[property('Год постройки','1880')]}});
  assert.equal(oldYear.building_year,null,file);
  assert.equal(oldYear.property_details['Год постройки'],'1880',file);
  const manyPhotos = extract(file,common,null,null,null,null,undefined,null,null,{nodes:{'.offer__photos img':Array.from({length:35},(_,index) => ({src:`https://images.example/photo-${index}.jpg`}))}});
  assert.equal(manyPhotos.photo_urls.length,30,file);

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
console.log('Browser extraction checks passed');
