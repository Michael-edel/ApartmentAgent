(() => {
  'use strict';
  const cityCenters = {astana:[51.128,71.43], ekibastuz:[51.718,75.322]};
  let active = null;
  const fmt = new Intl.NumberFormat('ru-RU');
  const node = (tag, text, className) => {
    const element = document.createElement(tag);
    if (text != null) element.textContent = text;
    if (className) element.className = className;
    return element;
  };
  const safeLink = raw => {
    try {
      const url = new URL(raw);
      return ['http:', 'https:'].includes(url.protocol) ? url.href : null;
    } catch (_) { return null; }
  };
  const sourceLabel = item => item.cross_platform === true ? 'Krisha.kz и OLX.kz' :
    item.platform === 'krisha' ? 'Krisha.kz' : item.platform === 'olx' ? 'OLX.kz' : 'Источник не определён';
  const groupSource = group => {
    if (group.every(item => item.cross_platform === true)) return 'cross';
    if (group.some(item => item.cross_platform === true)) return 'mixed';
    const platform = group[0]?.platform;
    return ['krisha', 'olx'].includes(platform) && group.every(item => item.platform === platform) ? platform : 'mixed';
  };
  const validPoint = item => Number.isFinite(item.latitude) && Number.isFinite(item.longitude)
    && item.latitude >= -90 && item.latitude <= 90 && item.longitude >= -180 && item.longitude <= 180;
  const matchesSource = (item, source) => source === 'all' || item.cross_platform === true
    || (source !== 'cross' && item.platform === source);

  function open(options = {}) {
    if (active) active.dialog.close();
    const dialog = node('dialog', null, 'listing-map-dialog');
    dialog.setAttribute('aria-label', 'Карта подходящих квартир');
    const shell = node('div', null, 'listing-map-shell');
    const header = node('div', null, 'listing-map-header');
    const headings = node('div');
    headings.append(node('h2', 'Карта подходящих квартир'));
    const close = node('button', 'Закрыть', 'listing-map-close');
    close.type = 'button'; close.addEventListener('click', () => dialog.close());
    header.append(headings, close);
    const content = node('div', null, 'listing-map-content');
    const status = node('p', 'Загружаем квартиры…', 'listing-map-status');
    status.setAttribute('aria-live', 'polite');
    const alert = node('p', null, 'listing-map-alert'); alert.hidden = true;
    const controls = node('div', null, 'listing-map-controls');
    const sourceFilter = node('label', 'Площадка', 'listing-map-filter');
    const sourceSelect = node('select');
    sourceSelect.disabled = true;
    for (const [value, label] of [['all', 'Все площадки'], ['krisha', 'Krisha'], ['olx', 'OLX'], ['cross', 'На обоих сайтах']]) {
      const option = node('option', label);
      option.value = value;
      sourceSelect.append(option);
    }
    sourceFilter.append(sourceSelect);
    const reset = node('button', 'Показать все метки', 'listing-map-reset'); reset.type = 'button'; reset.hidden = true;
    controls.append(sourceFilter, reset);
    const canvas = node('div', null, 'listing-map-canvas');
    canvas.setAttribute('aria-label', 'Карта квартир');
    const list = node('details', null, 'listing-map-list');
    const listLabel = node('summary', 'Список квартир на карте');
    const listItems = node('div', null, 'listing-map-list-items');
    list.append(listLabel, listItems);
    const missingList = node('details', null, 'listing-map-list');
    missingList.hidden = true;
    const missingLabel = node('summary');
    const missingItems = node('div', null, 'listing-map-list-items');
    missingList.append(missingLabel, missingItems);
    const legend = node('div', null, 'listing-map-legend');
    legend.setAttribute('aria-label', 'Обозначения источников');
    for (const [kind, label] of [['krisha', 'Krisha.kz'], ['olx', 'OLX.kz'], ['cross', 'Одна квартира на двух сайтах'], ['mixed', 'Разные источники или неизвестно'], ['approximate', 'Примерное местоположение']]) {
      const entry = node('span', null, 'listing-map-legend-entry');
      const swatch = node('span', null, `listing-map-swatch ${kind}`); swatch.setAttribute('aria-hidden', 'true');
      entry.append(swatch, document.createTextNode(label)); legend.append(entry);
    }
    content.append(status, alert, controls, legend, canvas, list, missingList); shell.append(header, content); dialog.append(shell); document.body.append(dialog);
    const controller = new AbortController();
    const state = {dialog, map: null, controller}; active = state;
    let timedOut = false;
    const timeout = window.setTimeout(() => { timedOut = true; controller.abort(); }, 10000);
    dialog.addEventListener('close', () => {
      window.clearTimeout(timeout);
      controller.abort();
      if (state.map) state.map.remove();
      dialog.remove();
      if (active === state) active = null;
    }, {once:true});
    dialog.showModal();
    close.focus();

    const makeEntry = item => {
      const entry = node('div', null, 'listing-map-entry');
      entry.append(node('strong', item.title || 'Квартира'));
      entry.append(node('span', `Источник: ${sourceLabel(item)}`, 'listing-map-source'));
      if (item.approximate_location === true) entry.append(node('span', 'Примерное местоположение. Точный адрес неизвестен.', 'listing-map-approximate-label'));
      if (Number.isFinite(item.price_kzt) && item.price_kzt > 0) entry.append(node('span', `${fmt.format(item.price_kzt)} ₸`));
      else entry.append(node('span', 'Цена не указана'));
      if (item.address || item.district) entry.append(node('span', item.address || item.district));
      if (item.listing_id && typeof options.openCard === 'function') {
        const card = node('button', 'Открыть карточку'); card.type = 'button';
        card.addEventListener('click', () => { dialog.close(); options.openCard(item.listing_id); });
        entry.append(card, document.createTextNode(' '));
      }
      const url = safeLink(item.source_url);
      if (url) {
        const link = node('a', 'Объявление ↗'); link.href = url; link.target = '_blank'; link.rel = 'noopener noreferrer';
        entry.append(link);
      }
      return entry;
    };
    const showAlert = message => { alert.textContent = message; alert.hidden = false; };
    const params = new URLSearchParams({seller: options.seller || 'all', bank: options.bank || 'all'});
    fetch(`/api/v1/search/map?${params}`, {signal:controller.signal})
      .then(response => { if (!response.ok) throw new Error(`HTTP ${response.status}`); return response.json(); })
      .then(data => {
        if (active !== state || !dialog.open) return;
        const allItems = Array.isArray(data.items) ? data.items.filter(validPoint) : [];
        const allMissing = Array.isArray(data.without_coordinates_items) ? data.without_coordinates_items : [];
        let items = [], missing = 0;
        const cityCenter = cityCenters[data.city] || cityCenters[options.city] || cityCenters.astana;
        const filterItems = () => {
          const source = sourceSelect.value;
          items = allItems.filter(item => matchesSource(item, source));
          const filteredMissing = allMissing.filter(item => matchesSource(item, source));
          missing = filteredMissing.length;
          missingList.hidden = !missing;
          missingLabel.textContent = `Объявления без координат: ${fmt.format(missing)}`;
          missingItems.replaceChildren(...filteredMissing.map(makeEntry));
        };
        const updateList = visible => {
          status.textContent = `На карте: ${fmt.format(items.length)}. В видимой области: ${fmt.format(visible.length)}. Без координат: ${fmt.format(missing)}.`;
          listLabel.textContent = `Квартиры в видимой области: ${fmt.format(visible.length)}`;
          listItems.replaceChildren(...visible.map(makeEntry));
          if (!visible.length) listItems.append(node('p', items.length ? 'В этой области квартир нет.' : 'У выбранных объявлений нет меток на карте.'));
        };
        sourceSelect.disabled = false;
        if (!window.L) {
          canvas.hidden = true; list.open = true;
          showAlert('Не удалось загрузить модуль карты. Квартиры с координатами доступны в списке.');
          const applyFilter = () => { filterItems(); updateList(items); };
          sourceSelect.addEventListener('change', applyFilter);
          applyFilter();
          return;
        }
        // Closing the dialog can remove the map during a zoom transition.
        const map = L.map(canvas, {zoomAnimation:false}).setView(cityCenter, 11); state.map = map;
        let tileErrors = 0, tileSucceeded = false;
        const tiles = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
          maxZoom:19, attribution:'&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap</a> contributors'
        });
        tiles.on('tileerror', () => { if (++tileErrors >= 4 && !tileSucceeded) showAlert('Не удалось загрузить подложку карты. Проверьте интернет; квартиры доступны в списке ниже.'); });
        tiles.on('tileload', () => { tileSucceeded = true; if (!alert.hidden) alert.hidden = true; });
        tiles.addTo(map);
        const markers = L.layerGroup().addTo(map);
        let positions = [];
        const renderMarkers = () => {
          markers.clearLayers();
          positions = [];
          const groups = new Map();
          for (const item of items) {
            const key = `${item.latitude},${item.longitude}:${item.approximate_location === true ? 'approximate' : 'exact'}`;
            if (!groups.has(key)) groups.set(key, []);
            groups.get(key).push(item);
          }
          for (const group of groups.values()) {
            const first = group[0]; const position = [first.latitude, first.longitude]; positions.push(position);
            const kind = groupSource(group);
            const approximate = first.approximate_location === true;
            const coordinateKey = `${first.latitude},${first.longitude}`;
            const hasOtherAccuracy = groups.has(`${coordinateKey}:${approximate ? 'exact' : 'approximate'}`);
            // Offset only icons when exact and approximate markers share coordinates.
            const anchorX = hasOtherAccuracy ? (approximate ? 3 : 27) : 15;
            const icon = L.divIcon({className:'listing-map-point', html:`<div class="listing-map-pin ${kind}${approximate ? ' approximate' : ''}"><span>${group.length > 1 ? group.length : '•'}</span></div>`, iconSize:[30,30], iconAnchor:[anchorX,30]});
            const popup = node('div', null, 'listing-map-popup'); group.forEach(item => popup.append(makeEntry(item)));
            L.marker(position, {icon, title:group.length > 1 ? `Квартир в одной точке: ${group.length}` : first.title || 'Квартира'}).bindPopup(popup).addTo(markers);
          }
          reset.disabled = !positions.length;
        };
        const fitAll = () => {
          if (positions.length === 1) map.setView(positions[0],15,{animate:false});
          else if (positions.length > 1) map.fitBounds(positions, {padding:[30,30], maxZoom:15, animate:false});
          else map.setView(cityCenter,11,{animate:false});
        };
        reset.hidden = false; reset.addEventListener('click', fitAll);
        const update = () => {
          if (!dialog.open || active !== state) return;
          const bounds = map.getBounds();
          updateList(items.filter(item => bounds.contains([item.latitude,item.longitude])));
        };
        const applyFilter = () => {
          filterItems();
          renderMarkers();
          fitAll();
          update();
        };
        sourceSelect.addEventListener('change', applyFilter);
        map.on('moveend', update);
        applyFilter();
        requestAnimationFrame(() => { if (active === state && dialog.open) map.invalidateSize(); });
      })
      .catch(error => {
        if (active !== state || !dialog.open || (error.name === 'AbortError' && !timedOut)) return;
        status.textContent = 'Не удалось получить квартиры для карты.';
        canvas.hidden = true; list.hidden = true;
        showAlert(timedOut ? 'Приложение не ответило за 10 секунд. Откройте карту ещё раз.' : 'Проверьте работу приложения и откройте карту ещё раз.');
      })
      .finally(() => window.clearTimeout(timeout));
  }
  window.ApartmentAgentMap = {open};
})();
