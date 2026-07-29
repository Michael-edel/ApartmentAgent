(() => {
  const clean = value => String(value ?? '').replace(/\s+/g, ' ').trim();
  const number = value => {
    if (typeof value === 'number' && value > 0) return value;
    const parsed = Number(clean(value).replace(/[^\d.,]/g, '').replace(',', '.'));
    return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
  };
  const parsePrice = value => {
    if (typeof value === 'number' && value >= 500000) return value;
    const raw = clean(value).replace(/\u00a0/g, ' ');
    const money = raw.match(/(\d[\d\s]{5,})\s*(?:₸|тг|тенге)/i);
    if (money) return Number(money[1].replace(/\D/g, '')) || null;
    const millions = raw.match(/(\d{1,2}(?:[.,]\d+)?)\s*млн/i);
    if (millions) return Math.round(Number(millions[1].replace(',', '.')) * 1000000);
    const digits = raw.replace(/\D/g, '');
    return digits.length >= 6 ? Number(digits) : null;
  };
  const meta = name => document.querySelector(`meta[property="${name}"], meta[name="${name}"]`)?.content || '';
  const roots = [];
  document.querySelectorAll('script[type="application/ld+json"], script#__NEXT_DATA__, script[type="application/json"]').forEach(node => {
    try {
      const value = JSON.parse(node.textContent || '');
      if (value) roots.push(value);
    } catch (_) {}
  });
  function* walk(value) {
    if (Array.isArray(value)) for (const child of value) yield* walk(child);
    else if (value && typeof value === 'object') {
      yield value;
      for (const child of Object.values(value)) yield* walk(child);
    }
  }
  const findValue = (keys, parser = number) => {
    const wanted = new Set(keys.map(key => key.toLowerCase()));
    for (const root of roots) for (const item of walk(root)) for (const [key, value] of Object.entries(item)) {
      if (wanted.has(key.toLowerCase())) {
        const parsed = parser(value);
        if (parsed !== null && parsed !== undefined && parsed !== '') return parsed;
      }
    }
    return null;
  };
  const findText = keys => findValue(keys, value => typeof value === 'string' ? clean(value) : null);
  const fullText = clean(`${document.title} ${document.body?.innerText || ''}`);
  const areaMatch = fullText.match(/(\d{2,3}(?:[.,]\d+)?)\s*(?:м²|м2|кв\.?\s*м)/i);
  const roomsMatch = fullText.match(/(\d+)\s*[-–]?\s*комн(?:ат(?:н(?:ая|ую|ой))?|\.)/i);
  const floorMatch = fullText.match(/(\d+)\s*(?:из|\/)\s*(\d+)\s*(?:этаж|эт\.)?/i);
  const yearMatch = fullText.match(/(?:год\s+постройки|построен\w*)\D{0,12}(19\d{2}|20\d{2})/i);
  const area = findValue(['area', 'area_m2', 'areaM2', 'square', 'floorSize', 'totalArea']) || (areaMatch ? number(areaMatch[1]) : null);
  const rooms = findValue(['rooms', 'roomCount', 'numberOfRooms', 'roomsCount']) || (roomsMatch ? Number(roomsMatch[1]) : null);
  const floor = findValue(['floor', 'floorNumber']) || (floorMatch ? Number(floorMatch[1]) : null);
  const floorsTotal = findValue(['floors', 'floorsTotal', 'floorCount', 'numberOfFloors']) || (floorMatch ? Number(floorMatch[2]) : null);
  const buildingYear = findValue(['year', 'buildYear', 'buildingYear', 'yearBuilt']) || (yearMatch ? Number(yearMatch[1]) : null);
  const price = findValue(['price', 'price_kzt', 'priceKzt', 'amount', 'value'], parsePrice) || parsePrice(fullText);
  const photos = [meta('og:image'), meta('twitter:image')];
  const imageKeys = new Set(['image', 'images', 'photo', 'photos', 'photoUrls', 'imageUrls', 'gallery', 'pictures']);
  for (const root of roots) for (const item of walk(root)) for (const [key, value] of Object.entries(item)) {
    if (!imageKeys.has(key)) continue;
    for (const candidate of (Array.isArray(value) ? value : [value])) {
      const url = typeof candidate === 'string' ? candidate : candidate?.url || candidate?.src;
      if (url) photos.push(url);
    }
  }
  document.querySelectorAll('img[src], img[data-src]').forEach(image => photos.push(image.currentSrc || image.dataset.src || image.src));
  const photoUrls = [...new Set(photos.map(clean).map(value => value.startsWith('//') ? `https:${value}` : value))]
    .filter(value => /^https?:\/\//i.test(value)).slice(0, 30);
  const lower = fullText.toLowerCase();
  const payload = {
    source: 'browser-extension',
    source_url: location.href.split('#')[0],
    title: clean(meta('og:title') || document.querySelector('h1')?.textContent || document.title || `${rooms || 2}-комнатная квартира`).slice(0, 300),
    description: clean(meta('og:description') || meta('description')).slice(0, 20000) || null,
    city: 'Астана',
    district: findText(['district', 'districtName', 'regionName', 'addressLocality', 'areaName']) || (fullText.match(/((?:Есильский|Нура|Алматы|Сарыарка|Байконур)\s+район)/i)?.[1] || null),
    residential_complex: findText(['complexName', 'residentialComplex', 'housingComplex', 'residentialComplexName']) || (fullText.match(/ЖК\s*[«"]?([^»".,;]{2,100})/i)?.[1]?.trim() || null),
    price_kzt: price,
    area_m2: area,
    rooms,
    floor,
    floors_total: floorsTotal,
    building_year: buildingYear,
    building_type: lower.includes('кирпич') ? 'brick' : lower.includes('монолит') ? 'monolith' : lower.includes('панел') ? 'panel' : null,
    is_full_two_room: rooms === 2,
    mortgage_supported: null,
    photo_urls: photoUrls,
  };
  window.ApartmentAgentExtract = () => payload;
})();
