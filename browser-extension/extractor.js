(() => {
  window.ApartmentAgentExtract = () => {
  const clean = value => String(value ?? '').replace(/\s+/g, ' ').trim();
  const number = value => {
    if (value && typeof value === 'object') value = value.value ?? value.amount;
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
  const adIdFromUrl = value => {
    if (typeof value !== 'string') return null;
    try {
      const url = new URL(value, location.href);
      return url.protocol === 'https:' && ['krisha.kz','www.krisha.kz'].includes(url.hostname)
        && !url.port && !url.username && !url.password ? url.pathname.match(/^\/a\/show\/(\d+)\/?$/)?.[1] || null : null;
    } catch (_) { return null; }
  };
  const targetId = adIdFromUrl(location.href);
  const outsideListing = (node, asideContext = null) => {
    let insideOfferInfo = false;
    for (let current = node; current; current = current.parentElement) {
      const markers = `${current.className || ''} ${current.getAttribute?.('data-testid') || ''}`.split(/\s+/);
      if (asideContext === 'condition' && markers.includes('offer__info')) insideOfferInfo = true;
      if (asideContext === 'specifications' && ['FOOTER','NAV'].includes(current.tagName)) return true;
      if (current.tagName === 'ASIDE' || current.getAttribute?.('role') === 'complementary') {
        const offerSidebar = (asideContext === 'offer' || asideContext === 'condition') && markers.includes('offer__sidebar');
        if (asideContext !== 'author' && asideContext !== 'specifications' && !offerSidebar && !insideOfferInfo) return true;
      }
      if (markers.some(marker => /^(?:(?:recommendations?|similar|related|nearby|promo(?:tion)?|sponsored)(?:[-_].*)?|advertisement|ad-banner)$/i.test(marker)
        || /^(?:(?:offer|listing)__(?:recommendations?|similar)|(?:a|offer|listing)-card)(?:$|[_-])/i.test(marker))) return true;
    }
    return false;
  };
  const listingNodes = (selector, asideContext = null) => [...document.querySelectorAll(selector)]
    .filter(node => visibleProperty(node) && !outsideListing(node, asideContext));
  const hasClass = (node, name) => String(node?.className || '').split(/\s+/).includes(name);
  const inAuthorContext = node => {
    for (let current = node; current; current = current.parentElement) {
      const marker = `${current.className || ''} ${current.id || ''} ${current.getAttribute?.('data-testid') || ''} ${current.getAttribute?.('itemprop') || ''}`;
      if (/(?:^|[\s_-])(?:owners?|sellers?|contacts?|agents?|authors?|advertisers?|brokers?|realtors?|company)(?=$|[\s_-])/i.test(marker)) return true;
    }
    return false;
  };
  const labels = {
    'город':'Город', 'район':'Район', 'адрес':'Адрес', 'жилой комплекс':'Жилой комплекс',
    'тип дома':'Тип дома', 'год постройки':'Год постройки', 'этаж':'Этаж', 'этажей в доме':'Этажей в доме',
    'площадь':'Площадь', 'общая площадь':'Площадь', 'жилая площадь':'Жилая площадь', 'площадь кухни':'Площадь кухни',
    'количество комнат':'Количество комнат', 'комнат':'Количество комнат',
    'состояние квартиры':'Состояние квартиры', 'ремонт':'Состояние квартиры',
    'санузел':'Санузел', 'санузлы':'Санузел', 'с/у':'Санузел',
    'ипотека':'Ипотека', 'в ипотеку':'Ипотека', 'возможность ипотеки':'Ипотека', 'высота потолков':'Высота потолков',
    'балкон':'Балкон', 'балкон или лоджия':'Балкон', 'балкон остеклён':'Остекление балкона', 'балкон остеклен':'Остекление балкона',
    'дверь':'Дверь', 'входная дверь':'Дверь', 'интернет':'Интернет', 'мебель':'Мебель', 'меблирована':'Мебель',
    'пол':'Пол', 'парковка':'Парковка', 'безопасность':'Безопасность', 'в залоге':'В залоге',
    'бывшее общежитие':'Бывшее общежитие', 'возможен обмен':'Возможен обмен', 'обмен':'Возможен обмен',
    'отопление':'Отопление', 'газ':'Газ', 'водоснабжение':'Водоснабжение', 'канализация':'Канализация',
    'планировка':'Планировка', 'окна':'Окна', 'лифт':'Лифт', 'документы':'Документы',
    'тип рынка':'Тип рынка', 'рынок жилья':'Рынок жилья', 'тип жилья':'Тип жилья',
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
  const detailValue = value => value && typeof value === 'object' ? value.text ?? value.label ?? value.value : value;
  const usableRepair = value => {
    const text = detailValue(value);
    return typeof text === 'string' && /\p{L}/u.test(text) && !/^(?:не\s+указан[оа]?|нет\s+данных|неизвестно)[.!]?$/i.test(clean(text)) ? clean(text) : null;
  };
  const propertyDetails = {};
  const propertyPair = node => {
    const text = propertyText(node);
    const generic = /(?:a-list__item|offer__info-item)/.test(String(node.className || ''));
    const rowLabel = value => labels[labelKey(value)] || (generic && clean(value).length <= 100 && !/Автор|Реклама|Похожие|Показать телефон|Политика|Подписаться/i.test(value) ? clean(value).replace(/[:—–-]\s*$/, '') : null);
    const exact = labels[labelKey(text)];
    const sibling = node.nextElementSibling;
    const separateTitle = hasClass(node, 'offer__info-title');
    if (exact && sibling && visibleProperty(sibling) && !outsideListing(sibling, 'offer') && !inAuthorContext(sibling)
      && (node.tagName !== 'DT' || sibling.tagName === 'DD')
      && (!separateTitle || node.tagName === 'DT' || hasClass(sibling, 'offer__info-value'))) return [exact, propertyText(sibling)];
    if (separateTitle) return null;
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
  const propertySelector = '.offer__info-item, .a-list__item, [data-name^="flat."], [data-name^="house."], [data-name="city"], dt, .offer__info .offer__info-title, .offer__sidebar .offer__info-title';
  for (const row of listingNodes(propertySelector, 'offer').filter(node => !inAuthorContext(node))) {
    const pair = propertyPair(row);
    if (pair && pair[1] && !(pair[0] in propertyDetails) && Object.keys(propertyDetails).length < 60) propertyDetails[pair[0].slice(0,100)] = pair[1].slice(0,1500);
  }
  const repairField = 'Состояние квартиры';
  const repairFromSpecifications = () => {
    if (!targetId) return null;
    const repairKey = labelKey(repairField);
    const plausibleValues = {
      'тип дома': /^\p{L}[\p{L}\s-]{1,39}$/u,
      'год постройки': /^(?:19\d{2}|20\d{2}|2100)$/,
      'этаж': /^\d{1,3}(?:\s*(?:из|\/)\s*\d{1,3})?$/i,
      'площадь': /^\d{1,4}(?:[.,]\d+)?\s*(?:м[²2]|кв\.\s*м)(?:\s|$)/i,
      'санузел': /^(?:раздельный|совмещ[её]нный|\d{1,2}(?:\s*с\s*\/\s*у)?(?:\s*и\s+более)?)$/i,
    };
    const ownVisible = node => visibleProperty(node) && !outsideListing(node, 'specifications') && !inAuthorContext(node);
    const filledChildren = node => [...(node.children || [])].filter(child => clean(child.textContent));
    const rowCells = row => {
      const cells = filledChildren(row);
      return cells.length === 1 ? filledChildren(cells[0]) : cells;
    };
    const groups = new Set();
    for (const node of document.querySelectorAll('dt, th, td, label, div, span, p, b, strong')) {
      if (labelKey(node.textContent) !== repairKey || !ownVisible(node)) continue;
      let group = node.parentElement;
      for (let level = 0; group && level < 5; level++, group = group.parentElement) groups.add(group);
    }
    const repairs = new Set();
    for (const group of groups) {
      if (['BODY','HTML','MAIN'].includes(group.tagName) || !ownVisible(group)) continue;
      const rows = [...(group.children || [])];
      if (rows.length < 4 || rows.length > 20) continue;
      const foreignLink = [...(group.querySelectorAll?.('a[href*="/a/show/"]') || [])].some(link => {
        const id = adIdFromUrl(link.href);
        return id && id !== targetId;
      });
      if (foreignLink) continue;
      const matches = new Set();
      const candidates = new Set();
      for (const row of rows) {
        const cells = row.tagName === 'DT' && row.nextElementSibling?.tagName === 'DD'
          ? [row,row.nextElementSibling] : rowCells(row);
        if (cells.length !== 2 || !cells.every(ownVisible)) continue;
        const key = labelKey(propertyText(cells[0]));
        const value = propertyText(cells[1]);
        if (key === repairKey && usableRepair(value) && value.length <= 500 && !labels[labelKey(value)]) candidates.add(value);
        if (Object.hasOwn(plausibleValues,key) && plausibleValues[key].test(value)) matches.add(key);
      }
      if (matches.size >= 3) for (const condition of candidates) repairs.add(condition);
    }
    return repairs.size === 1 ? repairs.values().next().value : null;
  };
  if (!usableRepair(propertyDetails[repairField]) && (repairField in propertyDetails || Object.keys(propertyDetails).length < 60)) {
    // A property may be displayed as a plain label/value pair without row classes.
    const selector = ':is(.offer__info, .offer__sidebar) :is(dt, div, span)';
    for (const label of document.querySelectorAll(selector)) {
      if (labelKey(label.textContent) !== labelKey(repairField)) continue;
      const value = label.nextElementSibling;
      if (!value || (label.tagName === 'DT' ? value.tagName !== 'DD' : label.parentElement?.children?.length !== 2)) continue;
      if (![label,value].every(node => visibleProperty(node) && !outsideListing(node, 'condition') && !inAuthorContext(node))) continue;
      const condition = usableRepair(propertyText(value));
      if (!condition || condition.length > 500 || labels[labelKey(condition)]) continue;
      propertyDetails[repairField] = condition;
      break;
    }
    if (!usableRepair(propertyDetails[repairField])) {
      const condition = repairFromSpecifications();
      if (condition) propertyDetails[repairField] = condition;
    }
  }
  const domMortgageField = propertyDetails['Ипотека'];
  const descriptionNodes = listingNodes('.offer__description, [data-testid="description"], [data-testid="advert-description"], [itemprop="description"]', 'offer');
  const prose = value => String(value ?? '').replace(/\r\n?/g,'\n').split('\n').map(line => line.replace(/[ \t]+/g,' ').trim()).join('\n').trim();
  const domDescription = descriptionNodes.filter(node => !inAuthorContext(node)).map(node => prose(node.innerText ?? node.textContent))
    .filter(text => text.length >= 10).sort((a,b) => b.length-a.length)[0]?.slice(0,20000) || null;
  const authorNodes = listingNodes('[data-testid="seller"], [data-testid="author"], .offer__contacts, .owners, .owner, [itemprop="seller"]', 'author');
  const withinAuthorBlock = node => {
    for (let current = node; current; current = current.parentElement) if (authorNodes.includes(current)) return true;
    return false;
  };
  const sellerRole = /^(Крыша\s+Агент|Хозяин(?:\s+недвижимости)?|Собственник|Специалист(?:\s+по\s+недвижимости)?|Компания|Агентство\s+недвижимости|Застройщик|Ри[еэ]лтор|Агент\s+по\s+недвижимости|Частное\s+лицо)(?=$|[\s.,;:])/i;
  const sellerTypeText = value => {
    const text = clean(value);
    const role = text.match(sellerRole)?.[0];
    return role === text ? role : null;
  };
  const sellerBlockEvidence = authorNodes.flatMap(node => [propertyText(node), ...[...(node.querySelectorAll?.('[title], [aria-label], img[alt]') || [])]
    .filter(child => visibleProperty(child) && !outsideListing(child, 'author')).flatMap(child => ['title','aria-label','alt'].map(name => child.getAttribute?.(name)).filter(Boolean))]);
  const hasKrishaAgentBadge = sellerBlockEvidence.some(text => /(?:^|[\s:—–-])Крыша\s+Агент(?=$|[\s.,;:—–-])/i.test(clean(text)));
  const domRole = listingNodes('[data-testid="seller-type"], .owners__type, .owner__type', 'author')
    .filter(withinAuthorBlock).map(node => sellerTypeText(propertyText(node))).find(Boolean)
    || authorNodes.flatMap(node => [...(node.querySelectorAll?.('span, p, div, strong, h2, h3, h4') || [])])
      .filter(node => visibleProperty(node) && !outsideListing(node, 'author'))
      .map(node => sellerTypeText(propertyText(node))).find(Boolean)
    || authorNodes.map(node => {
      const text = propertyText(node);
      return text.match(/^Автор\s+объявления\s*[:—–-]?\s*(.*)$/i)?.[1]?.match(sellerRole)?.[0] || sellerTypeText(text);
    }).find(Boolean) || null;
  const domSellerType = hasKrishaAgentBadge ? 'Крыша Агент'
    : /^Специалист/i.test(domRole || '') && sellerBlockEvidence.some(text => /Специалист\s+подтвердил\s+личность\s+документами|личность\s+подтверждена/i.test(clean(text)))
      ? 'Специалист — личность подтверждена' : domRole;
  const validSellerName = value => {
    if (typeof value !== 'string') return null;
    const text = clean(value);
    return text && text.length <= 200 && !sellerTypeText(text) && !/^(?:Автор объявления|Показать телефоны|Работает в компании(?:\s|$))/i.test(text)
      && !/^\+?[\d ()-]{6,}$/.test(text) ? text : null;
  };
  const domSellerName = listingNodes('[data-testid="seller-name"], .owners__name, .owner__name, [itemprop="seller"] [itemprop="name"], [data-name="seller.name"]', 'author')
    .filter(withinAuthorBlock).map(node => validSellerName(propertyText(node))).find(Boolean) || null;
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
  const rawRoots = [];
  document.querySelectorAll('script[type="application/ld+json"], script#__NEXT_DATA__, script[type="application/json"]').forEach(node => {
    try {
      const value = JSON.parse(node.textContent || '');
      if (value) rawRoots.push(value);
    } catch (_) {}
  });
  // Read only JSON literals from recognised assignments. Never execute page code.
  const assignment = /(?:^|[;\n])\s*(?:(?:var|let|const)\s+(?:data|advert|__INITIAL_STATE__)|window\.(?:data|advert|__INITIAL_STATE__))\s*=\s*([\[{])/g;
  const codePosition = (source, position) => {
    let mode = 'code', escaped = false, characterClass = false, previous = '', depth = 0;
    for (let index = 0; index < position; index++) {
      const char = source[index], next = source[index + 1];
      if (mode === 'line') { if (char === '\n') mode = 'code'; continue; }
      if (mode === 'block') { if (char === '*' && next === '/') { mode = 'code'; index++; } continue; }
      if (mode !== 'code') {
        if (escaped) { escaped = false; continue; }
        if (char === '\\') { escaped = true; continue; }
        if (mode === 'regex') {
          if (char === '[') characterClass = true;
          else if (char === ']') characterClass = false;
          else if (char === '/' && !characterClass) mode = 'code';
        } else if (char === mode) mode = 'code';
        continue;
      }
      if (char === '/' && next === '/') { mode = 'line'; index++; continue; }
      if (char === '/' && next === '*') { mode = 'block'; index++; continue; }
      if (['"', "'", '`'].includes(char)) { mode = char; continue; }
      if (char === '/' && (!previous || /[=([{,:;!&|?~%^<>]/.test(previous))) { mode = 'regex'; continue; }
      if ('({['.includes(char)) depth++;
      else if (')}]'.includes(char)) depth--;
      if (!/\s/.test(char)) previous = char;
    }
    return mode === 'code' && depth === 0;
  };
  for (const script of document.querySelectorAll('script:not([src])')) {
    const source = String(script.textContent || '');
    assignment.lastIndex = 0;
    for (let match; (match = assignment.exec(source));) {
      const position = match.index + match[0].search(/(?:var|let|const|window)\b/);
      if (!codePosition(source,position)) continue;
      const start = assignment.lastIndex - 1;
      const stack = [];
      let quoted = false, escaped = false;
      for (let index = start; index < source.length; index++) {
        const char = source[index];
        if (quoted) {
          if (escaped) escaped = false;
          else if (char === '\\') escaped = true;
          else if (char === '"') quoted = false;
          continue;
        }
        if (char === '"') { quoted = true; continue; }
        if (char === '{' || char === '[') stack.push(char);
        else if (char === '}' || char === ']') {
          if (stack.pop() !== (char === '}' ? '{' : '[')) break;
          if (!stack.length) {
            try { rawRoots.push(JSON.parse(source.slice(start,index + 1))); } catch (_) {}
            assignment.lastIndex = index + 1;
            break;
          }
        }
      }
    }
  }
  const excludedBranch = key => /^(?:recommendations?|recommended(?:Adverts?|Listings?|Offers?)?|other(?:Adverts?|Listings?|Offers?)|similar(?:Adverts?|Listings?|Offers?)?|related(?:Adverts?|Listings?|Offers?)?|suggestions?|nearby(?:Adverts?|Listings?|Offers?)?)$/i.test(key.replace(/[_-]/g, ''));
  const authorBranch = key => /^(?:(?:seller|owner|advertiser|author|publisher|agent|broker|realtor|agency|realEstateAgent|contact|provider)(?:s|Profile)?)$/i.test(key.replace(/[_-]/g, ''));
  const entityTypes = item => (Array.isArray(item['@type']) ? item['@type'] : [item['@type']]).map(value => String(value || ''));
  const nonListingType = item => entityTypes(item).some(type => /^(?:Person|Organization|RealEstateAgent|PostalAddress|GeoCoordinates|Offer|AggregateOffer)$/i.test(type));
  const listingShape = item => !nonListingType(item) && (['price','price_kzt','offers','area','area_m2','areaM2','floorSize','rooms','numberOfRooms','additionalProperty','properties','property_details','mainEntity']
    .some(key => key in item) || entityTypes(item).some(type => /^(?:Apartment|Residence|House|Product|RealEstateListing)$/i.test(type)));
  const identities = item => {
    if (nonListingType(item)) return [];
    const result = [];
    for (const key of ['advertId','advert_id','listingId','listing_id']) if (/^\d+$/.test(String(item[key] ?? ''))) result.push(String(item[key]));
    for (const key of ['url','@id','source_url','canonicalUrl']) {
      const id = adIdFromUrl(item[key]);
      if (id) result.push(id);
    }
    if (listingShape(item) && /^\d+$/.test(String(item.id ?? ''))) result.push(String(item.id));
    return [...new Set(result)];
  };
  const candidates = [];
  const selectCandidate = value => {
    if (Array.isArray(value)) { for (const child of value) selectCandidate(child); return; }
    if (!value || typeof value !== 'object') return;
    const ids = identities(value);
    if (ids.length && (ids.length !== 1 || ids[0] !== targetId)) return;
    if (targetId && ids[0] === targetId && listingShape(value)) candidates.push(value);
    for (const [key,child] of Object.entries(value)) if (!excludedBranch(key) && !authorBranch(key)) selectCandidate(child);
  };
  for (const root of rawRoots) selectCandidate(root);
  const prune = (value, isRoot = false) => {
    if (Array.isArray(value)) return value.map(child => prune(child)).filter(child => child !== undefined);
    if (!value || typeof value !== 'object') return value;
    const ids = identities(value);
    if (!isRoot && ids.length && (ids.length !== 1 || ids[0] !== targetId)) return undefined;
    return Object.fromEntries(Object.entries(value).filter(([key]) => !excludedBranch(key))
      .map(([key,child]) => [key,prune(child)]).filter(([,child]) => child !== undefined));
  };
  const roots = [...new Set(candidates)].map(value => prune(value,true));
  window.ApartmentAgentImportState = {matched_json_id:roots.length ? targetId : null};
  function* walk(value) {
    if (Array.isArray(value)) for (const child of value) yield* walk(child);
    else if (value && typeof value === 'object') {
      if (entityTypes(value).some(type => /^(?:Person|Organization|RealEstateAgent)$/i.test(type))) return;
      yield value;
      for (const [key,child] of Object.entries(value)) {
        if (!authorBranch(key)) yield* walk(child);
      }
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
  const jsonDetailValues = {};
  const addJsonDetail = (rawLabel, rawValue) => {
    const label = semanticLabels[labelKey(rawLabel)] || labels[labelKey(rawLabel)] || clean(rawLabel);
    if (!label || label.length > 100 || /Автор|Реклама|Похожие|телефон|Политика|Подписаться/i.test(label)) return;
    rawValue = detailValue(rawValue);
    if (label === 'Состояние квартиры' && !usableRepair(rawValue)) return;
    if (!['string','number','boolean'].includes(typeof rawValue)) return;
    const value = clean(typeof rawValue === 'boolean' ? rawValue ? 'Да' : 'Нет' : rawValue);
    if (value && !(label in jsonDetailValues) && Object.keys(jsonDetailValues).length < 60) jsonDetailValues[label] = value.slice(0,1500);
  };
  const jsonDetailSources = new Set(roots.flatMap(item => [item, item.details, item.data, item.attributes])
    .filter(item => item && typeof item === 'object' && !Array.isArray(item) && !nonListingType(item)));
  const jsonDetailField = (keys, parser) => {
    for (const item of jsonDetailSources) for (const key of keys) {
      const value = parser(item[key]);
      if (value !== null) return value;
    }
    return null;
  };
  for (const item of jsonDetailSources) {
    for (const container of ['additionalProperty','properties','property_details','characteristics','parameters']) {
      const values = item[container];
      if (Array.isArray(values)) for (const value of values) {
        if (value && typeof value === 'object') {
          const semanticKey = labelKey(value.propertyID);
          const name = semanticLabels[semanticKey] ? semanticKey : value.name ?? value.label ?? value.title ?? value.propertyID;
          addJsonDetail(name, value.text ?? value.value);
        }
      }
      else if (values && typeof values === 'object') for (const [key,value] of Object.entries(values)) addJsonDetail(key,value);
    }
    for (const [key,value] of Object.entries(item)) if (semanticLabels[key]) addJsonDetail(key,value);
  }
  for (const [key,value] of Object.entries(jsonDetailValues)) if (!(key in propertyDetails) && Object.keys(propertyDetails).length < 60) propertyDetails[key] = value;
  const jsonDescription = roots.map(item => typeof item.description === 'string' ? prose(item.description) : '')
    .filter(value => value.length >= 10).sort((a,b) => b.length-a.length)[0]?.slice(0,20000) || null;
  const jsonTitle = roots.map(item => typeof (item.title ?? item.headline ?? item.name) === 'string' ? clean(item.title ?? item.headline ?? item.name) : '').find(Boolean) || null;
  const sellerKeys = ['seller', 'owner', 'advertiser', 'author'];
  const sellerNameKeys = sellerKeys.flatMap(key => [`${key}Name`, `${key}_name`]);
  const sellerTypeKeys = sellerKeys.flatMap(key => [`${key}Type`, `${key}_type`]);
  const sellerDetails = (item, allowGeneric = false) => {
    const read = (keys, validate) => keys.map(key => validate(item?.[key])).find(Boolean) || null;
    return {
      name: read(allowGeneric ? [...sellerNameKeys, 'name', 'displayName', 'display_name'] : sellerNameKeys, validSellerName),
      type: read(allowGeneric ? [...sellerTypeKeys, 'role', 'type'] : sellerTypeKeys, sellerTypeText),
    };
  };
  const jsonSeller = roots.flatMap(item => [sellerDetails(item), ...sellerKeys.map(key => sellerDetails(item[key], true))])
    .find(seller => seller.name || seller.type);
  const jsonRepair = usableRepair(jsonDetailValues['Состояние квартиры'])
    || jsonDetailField(['repairCondition', 'repair_condition', 'renovation'], usableRepair);
  const jsonRichEvidence = !!(roots.length && jsonDescription
    && (Object.keys(jsonDetailValues).length >= 2 || (jsonSeller?.name && jsonRepair)));
  const jsonComparison = {
    krisha_district_price_per_m2:findValue(['krisha_district_price_per_m2'],value => Number.isSafeInteger(Number(value)) && Number(value) > 0 && Number(value) <= 1000000000 ? Number(value) : null),
    krisha_city_price_per_m2:findValue(['krisha_city_price_per_m2'],value => Number.isSafeInteger(Number(value)) && Number(value) > 0 && Number(value) <= 1000000000 ? Number(value) : null),
    krisha_comparison_district:findText(['krisha_comparison_district'])?.slice(0,150) || null,
  };
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
  const mortgageValue = value => {
    if (typeof value === 'boolean') return value;
    const text = detailValue(value);
    if (typeof text === 'boolean') return text;
    if (typeof text !== 'string') return null;
    const normalized = clean(text).replace(/[.!]+$/, '').toLowerCase();
    if (/^(?:(?:ипотека|в ипотеку)\s*[:—–-]?\s*)?(?:да|true)$/.test(normalized)
      || /^(?:можно в ипотеку(?: и без ипотеки)?|возможна покупка в ипотеку|ипотека (?:возможна|доступна|проходит)|в ипотеку проходит|подходит под ипотеку)$/.test(normalized)) return true;
    if (/^(?:ипотек[аи]\s*[:—–-]?\s*)?(?:нет|жоқ|жок|false)$/.test(normalized)
      || /^(?:ипотека (?:не (?:рассматривается|проходит|подходит|предусмотрена|возможна|доступна)|невозможна|недоступна)|в ипотеку не проходит|не подходит под ипотеку|не прода[её]тся в ипотеку)$/.test(normalized)) return false;
    return null;
  };
  const cashOnlyField = value => {
    const text = clean(value).replace(/[.!]+$/, '').toLowerCase();
    return /^(?:только|исключительно)\s+(?:(?:за|с|по)\s+)?(?:наличные|наличными|наличкой|наличный\s+расч[её]т(?:ом)?)$/.test(text)
      || /^(?:наличные|наличными|наличкой|наличный\s+расч[её]т(?:ом)?)\s+(?:только|исключительно)$/.test(text);
  };
  // Seller prose and the headline are sent separately so the server can retain the source and quote.
  const domMortgageStatement = value => {
    if (!value) return null;
    if (mortgageValue(value) !== null || cashOnlyField(value)) return value;
    const labelled = `Ипотека ${value}`;
    return mortgageValue(labelled) !== null ? labelled : null;
  };
  const domMortgageText = domMortgageStatement(domMortgageField)
    || listingNodes('[data-testid="mortgage"], [data-testid="mortgage-status"], [data-property="mortgage"]', 'offer')
      .filter(node => !inAuthorContext(node)).map(node => domMortgageStatement(propertyText(node))).find(Boolean);
  if (domMortgageText) propertyDetails['Ипотека'] = domMortgageText.slice(0,1500);
  const mortgageSupported = mortgageValue(domMortgageText)
    ?? (domMortgageText && cashOnlyField(domMortgageText) ? false : null)
    ?? mortgageValue(jsonDetailValues['Ипотека'])
    ?? jsonDetailField(['mortgageSupported', 'mortgage_supported', 'isMortgageAvailable'], mortgageValue);
  const addressFromText = value => {
    const normalized = clean(value);
    const patterns = [
      /\d{1,2}\s*(?:из|\/)\s*\d{1,2}\s*(?:этаж|эт\.)\s*,\s*([^—–]{3,180})(?:\s*[—–]|$)/i,
      /\d{2,3}(?:[.,]\d+)?\s*(?:м²|м2)\s*,\s*([^—–·]{3,160}\s\d+[A-Za-zА-Яа-яЁё]?(?:\/\d+[A-Za-zА-Яа-яЁё]?)?)(?:\s*[—–·]|$)/i,
      /(?:№\s*\d+\s*[:：]\s*|Продажа[^:—]{0,120}[:：]\s*)([^—]{3,120}?),\s*(?:Астана|Astana|Алматы|Almaty|Шымкент|Shymkent|Караганда|Karaganda|Павлодар|Pavlodar|Экибастуз|Екібастұз|Ekibastuz)(?![А-Яа-яЁё])/i,
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
    const raw = clean(value).replace(',', '.');
    const parsed = typeof value === 'number' ? value : /^[-+]?\d+(?:\.\d+)?$/.test(raw) ? Number(raw) : NaN;
    return Number.isFinite(parsed) && parsed >= minimum && parsed <= maximum ? parsed : null;
  };
  const cityAliases = {
    'астана':'astana', 'astana':'astana', 'нур-султан':'astana', 'нұр-сұлтан':'astana',
    'алматы':'almaty', 'almaty':'almaty', 'алма-ата':'almaty',
    'шымкент':'shymkent', 'shymkent':'shymkent', 'караганда':'karaganda',
    'қарағанды':'karaganda', 'karaganda':'karaganda', 'karagandy':'karaganda',
    'павлодар':'pavlodar', 'pavlodar':'pavlodar',
    'экибастуз':'ekibastuz', 'екібастұз':'ekibastuz', 'ekibastuz':'ekibastuz'
  };
  const cityNames = {astana:'Астана', almaty:'Алматы', shymkent:'Шымкент', karaganda:'Караганда', pavlodar:'Павлодар', ekibastuz:'Экибастуз'};
  const cityKey = value => cityAliases[clean(value).split(',')[0].replace(/^(?:город|г\.)\s*/i, '').trim().toLowerCase()];
  const isCityCoordinates = (city, latitude, longitude) => {
    const bounds = {astana:[50,52,69,73], almaty:[42.8,43.7,76,77.8], shymkent:[41.8,42.7,69,70.2], karaganda:[49.4,50.5,72.2,74.3], pavlodar:[51.6,53,75.9,78.5], ekibastuz:[51.3,52.1,74.8,75.9]}[cityKey(city)];
    return !!bounds && latitude !== null && longitude !== null && latitude >= bounds[0] && latitude <= bounds[1] && longitude >= bounds[2] && longitude <= bounds[3];
  };
  const coordinatePair = (value, city) => {
    let decoded = String(value || '');
    try { decoded = decodeURIComponent(decoded); } catch (_) { /* keep original */ }
    const match = decoded.match(/(?:[?&#](?:ll|center|pt|coords|coordinates)=)([-+]?\d{1,3}(?:\.\d+)?)[,%20]+([-+]?\d{1,2}(?:\.\d+)?)/i)
      || decoded.match(/^\s*([-+]?\d{1,3}(?:\.\d+)?)[,\s]+([-+]?\d{1,2}(?:\.\d+)?)\s*$/);
    if (!match) return null;
    const longitude = coordinate(match[1], -180, 180);
    const latitude = coordinate(match[2], -90, 90);
    return isCityCoordinates(city, latitude, longitude) ? {latitude, longitude} : null;
  };
  const pageCoordinates = city => {
    const nodes = document.querySelectorAll('a[href], iframe[src], [data-latitude], [data-longitude], [data-lat], [data-lng], [data-coordinates]');
    for (const node of nodes) {
      if (!visibleProperty(node) || outsideListing(node) || inAuthorContext(node)) continue;
      const pair = coordinatePair(node.href || node.src || node.getAttribute('data-coordinates'), city);
      if (pair) return pair;
      const latitude = coordinate(node.getAttribute('data-latitude') || node.getAttribute('data-lat'), -90, 90);
      const longitude = coordinate(node.getAttribute('data-longitude') || node.getAttribute('data-lng'), -180, 180);
      if (isCityCoordinates(city, latitude, longitude)) return {latitude, longitude};
    }
    return null;
  };
  let pageText = document.body?.innerText || '';
  for (const notice of document.querySelectorAll('[data-apartment-agent-extension-message], [data-apartment-agent-message]')) {
    pageText = pageText.replace(notice.innerText || notice.textContent || '', '');
  }
  const domHeadline = listingNodes('h1').map(propertyText).find(Boolean) || '';
  const headline = domHeadline || jsonTitle || meta('og:title') || document.title || '';
  const fullText = clean(`${headline} ${pageText}`);
  // Once structured data is present, missing values must not be borrowed from
  // arbitrary page text (for example, the "similar apartments" list).
  const fallbackText = rawRoots.length ? domHeadline : fullText;
  const areaMatch = fallbackText.match(/(\d{2,3}(?:[.,]\d+)?)\s*(?:м²|м2|кв\.?\s*м)/i);
  const roomsMatch = fallbackText.match(/(\d+)\s*[-–]?\s*комн(?:ат(?:н(?:ая|ую|ой))?|\.)/i);
  const floorMatch = fallbackText.match(/(\d{1,2})\s*(?:из|\/)\s*(\d{1,2})\s*(?:этаж|эт\.)|(?:этаж|эт\.)\s*[:—-]?\s*(\d{1,2})\s*(?:из|\/)\s*(\d{1,2})/i);
  const propertyFloor = clean(propertyDetails['Этаж']).match(/^(\d{1,2})(?:\s*(?:из|\/)\s*(\d{1,2}))?/i);
  const yearMatch = fallbackText.match(/(?:год\s+постройки|построен\w*)\D{0,12}(19\d{2}|20\d{2})/i);
  const headlineArea = domHeadline.match(/(\d{2,3}(?:[.,]\d+)?)\s*(?:м²|м2)/i);
  const headlineRooms = domHeadline.match(/(\d+)\s*[-–]?\s*комнат/i);
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
  const bathroomData = bathroomInfo(propertyDetails['Санузел']) ?? listingNodes('[data-testid="bathrooms"], [data-name="bathrooms"], [data-name="flat.toilet"], .a-list__item, .offer__info-item, dt', 'offer')
      .filter(node => !inAuthorContext(node)).map(bathroomField).find(value => value !== null)
    ?? findValue(['bathroomsCount', 'bathrooms_count', 'numberOfBathroomsTotal', 'numberOfBathrooms'], bathroomInfo) ?? null;
  const bathroomsCount = bathroomData?.count ?? null;
  let floor = positiveInteger((propertyFloor ? Number(propertyFloor[1]) : null) || findValue(['floor', 'floorNumber']) || (floorMatch ? Number(floorMatch[1] || floorMatch[3]) : null));
  let floorsTotal = positiveInteger((propertyFloor?.[2] ? Number(propertyFloor[2]) : null) || leadingNumber(propertyDetails['Этажей в доме']) || findValue(['floors', 'floorsTotal', 'floors_total', 'floorCount', 'numberOfFloors']) || (floorMatch ? Number(floorMatch[2] || floorMatch[4]) : null));
  if (floor && floorsTotal && floor > floorsTotal) { floor = null; floorsTotal = null; }
  const rawYear = leadingNumber(propertyDetails['Год постройки']) || findValue(['year', 'buildYear', 'buildingYear', 'building_year', 'yearBuilt']) || (yearMatch ? Number(yearMatch[1]) : null);
  const buildingYear = Number.isInteger(rawYear) && rawYear >= 1900 && rawYear <= 2100 ? rawYear : null;
  const domPrice = listingNodes('.offer__price, [data-testid="price"], [itemprop="price"]').map(node => parsePrice(node.getAttribute?.('content') || propertyText(node))).find(Boolean);
  const price = domPrice || findValue(['price', 'price_kzt', 'priceKzt'], parsePrice) || parsePrice(fallbackText, false);
  const photos = listingNodes('.offer__photos img, .offer__gallery img, .gallery img, .gallery__main img, img.gallery__main-image, [data-testid="gallery"] img, [data-testid="advert-gallery"] img, [data-testid="offer-gallery"] img, [data-name="photos"] img, .offer__photos a[href], .offer__gallery a[href]')
    .map(image => image.tagName === 'A' && !/\.(?:jpe?g|png|webp|avif|gif)(?:[?#]|$)/i.test(image.href || '') ? null
      : image.dataset?.full || image.dataset?.src || image.getAttribute?.('data-full') || image.currentSrc || image.src || image.href);
  photos.push(meta('og:image'), meta('twitter:image'));
  const jsonPhotos = [];
  const imageKeys = new Set(['image', 'images', 'photo', 'photos', 'photoUrls', 'photo_urls', 'imageUrls', 'gallery', 'pictures']);
  for (const item of roots) {
    for (const [key,value] of Object.entries(item)) {
      if (!imageKeys.has(key)) continue;
      for (const candidate of (Array.isArray(value) ? value : [value])) jsonPhotos.push(typeof candidate === 'string' ? candidate : candidate?.url || candidate?.src);
    }
  }
  photos.push(...jsonPhotos);
  const actualPhoto = value => /^(?:https?:)?\/\//i.test(clean(value))
    && !/\/empty-photo(?:-\d+)?\.[a-z0-9]+(?:[?#]|$)/i.test(clean(value));
  window.ApartmentAgentImportState.has_json_photos = jsonPhotos.some(actualPhoto);
  const photoUrls = [...new Set(photos.map(clean).map(value => value.startsWith('//') ? `https:${value}` : value))]
    .filter(actualPhoto).slice(0, 30);
  const buildingText = (propertyDetails['Тип дома'] || findText(['buildingType', 'building_type', 'houseType', 'wallMaterial']) || '').toLowerCase();
  const description = domDescription || jsonDescription || clean(meta('og:description') || meta('description')).slice(0,20000) || null;
  const archiveNotice = /^В\s+архиве\s*[.!]?\s*Объявление\s+может\s+быть\s+неактуальным\s*[.!]?$/i;
  const inDescription = node => {
    for (let current = node; current; current = current.parentElement) {
      const classes = String(current.className || '').split(/\s+/);
      if (classes.includes('offer__description') || current.getAttribute?.('itemprop') === 'description'
        || ['description','advert-description'].includes(current.getAttribute?.('data-testid'))) return true;
    }
    return false;
  };
  const describedNotice = descriptionNodes.some(node => archiveNotice.test(propertyText(node)));
  const archived = !!targetId && !describedNotice && listingNodes('div, p, section, aside, span', 'offer')
    .some(node => !inDescription(node) && !inAuthorContext(node) && archiveNotice.test(propertyText(node)));
  const normalOffer = !!(targetId && domHeadline && headlineRooms && headlineArea && domPrice && price && area && rooms
    && (Object.keys(propertyDetails).length >= 2 || domDescription)
    && !/проверка\s+(?:браузера|безопасности)|подтвердите.{0,30}(?:человек|робот)|access\s+denied/i.test(pageText));
  const cityText = clean(propertyDetails['Город'] || findText(['city', 'addressLocality']));
  const headlineCity = headline.split(',').map(part => clean(part)).find(part => cityKey(part));
  const rawCity = (cityText || headlineCity || 'Астана').split(',')[0].replace(/^(?:Город|г\.)\s*/i,'').trim().slice(0,100);
  const city = cityNames[cityKey(rawCity)] || rawCity;
  const addressNodes = listingNodes('[itemprop="streetAddress"], [data-testid*="address" i], [class*="address" i]');
  let addressFallbackText = fallbackText;
  for (const node of [...authorNodes,...addressNodes.filter(inAuthorContext)]) {
    const text = propertyText(node);
    if (text) addressFallbackText = addressFallbackText.replace(text,'');
  }
  const address = validPropertyAddress(propertyDetails['Адрес']) || addressFromText(domHeadline)
    || addressNodes.filter(node => !inAuthorContext(node))
      .map(propertyText).find(value => value.length <= 180 && validPropertyAddress(value))
    || findValue(['streetAddress', 'formattedAddress', 'fullAddress', 'addressLine', 'displayAddress', 'address'], value => validPropertyAddress(textValue(value?.streetAddress ?? value))) || addressFromText(addressFallbackText);
  const complex = propertyDetails['Жилой комплекс'] || findText(['complexName', 'residentialComplex', 'housingComplex', 'residentialComplexName']) || (headline.match(/ЖК\s*[«"]?([^»".,;]{2,100})/i)?.[1]?.trim() || null);
  const coordinates = pageCoordinates(city);
  // Both coordinates must come from the same advertised location, never from
  // two JSON snapshots or the author's office/profile.
  let jsonCoordinates = null;
  for (const item of roots.flatMap(value => [value,value.geo,value.coordinates,value.location?.geo])) {
    if (!item || typeof item !== 'object') continue;
    const latitudeKey = ['latitude','lat','geoLatitude','geoLat'].find(key => key in item);
    const longitudeKey = ['longitude','lng','lon','geoLongitude','geoLon'].find(key => key in item);
    if (!latitudeKey || !longitudeKey) continue;
    const latitude = coordinate(item[latitudeKey],-90,90), longitude = coordinate(item[longitudeKey],-180,180);
    if (isCityCoordinates(city,latitude,longitude)) { jsonCoordinates = {latitude,longitude}; break; }
  }
  const locationCoordinates = coordinates || jsonCoordinates;
  const marketValue = value => {
    const text = clean(value).toLowerCase().replace(/ё/g, 'е');
    if (['new','primary','первичка','первичный рынок','первичное жилье','новостройка'].includes(text)) return 'new';
    if (['secondary','вторичка','вторичный рынок','вторичное жилье'].includes(text)) return 'secondary';
    return null;
  };
  const rawMarketEvidence = [propertyDetails['Тип рынка'], propertyDetails['Рынок жилья'], propertyDetails['Тип жилья'],
    ...roots.map(item => item.market_type ?? item.marketType)].filter(Boolean);
  const marketEvidence = rawMarketEvidence.map(marketValue).filter(Boolean);
  const marketTypes = new Set(marketEvidence);
  const marketType = marketTypes.size === 1 ? marketEvidence[0] : !rawMarketEvidence.length && /^Хозяин недвижимости$/i.test(clean(domSellerType)) ? 'secondary' : null;
  const domComparison = cityKey(city) === 'astana' ? krishaComparison() : {};
  const comparison = Object.fromEntries(Object.keys(jsonComparison).map(key => [key,domComparison[key] ?? jsonComparison[key]]));
  const payload = {
    source: 'browser-extension',
    availability_status: archived ? 'archived' : normalOffer ? 'active' : null,
    ...comparison,
    ...complexDescription(),
    source_url: location.href.split('#')[0],
    title: clean(headline || `${rooms || 2}-комнатная квартира`).slice(0,300),
    description,
    repair_condition: (usableRepair(propertyDetails[repairField]) || jsonRepair)?.slice(0,500) || null,
    property_details: propertyDetails,
    details_extracted: !!(price && area && rooms && (hasDomEvidence || domMortgageText || jsonRichEvidence)),
    city,
    market_type: marketType,
    city_observed: !!(cityText || headlineCity),
    district: districtFromText(propertyDetails['Город']) || districtFromText(propertyDetails['Район'], true) || districtFromText(findText(['district', 'districtName', 'areaName']), true)
      || districtFromText(findText(['regionName', 'addressRegion']))
      || districtFromText(fallbackText),
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
    mortgage_supported: mortgageSupported,
    seller_name: domSellerName || jsonSeller?.name || null,
    seller_type: domSellerType || (domSellerName && jsonSeller?.name && jsonSeller.name !== domSellerName ? null : jsonSeller?.type) || null,
    photo_urls: photoUrls,
  };
  return payload;
  };
})();
