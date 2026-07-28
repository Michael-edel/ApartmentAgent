const money = new Intl.NumberFormat('ru-KZ', {style:'currency', currency:'KZT', maximumFractionDigits:0});
const number = new Intl.NumberFormat('ru-KZ', {maximumFractionDigits:1});
const dateTime = new Intl.DateTimeFormat('ru-RU', {day:'2-digit', month:'2-digit', hour:'2-digit', minute:'2-digit'});

const grid = document.querySelector('#listingGrid');
const filter = document.querySelector('#scoreFilter');
const refreshButton = document.querySelector('#refreshButton');
const form = document.querySelector('#listingForm');
const message = document.querySelector('#formMessage');
const importForm = document.querySelector('#importForm');
const importMessage = document.querySelector('#importMessage');
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

function render(listings) {
  savedUrls = new Set(listings.map(item => normalizeUrl(item.source_url)));
  const minScore = Number(filter?.value || 0);
  const visible = listings.filter(item => item.assessment.score >= minScore);
  const strong = listings.filter(item => item.assessment.score >= 85);
  document.querySelector('#totalCount').textContent = listings.length;
  document.querySelector('#strongCount').textContent = strong.length;
  if (!grid) return;
  if (!visible.length) {
    grid.innerHTML = '<section class="empty card"><strong>Сохранённых квартир пока нет</strong><p>Добавьте ссылку Krisha или заполните ручную форму.</p></section>';
    return;
  }
  grid.innerHTML = visible.map(item => {
    const title = item.residential_complex || item.title;
    const location = [item.district, item.city].filter(Boolean).join(' · ');
    const scoreClass = item.assessment.score < 85 ? 'medium' : '';
    return `<article class="listing card">
      <div class="listing-top"><div><h3>${escapeHtml(title)}</h3><div class="meta">${escapeHtml(location)}</div></div><div class="rating ${scoreClass}">${item.assessment.score}</div></div>
      <div class="price">${money.format(item.price_kzt)}</div>
      <div class="meta">${number.format(item.area_m2)} м² · ${money.format(item.assessment.price_per_m2)}/м² · этаж ${item.floor || '—'}/${item.floors_total || '—'}</div>
      <span class="verdict">${escapeHtml(item.assessment.verdict)}</span>
      <a href="${escapeHtml(item.source_url)}" target="_blank" rel="noopener">Открыть объявление →</a>
    </article>`;
  }).join('');
}

async function loadListings() {
  if (refreshButton) refreshButton.disabled = true;
  try {
    const response = await fetch('/api/v1/listings');
    if (!response.ok) throw new Error('Не удалось загрузить квартиры');
    render(await readJson(response));
  } catch (error) {
    if (message) message.textContent = error.message;
  } finally {
    if (refreshButton) refreshButton.disabled = false;
  }
}

async function markSeen(id) { await fetch(`/api/v1/search/results/${id}/seen`, {method:'POST'}); }

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
    const response = await fetch('/api/v1/search/results?limit=60&only_new=true');
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
      return `<article class="listing card ${priorityClass(item.priority)}">
        <div class="listing-top">
          <div><span class="verdict">${priorityLabel(item.priority)}</span><h3>${escapeHtml(cleanSearchTitle(item.title))}</h3></div>
          <div class="rating ${a.score < 85 ? 'medium' : ''}">${a.score ?? '—'}</div>
        </div>
        ${facts.length ? `<div class="search-facts">${facts.map(value => `<span>${escapeHtml(value)}</span>`).join('')}</div>` : ''}
        ${reasons ? `<ul class="reason-list">${reasons}</ul>` : `<p class="meta">${escapeHtml(item.snippet || 'Откройте объявление для проверки параметров.')}</p>`}
        <div class="meta source-meta">Источник: ${escapeHtml(item.search_engine)} · обнаружено ${dateTime.format(new Date(item.first_seen))}</div>
        <div class="card-actions">
          <button class="enrich-result primary secondary-action ${isSaved ? 'saved' : ''}" data-url="${escapeHtml(item.url)}" type="button" ${isSaved ? 'disabled' : ''}>${isSaved ? 'Уже сохранено' : 'Сохранить и оценить'}</button>
          <a class="search-open" data-result-id="${item.id}" href="${escapeHtml(item.url)}" target="_blank" rel="noopener">Открыть объявление →</a>
        </div>
      </article>`;
    }).join('');
    searchResults.querySelectorAll('.search-open').forEach(link => link.addEventListener('click', () => markSeen(link.dataset.resultId)));
    searchResults.querySelectorAll('.enrich-result:not(.saved)').forEach(button => button.addEventListener('click', () => importSearchResult(button.dataset.url, button)));
  } catch (error) {
    if (searchMessage) searchMessage.textContent = error.message;
  }
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
    const providers = (status.providers || []).join(', ') || 'нет';
    const lastRun = status.last_run ? dateTime.format(new Date(status.last_run)) : 'ещё не выполнялся';
    if (searchMeta) searchMeta.textContent = `Источники: ${providers}. Запросов за запуск: ${status.queries_per_run}. Последний поиск: ${lastRun}.`;
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
    if (searchMessage) searchMessage.textContent = `Запросов: ${body.queries}. Найдено ссылок: ${body.found}. Новых: ${body.new}. Ошибок: ${body.errors}.`;
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
refreshButton?.addEventListener('click', async () => { await loadListings(); await Promise.all([loadSearchStatus(), loadSearchResults()]); });
if ('serviceWorker' in navigator) window.addEventListener('load', () => navigator.serviceWorker.register('/service-worker.js'));
(async () => { await loadListings(); await Promise.all([loadSearchStatus(), loadSearchResults()]); })();