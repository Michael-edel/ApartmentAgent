(() => {
  'use strict';
  const cityCenters = {astana:[51.128,71.43], almaty:[43.238,76.889], shymkent:[42.32,69.59], karaganda:[49.806,73.085], pavlodar:[52.287,76.967]};
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
  const validPoint = item => Number.isFinite(item.latitude) && Number.isFinite(item.longitude)
    && item.latitude >= -90 && item.latitude <= 90 && item.longitude >= -180 && item.longitude <= 180;

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
    const reset = node('button', 'Показать все', 'listing-map-reset'); reset.type = 'button'; reset.hidden = true;
    const canvas = node('div', null, 'listing-map-canvas');
    canvas.setAttribute('aria-label', 'Карта квартир');
    const list = node('details', null, 'listing-map-list');
    const listLabel = node('summary', 'Список квартир на карте');
    const listItems = node('div', null, 'listing-map-list-items');
    list.append(listLabel, listItems);
    content.append(status, alert, reset, canvas, list); shell.append(header, content); dialog.append(shell); document.body.append(dialog);
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
      if (Number.isFinite(item.price_kzt) && item.price_kzt > 0) entry.append(node('span', `${fmt.format(item.price_kzt)} ₸`));
      else entry.append(node('span', 'Цена не указана'));
      if (item.address) entry.append(node('span', item.address));
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
        const items = Array.isArray(data.items) ? data.items.filter(validPoint) : [];
        const cityCenter = cityCenters[data.city] || cityCenters[options.city] || cityCenters.astana;
        const missing = Number.isFinite(data.without_coordinates) ? data.without_coordinates : 0;
        const updateList = visible => {
          status.textContent = `На карте: ${fmt.format(items.length)}. В видимой области: ${fmt.format(visible.length)}. Без координат: ${fmt.format(missing)}.`;
          listLabel.textContent = `Квартиры в видимой области: ${fmt.format(visible.length)}`;
          listItems.replaceChildren(...visible.map(makeEntry));
          if (!visible.length) listItems.append(node('p', 'В этой области квартир нет.'));
        };
        if (!window.L) {
          canvas.hidden = true; list.open = true;
          showAlert('Не удалось загрузить модуль карты. Квартиры с координатами доступны в списке.');
          updateList(items); return;
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
        const groups = new Map();
        for (const item of items) {
          const key = `${item.latitude},${item.longitude}`;
          if (!groups.has(key)) groups.set(key, []);
          groups.get(key).push(item);
        }
        const positions = [];
        for (const group of groups.values()) {
          const first = group[0]; const position = [first.latitude, first.longitude]; positions.push(position);
          const icon = L.divIcon({className:'listing-map-point', html:`<div class="listing-map-pin"><span>${group.length > 1 ? group.length : '•'}</span></div>`, iconSize:[30,30], iconAnchor:[15,30]});
          const popup = node('div', null, 'listing-map-popup'); group.forEach(item => popup.append(makeEntry(item)));
          L.marker(position, {icon, title:group.length > 1 ? `Квартир в одной точке: ${group.length}` : first.title || 'Квартира'}).bindPopup(popup).addTo(map);
        }
        const fitAll = () => {
          if (positions.length === 1) map.setView(positions[0],15,{animate:false});
          else if (positions.length > 1) map.fitBounds(positions, {padding:[30,30], maxZoom:15, animate:false});
          else map.setView(cityCenter,11,{animate:false});
        };
        reset.hidden = false; reset.disabled = !positions.length; reset.addEventListener('click', fitAll);
        fitAll();
        const update = () => {
          if (!dialog.open || active !== state) return;
          const bounds = map.getBounds();
          updateList(items.filter(item => bounds.contains([item.latitude,item.longitude])));
        };
        map.on('moveend', update); update();
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
