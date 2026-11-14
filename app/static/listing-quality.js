(() => {
  'use strict';
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const money = new Intl.NumberFormat('ru-RU');
  const labels = {city:'Город', address:'Адрес', price_kzt:'Цена', area_m2:'Площадь', rooms:'Комнат', floor:'Этаж', floors_total:'Этажей', building_year:'Год постройки', bathrooms_count:'Санузлы', repair_condition:'Ремонт', mortgage_supported:'Ипотека', seller_type:'Продавец', is_pledged:'Залог', market_type:'Тип рынка', description:'Описание'};
  const correctionFields = ['bathrooms_count','repair_condition','mortgage_supported','is_pledged','seller_type'];
  let savedCallback = null, bound = false;
  const valueText = value => value == null || value === '' ? 'неизвестно' : typeof value === 'boolean' ? value ? 'да' : 'нет' : String(value);
  const dt = value => { const result = new Date(value); return value && !Number.isNaN(result.getTime()) ? result.toLocaleString('ru-RU') : 'дата неизвестна'; };
  const safeUrl = raw => { try { const u = new URL(raw); return ['https:','http:'].includes(u.protocol) && !u.username && !u.password ? u.href : null; } catch { return null; } };
  const precision = value => ({listing_coordinates:'координаты объявления', approximate:'приблизительное положение', unknown:'нет координат'}[value] || 'нет данных');
  function render(item) {
    const id = Number(item?.id);
    if (!Number.isSafeInteger(id) || id < 1) return '';
    return `<details class="listing-quality" data-listing-quality-id="${id}" data-details-key="quality"><summary>Качество данных и исправления</summary><div data-quality-content><p>Откройте, чтобы проверить данные.</p></div></details>`;
  }
  function evidenceBlock(evidence = {}) {
    const bath = evidence.bathrooms || {}, repair = evidence.repair || {}, mortgage = evidence.mortgage || {}, seller = evidence.seller || {};
    const row = (label, value, extra) => `<li><strong>${esc(label)}:</strong> ${esc(valueText(value))}${extra ? ` · ${esc(extra)}` : ''}</li>`;
    return `<ul>${row('Санузлы', bath.value, [bath.lower_bound ? 'не менее указанного' : '', bath.field || ''].filter(Boolean).join(' · '))}${row('Ремонт', repair.value, [repair.source_kind,repair.evidence].filter(Boolean).join(' · '))}${row('Ипотека', mortgage.value, [mortgage.source_kind,mortgage.evidence].filter(Boolean).join(' · '))}${row('Продавец', seller.value, '')}</ul>`;
  }
  function qualityBlock(quality) {
    if (!quality) return '<p class="quality-warning">Данные о заполненности не загрузились.</p>';
    const missing = Object.entries(quality.fields || {}).filter(([,known]) => !known).map(([key]) => quality.field_labels?.[key] || labels[key] || key);
    const flags = Array.isArray(quality.review_flags) ? quality.review_flags : [];
    const prompts = {
      compact_for_room_count:'Проверьте планировку. Для четырёх и более комнат указано меньше 70 м².',
      older_new_building:'Сверьте тип рынка с годом постройки. Дом отмечен как новостройка, но ему больше 10 лет.',
      multiple_bathrooms_no_photos:'Указано от двух санузлов. В карточке пока нет фотографий для сверки.',
    };
    const review = flags.filter(flag => prompts[flag]).map(flag => `<li>${prompts[flag]}</li>`).join('');
    return `<div class="quality-overview"><p><strong>Заполнено: ${esc(quality.completeness_percent)}%</strong> · фото: ${esc(quality.photo_count)} · ${esc(precision(quality.location_precision))}.</p>
      <p>${missing.length ? `Нет данных: ${esc(missing.join(', '))}.` : 'Основные поля заполнены.'}</p>
      ${flags.includes('bathrooms_lower_bound') ? '<p>Количество санузлов указано как нижняя граница.</p>' : ''}
      ${review ? `<p>Стоит сверить</p><ul>${review}</ul>` : ''}</div>
      <details class="quality-subsection"><summary>Источники отдельных сведений</summary>${evidenceBlock(quality.evidence)}</details>`;
  }
  function mergeBlock(data) {
    if (!data) return '<p class="quality-warning">Объединённый просмотр не загрузился.</p>';
    const offers = Array.isArray(data.offers) ? data.offers : [], fields = data.fields || {}, conflicts = Array.isArray(data.conflicts) ? data.conflicts : [];
    const rows = Object.entries(fields).map(([key,entry]) => {
      const source = safeUrl(entry.source_url);
      const alternatives = Array.isArray(entry.alternatives) && entry.alternatives.length > 1 ? `<ul>${entry.alternatives.map(alt => `<li>Карточка №${esc(alt.listing_id)}: ${esc(valueText(alt.value))}</li>`).join('')}</ul>` : '';
      return `<tr><th scope="row">${esc(labels[key] || key)}</th><td>${esc(valueText(entry.value))}${conflicts.includes(key) ? ' ⚠' : ''}${alternatives}</td><td>${source ? `<a href="${esc(source)}" target="_blank" rel="noopener noreferrer">Карточка №${esc(entry.listing_id)} ↗</a>` : `Карточка №${esc(entry.listing_id)}`}</td></tr>`;
    }).join('');
    const offerHtml = offers.map(offer => {
      const source = safeUrl(offer.source_url), photos = (Array.isArray(offer.photo_urls) ? offer.photo_urls : []).map(safeUrl).filter(Boolean), history = Array.isArray(offer.price_history) ? offer.price_history : [];
      return `<li><strong>Карточка №${esc(offer.listing_id)}</strong>${source ? ` · <a href="${esc(source)}" target="_blank" rel="noopener noreferrer">Объявление ↗</a>` : ''} · ${Number.isFinite(offer.price_kzt) ? `${esc(money.format(offer.price_kzt))} ₸` : 'цена неизвестна'}
        ${photos.length ? `<div class="quality-photos">${photos.slice(0,12).map((url,index) => `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer"><img loading="lazy" src="${esc(url)}" alt="Фото ${index+1} объявления №${esc(offer.listing_id)}"></a>`).join('')}</div>` : '<p>Фотографии отсутствуют.</p>'}
        <details><summary>История цены (${history.length})</summary>${history.length ? `<ul>${history.map(entry => `<li>${esc(dt(entry.observed_at))}: ${Number.isFinite(entry.price_kzt) ? `${esc(money.format(entry.price_kzt))} ₸` : 'неизвестно'}</li>`).join('')}</ul>` : '<p>Истории пока нет.</p>'}</details></li>`;
    }).join('');
    if (offers.length < 2) return '<p>Подтверждённых дублей пока нет.</p>';
    return `<details class="quality-subsection"><summary>Подтверждённые дубли и источники (${offers.length})</summary>
      <p>Сведения показаны вместе; исходные карточки сохранены.${conflicts.length ? ' ⚠ Есть различия: проверьте источники.' : ''}</p>
      <div class="quality-table-scroll"><table><thead><tr><th>Поле</th><th>Значение</th><th>Источник</th></tr></thead><tbody>${rows}</tbody></table></div>
      <ul class="quality-offers">${offerHtml}</ul></details>`;
  }
  function correctionsBlock(data) {
    const rows = Array.isArray(data?.items) ? data.items : [];
    return `<details class="quality-subsection"><summary>История исправлений (${rows.length})</summary>${rows.length ? `<ol>${rows.map(row => `<li>${esc(dt(row.observed_at))} · ${esc(labels[row.field] || row.field)}: ${esc(valueText(row.old_value))} → ${esc(valueText(row.new_value))}<br><span>${esc(row.reason)}</span></li>`).join('')}</ol>` : '<p>Ручных исправлений пока нет.</p>'}</details>`;
  }
  function formBlock() {
    return `<details class="quality-subsection"><summary>Исправить данные</summary><form data-quality-form>
      <label>Поле <select name="field">${correctionFields.map(key => `<option value="${key}">${esc(labels[key])}</option>`).join('')}</select></label>
      <label data-quality-value-label>Значение <input name="value" type="number" min="1" max="20" step="1" inputmode="numeric"></label>
      <label>Причина исправления <input name="reason" required minlength="3" maxlength="500" placeholder="Например, проверено по фото"></label>
      <button type="submit">Сохранить исправление</button><p data-quality-message role="status"></p></form></details>`;
  }
  const fieldInput = field => {
    if (field === 'bathrooms_count') return '<input name="value" type="number" min="1" max="20" step="1" inputmode="numeric" placeholder="Пусто — неизвестно">';
    if (field === 'mortgage_supported' || field === 'is_pledged') return '<select name="value"><option value="unknown">Неизвестно</option><option value="true">Да</option><option value="false">Нет</option></select>';
    if (field === 'seller_type') return '<select name="value"><option value="">Неизвестно</option><option>Хозяин недвижимости</option><option>Риелтор</option><option>Крыша Агент</option></select>';
    return '<input name="value" type="text" maxlength="500" placeholder="Пусто — неизвестно">';
  };
  async function request(url) { const response = await fetch(url, {signal:AbortSignal.timeout(12000)}); if (!response.ok) throw Error('Ошибка загрузки'); return response.json(); }
  async function load(details, notice = '') {
    const content = details?.querySelector('[data-quality-content]'), id = Number(details?.dataset.listingQualityId);
    if (!content || content.dataset.loading === 'true' || !Number.isSafeInteger(id) || id < 1) return;
    content.dataset.loading = 'true';
    content.innerHTML = '<p role="status">Загружаю данные карточки…</p>';
    try {
      const base = `/api/v1/listings/${id}`;
      const parts = await Promise.allSettled([request(`${base}/quality`),request(`${base}/merged-view`),request(`${base}/corrections`)]);
      if (!details.isConnected) return;
      const value = i => parts[i].status === 'fulfilled' ? parts[i].value : null;
      if (parts.every(part => part.status === 'rejected')) throw Error('Нет ответа');
      content.innerHTML = `${notice ? `<p role="status">${esc(notice)}</p>` : ''}${qualityBlock(value(0))}${mergeBlock(value(1))}${value(2) ? correctionsBlock(value(2)) : '<p class="quality-warning">История исправлений не загрузилась.</p>'}${formBlock()}`;
      content.dataset.loaded = 'true';
    } catch (_) { if (details.isConnected) content.innerHTML = '<p class="quality-warning" role="alert">Не удалось загрузить данные.</p><button type="button" data-quality-retry>Повторить</button>'; }
    finally { content.dataset.loading = 'false'; }
  }
  function bind(options = {}) {
    if (typeof options.onSaved === 'function') savedCallback = options.onSaved;
    if (bound) return;
    bound = true;
    document.addEventListener('toggle', event => { const details = event.target; if (details.matches?.('details[data-listing-quality-id]') && details.open && !details.querySelector('[data-quality-content]')?.dataset.loaded) void load(details); }, true);
    document.addEventListener('click', event => { const retry = event.target.closest?.('[data-quality-retry]'); if (retry) void load(retry.closest('details[data-listing-quality-id]')); });
    document.addEventListener('change', event => { const select = event.target; if (select.matches?.('[data-quality-form] select[name="field"]')) { const label = select.form.querySelector('[data-quality-value-label]'); label.innerHTML = `Значение ${fieldInput(select.value)}`; } });
    document.addEventListener('submit', async event => {
      const form = event.target;
      if (!form.matches?.('[data-quality-form]')) return;
      event.preventDefault();
      const details = form.closest('details[data-listing-quality-id]'), id = Number(details.dataset.listingQualityId);
      if (!Number.isSafeInteger(id) || !form.reportValidity()) return;
      const field = form.elements.field.value, raw = form.elements.value.value.trim(), reason = form.elements.reason.value.trim();
      let value = raw || null;
      if (field === 'bathrooms_count' && raw) value = Number(raw);
      if (field === 'mortgage_supported' || field === 'is_pledged') value = raw === 'unknown' ? null : raw === 'true';
      const button = form.querySelector('button'), message = form.querySelector('[data-quality-message]');
      button.disabled = true; message.textContent = 'Сохраняю…';
      try {
        const response = await fetch(`/api/v1/listings/${id}/corrections`, {method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({field,value,reason}),signal:AbortSignal.timeout(12000)});
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw Error(typeof data.detail === 'string' ? data.detail : 'Не удалось сохранить исправление.');
        await load(details, data.changed ? 'Исправление сохранено.' : 'Значение не изменилось.');
        if (typeof savedCallback === 'function') await savedCallback({listingId:id, changed:!!data.changed});
      } catch (error) { if (message.isConnected) message.textContent = error.message || 'Не удалось сохранить исправление.'; }
      finally { button.disabled = false; }
    });
  }
  window.ApartmentAgentQuality = {render, bind};
})();
