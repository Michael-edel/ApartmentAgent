const money = new Intl.NumberFormat('ru-KZ', {style:'currency', currency:'KZT', maximumFractionDigits:0});
const number = new Intl.NumberFormat('ru-KZ', {maximumFractionDigits:1});
const dateTime = new Intl.DateTimeFormat('ru-RU', {day:'2-digit', month:'2-digit', hour:'2-digit', minute:'2-digit'});

const grid = document.querySelector('#listingGrid');
const filter = document.querySelector('#scoreFilter');
const listingSort = document.querySelector('#listingSort');
const refreshButton = document.querySelector('#refreshButton');
const form = document.querySelector('#listingForm');
const message = document.querySelector('#formMessage');
const importForm = document.querySelector('#importForm');
const importMessage = document.querySelector('#importMessage');
const copyBookmarkletButton = document.querySelector('#copyBookmarkletButton');
const bookmarkletCode = document.querySelector('#bookmarkletCode');
const bookmarkletMessage = document.querySelector('#bookmarkletMessage');
const runChecksButton = document.querySelector('#runChecksButton');
const checkMessage = document.querySelector('#checkMessage');
const runSearchButton = document.querySelector('#runSearchButton');
const searchMessage = document.querySelector('#searchMessage');
const searchMeta = document.querySelector('#searchMeta');
const searchResults = document.querySelector('#searchResults');
const reloadSearchResults = document.querySelector('#refreshSearchButton');
let savedUrls = new Set();

function escapeHtml(value='') {
  return String(value).replace(/[&<>'"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#039;','"':'&quot;'}[ch]));
}

function buildBookmarklet() {
  const scriptUrl = `${location.origin}/static/browser-import.js`;
  return `javascript:(()=>{const s=document.createElement('script');s.src=${JSON.stringify(scriptUrl)};document.body.appendChild(s)})()`;
}

if (bookmarkletCode) bookmarkletCode.value = buildBookmarklet();
copyBookmarkletButton?.addEventListener('click', async () => {
  const code = buildBookmarklet();
  try {
    await navigator.clipboard.writeText(code);
    if (bookmarkletMessage) bookmarkletMessage.textContent = 'Скопировано. Создайте закладку и вставьте код в поле URL.';
  } catch (_) {
    bookmarkletCode?.select();
    document.execCommand('copy');
    if (bookmarkletMessage) bookmarkletMessage.textContent = 'Выделите код вручную и сохраните его как закладку браузера.';
  }
});

async function readJson(response) {
  const text = await response.text();
  if (!text.trim()) throw new Error(`Сервер не вернул ответ (HTTP ${response.status}). Повторите попытку.`);
  try { return JSON.parse(text); }
  catch { throw new Error(response.ok ? 'Сервер вернул некорректный ответ' : `Ошибка сервера HTTP ${response.status}`); }
}

function normalizeUrl(value='') {
  try { const u = new URL(value, location.origin); return `${u.origin}${u.pathname.replace(/\/$/, '')}`; }
  catch { return String(value).replace(/\/$/, ''); }
}

function cleanSearchTitle(title='') {
  let value = String(title).replace(/\s+/g, ' ').trim();
  value = value.replace(/\s*[·|,-]\s*\d{2,3}(?:[.,]\d+)?\s*м(?:²|2).*$/i, '');
  value = value.replace(/\s*\d{1,2}\s*\/\s*\d{1,2}\s*этаж.*$/i, '');
  return value || '2-комнатная квартира';
}

function priorityLabel(priority) {
  if (priority === 'urgent') return 'Срочно посмотреть';
  if (priority === 'good') return 'Хороший вариант';
  return 'Новая ссылка';
}

function priorityClass(priority) {
  if (priority === 'urgent') return 'priority-urgent';
  if (priority === 'good') return 'priority-good';
  return 'priority-normal';
}

function sortListings(listings) {
  const items = [...listings];
  const sort = listingSort?.value || 'newest';
  const compare = (left, right) => {
    if (sort === 'score') return right.assessment.score - left.assessment.score || right.id - left.id;
    if (sort === 'price_m2') return left.assessment.price_per_m2 - right.assessment.price_per_m2 || left.id - right.id;
    if (sort === 'price') return left.price_kzt - right.price_kzt || left.id - right.id;
    return String(right.created_at).localeCompare(String(left.created_at)) || right.id - left.id;
  };
  return items.sort(compare);
}

function render(listings) {
  savedUrls = new Set(listings.map(item => normalizeUrl(item.source_url)));
  const minScore = Number(filter?.value || 0);
  const visible = sortListings(listings).filter(item => item.assessment.score >= minScore);
  const strong = listings.filter(item => item.assessment.score >= 85);
  document.querySelector('#totalCount').textContent = listings.length;
  document.querySelector('#strongCount').textContent = strong.length;
  if (!grid) return;
  if (!visible.length) {
    grid.innerHTML = '<section class="empty card"><strong>Сохранённых квартир пока нет</strong><p>Нажмите «Открыть и мониторить» у найденного объявления — браузер автоматически передаст данные в приложение.</p></section>';
    return;
  }
  grid.innerHTML = visible.map(item => {
    const title = item.residential_complex || item.title;
    const location = [item.address, item.district, item.city].filter(Boolean).join(' · ');
    const hasCoordinates = item.latitude != null && item.longitude != null;
    const locationMissing = !item.address && !hasCoordinates;
    const mapUrl = item.latitude != null && item.longitude != null
      ? `https://yandex.ru/maps/?ll=${encodeURIComponent(`${item.longitude},${item.latitude}`)}&z=16&pt=${encodeURIComponent(`${item.longitude},${item.latitude},pm2rdm`)}`
      : null;
    const scoreClass = item.assessment.score < 85 ? 'medium' : '';
    const photos = (item.photo_urls || []).slice(0, 4);
    const ai = item.ai_analysis || {};
    const latestChange = (item.price_history || []).find(snapshot => snapshot.change_kzt);
    const twogisRating = Number(item.twogis_rating);
    const twogisReviews = Number(item.twogis_review_count || 0);
    const twogisLine = item.twogis_name ? `<div class="twogis-review"><b>2GIS</b> ${Number.isFinite(twogisRating) && twogisReviews ? `★ ${twogisRating.toFixed(1)}/5 · ${twogisReviews} отзывов` : 'оценка и отзывы не опубликованы'}${item.twogis_url ? ` · <a href="${escapeHtml(item.twogis_url)}" target="_blank" rel="noopener noreferrer">Открыть в 2GIS →</a>` : ''}</div>` : '';
    return `<article class="listing card">
      ${photos.length ? `<div class="listing-photos">${photos.map((photo, index) => `<img class="listing-photo ${index ? 'thumbnail' : ''}" src="${escapeHtml(photo)}" alt="Фото квартиры ${index + 1}" loading="lazy" referrerpolicy="no-referrer">`).join('')}</div>` : ''}
      <div class="listing-top"><div><h3>${escapeHtml(title)}</h3><div class="meta">${escapeHtml(location)}</div></div><div class="rating ${scoreClass}">${item.assessment.score}</div></div>
      <div class="price">${money.format(item.price_kzt)}</div>
      <div class="meta">${number.format(item.area_m2)} м² · ${money.format(item.assessment.price_per_m2)}/м² · этаж ${item.floor || '—'}/${item.floors_total || '—'}</div>
      ${mapUrl ? `<a class="location-link" href="${escapeHtml(mapUrl)}" target="_blank" rel="noopener noreferrer">Открыть местоположение на карте →</a>` : ''}
      ${locationMissing ? '<div class="meta location-missing">Точное местоположение не опубликовано в открытых данных Krisha</div>' : ''}
      ${item.building_year || item.building_type ? `<div class="meta">${item.building_year ? `Построен ${item.building_year}` : ''}${item.building_year && item.building_type ? ' · ' : ''}${escapeHtml(item.building_type || '')}</div>` : ''}
      ${twogisLine}
      ${latestChange ? `<div class="price-change ${latestChange.change_kzt < 0 ? 'down' : 'up'}">${latestChange.change_kzt < 0 ? '↓' : '↑'} ${money.format(Math.abs(latestChange.change_kzt))} с последнего наблюдения</div>` : ''}
      <span class="verdict">${escapeHtml(item.assessment.verdict)}</span>
      ${ai.summary ? `<div class="ai-summary"><b>AI-анализ</b><p>${escapeHtml(ai.summary)}</p></div>` : ''}
      <div class="listing-actions"><button class="check-listing secondary-action" data-listing-id="${item.id}" type="button">↻ Проверить квартиру</button></div>
      <a href="${escapeHtml(item.source_url)}">Открыть объявление →</a>
    </article>`;
  }).join('');
}

async function checkListingNow(id, button) {
  button.disabled = true;
  button.textContent = 'Проверяю…';
  try {
    const response = await fetch(`/api/v1/listings/${id}/check`, {method:'POST'});
    const body = await readJson(response);
    if (!response.ok) throw new Error(body.detail || 'Не удалось проверить квартиру');
    if (checkMessage) checkMessage.textContent = `Квартира #${id}: ${body.message}`;
    await loadListings();
  } catch (error) {
    button.textContent = error.message;
    button.classList.add('button-error');
    setTimeout(() => {
      if (!button.isConnected) return;
      button.textContent = '↻ Проверить квартиру';
      button.disabled = false;
      button.classList.remove('button-error');
    }, 3500);
  }
}

async function loadListings() {
  if (refreshButton) refreshButton.disabled = true;
  try {
    const response = await fetch('/api/v1/listings');
    if (!response.ok) throw new Error('Не удалось загрузить квартиры');
    render(await readJson(response));
    grid?.querySelectorAll('.check-listing').forEach(button => {
      button.addEventListener('click', () => checkListingNow(Number(button.dataset.listingId), button));
    });
  } catch (error) {
    if (message) message.textContent = error.message;
  } finally {
    if (refreshButton) refreshButton.disabled = false;
  }
}

async function markSeen(id) { await fetch(`/api/v1/search/results/${id}/seen`, {method:'POST'}); }

function openListingInYandexBrowser(targetUrl) {
  const parsed = new URL(targetUrl, location.href);
  const normalizedUrl = parsed.toString();
  const userAgent = navigator.userAgent || '';
  const isAndroid = /Android/i.test(userAgent);

  if (!isAndroid) {
    const opened = window.open(normalizedUrl, '_blank', 'noopener,noreferrer');
    if (!opened) window.location.assign(normalizedUrl);
    return;
  }

  // Android may route an ordinary Krisha HTTPS link to the Krisha app. An
  // explicit package keeps the listing in Yandex Browser, including when the
  // ApartmentAgent PWA itself is already running there.
  const scheme = parsed.protocol.replace(':', '') || 'https';
  const intentTarget = `${parsed.host}${parsed.pathname}${parsed.search}`;
  const intentUrl = `intent://${intentTarget}#Intent;scheme=${scheme};package=com.yandex.browser;S.browser_fallback_url=${encodeURIComponent(normalizedUrl)};end`;
  window.location.href = intentUrl;

  // If Yandex Browser is not installed or Android rejects the intent, keep a
  // reliable HTTPS fallback instead of leaving the user on an error page.
  setTimeout(() => {
    if (!document.hidden) window.location.assign(normalizedUrl);
  }, 1500);
}

async function importSearchResult(url, button) {
  button.disabled = true;
  button.textContent = 'Загружаю данные…';
  try {
    const response = await fetch('/api/v1/listings/import', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({source_url:url})
    });
    const body = await readJson(response);
    if (!response.ok && response.status !== 409) throw new Error(body.detail || 'Не удалось загрузить данные');
    savedUrls.add(normalizeUrl(url));
    button.textContent = response.status === 409 ? 'Уже сохранено' : `Сохранено · ${body.assessment?.score || '—'}/100`;
    button.classList.add('saved');
    await loadListings();
  } catch (error) {
    button.textContent = error.message;
    button.classList.add('button-error');
    setTimeout(() => { button.textContent = 'Повторить сохранение'; button.disabled = false; button.classList.remove('button-error'); }, 3500);
  }
}

async function loadSearchResults() {
  if (!searchResults) return;
  try {
    const response = await fetch('/api/v1/search/results?limit=200&only_new=false');
    if (!response.ok) throw new Error('Не удалось загрузить найденные ссылки');
    const items = await readJson(response);
    if (!items.length) {
      searchResults.innerHTML = '<section class="empty card"><strong>Новых ссылок пока нет</strong><p>Новые объявления появятся после следующей публикации на Krisha.</p></section>';
      return;
    }
    searchResults.innerHTML = items.map(item => {
      const a = item.analysis || {};
      const facts = [
        a.price_kzt ? money.format(a.price_kzt) : null,
        a.area_m2 ? `${number.format(a.area_m2)} м²` : null,
        a.price_per_m2 ? `${money.format(a.price_per_m2)}/м²` : null,
        a.floor ? `${a.floor}/${a.floors_total || '—'} этаж` : null
      ].filter(Boolean);
      const reasons = (a.reasons || []).slice(0, 3).map(reason => `<li>✓ ${escapeHtml(reason)}</li>`).join('');
      const isSaved = savedUrls.has(normalizeUrl(item.url));
      const importStatus = item.import_status || 'pending';
      const imported = importStatus === 'imported' || isSaved;
      const importLabel = imported ? 'Автоматически сохранено' : importStatus === 'queued' ? 'Мониторинг запущен' : importStatus === 'blocked' ? 'Источник ограничил импорт' : importStatus === 'error' ? 'Импорт не удался — повторить' : 'Импортирую…';
      const seenLabel = item.status === 'seen' ? 'Просмотрено' : priorityLabel(item.priority);
      return `<article class="listing card ${priorityClass(item.priority)} ${item.status === 'seen' ? 'result-seen' : ''}">
        <div class="listing-top">
          <div><span class="verdict">${escapeHtml(seenLabel)}</span><h3>${escapeHtml(cleanSearchTitle(item.title))}</h3></div>
          <div class="rating ${a.score < 85 ? 'medium' : ''}">${a.score ?? '—'}</div>
        </div>
        ${facts.length ? `<div class="search-facts">${facts.map(value => `<span>${escapeHtml(value)}</span>`).join('')}</div>` : ''}
        ${reasons ? `<ul class="reason-list">${reasons}</ul>` : `<p class="meta">${escapeHtml(item.snippet || 'Откройте объявление для проверки параметров.')}</p>`}
        <div class="meta source-meta">Источник: ${escapeHtml(item.search_engine)} · обнаружено ${dateTime.format(new Date(item.first_seen))}</div>
        <div class="card-actions">
          <button class="enrich-result primary secondary-action ${imported ? 'saved' : ''}" data-url="${escapeHtml(item.url)}" type="button" ${imported ? 'disabled' : ''}>${imported ? 'Сохранено автоматически' : importLabel}</button>
          <a class="search-open" data-result-id="${item.id}" href="${escapeHtml(item.url)}">Открыть и мониторить →</a>
        </div>
      </article>`;
    }).join('');
    searchResults.querySelectorAll('.search-open').forEach(link => link.addEventListener('click', event => {
      event.preventDefault();
      void monitorBeforeOpen(link);
    }));
    searchResults.querySelectorAll('.enrich-result:not(.saved)').forEach(button => button.addEventListener('click', () => importSearchResult(button.dataset.url, button)));
  } catch (error) {
    if (searchMessage) searchMessage.textContent = error.message;
  }
}

function monitorBeforeOpen(link) {
  const resultId = link.dataset.resultId;
  const targetUrl = link.href;
  link.classList.add('is-monitoring');
  link.setAttribute('aria-busy', 'true');
  link.textContent = 'Ставлю на мониторинг…';
  // Start the monitoring request, but do not await it: Android only permits an
  // external intent while the original click still has user activation.
  const monitorRequest = fetch(`/api/v1/search/results/${resultId}/monitor`, {
    method:'POST',
    keepalive:true,
  }).then(response => {
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
  }).catch(() => markSeen(resultId).catch(() => {}));

  openListingInYandexBrowser(targetUrl);
  void monitorRequest;
}

async function loadSearchStatus() {
  try {
    const response = await fetch('/api/v1/search/status');
    if (!response.ok) throw new Error('Не удалось получить статус поиска');
    const status = await readJson(response);
    document.querySelector('#newSearchCount').textContent = status.new_results || 0;
    document.querySelector('#foundHour').textContent = status.found_last_hour || 0;
    document.querySelector('#foundToday').textContent = status.found_today || 0;
    document.querySelector('#urgentCount').textContent = status.urgent_results || 0;
    document.querySelector('#importedCount').textContent = status.imported_results || 0;
    const providers = (status.providers || []).join(', ') || 'нет';
    const lastRun = status.last_run ? dateTime.format(new Date(status.last_run)) : 'ещё не выполнялся';
    if (searchMeta) searchMeta.textContent = `Источники: ${providers}. Запросов за запуск: ${status.queries_per_run}. Последний поиск: ${lastRun}.`;
    const telegram = await fetch('/api/v1/notifications/status').then(readJson);
    const telegramNode = document.querySelector('#telegramStatus');
    if (telegramNode) telegramNode.textContent = telegram.configured ? `Telegram: подключён · отправлено ${telegram.sent_notifications || 0}` : 'Telegram: добавьте TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID для уведомлений';
    const health = await fetch('/health').then(readJson);
    const twogisNode = document.querySelector('#twogisStatus');
    if (twogisNode) twogisNode.textContent = health.twogis_configured ? '2GIS: автоматический поиск ЖК и отзывов включён' : '2GIS: добавьте TWOGIS_API_KEY для автоматического рейтинга ЖК';
  } catch (error) {
    if (searchMessage) searchMessage.textContent = error.message;
  }
}

runSearchButton?.addEventListener('click', async () => {
  runSearchButton.disabled = true;
  if (searchMessage) searchMessage.textContent = 'Проверяю поисковую выдачу…';
  try {
    const response = await fetch('/api/v1/search/run', {method:'POST'});
    const body = await readJson(response);
    if (!response.ok) throw new Error(body.detail || 'Не удалось выполнить поиск');
    if (searchMessage) searchMessage.textContent = `Запросов: ${body.queries}. Найдено ссылок: ${body.found}. Новых: ${body.new}. Автоимпортировано: ${body.imported || 0}. Ошибок: ${body.errors + (body.import_errors || 0)}.`;
    await Promise.all([loadListings(), loadSearchStatus()]);
    await loadSearchResults();
  } catch (error) {
    if (searchMessage) searchMessage.textContent = error.message;
  } finally { runSearchButton.disabled = false; }
});

reloadSearchResults?.addEventListener('click', async () => {
  reloadSearchResults.disabled = true;
  await loadListings();
  await Promise.all([loadSearchStatus(), loadSearchResults()]);
  reloadSearchResults.disabled = false;
});

runChecksButton?.addEventListener('click', async () => {
  runChecksButton.disabled = true;
  if (checkMessage) checkMessage.textContent = 'Проверяю сохранённые объявления…';
  try {
    const response = await fetch('/api/v1/checks/run', {method:'POST'});
    const body = await readJson(response);
    if (!response.ok) throw new Error(body.detail || 'Не удалось выполнить проверку');
    if (checkMessage) checkMessage.textContent = `Проверено: ${body.checked}. Изменений цены: ${body.updated}. Блокировок источника: ${body.blocked}. Ошибок: ${body.errors}.`;
    await loadListings();
  } catch (error) {
    if (checkMessage) checkMessage.textContent = error.message;
  } finally { runChecksButton.disabled = false; }
});

importForm?.addEventListener('submit', async event => {
  event.preventDefault();
  if (importMessage) importMessage.textContent = 'Загружаю объявление и определяю параметры…';
  const button = importForm.querySelector('button[type="submit"]');
  button.disabled = true;
  const data = new FormData(importForm);
  try {
    const response = await fetch('/api/v1/listings/import', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({source_url:data.get('source_url')})});
    const body = await readJson(response);
    if (!response.ok) throw new Error(body.detail || 'Не удалось импортировать объявление');
    if (importMessage) importMessage.textContent = `Готово: ${money.format(body.price_kzt)}, ${number.format(body.area_m2)} м², рейтинг ${body.assessment.score}/100.`;
    importForm.reset();
    if (filter) filter.value = '0';
    await loadListings();
    await loadSearchResults();
  } catch (error) {
    if (importMessage) importMessage.textContent = error.message;
  } finally { button.disabled = false; }
});

form?.addEventListener('submit', async event => {
  event.preventDefault();
  if (message) message.textContent = 'Проверяю квартиру…';
  const data = new FormData(form);
  const payload = {
    source:'manual', source_url:data.get('source_url'), title:data.get('title') || '2-комнатная квартира в Астане', city:'Астана',
    district:data.get('district') || null, residential_complex:data.get('residential_complex') || null,
    address:data.get('address') || null,
    latitude:data.get('latitude') ? Number(data.get('latitude')) : null,
    longitude:data.get('longitude') ? Number(data.get('longitude')) : null,
    price_kzt:Number(data.get('price_kzt')), area_m2:Number(data.get('area_m2')), rooms:Number(data.get('rooms')),
    floor:data.get('floor') ? Number(data.get('floor')) : null, floors_total:data.get('floors_total') ? Number(data.get('floors_total')) : null,
    building_year:data.get('building_year') ? Number(data.get('building_year')) : null, building_type:data.get('building_type') || null,
    is_full_two_room:data.get('is_full_two_room') === 'on', mortgage_supported:null
  };
  try {
    const response = await fetch('/api/v1/listings', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
    const body = await readJson(response);
    if (!response.ok) throw new Error(body.detail || 'Не удалось сохранить квартиру');
    if (message) message.textContent = `Сохранено. Рейтинг ${body.assessment.score}/100 — ${body.assessment.verdict}`;
    await loadListings();
    await loadSearchResults();
  } catch (error) {
    if (message) message.textContent = error.message;
  }
});

filter?.addEventListener('change', loadListings);
listingSort?.addEventListener('change', loadListings);
refreshButton?.addEventListener('click', async () => { await loadListings(); await Promise.all([loadSearchStatus(), loadSearchResults()]); });
if ('serviceWorker' in navigator) window.addEventListener('load', () => navigator.serviceWorker.register('/service-worker.js'));
(async () => { await loadListings(); await Promise.all([loadSearchStatus(), loadSearchResults()]); })();
