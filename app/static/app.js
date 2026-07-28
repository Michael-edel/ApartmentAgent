const money = new Intl.NumberFormat('ru-KZ', {style:'currency', currency:'KZT', maximumFractionDigits:0});
const number = new Intl.NumberFormat('ru-KZ', {maximumFractionDigits:1});
const dateTime = new Intl.DateTimeFormat('ru-RU', {day:'2-digit', month:'2-digit', hour:'2-digit', minute:'2-digit'});

const grid = document.querySelector('#listingGrid');
const empty = document.querySelector('#emptyState');
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
const searchEmpty = document.querySelector('#searchEmpty');
const reloadSearchResults = document.querySelector('#reloadSearchResults');

function escapeHtml(value='') {
  return String(value).replace(/[&<>'"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#039;','"':'&quot;'}[ch]));
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
  const minScore = Number(filter.value || 0);
  const visible = listings.filter(item => item.assessment.score >= minScore);
  const strong = listings.filter(item => item.assessment.score >= 85);

  document.querySelector('#totalCount').textContent = listings.length;
  document.querySelector('#strongCount').textContent = strong.length;
  empty.hidden = visible.length > 0;

  grid.innerHTML = visible.map(item => {
    const title = item.residential_complex || item.title;
    const location = [item.district, item.city].filter(Boolean).join(' · ');
    const scoreClass = item.assessment.score < 85 ? 'medium' : '';
    return `<article class="listing card">
      <div class="listing-top">
        <div><h3>${escapeHtml(title)}</h3><div class="meta">${escapeHtml(location)}</div></div>
        <div class="rating ${scoreClass}">${item.assessment.score}</div>
      </div>
      <div class="price">${money.format(item.price_kzt)}</div>
      <div class="meta">${number.format(item.area_m2)} м² · ${money.format(item.assessment.price_per_m2)}/м² · этаж ${item.floor || '—'}/${item.floors_total || '—'}</div>
      <span class="verdict">${escapeHtml(item.assessment.verdict)}</span>
      <a href="${escapeHtml(item.source_url)}" target="_blank" rel="noopener">Открыть объявление →</a>
    </article>`;
  }).join('');
}

async function loadListings() {
  refreshButton.disabled = true;
  try {
    const response = await fetch('/api/v1/listings');
    if (!response.ok) throw new Error('Не удалось загрузить квартиры');
    render(await response.json());
  } catch (error) {
    message.textContent = error.message;
  } finally {
    refreshButton.disabled = false;
  }
}

async function markSeen(id) {
  await fetch(`/api/v1/search/results/${id}/seen`, {method:'POST'});
}

async function loadSearchResults() {
  try {
    const response = await fetch('/api/v1/search/results?limit=30&only_new=true');
    if (!response.ok) throw new Error('Не удалось загрузить найденные ссылки');
    const items = await response.json();
    searchEmpty.hidden = items.length > 0;
    searchResults.innerHTML = items.map(item => `<article class="listing card ${priorityClass(item.priority)}">
      <div class="listing-top">
        <div>
          <span class="verdict">${priorityLabel(item.priority)}</span>
          <h3>${escapeHtml(item.title || 'Новое объявление Krisha')}</h3>
        </div>
        <div class="meta">${escapeHtml(item.search_engine)}</div>
      </div>
      <p class="meta">${escapeHtml(item.snippet || 'Данные доступны в поисковой выдаче. Откройте объявление для проверки.')}</p>
      <div class="meta">Обнаружено: ${dateTime.format(new Date(item.first_seen))}</div>
      <a class="search-open" data-result-id="${item.id}" href="${escapeHtml(item.url)}" target="_blank" rel="noopener">Открыть объявление →</a>
    </article>`).join('');

    searchResults.querySelectorAll('.search-open').forEach(link => {
      link.addEventListener('click', () => markSeen(link.dataset.resultId));
    });
  } catch (error) {
    searchMessage.textContent = error.message;
  }
}

async function loadSearchStatus() {
  try {
    const response = await fetch('/api/v1/search/status');
    if (!response.ok) throw new Error('Не удалось получить статус поиска');
    const status = await response.json();
    document.querySelector('#newSearchCount').textContent = status.new_results || 0;
    document.querySelector('#foundHour').textContent = status.found_last_hour || 0;
    document.querySelector('#foundToday').textContent = status.found_today || 0;
    document.querySelector('#urgentCount').textContent = status.urgent_results || 0;
    const providers = (status.providers || []).join(', ') || 'нет';
    const lastRun = status.last_run ? dateTime.format(new Date(status.last_run)) : 'ещё не выполнялся';
    searchMeta.textContent = `Источники: ${providers}. Запросов за запуск: ${status.queries_per_run}. Последний поиск: ${lastRun}.`;
  } catch (error) {
    searchMessage.textContent = error.message;
  }
}

runSearchButton.addEventListener('click', async () => {
  runSearchButton.disabled = true;
  searchMessage.textContent = 'Проверяю поисковую выдачу…';
  try {
    const response = await fetch('/api/v1/search/run', {method:'POST'});
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || 'Не удалось выполнить поиск');
    searchMessage.textContent = `Запросов: ${body.queries}. Найдено ссылок: ${body.found}. Новых: ${body.new}. Ошибок: ${body.errors}.`;
    await Promise.all([loadSearchStatus(), loadSearchResults()]);
  } catch (error) {
    searchMessage.textContent = error.message;
  } finally {
    runSearchButton.disabled = false;
  }
});

reloadSearchResults.addEventListener('click', async () => {
  reloadSearchResults.disabled = true;
  await Promise.all([loadSearchStatus(), loadSearchResults()]);
  reloadSearchResults.disabled = false;
});

runChecksButton.addEventListener('click', async () => {
  runChecksButton.disabled = true;
  checkMessage.textContent = 'Проверяю сохранённые объявления…';
  try {
    const response = await fetch('/api/v1/checks/run', {method:'POST'});
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || 'Не удалось выполнить проверку');
    checkMessage.textContent = `Проверено: ${body.checked}. Изменений цены: ${body.updated}. Блокировок источника: ${body.blocked}. Ошибок: ${body.errors}.`;
    await loadListings();
  } catch (error) {
    checkMessage.textContent = error.message;
  } finally {
    runChecksButton.disabled = false;
  }
});

importForm.addEventListener('submit', async event => {
  event.preventDefault();
  importMessage.textContent = 'Загружаю объявление и определяю параметры…';
  const button = importForm.querySelector('button[type="submit"]');
  button.disabled = true;
  const data = new FormData(importForm);
  try {
    const response = await fetch('/api/v1/listings/import', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({source_url: data.get('source_url')})
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || 'Не удалось импортировать объявление');
    importMessage.textContent = `Готово: ${money.format(body.price_kzt)}, ${number.format(body.area_m2)} м², рейтинг ${body.assessment.score}/100.`;
    importForm.reset();
    filter.value = '0';
    await loadListings();
    document.querySelector('#listingGrid').scrollIntoView({behavior:'smooth'});
  } catch (error) {
    importMessage.textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

form.addEventListener('submit', async event => {
  event.preventDefault();
  message.textContent = 'Проверяю квартиру…';
  const data = new FormData(form);
  const payload = {
    source: 'manual',
    source_url: data.get('source_url'),
    title: '2-комнатная квартира в Астане',
    city: 'Астана',
    price_kzt: Number(data.get('price_kzt')),
    area_m2: Number(data.get('area_m2')),
    rooms: Number(data.get('rooms')),
    floor: Number(data.get('floor')),
    floors_total: Number(data.get('total_floors')),
    building_year: data.get('build_year') ? Number(data.get('build_year')) : null,
    building_type: data.get('building_type'),
    is_full_two_room: true,
    mortgage_supported: data.get('otbasy_eligible') === 'on'
  };
  try {
    const response = await fetch('/api/v1/listings', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify(payload)
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || 'Не удалось сохранить квартиру');
    message.textContent = `Сохранено. Рейтинг ${body.assessment.score}/100 — ${body.assessment.verdict}`;
    await loadListings();
  } catch (error) {
    message.textContent = error.message;
  }
});

filter.addEventListener('change', loadListings);
refreshButton.addEventListener('click', async () => {
  await Promise.all([loadListings(), loadSearchStatus(), loadSearchResults()]);
});

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => navigator.serviceWorker.register('/service-worker.js'));
}

Promise.all([loadListings(), loadSearchStatus(), loadSearchResults()]);
