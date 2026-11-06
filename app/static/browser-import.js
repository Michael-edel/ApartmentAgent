(() => {
  window.ApartmentAgentExtract = () => {
  const clean = value => String(value ?? '').replace(/\s+/g, ' ').trim();
  const number = value => {
    if (typeof value === 'number' && value > 0) return value;
    const parsed = Number(clean(value).replace(/[^\d.,]/g, '').replace(',', '.'));
    return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
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
  const outsideListing = (node, allowAside = false) => {
    for (let current = node; current; current = current.parentElement) {
      if (!allowAside && (current.tagName === 'ASIDE' || current.getAttribute?.('role') === 'complementary')) return true;
      const markers = `${current.className || ''} ${current.getAttribute?.('data-testid') || ''}`.split(/\s+/);
      if (markers.some(marker => /^(?:recommendations?(?:__.*|-.*)?|similar(?:__.*|-.*)?|related(?:__.*|-.*)?|advertisement|ad-banner)$/i.test(marker)
        || /^(?:offer|listing)__(?:recommendations?|similar)(?:$|[_-])/i.test(marker))) return true;
    }
    return false;
  };
  const listingNodes = (selector, allowAside = false) => [...document.querySelectorAll(selector)]
    .filter(node => visibleProperty(node) && !outsideListing(node, allowAside));
  const labels = {
    'город':'Город', 'район':'Район', 'адрес':'Адрес', 'жилой комплекс':'Жилой комплекс',
    'тип дома':'Тип дома', 'год постройки':'Год постройки', 'этаж':'Этаж', 'этажей в доме':'Этажей в доме',
    'площадь':'Площадь', 'общая площадь':'Площадь', 'жилая площадь':'Жилая площадь', 'площадь кухни':'Площадь кухни',
    'количество комнат':'Количество комнат', 'комнат':'Количество комнат',
    'состояние квартиры':'Состояние квартиры', 'ремонт':'Состояние квартиры',
    'санузел':'Санузел', 'санузлы':'Санузел', 'с/у':'Санузел',
    'ипотека':'Ипотека', 'в ипотеку':'Ипотека', 'высота потолков':'Высота потолков',
    'балкон':'Балкон', 'балкон или лоджия':'Балкон', 'балкон остеклён':'Остекление балкона', 'балкон остеклен':'Остекление балкона',
    'дверь':'Дверь', 'входная дверь':'Дверь', 'интернет':'Интернет', 'мебель':'Мебель', 'меблирована':'Мебель',
    'пол':'Пол', 'парковка':'Парковка', 'безопасность':'Безопасность', 'в залоге':'В залоге',
    'бывшее общежитие':'Бывшее общежитие', 'возможен обмен':'Возможен обмен', 'обмен':'Возможен обмен',
    'отопление':'Отопление', 'газ':'Газ', 'водоснабжение':'Водоснабжение', 'канализация':'Канализация',
    'планировка':'Планировка', 'окна':'Окна', 'лифт':'Лифт', 'документы':'Документы',
  };
  const semanticLabels = {
    'flat.toilet':'Санузел', 'bathrooms':'Санузел', 'flat.renovation':'Состояние квартиры',
    'flat.condition':'Состояние квартиры', 'flat.building':'Тип дома', 'house.type':'Тип дома',
    'house.year':'Год постройки', 'flat.year':'Год постройки', 'flat.floor':'Этаж', 'house.floor_num':'Этажей в доме',
    'flat.area':'Площадь', 'flat.rooms':'Количество комнат', 'flat.kitchen':'Площадь кухни',
    'flat.ceiling':'Высота потолков', 'flat.balcony':'Балкон', 'flat.balcony_g':'Остекление балкона',
    'flat.door':'Дверь', 'flat.internet':'Интернет', 'flat.furniture':'Мебель', 'flat.parking':'Парковка',
    'flat.security':'Безопасность', 'flat.flooring':'Пол', 'flat.mortgage':'Ипотека', 'flat.is_in_pledge':'В залоге',
    'city':'Город', 'flat.city':'Город', 'flat.address':'Адрес',
  };
  const labelKey = value => clean(value).replace(/[:—–-]\s*$/, '').toLowerCase();
  const propertyDetails = {};
  const propertyPair = node => {
    const text = propertyText(node);
    const generic = /(?:a-list__item|offer__info-item)/.test(String(node.className || ''));
    const rowLabel = value => labels[labelKey(value)] || (generic && clean(value).length <= 100 && !/Автор|Реклама|Похожие|Показать телефон|Политика|Подписаться/i.test(value) ? clean(value).replace(/[:—–-]\s*$/, '') : null);
    const exact = labels[labelKey(text)];
    const sibling = node.nextElementSibling;
    if (exact && sibling && visibleProperty(sibling) && !outsideListing(sibling)
      && (node.tagName !== 'DT' || sibling.tagName === 'DD')) return [exact, propertyText(sibling)];
    for (const label of [...(node.children || [])]) {
      const name = rowLabel(propertyText(label));
      const value = label.nextElementSibling;
      if (name && visibleProperty(label) && value && visibleProperty(value)) return [name, propertyText(value)];
    }
    for (const alias of Object.keys(labels).sort((a,b) => b.length-a.length)) {
      if (text.toLowerCase().startsWith(alias)) {
        const value = text.slice(alias.length).replace(/^\s*[:—–-]?\s*/, '');
        if (value) return [labels[alias], value];
      }
    }
    const semantic = semanticLabels[node.getAttribute?.('data-name')];
    return semantic && text ? [semantic,text] : null;
  };
  for (const row of listingNodes('.offer__info-item, .a-list__item, [data-name^="flat."], [data-name^="house."], [data-name="city"], dt')) {
    const pair = propertyPair(row);
    if (pair && pair[1] && !(pair[0] in propertyDetails) && Object.keys(propertyDetails).length < 60) propertyDetails[pair[0].slice(0,100)] = pair[1].slice(0,1500);
  }
  const descriptionNodes = listingNodes('.offer__description, [data-testid="description"], [data-testid="advert-description"], [itemprop="description"]');
  const prose = value => String(value ?? '').replace(/\r\n?/g,'\n').split('\n').map(line => line.replace(/[ \t]+/g,' ').trim()).join('\n').trim();
  const domDescription = descriptionNodes.map(node => prose(node.innerText ?? node.textContent))
    .filter(text => text.length >= 10).sort((a,b) => b.length-a.length)[0]?.slice(0,20000) || null;
  const authorNodes = listingNodes('[data-testid="seller"], [data-testid="author"], .offer__contacts, .owners, .owner, [itemprop="seller"]', true);
  const authorText = authorNodes.map(propertyText).join(' ');
  const sellerTypeText = value => {
    const text = clean(value);
    const match = text.match(/Хозяин(?:\s+недвижимости)?|Собственник|Специалист(?:\s+по\s+недвижимости)?|Компания|Агентство\s+недвижимости|Застройщик/i);
    return match ? clean(match[0]).slice(0,100) : null;
  };
  const domSellerType = sellerTypeText(authorText)
    || listingNodes('[data-testid="seller-type"], .owners__type, .owner__type', true).map(node => sellerTypeText(propertyText(node))).find(Boolean) || null;
  const validSellerName = value => {
    const text = clean(value);
    return text && text.length <= 200 && !/^(?:Автор объявления|Хозяин(?: недвижимости)?|Собственник|Специалист(?: по недвижимости)?|Компания|Агентство недвижимости|Застройщик|Показать телефоны)$/i.test(text)
      && !/^\+?[\d ()-]{6,}$/.test(text) ? text : null;
  };
  const domSellerName = listingNodes('[data-testid="seller-name"], .owners__name, .owner__name, [itemprop="seller"] [itemprop="name"], [data-name="seller.name"]', true)
    .map(node => validSellerName(propertyText(node))).find(Boolean) || null;
  const hasDomEvidence = Object.keys(propertyDetails).length > 0 || !!domDescription
    || authorNodes.some(node => /Автор\s+объявления/i.test(propertyText(node))) || !!domSellerName || !!domSellerType;
  const leadingNumber = value => {
    const match = clean(value).match(/^(\d{1,5}(?:[.,]\d+)?)(?=$|\s|м)/i);
    return match ? Number(match[1].replace(',','.')) : null;
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
    const blocks = listingNodes('section, article, div')
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
    if (typeof value === 'number' && Number.isSafeInteger(value) && value >= 500000) return value;
    const raw = clean(value).replace(/\u00a0/g, ' ');
    const money = raw.match(/(\d[\d\s]{5,})\s*(?:₸|тг|тенге)/i);
    if (money) return /(?:^|\s)от\s*$/i.test(raw.slice(0, money.index)) ? null : Number(money[1].replace(/\D/g, '')) || null;
    const millions = raw.match(/(\d{1,3}(?:[.,]\d+)?)\s*млн/i);
    if (millions) return /(?:^|\s)от\s*$/i.test(raw.slice(0, millions.index)) ? null : Math.round(Number(millions[1].replace(',', '.')) * 1000000);
    if (!allowPlain || !/^\d[\d\s]*$/.test(raw)) return null;
    const digits = raw.replace(/\s/g, '');
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
  const sellerName = () => domSellerName || findValue(['sellerName', 'ownerName', 'advertiserName'], value =>
    typeof value === 'string' ? validSellerName(value) : null);
  const sellerType = () => domSellerType || findValue(['sellerType', 'ownerType', 'advertiserType'], value =>
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
    const structured = mortgageValue(propertyDetails['Ипотека']) ?? findValue(['mortgageSupported', 'mortgage_supported', 'isMortgageAvailable'], mortgageValue);
    const fieldText = listingNodes('[data-testid="mortgage"], [data-testid="mortgage-status"], [data-property="mortgage"]').map(propertyText).join(' ');
    const descriptionText = domDescription || '';
    const values = [structured, mortgageTextValue(fieldText), mortgageTextValue(descriptionText), domDescription ? null : mortgageTextValue(meta('og:description') || meta('description'))];
    if (values.includes(false)) return false;
    return values.includes(true) ? true : null;
  };
  const addressFromText = value => {
    const normalized = clean(value);
    const patterns = [
      /\d{1,2}\s*(?:из|\/)\s*\d{1,2}\s*(?:этаж|эт\.)\s*,\s*([^—–]{3,180})(?:\s*[—–]|$)/i,
      /(?:№\s*\d+\s*[:：]\s*|Продажа[^:—]{0,120}[:：]\s*)([^—]{3,120}?),\s*(?:Астана|Astana)(?![А-Яа-яЁё])/i,
      /(?:^|[^\p{L}])((?:ул\.?|улица|просп\.?|проспект|пер\.?|переулок|мкр\.?|микрорайон|шоссе|набережная)\s+[^,;]{2,80},?\s+\d+[A-Za-zА-Яа-яЁё]?(?:\/\d+)?)/iu,
    ];
    for (const pattern of patterns) {
      const match = normalized.match(pattern);
      const candidate = clean(match?.[1]).replace(/[ ,.-]+$/, '');
      if (candidate.length >= 5 && candidate.length <= 180) return candidate;
    }
    return null;
  };
  const validPropertyAddress = value => {
    const text = clean(value);
    // Some seller cards use an "address" class for the employer caption.
    // That caption is not the apartment's location, even when it precedes h1.
    return text.length >= 5 && text.length <= 500 && !/работает\s+в\s+компании/i.test(text) ? text : null;
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
      if (!visibleProperty(node) || outsideListing(node)) continue;
      const pair = coordinatePair(node.href || node.src || node.getAttribute('data-coordinates'));
      if (pair) return pair;
      const latitude = coordinate(node.getAttribute('data-latitude') || node.getAttribute('data-lat'), -90, 90);
      const longitude = coordinate(node.getAttribute('data-longitude') || node.getAttribute('data-lng'), -180, 180);
      if (isAstanaCoordinates(latitude, longitude)) return {latitude, longitude};
    }
    return null;
  };
  let pageText = document.body?.innerText || '';
  for (const notice of document.querySelectorAll('[data-apartment-agent-extension-message], [data-apartment-agent-message]')) {
    pageText = pageText.replace(notice.innerText || notice.textContent || '', '');
  }
  const headline = listingNodes('h1').map(propertyText).find(Boolean) || meta('og:title') || document.title || '';
  const fullText = clean(`${headline} ${pageText}`);
  const areaMatch = fullText.match(/(\d{2,3}(?:[.,]\d+)?)\s*(?:м²|м2|кв\.?\s*м)/i);
  const roomsMatch = fullText.match(/(\d+)\s*[-–]?\s*комн(?:ат(?:н(?:ая|ую|ой))?|\.)/i);
  const floorMatch = fullText.match(/(\d{1,2})\s*(?:из|\/)\s*(\d{1,2})\s*(?:этаж|эт\.)|(?:этаж|эт\.)\s*[:—-]?\s*(\d{1,2})\s*(?:из|\/)\s*(\d{1,2})/i);
  const propertyFloor = clean(propertyDetails['Этаж']).match(/^(\d{1,2})(?:\s*(?:из|\/)\s*(\d{1,2}))?/i);
  const yearMatch = fullText.match(/(?:год\s+постройки|построен\w*)\D{0,12}(19\d{2}|20\d{2})/i);
  const headlineArea = headline.match(/(\d{2,3}(?:[.,]\d+)?)\s*(?:м²|м2)/i);
  const headlineRooms = headline.match(/(\d+)\s*[-–]?\s*комнат/i);
  const area = leadingNumber(propertyDetails['Площадь']) || (headlineArea ? number(headlineArea[1]) : null) || findValue(['area', 'area_m2', 'areaM2', 'square', 'floorSize', 'totalArea']) || (areaMatch ? number(areaMatch[1]) : null);
  const positiveInteger = value => Number.isInteger(value) && value > 0 ? value : null;
  const rooms = positiveInteger(leadingNumber(propertyDetails['Количество комнат']) || (headlineRooms ? Number(headlineRooms[1]) : null) || findValue(['rooms', 'roomCount', 'numberOfRooms', 'roomsCount']) || (roomsMatch ? Number(roomsMatch[1]) : null));
  const bathroomInfo = value => {
    const s = String(value ?? '').trim();
    if (/^(?:раздельный|совмещ[её]нный)$/i.test(s)) return {count:1, lower:false};
    const match = s.match(/^(\d{1,2})(?:\s*с\s*\/\s*у)?\s*(и\s+более|и\s+больше|\+)?$/i);
    if (!match || Number(match[1]) > 20) return null;
    return {count:Number(match[1]), lower:!!match[2]};
  };
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
  const bathroomData = bathroomInfo(propertyDetails['Санузел']) ?? listingNodes('[data-testid="bathrooms"], [data-name="bathrooms"], [data-name="flat.toilet"], .a-list__item, .offer__info-item, dt')
      .map(bathroomField).find(value => value !== null)
    ?? findValue(['bathroomsCount', 'bathrooms_count', 'numberOfBathroomsTotal', 'numberOfBathrooms'], bathroomInfo) ?? null;
  const bathroomsCount = bathroomData?.count ?? null;
  let floor = positiveInteger((propertyFloor ? Number(propertyFloor[1]) : null) || findValue(['floor', 'floorNumber']) || (floorMatch ? Number(floorMatch[1] || floorMatch[3]) : null));
  let floorsTotal = positiveInteger((propertyFloor?.[2] ? Number(propertyFloor[2]) : null) || leadingNumber(propertyDetails['Этажей в доме']) || findValue(['floors', 'floorsTotal', 'floorCount', 'numberOfFloors']) || (floorMatch ? Number(floorMatch[2] || floorMatch[4]) : null));
  if (floor && floorsTotal && floor > floorsTotal) { floor = null; floorsTotal = null; }
  const rawYear = leadingNumber(propertyDetails['Год постройки']) || findValue(['year', 'buildYear', 'buildingYear', 'yearBuilt']) || (yearMatch ? Number(yearMatch[1]) : null);
  const buildingYear = Number.isInteger(rawYear) && rawYear >= 1900 && rawYear <= 2100 ? rawYear : null;
  const domPrice = listingNodes('.offer__price, [data-testid="price"], [itemprop="price"]').map(node => parsePrice(node.getAttribute?.('content') || propertyText(node))).find(Boolean);
  const price = domPrice || findValue(['price', 'price_kzt', 'priceKzt'], parsePrice) || parsePrice(fullText, false);
  const photos = listingNodes('.offer__photos img, .offer__gallery img, .gallery img, .gallery__main img, img.gallery__main-image, [data-testid="gallery"] img, [data-testid="advert-gallery"] img, [data-testid="offer-gallery"] img, [data-name="photos"] img, .offer__photos a[href], .offer__gallery a[href]')
    .map(image => image.tagName === 'A' && !/\.(?:jpe?g|png|webp|avif|gif)(?:[?#]|$)/i.test(image.href || '') ? null
      : image.dataset?.full || image.dataset?.src || image.getAttribute?.('data-full') || image.currentSrc || image.src || image.href);
  photos.push(meta('og:image'), meta('twitter:image'));
  const imageKeys = new Set(['image', 'images', 'photo', 'photos', 'photoUrls', 'imageUrls', 'gallery', 'pictures']);
  for (const root of roots) for (const item of walk(root)) {
    const source = String(item.url || item['@id'] || '').split(/[?#]/)[0].replace(/\/$/,'');
    if (source !== location.href.split(/[?#]/)[0].replace(/\/$/,'')) continue;
    for (const [key,value] of Object.entries(item)) {
      if (!imageKeys.has(key)) continue;
      for (const candidate of (Array.isArray(value) ? value : [value])) photos.push(typeof candidate === 'string' ? candidate : candidate?.url || candidate?.src);
    }
  }
  const photoUrls = [...new Set(photos.map(clean).map(value => value.startsWith('//') ? `https:${value}` : value))]
    .filter(value => /^https?:\/\//i.test(value)).slice(0, 30);
  const buildingText = (propertyDetails['Тип дома'] || findText(['buildingType', 'houseType', 'wallMaterial']) || '').toLowerCase();
  const description = domDescription || clean(meta('og:description') || meta('description')).slice(0,20000) || null;
  const cityText = clean(propertyDetails['Город'] || findText(['city', 'addressLocality']));
  const city = cityText ? cityText.split(',')[0].replace(/^Город\s+/i,'').replace(/\s+(?:р\s*-\s*н|район)\s+.*$/i,'').trim().slice(0,100) : 'Астана';
  const address = validPropertyAddress(propertyDetails['Адрес']) || listingNodes('[itemprop="streetAddress"], [data-testid*="address" i], [class*="address" i]').map(propertyText).find(value => value.length <= 180 && validPropertyAddress(value))
    || addressFromText(headline) || findValue(['streetAddress', 'formattedAddress', 'fullAddress', 'addressLine', 'displayAddress', 'address'], value => validPropertyAddress(textValue(value))) || addressFromText(fullText);
  const complex = propertyDetails['Жилой комплекс'] || findText(['complexName', 'residentialComplex', 'housingComplex', 'residentialComplexName']) || (headline.match(/ЖК\s*[«"]?([^»".,;]{2,100})/i)?.[1]?.trim() || null);
  const coordinates = pageCoordinates();
  const dataLatitude = findValue(['latitude', 'lat', 'geoLatitude', 'geoLat'], value => coordinate(value, -90, 90));
  const dataLongitude = findValue(['longitude', 'lng', 'lon', 'geoLongitude', 'geoLon'], value => coordinate(value, -180, 180));
  const locationCoordinates = isAstanaCoordinates(dataLatitude, dataLongitude)
    ? {latitude: dataLatitude, longitude: dataLongitude}
    : coordinates;
  const payload = {
    source: 'browser-extension',
    ...krishaComparison(),
    ...complexDescription(),
    source_url: location.href.split('#')[0],
    title: clean(headline || `${rooms || 2}-комнатная квартира`).slice(0,300),
    description,
    repair_condition: (propertyDetails['Состояние квартиры'] || findText(['repairCondition', 'repair_condition', 'renovation']))?.slice(0,500) || null,
    property_details: propertyDetails,
    details_extracted: !!(price && area && rooms && hasDomEvidence),
    city,
    district: districtFromText(propertyDetails['Город']) || districtFromText(propertyDetails['Район'], true) || districtFromText(findText(['district', 'districtName', 'areaName']), true)
      || districtFromText(findText(['regionName', 'addressRegion']))
      || districtFromText(fullText),
    residential_complex: complex?.slice(0,200) || null,
    address: address?.slice(0,500) || null,
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
    building_type: /кирпич|brick/.test(buildingText) ? 'brick' : /монолит|monolith/.test(buildingText) ? 'monolith' : /панел|panel/.test(buildingText) ? 'panel' : null,
    is_full_two_room: rooms === 2,
    mortgage_supported: mortgageSupported(),
    seller_name: sellerName(),
    seller_type: sellerType(),
    photo_urls: photoUrls,
  };
  return payload;
  };
})();

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


  const headings = [document.title || '', ...document.querySelectorAll('h1')].map(value => typeof value === 'string' ? value : value.innerText || value.textContent || '').join(' ');
  if (/(?:^|[^\p{L}])(?:captcha|recaptcha)(?=$|[^\p{L}])|проверка\s+(?:браузера|безопасности)|(?:HTTP\s*)?(?:403|429|468)\s*(?:Forbidden|Too many requests|Access denied)|HTTP(?:\s+Error)?\s*(?:403|429|468)/iu.test(headings)) {
    showMessage('ApartmentAgent: сайт запросил проверку браузера. Пройдите её самостоятельно.', true);
    return;
  }
  const payload = {...window.ApartmentAgentExtract(), source:'browser'};
  const missing = ['price_kzt', 'area_m2', 'rooms'].filter(key => !payload[key]);
  if (!apiOrigin) { showMessage('Не удалось определить адрес ApartmentAgent. Запустите bookmarklet из приложения ещё раз.', true); return; }
  if (missing.length) { showMessage(`Не удалось определить: ${missing.join(', ')}. Используйте ручной ввод в ApartmentAgent.`, true); return; }
  showMessage('ApartmentAgent: отправляю данные открытого объявления…');
  fetch(`${apiOrigin}/api/v1/listings/browser-import`, {
    method:'POST', mode:'cors', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload),
  }).then(async response => {
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = Array.isArray(body.detail) ? body.detail.map(item => `${(item.loc || []).filter(key => key !== 'body').join('.')}: ${item.msg || 'некорректное значение'}`).join('; ') : typeof body.detail === 'string' ? body.detail : `HTTP ${response.status}`;
      throw new Error(detail);
    }
    return body;
  }).then(body => showMessage(`ApartmentAgent: сохранено, рейтинг ${body.assessment?.score ?? '—'}/100. Вернитесь в приложение.`))
    .catch(error => showMessage(`ApartmentAgent: ${error.message}`, true));
})();
