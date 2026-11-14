(() => {
  'use strict';
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const number = new Intl.NumberFormat('ru-RU', {maximumFractionDigits:1});
  const money = value => Number.isFinite(value) ? `${number.format(value)} ₸` : 'нет данных';
  const pct = value => Number.isFinite(value) ? `${number.format(value)}%` : 'нет данных';
  const fraction = value => Number.isFinite(value) ? pct(value * 100) : 'нет данных';
  const date = value => {
    if (!value) return 'нет данных';
    const result = new Date(value);
    return Number.isNaN(result.getTime()) ? 'нет данных' : result.toLocaleDateString('ru-RU', {timeZone:'UTC'});
  };
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const rows = (data, key) => (Array.isArray(data?.[key]?.series) ? data[key].series : [])
    .filter(row => row && finite(row.median) && !Number.isNaN(Date.parse(row.day)))
    .map(row => ({time:Date.parse(row.day), value:row.median, sample:row.sample_size}));
  function chart(data) {
    const today = Date.parse(new Date().toISOString().slice(0, 10));
    const start = Date.parse(data.chart_window?.start_day) || today - 29 * 86400000;
    const last = (Date.parse(data.chart_window?.end_day) || today) + 86400000 - 1;
    const offset = (Number(data.chart_window?.utc_offset_minutes) || 0) * 60000;
    const histories = (Array.isArray(data.price_history) ? data.price_history : [])
      .filter(row => row && finite(row.price_per_m2) && !Number.isNaN(Date.parse(row.observed_at)))
      .map(row => ({time:Date.parse(row.observed_at) + offset, value:row.price_per_m2}));
    const groups = [
      {key:'listing', label:'Эта квартира', points:histories},
      {key:'city', label:'Город', points:rows(data,'city')},
      {key:'district', label:'Район', points:rows(data,'district')},
    ];
    let ymin = Infinity, ymax = -Infinity;
    for (const group of groups) {
      group.points = group.points.filter(point => point.time >= start && point.time <= last).sort((a,b) => a.time-b.time);
      for (const point of group.points) {
        ymin = Math.min(ymin, point.value);
        ymax = Math.max(ymax, point.value);
      }
    }
    if (!Number.isFinite(ymin)) return '<p class="market-index-empty">За последние 30 дней нет наблюдений для графика.</p>';
    const pad = Math.max((ymax-ymin)*0.12, ymax*0.02, 1), low = Math.max(0,ymin-pad), high = ymax+pad;
    const x = time => 48 + (time-start)/(last-start)*520;
    const y = value => 165 - (value-low)/(high-low)*140;
    const paths = groups.map(group => group.points.length ? `<g class="market-index-${group.key}">${group.points.length > 1 ? `<polyline fill="none" points="${group.points.map(p => `${x(p.time).toFixed(1)},${y(p.value).toFixed(1)}`).join(' ')}"/>` : ''}${group.points.map(p => `<circle cx="${x(p.time).toFixed(1)}" cy="${y(p.value).toFixed(1)}" r="3"/>`).join('')}</g>` : '').join('');
    const visible = groups.filter(group => group.points.length);
    const tableRows = visible.flatMap(group => group.points.map(point => `<tr><td>${esc(group.label)}</td><td>${esc(date(point.time))}</td><td>${esc(money(point.value))}</td><td>${Number.isSafeInteger(point.sample) ? esc(point.sample) : '—'}</td></tr>`)).join('');
    return `<figure class="market-index-figure"><figcaption>Цена за м² за последние 30 дней</figcaption>
      <svg class="market-index-chart" viewBox="0 0 590 200" role="img" aria-label="Динамика цены за квадратный метр. Значения приведены в таблице ниже.">
        <line class="market-index-axis" x1="48" y1="165" x2="568" y2="165"/><line class="market-index-axis" x1="48" y1="25" x2="48" y2="165"/>
        <text x="45" y="19" text-anchor="end">${esc(number.format(high))}</text><text x="45" y="171" text-anchor="end">${esc(number.format(low))}</text>
        <text x="48" y="190">${esc(date(start))}</text><text x="568" y="190" text-anchor="end">${esc(date(last))}</text>${paths}</svg>
      <div class="market-index-legend">${visible.map(group => `<span class="market-index-legend-${group.key}">${esc(group.label)}</span>`).join('')}</div>
      <details class="market-index-table"><summary>Значения графика</summary><div class="market-index-scroll"><table><thead><tr><th>Ряд</th><th>Дата</th><th>Цена за м²</th><th>Выборка</th></tr></thead><tbody>${tableRows}</tbody></table></div></details></figure>`;
  }
  function cohort(cohorts, days) {
    const item = cohorts?.[String(days)] || {};
    const n = Number.isSafeInteger(item.sample_size) ? item.sample_size : 0;
    if (!n) return `<p>${days} дней: недостаточно наблюдений.</p>`;
    return `<div class="market-index-cohort"><strong>${days} дней · ${esc(n)} аналогов</strong>
      <p>Сняты или недоступны: ${esc(fraction(item.removed_fraction))} (${esc(item.removed_count ?? 0)}). Отмечены проданными: ${esc(fraction(item.sold_fraction))} (${esc(item.sold_count ?? 0)}).</p>
      <p>В архиве: ${esc(fraction(item.archived_fraction))} (${esc(item.archived_count ?? 0)}); недоступны: ${esc(fraction(item.unavailable_fraction))} (${esc(item.unavailable_count ?? 0)}).</p>
      <p>Средний срок наблюдения снятых, недоступных и отмеченных проданными: ${finite(item.mean_ended_observed_days) ? `${esc(number.format(item.mean_ended_observed_days))} дн.` : 'нет данных'}${Number.isSafeInteger(item.ended_duration_sample_size) ? ` · выборка ${esc(item.ended_duration_sample_size)}` : ''}.</p></div>`;
  }
  function renderContent(data) {
    const comparison = data.district_comparison || {}, liquidity = data.liquidity || {}, signal = liquidity.signal || null;
    const cityLabel = data.city?.label || 'Город', districtLabel = data.district?.label || 'Район';
    const comparisonText = finite(comparison.difference_percent) ? `${comparison.difference_percent > 0 ? '+' : ''}${pct(comparison.difference_percent)} к медиане района` : 'Недостаточно данных для сравнения с районом';
    return `<div class="market-index-panel"><p class="market-index-note">По сохранённым наблюдениям приложения, не по всему рынку. Снятие объявления не означает продажу.</p>
      <div class="market-index-summary"><div><span>Цена квартиры за м²</span><strong>${esc(money(comparison.listing_price_per_m2))}</strong></div><div><span>${esc(districtLabel)} · медиана</span><strong>${esc(money(comparison.median_price_per_m2))}</strong></div><div><span>Отклонение</span><strong>${esc(comparisonText)}</strong></div></div>
      <p class="market-index-note">${esc(cityLabel)} и ${esc(districtLabel)}: показываются дни с достаточной выборкой. Для районного сравнения учтено дней: ${esc(comparison.sample_days ?? 0)}${comparison.latest_day ? `, последнее наблюдение ${esc(date(comparison.latest_day))}` : ''}.</p>
      ${chart(data)}
      <div class="market-index-liquidity"><h4>Ликвидность</h4>${signal?.label ? `<p class="market-index-signal"><strong>${esc(signal.label)}</strong>${signal.reason ? ` · ${esc(signal.reason)}` : ''}</p>` : '<p>Недостаточно данных для оценки ликвидности.</p>'}
      <p>Квартира наблюдается ${esc(liquidity.observed_days ?? 0)} дн. · изменений цены: ${esc(liquidity.price_change_count ?? 0)} · снижений: ${esc(liquidity.price_reduction_count ?? 0)}.</p>
      <p>Сумма снижений: ${esc(money(liquidity.total_reduction_kzt))} · ${esc(pct(liquidity.total_reduction_percent))}.</p>
      ${cohort(liquidity.cohorts,30)}${cohort(liquidity.cohorts,90)}</div></div>`;
  }
  function render(item) {
    const id = Number(item?.id);
    if (!Number.isSafeInteger(id) || id < 1) return '';
    return `<details class="listing-market-index" data-market-index-id="${id}" data-details-key="market-index"><summary>Индекс цен и ликвидность</summary><div data-market-index-body><p class="market-index-note">Откройте для загрузки.</p></div></details>`;
  }
  async function load(details) {
    const body = details?.querySelector('[data-market-index-body]'), id = Number(details?.dataset.marketIndexId);
    if (!body || body.dataset.loading === 'true' || !Number.isSafeInteger(id) || id < 1) return;
    body.dataset.loading = 'true';
    body.innerHTML = '<p class="market-index-note" role="status">Загружаю наблюдения…</p>';
    try {
      const response = await fetch(`/api/v1/listings/${id}/market-history`, {signal:AbortSignal.timeout(12000)});
      if (!response.ok) throw Error('Ошибка загрузки');
      const data = await response.json();
      if (!details.isConnected) return;
      body.innerHTML = renderContent(data);
      body.dataset.loaded = 'true';
    } catch (_) {
      if (details.isConnected) body.innerHTML = '<p class="market-index-note" role="alert">Не удалось загрузить индекс.</p><button type="button" data-market-index-retry>Повторить</button>';
    } finally { body.dataset.loading = 'false'; }
  }
  function setOptions(select, options, placeholder) {
    const selected = select.value;
    const entries = options.map(({key,label}) => ({key:String(key), label}));
    if (selected && !entries.some(entry => entry.key === selected)) {
      entries.push({key:selected, label:select.selectedOptions[0]?.textContent || selected});
    }
    select.innerHTML = `<option value="">${esc(placeholder)}</option>${entries.map(({key,label}) => `<option value="${esc(key)}">${esc(label)}</option>`).join('')}`;
    select.value = selected;
  }
  function renderMarketSeries(data) {
    const series = data.series || [];
    const latest = series.at(-1);
    if (!latest) return '<p>Для выбранных параметров пока нет наблюдений.</p>';
    const enough = series.filter(row => finite(row.median)).length;
    const table = [...series].reverse().map(row => `<tr><td>${esc(date(row.day))}</td><td>${esc(row.sample_size)}</td><td>${esc(money(row.median))}</td><td>${esc(money(row.p25))}</td><td>${esc(money(row.p75))}</td></tr>`).join('');
    return `<div class="market-index-summary">
      <div><span>Дата последнего наблюдения</span><strong>${esc(date(latest.day))}</strong></div>
      <div><span>Квартир в выборке на эту дату</span><strong>${esc(latest.sample_size)}</strong></div>
      <div><span>Медиана за м² на эту дату</span><strong>${esc(money(latest.median))}</strong></div></div>
      <p>Наблюдений по дням: ${esc(series.length)} · с расчётом медианы: ${esc(enough)}. Для расчёта нужно от ${esc(data.min_sample)} квартир на дату.</p>
      <details class="market-index-table"><summary>Значения по датам</summary><div class="market-index-scroll"><table>
      <thead><tr><th>Дата</th><th>Квартир</th><th>Медиана за м²</th><th>25-й процентиль</th><th>75-й процентиль</th></tr></thead><tbody>${table}</tbody></table></div></details>`;
  }
  function mountMarketPanel() {
    const anchor = document.getElementById('savedListings');
    if (!anchor) return;
    const panel = document.createElement('details');
    panel.className = 'form-card card market-index-global';
    panel.innerHTML = `<summary>Индекс цен Астаны</summary><div class="market-index-panel">
      <p class="market-index-note">По ценам объявлений, сохранённых в приложении. Каждый день учитываются доступные квартиры, дубли объединяются.</p>
      <div class="market-index-filters">
        <label>Территория<select name="scope"><option value="city">Вся Астана</option><option value="district">Район</option><option value="complex">Жилой комплекс</option></select></label>
        <label data-market-location hidden><span></span><select name="location"></select></label>
        <label>Тип дома<select name="building_type"><option value="">Все типы</option></select></label>
        <label>Комнат<select name="rooms"><option value="">Любое количество</option></select></label>
        <label>Площадь<select name="area_band"><option value="">Любая площадь</option></select></label>
        <label>Период<select name="days"><option value="30">30 дней</option><option value="90" selected>90 дней</option><option value="180">180 дней</option><option value="365">365 дней</option></select></label>
      </div>
      <p class="market-index-note" data-market-status role="status"></p>
      <button type="button" data-market-retry hidden>Повторить</button>
      <div data-market-series></div></div>`;
    anchor.before(panel);
    const scope = panel.querySelector('[name="scope"]');
    const locationLabel = panel.querySelector('[data-market-location]');
    const location = panel.querySelector('[name="location"]');
    const buildingType = panel.querySelector('[name="building_type"]');
    const rooms = panel.querySelector('[name="rooms"]');
    const area = panel.querySelector('[name="area_band"]');
    const days = panel.querySelector('[name="days"]');
    const status = panel.querySelector('[data-market-status]');
    const series = panel.querySelector('[data-market-series]');
    const retry = panel.querySelector('[data-market-retry]');
    let pending;
    async function refresh() {
      pending?.abort();
      const request = new AbortController();
      pending = request;
      const timer = setTimeout(() => request.abort(), 12000);
      const place = location.value;
      const params = new URLSearchParams({scope:scope.value, days:days.value});
      for (const select of [buildingType, rooms, area]) {
        if (select.value) params.set(select.name, select.value);
      }
      if (scope.value !== 'city' && place) params.set('location', place);
      series.innerHTML = '';
      retry.hidden = true;
      status.textContent = 'Загружаю наблюдения…';
      try {
        const response = await fetch(`/api/v1/market-index?${params}`, {signal:request.signal});
        if (!response.ok) throw Error('Ошибка загрузки');
        const data = await response.json();
        if (pending !== request) return;
        const isDistrict = scope.value === 'district';
        setOptions(location, data.locations, isDistrict ? 'Выберите район' : 'Выберите ЖК');
        setOptions(buildingType, data.building_types, 'Все типы');
        setOptions(rooms, data.room_options.map(value => ({key:value,label:value})), 'Любое количество');
        setOptions(area, data.area_bands, 'Любая площадь');
        if (scope.value !== 'city' && !place) {
          status.textContent = data.locations.length
            ? (isDistrict ? 'Выберите район.' : 'Выберите жилой комплекс.')
            : 'За этот период пока нет наблюдений для выбранной территории.';
          return;
        }
        status.textContent = data.label || '';
        series.innerHTML = renderMarketSeries(data);
      } catch (_) {
        if (pending !== request) return;
        status.textContent = 'Не удалось загрузить индекс цен.';
        retry.hidden = false;
      } finally { clearTimeout(timer); }
    }
    panel.addEventListener('toggle', () => {
      if (panel.open) {
        void refresh();
      } else {
        pending?.abort();
        pending = null;
      }
    });
    panel.addEventListener('change', event => {
      if (event.target === scope) {
        location.replaceChildren();
        locationLabel.hidden = scope.value === 'city';
        locationLabel.firstElementChild.textContent = scope.value === 'district' ? 'Район' : 'Жилой комплекс';
      }
      void refresh();
    });
    retry.addEventListener('click', () => { void refresh(); });
  }
  let bound = false;
  function bind() {
    if (bound) return;
    bound = true;
    mountMarketPanel();
    document.addEventListener('toggle', event => {
      const details = event.target;
      if (details.matches?.('details[data-market-index-id]') && details.open && !details.querySelector('[data-market-index-body]')?.dataset.loaded) void load(details);
    }, true);
    document.addEventListener('click', event => {
      const retry = event.target.closest?.('[data-market-index-retry]');
      if (retry) void load(retry.closest('details[data-market-index-id]'));
    });
  }
  window.ApartmentAgentMarketIndex = {render, bind};
})();
