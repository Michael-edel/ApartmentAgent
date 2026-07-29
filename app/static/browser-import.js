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
  const text = clean(document.body?.innerText || '');
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
  const findText = keys => findValue(keys, value => typeof value === 'string' ? clean(value) : null);

  const price = findValue(['price', 'price_kzt', 'priceKzt', 'amount', 'value'], parsePrice) || parsePrice(fullText);
  const areaMatch = fullText.match(/(\d{2,3}(?:[.,]\d+)?)\s*(?:м²|м2|кв\.?\s*м)/i);
  const roomsMatch = fullText.match(/(\d+)\s*[-–]?\s*комн(?:ат(?:н(?:ая|ую|ой))?|\.)/i);
  const floorMatch = fullText.match(/(\d+)\s*(?:из|\/)\s*(\d+)\s*(?:этаж|эт\.)?/i);
  const yearMatch = fullText.match(/(?:год\s+постройки|построен\w*)\D{0,12}(19\d{2}|20\d{2})/i);
  const area = findValue(['area', 'area_m2', 'areaM2', 'square', 'floorSize', 'totalArea']) || (areaMatch ? number(areaMatch[1].replace(',', '.')) : null);
  const rooms = findValue(['rooms', 'roomCount', 'numberOfRooms', 'roomsCount']) || (roomsMatch ? Number(roomsMatch[1]) : null);
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
  const district = findText(['district', 'districtName', 'regionName', 'addressLocality', 'areaName'])
    || (fullText.match(/((?:Есильский|Нура|Алматы|Сарыарка|Байконур)\s+район)/i)?.[1] || null);
  const residentialComplex = findText(['complexName', 'residentialComplex', 'housingComplex', 'residentialComplexName'])
    || (fullText.match(/ЖК\s*[«"]?([^»".,;]{2,100})/i)?.[1]?.trim() || null);
  const payload = {
    source: 'browser',
    source_url: location.href.split('#')[0],
    title: title.slice(0, 300),
    description,
    city: 'Астана',
    district,
    residential_complex: residentialComplex,
    price_kzt: price,
    area_m2: area,
    rooms,
    floor,
    floors_total: floorsTotal,
    building_year: buildingYear,
    building_type: buildingType,
    is_full_two_room: rooms === 2,
    mortgage_supported: null,
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
