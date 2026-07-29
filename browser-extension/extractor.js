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
  const textValue = value => {
    if (typeof value === 'string' && clean(value)) return clean(value);
    if (!value || typeof value !== 'object') return null;
    for (const key of ['formattedAddress', 'fullAddress', 'displayAddress']) {
      const candidate = textValue(value[key]);
      if (candidate) return candidate;
    }
    const parts = ['streetAddress', 'addressLocality', 'addressRegion']
      .map(key => textValue(value[key]))
      .filter(Boolean);
    return [...new Set(parts)].join(', ') || null;
  };
  const findText = keys => findValue(keys, textValue);
  const addressFromText = value => {
    const normalized = clean(value);
    const patterns = [
      /(?:№\s*\d+\s*[:：]\s*|Продажа[^:—]{0,120}[:：]\s*)([^—]{3,120}?),\s*(?:Астана|Astana)(?![А-Яа-яЁё])/i,
      /\b((?:ул\.?|улица|просп\.?|проспект|пер\.?|переулок|мкр\.?|микрорайон|шоссе|набережная)\s+[^,;]{2,80},?\s+\d+[A-Za-zА-Яа-яЁё]?)/i,
    ];
    for (const pattern of patterns) {
      const match = normalized.match(pattern);
      const candidate = clean(match?.[1]).replace(/[ ,.-]+$/, '');
      if (candidate.length >= 5 && candidate.length <= 180) return candidate;
    }
    return null;
  };
  const coordinate = (value, minimum, maximum) => {
    const parsed = typeof value === 'number' ? value : Number(clean(value).replace(',', '.').replace(/[^0-9+-.]/g, ''));
    return Number.isFinite(parsed) && parsed >= minimum && parsed <= maximum ? parsed : null;
  };
  const isAstanaCoordinates = (latitude, longitude) => latitude !== null && longitude !== null
    && latitude >= 50 && latitude <= 52 && longitude >= 69 && longitude <= 73;
  const coordinatePair = value => {
    let decoded = String(value || '');
    try { decoded = decodeURIComponent(decoded); } catch (_) { /* keep original */ }
    const match = decoded.match(/(?:[?&#](?:ll|center|pt|coords|coordinates)=)([-+]?\d{1,3}(?:\.\d+)?)[,%20]+([-+]?\d{1,2}(?:\.\d+)?)/i)
      || decoded.match(/^\s*([-+]?\d{1,3}(?:\.\d+)?)[,\s]+([-+]?\d{1,2}(?:\.\d+)?)\s*$/);
    if (!match) return null;
    const longitude = coordinate(match[1], -180, 180);
    const latitude = coordinate(match[2], -90, 90);
    return isAstanaCoordinates(latitude, longitude) ? {latitude, longitude} : null;
  };
  const pageCoordinates = () => {
    const nodes = document.querySelectorAll('a[href], iframe[src], [data-latitude], [data-longitude], [data-lat], [data-lng], [data-coordinates]');
    for (const node of nodes) {
      const pair = coordinatePair(node.href || node.src || node.getAttribute('data-coordinates'));
      if (pair) return pair;
      const latitude = coordinate(node.getAttribute('data-latitude') || node.getAttribute('data-lat'), -90, 90);
      const longitude = coordinate(node.getAttribute('data-longitude') || node.getAttribute('data-lng'), -180, 180);
      if (isAstanaCoordinates(latitude, longitude)) return {latitude, longitude};
    }
    return null;
  };
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
  const coordinates = pageCoordinates();
  const dataLatitude = findValue(['latitude', 'lat', 'geoLatitude', 'geoLat'], value => coordinate(value, -90, 90));
  const dataLongitude = findValue(['longitude', 'lng', 'lon', 'geoLongitude', 'geoLon'], value => coordinate(value, -180, 180));
  const locationCoordinates = isAstanaCoordinates(dataLatitude, dataLongitude)
    ? {latitude: dataLatitude, longitude: dataLongitude}
    : coordinates;
  const payload = {
    source: 'browser-extension',
    source_url: location.href.split('#')[0],
    title: clean(meta('og:title') || document.querySelector('h1')?.textContent || document.title || `${rooms || 2}-комнатная квартира`).slice(0, 300),
    description: clean(meta('og:description') || meta('description')).slice(0, 20000) || null,
    city: 'Астана',
    district: (() => {
      const candidate = findText(['district', 'districtName', 'areaName'])
        || [findText(['regionName', 'addressRegion'])].find(value => value && /\b(?:район|р-н)\b/i.test(value))
        || (fullText.match(/((?:Есильский|Нура|Алматы|Сарыарка|Байконур)\s+(?:район|р-н))/i)?.[1] || null);
      return candidate ? candidate.replace(/\s+р-н\b/i, ' район') : null;
    })(),
    residential_complex: findText(['complexName', 'residentialComplex', 'housingComplex', 'residentialComplexName']) || (fullText.match(/ЖК\s*[«"]?([^»".,;]{2,100})/i)?.[1]?.trim() || null),
    address: findText(['address', 'streetAddress', 'formattedAddress', 'fullAddress', 'addressLine', 'displayAddress'])
      || [...document.querySelectorAll('[itemprop="streetAddress"], [data-testid*="address" i], [class*="address" i]')]
        .map(node => clean(node.innerText || node.textContent))
        .find(value => value.length >= 5 && value.length <= 180)
      || addressFromText(fullText),
    latitude: locationCoordinates?.latitude ?? null,
    longitude: locationCoordinates?.longitude ?? null,
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
