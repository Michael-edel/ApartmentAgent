(() => {
  'use strict';
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const money = new Intl.NumberFormat('ru-KZ');
  const drafts = new Map();
  const date = value => { const d = new Date(value); return value && !Number.isNaN(d.getTime()) ? d.toLocaleString('ru-RU') : 'дата неизвестна'; };
  const labels = {price_kzt:'Цена, ₸', area_m2:'Площадь, м²', rooms:'Комнат', floor:'Этаж', floors_total:'Этажей', description:'Описание', title:'Заголовок', address:'Адрес', seller_name:'Автор', seller_type:'Тип продавца', mortgage_supported:'Ипотека', is_pledged:'Залог', repair_condition:'Ремонт', bathrooms_count:'Санузлов', building_year:'Год постройки'};
  const valueText = value => value == null ? 'нет данных' : typeof value === 'boolean' ? (value ? 'да' : 'нет') : typeof value === 'object' ? JSON.stringify(value) : String(value);
  const safeUrl = raw => { try { const u=new URL(raw); return ['https:','http:'].includes(u.protocol) ? u.href : null; } catch { return null; } };
  function render(item) {
    return `<details class="listing-comparables" data-tracking-id="${Number(item.id)}" data-details-key="tracking"><summary>Контроль цены и изменения</summary><div data-tracking-content><p class="meta">Откройте для загрузки истории.</p></div></details>`;
  }
  async function load(details, savedMessage = '') {
    const content = details.querySelector('[data-tracking-content]');
    if (!content || content.dataset.loading === 'true') return;
    content.dataset.loading = 'true';
    try {
      const response = await fetch(`/api/v1/listings/${Number(details.dataset.trackingId)}/tracking`);
      if (!response.ok) throw Error('Не удалось загрузить историю.');
      const data = await response.json();
      if (!details.isConnected) return;
      const changes = Array.isArray(data.changes) ? data.changes : [];
      const matches = Array.isArray(data.matching_offers) ? data.matching_offers : [];
      const list = changes.map(entry => `<li><b>${esc(date(entry.observed_at))}</b><ul>${Object.entries(entry.changes || {}).map(([key, change]) => `<li>${esc(labels[key] || key)}: ${esc(valueText(change.old))} → ${esc(valueText(change.new))}</li>`).join('')}</ul></li>`).join('');
      const offers = matches.map(item => { const url=safeUrl(item.source_url); return `<li>${item.relationship === 'self' ? 'Это объявление' : item.relationship === 'confirmed' ? 'Подтверждённый дубль' : 'Возможный дубль — проверьте'}: ${Number.isFinite(item.price_kzt) ? `${esc(money.format(item.price_kzt))} ₸` : 'цена неизвестна'}${Number.isSafeInteger(item.listing_id) ? ` · <a href="#listing-${item.listing_id}">Карточка</a>` : ''}${url ? ` · <a href="${esc(url)}" target="_blank" rel="noopener noreferrer">Объявление ↗</a>` : ''}</li>`; }).join('');
      const target = data.target || {};
      const relatedUrl = safeUrl(data.related_offer?.source_url);
      content.innerHTML = `<p class="meta">Приложение впервые заметило объявление ${esc(date(data.first_seen_at))}${Number.isFinite(data.listing_age_days) ? ` · ${esc(data.listing_age_days)} дн.` : ''}.</p>
        ${data.related_first_seen_at ? `<p class="meta">Похожее объявление впервые замечено: ${esc(date(data.related_first_seen_at))} (${data.related_confidence === 'confirmed' ? 'совпадение подтверждено' : 'возможное совпадение'}).${relatedUrl ? ` <a href="${esc(relatedUrl)}" target="_blank" rel="noopener noreferrer">Открыть объявление ↗</a>` : ''}</p>` : ''}
        <p class="meta">Цель цены работает для понравившихся квартир. Уведомления требуют настроенного Telegram.${target.paused ? ' Контроль приостановлен: верните квартиру в понравившиеся.' : ''}</p>
        <form data-target-form><label>Целевая цена, ₸ <input name="target_price_kzt" type="number" min="1" max="10000000000" step="1" inputmode="numeric" value="${Number.isSafeInteger(target.target_price_kzt) ? target.target_price_kzt : ''}" placeholder="Без цели"></label><label class="checkbox"><input type="checkbox" name="include_similar" ${target.include_similar ? 'checked' : ''}> Учитывать похожие объявления той же квартиры</label><button class="panel-action" type="submit">Сохранить цель</button><p class="meta" data-target-message role="status">${esc(savedMessage)}</p></form>
        ${target.target_price_kzt ? offers ? `<details><summary>Предложения не дороже цели (${matches.length})</summary><p class="meta">Показаны последние сохранённые цены.</p><ul>${offers}</ul></details>` : '<p class="meta">Пока нет сохранённых предложений по целевой цене.</p>' : ''}
        <details><summary>Изменения объявления (${changes.length})</summary>${list ? `<ol>${list}</ol>` : '<p class="meta">Изменений пока не обнаружено.</p>'}</details>`;
      const draft = drafts.get(details.dataset.trackingId);
      if (draft) {
        const fields = content.querySelector('[data-target-form]').elements;
        fields.target_price_kzt.value = draft.price;
        fields.include_similar.checked = draft.includeSimilar;
      }
      content.dataset.loaded = 'true';
    } catch { if (details.isConnected) content.innerHTML = '<p class="meta" role="alert">Не удалось загрузить контроль цены.</p><button class="panel-action" type="button" data-tracking-retry>Повторить</button>'; }
    finally { content.dataset.loading = 'false'; }
  }
  document.addEventListener('toggle', event => { const details=event.target; if (details.matches?.('details[data-tracking-id]') && details.open && !details.querySelector('[data-tracking-content]')?.dataset.loaded) void load(details); }, true);
  document.addEventListener('click', event => { const retry=event.target.closest?.('[data-tracking-retry]'); if (retry) void load(retry.closest('details[data-tracking-id]')); });
  document.addEventListener('input', event => {
    const form = event.target.form;
    if (!form?.matches('[data-target-form]')) return;
    const {trackingId} = form.closest('[data-tracking-id]').dataset;
    drafts.set(trackingId, {price: form.elements.target_price_kzt.value, includeSimilar: form.elements.include_similar.checked});
  });
  document.addEventListener('submit', async event => {
    const form=event.target;
    if (!form.matches?.('[data-target-form]')) return;
    event.preventDefault();
    const details=form.closest('details[data-tracking-id]'), field=form.elements.target_price_kzt, message=form.querySelector('[data-target-message]'), button=form.querySelector('button');
    if (!field.checkValidity()) { field.reportValidity(); return; }
    const {trackingId} = details.dataset;
    const submittedDraft = drafts.get(trackingId);
    const price = field.value === '' ? null : Number(field.value);
    button.disabled=true; message.textContent='Сохраняю…';
    try {
      const response=await fetch(`/api/v1/listings/${Number(trackingId)}/target-price`, {method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({target_price_kzt:price,include_similar:form.elements.include_similar.checked})});
      const data=await response.json().catch(() => ({}));
      if (!response.ok) throw Error(typeof data.detail === 'string' ? data.detail : 'Не удалось сохранить цель.');
      if (drafts.get(trackingId) === submittedDraft) drafts.delete(trackingId);
      const currentDetails = document.querySelector(`[data-tracking-id="${Number(trackingId)}"]`);
      if (currentDetails?.open) await load(currentDetails, price ? 'Целевая цена сохранена.' : 'Целевая цена удалена.');
    } catch (error) { message.textContent=error.message || 'Не удалось сохранить цель. Повторите попытку.'; }
    finally { button.disabled=false; }
  });
  window.ApartmentAgentTracking={render};
})();
