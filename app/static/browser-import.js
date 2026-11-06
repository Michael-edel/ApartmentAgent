(() => {
  const scriptUrl = document.currentScript?.src;
  const apiOrigin = scriptUrl ? new URL(scriptUrl).origin : window.__APARTMENT_AGENT_API_ORIGIN__;

  const showMessage = (message, error = false) => {
    const old = document.querySelector('[data-apartment-agent-message]');
    old?.remove();
    const node = document.createElement('div');
    node.dataset.apartmentAgentMessage = 'true';
    node.textContent = message;
    Object.assign(node.style, {
      position: 'fixed',
      zIndex: '2147483647',
      left: '16px',
      right: '16px',
      bottom: '20px',
      padding: '16px 18px',
      borderRadius: '14px',
      background: error ? '#fee4e2' : '#e5f5ef',
      color: error ? '#9b1c1c' : '#126b4d',
      boxShadow: '0 8px 24px #0003',
      font: '600 16px -apple-system,BlinkMacSystemFont,Segoe UI,sans-serif',
    });
    document.body.appendChild(node);
    window.setTimeout(() => node.remove(), 9000);
  };

  const clean = value => String(value ?? '').replace(/\s+/g, ' ').trim();
  const number = value => {
    if (typeof value === 'number' && value > 0) return value;
    const digits = clean(value).replace(/[^\d.,]/g, '').replace(/,/g, '.');
    const parsed = Number(digits);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
  };


  const complexDescription = () => {
    const empty = {complex_description:null, complex_description_source_url:null, complex_description_source_kind:null};
    for (const heading of document.querySelectorAll('h2, h3, h4')) {
      if (!/^(?:Описание ЖК|О жилом комплексе)\s*:?$/i.test(clean(heading.textContent))) continue;
      const sibling = heading.nextElementSibling;
      // Read only the immediate prose block; do not absorb later widgets or recommendations.
      if (!sibling || !['P','DIV','UL','OL'].includes(sibling.tagName)) continue;
      const description = clean(sibling.textContent);
      if (description.length > 5000) continue;
      if (description.length >= 20) return {complex_description:description,
        complex_description_source_url:location.href.split('#')[0], complex_description_source_kind:'listing'};
    }
    return empty;
  };
  const krishaComparison = () => {
    const result = {krisha_district_price_per_m2:null, krisha_city_price_per_m2:null, krisha_comparison_district:null};
    // A labelled statistics block is required. Never search arbitrary page text for averages.
    const blocks = [...document.querySelectorAll('section, article, div')]
      .filter(node => {
        const text = clean(node.textContent);
        return text.length <= 1800 && /Цена\s*м[²2]\s*в похожих квартирах/i.test(text)
          && /Похожие квартиры в Астане/i.test(text);
      }).sort((a, b) => clean(a.textContent).length - clean(b.textContent).length);
    for (const block of blocks) {
      const found = {...result};
      for (const cell of block.querySelectorAll('div, td, li')) {
        const text = clean(cell.textContent);
        if (text.length > 220) continue;
        const valueFirst = text.match(/^(\d[\d ]*)\s*(?:₸|тг|тенге)(?:\s*\/\s*м[²2])?\s*Похожие квартиры в (.+)$/i);
        const labelFirst = text.match(/^Похожие квартиры в (.+?)\s+(\d[\d ]*)\s*(?:₸|тг|тенге)(?:\s*\/\s*м[²2])?$/i);
        if (!valueFirst && !labelFirst) continue;
        const label = clean(valueFirst ? valueFirst[2] : labelFirst[1]);
        const value = Number((valueFirst ? valueFirst[1] : labelFirst[2]).replace(/\s/g, ''));
        if (!Number.isSafeInteger(value) || value <= 0 || value > 1000000000) continue;
        if (/^Астане$/i.test(label)) found.krisha_city_price_per_m2 = value;
        else if (/(?:район[еа]?|р-н(?:е)?)$/i.test(label)) {
          found.krisha_district_price_per_m2 = value;
          found.krisha_comparison_district = label;
        }
      }
      if (found.krisha_city_price_per_m2 || found.krisha_district_price_per_m2) return found;
    }
    return result;
  };
  const parsePrice = (value, allowPlain = true) => {
    if (typeof value === 'number' && value >= 500000) return value;
    const raw = clean(value).replace(/\u00a0/g, ' ');
    const money = raw.match(/(\d[\d\s]{5,})\s*(?:₸|тг|тенге)/i);
    if (money) return /(?:^|\s)от\s*$/i.test(raw.slice(0, money.index)) ? null : Number(money[1].replace(/\D/g, '')) || null;
    const millions = raw.match(/(\d{1,3}(?:[.,]\d+)?)\s*млн/i);
    if (millions) return /(?:^|\s)от\s*$/i.test(raw.slice(0, millions.index)) ? null : Math.round(Number(millions[1].replace(',', '.')) * 1000000);
    if (!allowPlain || !/^\d[\d\s]*$/.test(raw)) return null;
    const digits = raw.replace(/\s/g, '');
    return digits.length >= 6 ? Number(digits) : null;
  };
  let pageText = document.body?.innerText || '';
  for (const notice of document.querySelectorAll('[data-apartment-agent-extension-message], [data-apartment-agent-message]')) {
    pageText = pageText.replace(notice.innerText || notice.textContent || '', '');
  }
  const text = clean(pageText);
  const fullText = `${document.title} ${text}`;
  const meta = name => document.querySelector(`meta[property="${name}"], meta[name="${name}"]`)?.content || '';
  const roots = [];

  document.querySelectorAll('script[type="application/ld+json"], script#__NEXT_DATA__, script[type="application/json"]').forEach(node => {
    try {
      const value = JSON.parse(node.textContent || '');
      if (value) roots.push(value);
    } catch (_) {
      // Some pages contain JSON fragments intended for another script.
    }
  });

  function* walk(value) {
    if (Array.isArray(value)) {
      for (const child of value) yield* walk(child);
    } else if (value && typeof value === 'object') {
      yield value;
      for (const child of Object.values(value)) yield* walk(child);
    }
  }

  const findValue = (keys, parser = number) => {
    const wanted = new Set(keys.map(key => key.toLowerCase()));
    for (const root of roots) {
      for (const item of walk(root)) {
        for (const [key, value] of Object.entries(item)) {
          if (wanted.has(key.toLowerCase())) {
            const parsed = parser(value);
            if (parsed !== null && parsed !== undefined && parsed !== '') return parsed;
          }
        }
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
  const findText = keys => findValue(keys, textValue);
  const districtFromText = (value, allowBare = false) => {
    const text = clean(value);
    const names = [
      ['Байконур', 'Байконур(?:ский)?|Байконыр|Байқоңыр'],
      ['Есильский', 'Есильский|Есиль|Есіл'],
      ['Нура', 'Нура|Нұра|Нуринский'],
      ['Алматы', 'Алматы|Алматинский'],
      ['Сарыарка', 'Сарыарка|Сарыаркинский'],
      ['Сарайшык', 'Сарайшык|Сарайшық'],
    ];
    const label = '(?:р\\s*[-–—]\\s*н|район)';
    for (const [name, aliases] of names) {
      // JS \\b does not delimit Cyrillic words; use Unicode letter boundaries.
      const namePattern = `(?:${aliases})`;
      const labelled = new RegExp(`(?:^|[^\\p{L}])(?:${label}\\s*${namePattern}|${namePattern}\\s*${label})(?=$|[^\\p{L}])`, 'iu');
      if (labelled.test(text) || allowBare && new RegExp(`^${namePattern}$`, 'iu').test(text)) return `${name} район`;
    }
    return null;
  };
  const sellerName = () => findValue(['sellerName', 'ownerName', 'advertiserName'], value =>
    typeof value === 'string' && clean(value) ? clean(value).slice(0, 200) : null)
    || clean(document.querySelector('[data-testid="seller-name"], [itemprop="seller"] [itemprop="name"]')?.textContent).slice(0, 200) || null;
  const sellerType = () => findValue(['sellerType', 'ownerType', 'advertiserType'], value =>
    typeof value === 'string' && clean(value) ? clean(value).slice(0, 100) : null);
  const mortgageValue = value => {
    if (typeof value === 'boolean') return value;
    if (typeof value !== 'string') return null;
    const normalized = clean(value).toLowerCase();
    if (['да', 'true'].includes(normalized)) return true;
    if (['нет', 'жоқ', 'жок', 'false'].includes(normalized)) return false;
    return mortgageTextValue(normalized);
  };
  const mortgageTextValue = text => {
    const value = clean(text);
    if (/(?:ипотек(?:а|и|у|ой|е)\s*[:—–-]?\s*(?:нет|жоқ|жок)(?=$|[\s.,;!?])(?!\s+(?:емес|проблем|ограничений|трудностей))|ипотека\s+не\s+(?:рассматривается|проходит|подходит|предусмотрена)|не\s+прода[её]тся\s+в\s+ипотеку)/i.test(value)) return false;
    if (/(?:ипотека\s*[:—–-]\s*да|(?:можно|возможна\s+покупка)\s+в\s+ипотеку|ипотека\s+(?:возможна|доступна)|подходит\s+под\s+ипотеку)/i.test(value)) return true;
    return null;
  };
  const mortgageSupported = () => {
    const structured = findValue(['mortgageSupported', 'mortgage_supported', 'isMortgageAvailable'], mortgageValue);
    const fieldText = [...document.querySelectorAll('[data-testid="mortgage"], [data-testid="mortgage-status"], [data-property="mortgage"]')].map(node => clean(node.textContent)).join(' ');
    const descriptionText = [...document.querySelectorAll('.offer__description, [data-testid="description"], [data-testid="advert-description"], [itemprop="description"]')].map(node => clean(node.textContent)).join(' ');
    const values = [structured, mortgageTextValue(fieldText), mortgageTextValue(descriptionText), mortgageTextValue(meta('og:description') || meta('description'))];
    if (values.includes(false)) return false;
    return values.includes(true) ? true : null;
  };

  const price = findValue(['price', 'price_kzt', 'priceKzt'], parsePrice) || parsePrice(fullText, false);
  const areaMatch = fullText.match(/(\d{2,3}(?:[.,]\d+)?)\s*(?:м²|м2|кв\.?\s*м)/i);
  const roomsMatch = fullText.match(/(\d+)\s*[-–]?\s*комн(?:ат(?:н(?:ая|ую|ой))?|\.)/i);
  const floorMatch = fullText.match(/(\d+)\s*(?:из|\/)\s*(\d+)\s*(?:этаж|эт\.)?/i);
  const yearMatch = fullText.match(/(?:год\s+постройки|построен\w*)\D{0,12}(19\d{2}|20\d{2})/i);
  const area = findValue(['area', 'area_m2', 'areaM2', 'square', 'floorSize', 'totalArea']) || (areaMatch ? number(areaMatch[1].replace(',', '.')) : null);
  const rooms = findValue(['rooms', 'roomCount', 'numberOfRooms', 'roomsCount']) || (roomsMatch ? Number(roomsMatch[1]) : null);
  const bathroomInfo = value => {
    const s = String(value ?? '').trim();
    if (/^(?:раздельный|совмещ[её]нный)$/i.test(s)) return {count:1, lower:false};
    const match = s.match(/^(\d{1,2})(?:\s*с\s*\/\s*у)?\s*(и\s+более|и\s+больше|\+)?$/i);
    if (!match || Number(match[1]) > 20) return null;
    return {count:Number(match[1]), lower:!!match[2]};
  };
  const visibleProperty = node => {
    for (let current = node; current; current = current.parentElement) {
      if (current.hidden || current.getAttribute?.('hidden') !== null && current.getAttribute?.('hidden') !== undefined
        || current.getAttribute?.('aria-hidden') === 'true' || ['SCRIPT', 'STYLE'].includes(current.tagName)) return false;
      const style = typeof window.getComputedStyle === 'function' ? window.getComputedStyle(current) : current.style;
      if (style?.display === 'none' || ['hidden', 'collapse'].includes(style?.visibility)) return false;
    }
    return true;
  };
  const propertyText = node => clean(node?.innerText ?? node?.textContent);
  const bathroomLabel = /^(?:санузел|санузла|санузлов|санузлы|с\s*\/\s*у)\s*[:—-]?\s*$/i;
  const bathroomField = node => {
    if (!visibleProperty(node)) return null;
    const text = propertyText(node);
    const labelled = text.match(/^(?:санузел|санузла|санузлов|санузлы|с\s*\/\s*у)\s*[:—-]?\s*(.+)$/i);
    const inline = labelled ? bathroomInfo(clean(labelled[1])) : null;
    if (inline) return inline;
    // A semantic property name is a label even when only its value is rendered.
    if (['bathrooms', 'flat.toilet'].includes(node.getAttribute?.('data-name'))
      || node.getAttribute?.('data-testid') === 'bathrooms') {
      const value = bathroomInfo(text);
      if (value) return value;
    }
    if (bathroomLabel.test(text) && node.nextElementSibling && visibleProperty(node.nextElementSibling)
      && (!node.tagName || node.tagName !== 'DT' || node.nextElementSibling.tagName === 'DD')) {
      const value = bathroomInfo(propertyText(node.nextElementSibling));
      if (value) return value;
    }
    // Labels and values can be sibling children of a property row.
    for (const label of [...(node.children || [])]) {
      if (!visibleProperty(label) || !bathroomLabel.test(propertyText(label))
        || !label.nextElementSibling || !visibleProperty(label.nextElementSibling)) continue;
      const value = bathroomInfo(propertyText(label.nextElementSibling));
      if (value) return value;
    }
    return null;
  };
  const bathroomData = [...document.querySelectorAll('[data-testid="bathrooms"], [data-name="bathrooms"], [data-name="flat.toilet"], .a-list__item, .offer__info-item, dt')]
      .map(bathroomField).find(value => value !== null)
    ?? findValue(['bathroomsCount', 'bathrooms_count', 'numberOfBathroomsTotal', 'numberOfBathrooms'], bathroomInfo) ?? null;
  const bathroomsCount = bathroomData?.count ?? null;
  const floor = findValue(['floor', 'floorNumber']) || (floorMatch ? Number(floorMatch[1]) : null);
  const floorsTotal = findValue(['floors', 'floorsTotal', 'floorCount', 'numberOfFloors']) || (floorMatch ? Number(floorMatch[2]) : null);
  const buildingYear = findValue(['year', 'buildYear', 'buildingYear', 'yearBuilt']) || (yearMatch ? Number(yearMatch[1]) : null);

  const photos = [meta('og:image'), meta('twitter:image')];
  const imageKeys = new Set(['image', 'images', 'photo', 'photos', 'photoUrls', 'imageUrls', 'gallery', 'pictures']);
  for (const root of roots) {
    for (const item of walk(root)) {
      for (const [key, value] of Object.entries(item)) {
        if (!imageKeys.has(key)) continue;
        for (const candidate of (Array.isArray(value) ? value : [value])) {
          const url = typeof candidate === 'string' ? candidate : candidate?.url || candidate?.src;
          if (url) photos.push(url);
        }
      }
    }
  }
  document.querySelectorAll('img[src], img[data-src]').forEach(image => photos.push(image.currentSrc || image.dataset.src || image.src));
  const photoUrls = [...new Set(photos.map(value => clean(value)).map(value => value.startsWith('//') ? `https:${value}` : value))]
    .filter(value => /^https?:\/\//i.test(value)).slice(0, 30);

  const title = clean(meta('og:title') || document.querySelector('h1')?.textContent || document.title || `${rooms || 2}-комнатная квартира`);
  const description = clean(meta('og:description') || meta('description')).slice(0, 20000) || null;
  const lower = fullText.toLowerCase();
  const buildingType = lower.includes('кирпич') ? 'brick' : lower.includes('монолит') ? 'monolith' : lower.includes('панел') ? 'panel' : null;
  const district = districtFromText(findText(['district', 'districtName', 'areaName']), true)
    || districtFromText(findText(['regionName', 'addressRegion']))
    || districtFromText(fullText);
  const residentialComplex = findText(['complexName', 'residentialComplex', 'housingComplex', 'residentialComplexName'])
    || (fullText.match(/ЖК\s*[«"]?([^»".,;]{2,100})/i)?.[1]?.trim() || null);
  const domAddress = [...document.querySelectorAll('[itemprop="streetAddress"], [data-testid*="address" i], [class*="address" i]')]
    .map(node => clean(node.innerText || node.textContent))
    .find(value => value.length >= 5 && value.length <= 180) || null;
  const address = findText(['address', 'streetAddress', 'formattedAddress', 'fullAddress', 'addressLine', 'displayAddress'])
    || domAddress
    || addressFromText(fullText);
  const coordinates = pageCoordinates();
  const dataLatitude = findValue(['latitude', 'lat', 'geoLatitude', 'geoLat'], value => coordinate(value, -90, 90));
  const dataLongitude = findValue(['longitude', 'lng', 'lon', 'geoLongitude', 'geoLon'], value => coordinate(value, -180, 180));
  const locationCoordinates = isAstanaCoordinates(dataLatitude, dataLongitude)
    ? {latitude: dataLatitude, longitude: dataLongitude}
    : coordinates;
  const payload = {
    source: 'browser',
    ...krishaComparison(),
    ...complexDescription(),
    source_url: location.href.split('#')[0],
    title: title.slice(0, 300),
    description,
    city: 'Астана',
    district,
    residential_complex: residentialComplex,
    address,
    latitude: locationCoordinates?.latitude ?? null,
    longitude: locationCoordinates?.longitude ?? null,
    price_kzt: price,
    area_m2: area,
    rooms,
    bathrooms_count: bathroomsCount,
    bathrooms_count_is_lower_bound: bathroomData?.lower ?? false,
    floor,
    floors_total: floorsTotal,
    building_year: buildingYear,
    building_type: buildingType,
    is_full_two_room: rooms === 2,
    mortgage_supported: mortgageSupported(),
    seller_name: sellerName(),
    seller_type: sellerType(),
    photo_urls: photoUrls,
  };

  const missing = ['price_kzt', 'area_m2', 'rooms'].filter(key => !payload[key]);
  if (!apiOrigin) {
    showMessage('Не удалось определить адрес ApartmentAgent. Запустите bookmarklet из приложения ещё раз.', true);
    return;
  }
  if (missing.length) {
    showMessage(`Не удалось определить: ${missing.join(', ')}. Используйте ручной ввод в ApartmentAgent.`, true);
    return;
  }

  showMessage('ApartmentAgent: отправляю данные открытого объявления…');
  fetch(`${apiOrigin}/api/v1/listings/browser-import`, {
    method: 'POST',
    mode: 'cors',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload),
  })
    .then(async response => {
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
      return body;
    })
    .then(body => showMessage(`ApartmentAgent: сохранено, рейтинг ${body.assessment?.score ?? '—'}/100. Вернитесь в приложение.`))
    .catch(error => showMessage(`ApartmentAgent: ${error.message}`, true));
})();
