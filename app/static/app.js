const money = new Intl.NumberFormat('ru-KZ', {style:'currency', currency:'KZT', maximumFractionDigits:0});
const number = new Intl.NumberFormat('ru-KZ', {maximumFractionDigits:1});
const dateTime = new Intl.DateTimeFormat('ru-RU', {day:'2-digit', month:'2-digit', hour:'2-digit', minute:'2-digit'});

const grid = document.querySelector('#listingGrid');
const filter = document.querySelector('#scoreFilter');
const listingSort = document.querySelector('#listingSort');
const listingSellerFilter = document.querySelector('#listingSellerFilter');
const listingLeftBankOnly = document.querySelector('#listingLeftBankOnly');
let listingsRevision = 0;
const listingShowPledged = document.querySelector('#listingShowPledged');
const listingShowInactive = document.querySelector('#listingShowInactive');
const listingAvailabilitySummary = document.querySelector('#listingAvailabilitySummary');
const listingAvailabilityMessage = document.querySelector('#listingAvailabilityMessage');
const listingPledgeSummary = document.querySelector('#listingPledgeSummary');
const refreshButton = document.querySelector('#refreshButton');
const form = document.querySelector('#listingForm');
const manualRooms = form?.querySelector('[name="rooms"]');
const fullTwoRoom = form?.querySelector('[name="is_full_two_room"]');
manualRooms?.addEventListener('input', () => {
  if (Number(manualRooms.value) !== 2 && fullTwoRoom) fullTwoRoom.checked = false;
});
const message = document.querySelector('#formMessage');
const importForm = document.querySelector('#importForm');
const importMessage = document.querySelector('#importMessage');
const copyBookmarkletButton = document.querySelector('#copyBookmarkletButton');
const bookmarkletCode = document.querySelector('#bookmarkletCode');
const bookmarkletMessage = document.querySelector('#bookmarkletMessage');
const runChecksButton = document.querySelector('#runChecksButton');
const checkMessage = document.querySelector('#checkMessage');
const runSearchButton = document.querySelector('#runSearchButton');
const searchRooms = document.querySelector('#searchRooms');
const searchMinPrice = document.querySelector('#searchMinPrice');
const searchMaxPrice = document.querySelector('#searchMaxPrice');
const searchMinArea = document.querySelector('#searchMinArea');
const searchMaxArea = document.querySelector('#searchMaxArea');
const searchBathrooms = document.querySelector('#searchBathrooms');
const searchExcludePledged = document.querySelector('#searchExcludePledged');
let searchRoomsInitialized = false;
let searchMinPriceInitialized = false;
let searchPriceInitialized = false;
let searchMinAreaInitialized = false;
let searchMaxAreaInitialized = false;
let searchBathroomsInitialized = false;
let searchPledgeInitialized = false;
searchBathrooms?.addEventListener('change', () => { searchBathroomsInitialized = true; });
searchExcludePledged?.addEventListener('change', () => { searchPledgeInitialized = true; });
function updateCriteriaRooms() {
  const heading = document.querySelector('#criteriaRooms');
  if (heading && searchRooms) heading.textContent = `От ${searchRooms.value} комнат`;
}
searchRooms?.addEventListener('change', () => {
  searchRoomsInitialized = true;
  updateCriteriaRooms();
});
function updateCriteriaPrice() {
  const label = document.querySelector('#criteriaPrice');
  if (label && searchMinPrice?.checkValidity() && searchMaxPrice?.checkValidity()) {
    const minimum = searchMinPrice.value === '' ? null : Number(searchMinPrice.value);
    const maximum = Number(searchMaxPrice.value);
    if (minimum === null || minimum <= maximum) label.textContent = formatPriceRange(minimum, maximum);
  }
}
function formatPriceRange(minimum, maximum) {
  return minimum > 0 ? `от ${money.format(minimum)} до ${money.format(maximum)}` : `до ${money.format(maximum)}`;
}
searchMinPrice?.addEventListener('input', () => { searchMinPriceInitialized = true; updateCriteriaPrice(); });
searchMaxPrice?.addEventListener('input', () => {
  searchPriceInitialized = true;
  updateCriteriaPrice();
});
function updateCriteriaArea() {
  const label = document.querySelector('#criteriaArea');
  if (label && searchMinArea?.checkValidity() && searchMaxArea?.checkValidity()) {
    const minimum = Number(searchMinArea.value);
    const maximum = searchMaxArea.value === '' ? null : Number(searchMaxArea.value);
    if (maximum === null || minimum <= maximum) label.textContent = formatAreaRange(minimum, maximum);
  }
}
function formatAreaRange(minimum, maximum) {
  return Number.isFinite(maximum) ? `${number.format(minimum)}–${number.format(maximum)}` : `от ${number.format(minimum)}`;
}
searchMinArea?.addEventListener('input', () => { searchMinAreaInitialized = true; updateCriteriaArea(); });
searchMaxArea?.addEventListener('input', () => { searchMaxAreaInitialized = true; updateCriteriaArea(); });
const searchMessage = document.querySelector('#searchMessage');
const searchMeta = document.querySelector('#searchMeta');
const searchResults = document.querySelector('#searchResults');
const reloadSearchResults = document.querySelector('#refreshSearchButton');
const searchResultsSort = document.querySelector('#searchResultsSort');
const searchSellerFilter = document.querySelector('#searchSellerFilter');
const searchLeftBankOnly = document.querySelector('#searchLeftBankOnly');
let searchResultsRevision = 0;
const bathroomCheckPanel = document.querySelector('#bathroomCheckPanel');
const bathroomCheckCounts = document.querySelector('#bathroomCheckCounts');
const bathroomCheckDetails = document.querySelector('#bathroomCheckDetails');
const bathroomCheckMode = document.querySelector('#bathroomCheckMode');
const bathroomCheckCriteria = document.querySelector('#bathroomCheckCriteria');
const bathroomCheckProgress = document.querySelector('#bathroomCheckProgress');
const bathroomCheckMessage = document.querySelector('#bathroomCheckMessage');
const bathroomCheckCurrent = document.querySelector('#bathroomCheckCurrent');
const bathroomCheckButtons = {
  start: document.querySelector('#startBathroomCheck'),
  details: document.querySelector('#startDetailsCheck'),
  pause: document.querySelector('#pauseBathroomCheck'),
  resume: document.querySelector('#resumeBathroomCheck'),
  cancel: document.querySelector('#cancelBathroomCheck'),
};
const bathroomCheckApi = '/api/v1/search/bathroom-check';
let bathroomCheckState = null;
let bathroomCheckBusy = false;
let bathroomCheckPolling = false;
let bathroomCheckSignature = null;
let bathroomCheckRevision = 0;
let bathroomCheckFeedback = '';
let savedUrls = new Set();
let savedListingIds = new Map();

function escapeHtml(value='') {
  return String(value).replace(/[&<>'"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#039;','"':'&quot;'}[ch]));
}

function safeExternalUrl(value) {
  try {
    const url = new URL(value);
    return ['http:', 'https:'].includes(url.protocol) ? url.href : null;
  } catch { return null; }
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

function queueCount(value) {
  return Number.isSafeInteger(value) && value >= 0 ? value : 0;
}

function fetchBathroomCheck(path = '', options = {}) {
  return fetch(`${bathroomCheckApi}${path}`, {...options, cache:'no-store', signal:AbortSignal.timeout(10000)});
}

function renderBathroomCheck(state) {
  if (!bathroomCheckPanel) return;
  bathroomCheckState = state;
  const running = state.status === 'running';
  const paused = state.status === 'paused';
  const active = running || paused;
  const total = queueCount(state.total);
  const checked = queueCount(state.checked);
  const candidates = queueCount(state.candidate_count);
  const detailsCandidates = queueCount(state.details_candidate_count);
  const detailsMode = state.mode === 'details';
  const labels = {idle:'Готово к запуску', running:'Идёт проверка', paused:'Проверка на паузе', completed:'Проверка завершена', cancelled:'Проверка остановлена'};
  if (bathroomCheckMode) {
    bathroomCheckMode.hidden = !state.job_id;
    bathroomCheckMode.textContent = state.job_id ? `Режим: ${detailsMode ? 'дополнение карточек' : 'проверка количества санузлов'}.` : '';
  }
  if (bathroomCheckCriteria) {
    const criteria = state.criteria || {};
    const parts = [];
    if ([2, 3, 4].includes(criteria.target_rooms)) parts.push(`от ${criteria.target_rooms} комн.`);
    if (Number.isFinite(criteria.min_area_m2)) parts.push(`${formatAreaRange(criteria.min_area_m2, criteria.max_area_m2)} м²`);
    if (Number.isSafeInteger(criteria.max_price_kzt) && criteria.max_price_kzt > 0) parts.push(formatPriceRange(criteria.min_price_kzt, criteria.max_price_kzt));
    if (criteria.exclude_pledged && !criteria.manual_import) parts.push('известные залоговые исключены');
    bathroomCheckCriteria.hidden = !state.job_id || parts.length === 0;
    bathroomCheckCriteria.textContent = criteria.manual_import
      ? 'Импорт выбранного объявления по номеру. Поисковые фильтры не ограничивают его сохранение.'
      : state.job_id && parts.length ? `Проверяются квартиры: ${parts.join(', ')}. Критерии сохранены при запуске проверки.` : '';
  }
  const unavailable = queueCount(state.unavailable);
  const noBath = Math.max(0, queueCount(state.unknown) - unavailable);
  if (bathroomCheckCounts) bathroomCheckCounts.textContent = total
    ? `Обработано: ${checked} из ${total}. Карточек сохранено или обновлено: ${queueCount(state.enriched)}. Санузлов от 2: ${queueCount(state.confirmed)}. Санузлов меньше 2: ${queueCount(state.rejected)}. Без данных о санузлах: ${noBath}. ${unavailable ? `Снято с публикации: ${unavailable}. ` : ''}Ошибок: ${queueCount(state.failed)}. Осталось: ${queueCount(state.pending)}.`
    : `Объявлений с неизвестным количеством санузлов: ${candidates}. Карточек для дополнения: ${detailsCandidates}.`;
  if (bathroomCheckDetails) {
    const facts = state.detail_fields;
    bathroomCheckDetails.hidden = !facts || !queueCount(state.enriched);
    bathroomCheckDetails.textContent = bathroomCheckDetails.hidden ? '' :
      `Ремонт, ипотека и тип продавца получены вместе: ${queueCount(facts.complete)}. В сохранённых карточках не получено: ремонт — ${queueCount(facts.missing_repair)}, ипотека — ${queueCount(facts.missing_mortgage)}, тип продавца — ${queueCount(facts.missing_author)}.`;
  }
  if (bathroomCheckProgress) {
    bathroomCheckProgress.hidden = total === 0;
    bathroomCheckProgress.max = Math.max(total, 1);
    bathroomCheckProgress.value = Math.min(checked, total);
  }
  const phase = running && checked === 0 && !state.current_url ? 'Ожидается подключение расширения' : running && detailsMode ? 'Идёт дополнение карточек' : labels[state.status];
  if (bathroomCheckMessage) {
    const statusText = `${phase || 'Состояние проверки неизвестно'}.${state.message ? ` ${state.message}` : ''}`;
    bathroomCheckMessage.textContent = bathroomCheckBusy && bathroomCheckFeedback
      ? bathroomCheckFeedback : [statusText, bathroomCheckFeedback].filter(Boolean).join(' ');
  }
  if (bathroomCheckCurrent) {
    const currentUrl = safeExternalUrl(state.current_url);
    bathroomCheckCurrent.hidden = !currentUrl;
    bathroomCheckCurrent.innerHTML = currentUrl
      ? `Текущее объявление: <a href="${escapeHtml(currentUrl)}" target="_blank" rel="noopener noreferrer">Открыть в новой вкладке →</a>` : '';
  }
  if (bathroomCheckButtons.start) {
    bathroomCheckButtons.start.hidden = active;
    bathroomCheckButtons.start.disabled = bathroomCheckBusy || candidates === 0;
  }
  if (bathroomCheckButtons.details) {
    bathroomCheckButtons.details.hidden = active;
    bathroomCheckButtons.details.disabled = bathroomCheckBusy || detailsCandidates === 0;
  }
  if (bathroomCheckButtons.pause) bathroomCheckButtons.pause.hidden = !running;
  if (bathroomCheckButtons.resume) bathroomCheckButtons.resume.hidden = !paused;
  if (bathroomCheckButtons.cancel) bathroomCheckButtons.cancel.hidden = !active;
  for (const action of ['pause', 'resume', 'cancel']) {
    if (bathroomCheckButtons[action]) bathroomCheckButtons[action].disabled = bathroomCheckBusy;
  }
}

function updateBathroomCheck(state) {
  if (!bathroomCheckBusy && (state.job_id !== bathroomCheckState?.job_id || state.status !== bathroomCheckState?.status)) {
    bathroomCheckFeedback = '';
  }
  const nextSignature = `${state.job_id || ''}:${state.mode || 'bathrooms'}:${state.status}:${queueCount(state.checked)}`;
  const changed = bathroomCheckSignature !== null && bathroomCheckSignature !== nextSignature;
  bathroomCheckSignature = nextSignature;
  renderBathroomCheck(state);
  if (changed) void Promise.all([loadListings(), loadSearchResults(), loadSearchStatus()]);
}

async function loadBathroomCheckStatus() {
  if (!bathroomCheckPanel || bathroomCheckPolling || bathroomCheckBusy) return;
  bathroomCheckPolling = true;
  const revision = bathroomCheckRevision;
  try {
    const response = await fetchBathroomCheck();
    const state = await readJson(response);
    if (!response.ok) throw new Error(state.detail || 'Не удалось получить состояние автопроверки');
    if (revision === bathroomCheckRevision && !bathroomCheckBusy) {
      const reconnect = bathroomCheckSignature === null && state.status === 'running';
      updateBathroomCheck(state);
      if (reconnect) window.setTimeout(() => {
        if (bathroomCheckState?.status === 'running') window.postMessage({type:'apartment-agent-bathroom-queue'}, location.origin);
      }, 1500);
    }
  } catch (error) {
    if (revision === bathroomCheckRevision && !bathroomCheckBusy && !bathroomCheckFeedback && bathroomCheckMessage) {
      bathroomCheckMessage.textContent = error.name === 'TimeoutError' ? 'Сервер не ответил. Проверю состояние снова.' : error.message;
    }
  } finally {
    bathroomCheckPolling = false;
  }
}

async function controlBathroomCheck(action, mode = 'bathrooms') {
  if (bathroomCheckBusy) return;
  const [pending, unconfirmed] = {
    start: ['Запускаю автопроверку…', 'Сервер не подтвердил запуск автопроверки.'],
    pause: ['Отправляю команду «Пауза»…', 'Сервер не подтвердил паузу.'],
    resume: ['Возобновляю автопроверку…', 'Сервер не подтвердил возобновление.'],
    cancel: ['Останавливаю автопроверку…', 'Сервер не подтвердил остановку.'],
  }[action];
  bathroomCheckRevision += 1;
  bathroomCheckBusy = true;
  bathroomCheckFeedback = pending;
  if (bathroomCheckState) renderBathroomCheck(bathroomCheckState);
  try {
    const options = action === 'start'
      ? {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({mode})}
      : {method:'POST'};
    const response = await fetchBathroomCheck(`/${action}`, options);
    const state = await readJson(response);
    if (!response.ok) throw new Error(state.detail || 'Не удалось изменить состояние автопроверки');
    bathroomCheckFeedback = '';
    updateBathroomCheck(state);
    window.postMessage({type:'apartment-agent-bathroom-queue'}, location.origin);
  } catch (error) {
    const reason = error.name === 'TimeoutError' ? 'Истекло время ожидания. Попробуйте ещё раз.'
      : error.name === 'TypeError' ? 'Не удалось связаться с приложением. Попробуйте ещё раз.' : error.message;
    bathroomCheckFeedback = `${unconfirmed} ${reason}`;
  } finally {
    bathroomCheckBusy = false;
    if (bathroomCheckState) renderBathroomCheck(bathroomCheckState);
  }
}

for (const [action, button] of Object.entries(bathroomCheckButtons)) {
  button?.addEventListener('click', () => { void controlBathroomCheck(action === 'details' ? 'start' : action, action === 'details' ? 'details' : 'bathrooms'); });
}
if (bathroomCheckPanel) {
  void loadBathroomCheckStatus();
  window.setInterval(() => { void loadBathroomCheckStatus(); }, 5000);
}

function normalizeUrl(value='') {
  try { const u = new URL(value, location.origin); const origin = ['krisha.kz', 'www.krisha.kz'].includes(u.hostname.toLowerCase()) ? 'https://krisha.kz' : u.origin; return `${origin}${u.pathname.replace(/\/$/, '')}`; }
  catch { return String(value).replace(/\/$/, ''); }
}

function cleanSearchTitle(title='') {
  let value = String(title).replace(/\s+/g, ' ').trim();
  value = value.replace(/\s*[·|,-]\s*\d{2,3}(?:[.,]\d+)?\s*м(?:²|2).*$/i, '');
  value = value.replace(/\s*\d{1,2}\s*\/\s*\d{1,2}\s*этаж.*$/i, '');
  return value || 'Квартира';
}

function priorityLabel(priority) {
  if (priority === 'urgent') return 'Срочно посмотреть';
  if (priority === 'good') return 'Проверить';
  return 'Низкий приоритет';
}

function priorityClass(priority) {
  if (priority === 'urgent') return 'priority-urgent';
  if (priority === 'good') return 'priority-good';
  return 'priority-normal';
}

function bathroomBadge(item, search = false) {
  const count = item.bathrooms_count;
  const known = Number.isInteger(count);
  const confirmed = known && count >= 2;
  const text = known
    ? `Санузлов: ${item.bathrooms_count_is_lower_bound ? 'не менее ' : ''}${count}`
    : search ? 'Санузел не подтверждён' : 'Санузлы: не указано';
  return `<span class="bathroom-badge${confirmed ? ' bathrooms-confirmed' : ''}">${confirmed ? '✓ ' : ''}${escapeHtml(text)}</span>`;
}

function repairDetails(item) {
  const known = typeof item.repair_condition === 'string' && item.repair_condition.trim();
  const kind = known ? item.repair_source_kind : null;
  const source = kind === 'description' ? 'По описанию автора' : kind === 'field' ? 'Поле объявления' : kind === 'manual' ? 'Ручной ввод' : '';
  const evidence = known && kind === 'description' && typeof item.repair_evidence === 'string'
    ? item.repair_evidence : '';
  return `<div class="repair-condition"><b>Состояние / ремонт:</b> ${known ? escapeHtml(item.repair_condition) : 'Не указано'}${source ? `<small class="repair-source">${source}</small>` : ''}${evidence.trim() ? `<details class="repair-evidence" data-details-key="repair-evidence"><summary>Цитата из описания</summary><blockquote>${escapeHtml(evidence)}</blockquote></details>` : ''}</div>`;
}

function mortgageDetails(item) {
  const known = typeof item.mortgage_supported === 'boolean';
  const value = item.mortgage_supported === true ? 'Указана в объявлении' : item.mortgage_supported === false ? 'Не предусмотрена' : 'Не указана';
  const kind = known ? item.mortgage_source_kind : null;
  const source = kind === 'description' ? 'По описанию автора' : kind === 'title' ? 'По заголовку объявления' : kind === 'field' ? 'Данные объявления' : kind === 'manual' ? 'Ручной ввод' : '';
  const evidence = known && (kind === 'description' || kind === 'title') && typeof item.mortgage_evidence === 'string'
    ? item.mortgage_evidence : '';
  return `<div class="mortgage-condition"><b>Ипотека:</b> ${value}${source ? `<small class="mortgage-source">${source}</small>` : ''}${evidence.trim() ? `<details class="mortgage-evidence" data-details-key="mortgage-evidence"><summary>${kind === 'title' ? 'Цитата из заголовка' : 'Цитата из описания'}</summary><blockquote>${escapeHtml(evidence)}</blockquote></details>` : ''}</div>`;
}

function pledgeBadge(item) {
  if (item.is_pledged === true) return '<span class="pledge-flag">В залоге</span>';
  return item.is_pledged === false ? 'Нет залога' : 'Не указано';
}

const availabilityLabels = new Map([
  ['sold', 'Продана'],
  ['archived', 'В архиве'],
  ['unavailable', 'Объявление недоступно'],
]);

function availabilityBadge(item) {
  const label = availabilityLabels.get(item.availability);
  return label ? `<span class="availability-badge availability-${item.availability}">${label}</span>` : '';
}

const sellerRoles = new Map([
  ['owner', {label:'Собственник', order:0}],
  ['verified_agent', {label:'Крыша Агент', order:1}],
  ['realtor', {label:'Риелтор', order:2}],
  ['unknown', {label:'Тип продавца не указан', order:3}],
]);

function sellerBadge(item) {
  const category = sellerRoles.has(item.seller_category) ? item.seller_category : 'unknown';
  const identityConfirmed = category === 'realtor' && /личность подтверждена/i.test(item.seller_type || '');
  const otherRole = category === 'unknown' ? item.seller_type?.trim() : null;
  const label = identityConfirmed ? 'Риелтор · личность подтверждена' : otherRole || sellerRoles.get(category).label;
  return `<span class="seller-badge seller-${category}" data-seller-category="${category}">${escapeHtml(label)}</span>`;
}

function riverBankBadge(item) {
  if (item.river_bank === 'left') return '<span class="bank-badge bank-left" data-river-bank="left">Левый берег · +5</span>';
  if (item.river_bank === 'right') return '<span class="bank-badge" data-river-bank="right">Правый берег</span>';
  return '<span class="bank-badge" data-river-bank="unknown">Берег не определён</span>';
}

function setupLikedComparison() {
  const openButton = document.querySelector('#openLikedComparison');
  const dialog = document.querySelector('#likedComparison');
  if (!openButton || !dialog) return;
  const picker = dialog.querySelector('#likedComparisonPicker');
  const table = dialog.querySelector('#likedComparisonTable');
  const status = dialog.querySelector('#likedComparisonStatus');
  const action = dialog.querySelector('#runLikedComparison');
  const selected = new Set();
  let apartments = [];
  let requestRevision = 0;
  const apartmentName = item => item.address || cleanSearchTitle(item.title || '');
  const unknown = '<span class="compare-unknown">Не указано</span>';
  const textCell = value => value === null || value === undefined || value === '' ? unknown : escapeHtml(value);
  const formattedPrice = value => Number.isFinite(value) ? money.format(value) : null;
  const comparisonPrice = item => Number.isFinite(item.price_lower_bound_kzt)
    ? `от ${money.format(item.price_lower_bound_kzt)}` : formattedPrice(item.price_kzt);
  const fields = [
    ['Цена', item => textCell(comparisonPrice(item))],
    ['Цена за м²', item => textCell(Number.isFinite(item.price_per_m2) ? `${money.format(item.price_per_m2)}/м²` : null)],
    ['Адрес', item => textCell(item.address)],
    ['Этаж', item => textCell(Number.isInteger(item.floor) ? (Number.isInteger(item.floors_total) ? `${item.floor} из ${item.floors_total}` : item.floor) : null)],
    ['Санузлы', item => Number.isInteger(item.bathrooms_count) ? bathroomBadge(item) : unknown],
    ['Ремонт', item => textCell(item.repair_condition)],
    ['Ипотека', item => textCell(item.mortgage_supported === true ? 'Указана возможность' : item.mortgage_supported === false ? 'Не предусмотрена' : null)],
    ['Залог', item => typeof item.is_pledged === 'boolean' ? pledgeBadge(item) : unknown],
    ['Тип продавца', item => sellerBadge(item)],
  ];

  function updatePicker() {
    for (const input of picker.querySelectorAll('input[type="checkbox"]')) {
      input.checked = selected.has(input.value);
      input.disabled = selected.size >= 4 && !input.checked;
    }
    status.textContent = apartments.length < 2
      ? 'Отметьте минимум две квартиры галочкой «Понравилась».'
      : `Выбрано ${selected.size} из 4. Для сравнения нужны минимум две квартиры.`;
    action.disabled = selected.size < 2;
  }

  function showPicker() {
    table.hidden = true;
    picker.hidden = !apartments.length;
    action.hidden = apartments.length < 2;
    action.textContent = 'Сравнить выбранные';
    updatePicker();
  }

  openButton.addEventListener('click', async () => {
    if (dialog.open) return;
    const revision = ++requestRevision;
    picker.hidden = table.hidden = action.hidden = true;
    status.textContent = 'Загружаю понравившиеся квартиры…';
    dialog.showModal();
    try {
      const response = await fetch('/api/v1/comparison/liked', {signal:AbortSignal.timeout(10000)});
      if (!response.ok) throw new Error('Не удалось загрузить понравившиеся квартиры');
      const data = await readJson(response);
      if (!dialog.open || revision !== requestRevision) return;
      apartments = data;
      const available = new Set(apartments.map(item => item.source_url));
      for (const key of selected) if (!available.has(key)) selected.delete(key);
      picker.innerHTML = apartments.map(item => {
        const meta = [comparisonPrice(item), Number.isFinite(item.area_m2) ? `${number.format(item.area_m2)} м²` : null].filter(Boolean);
        if (item.source === 'found') meta.push('Из поиска');
        return `<label class="compare-option"><input type="checkbox" value="${escapeHtml(item.source_url)}"><span><b>${escapeHtml(apartmentName(item))}</b>${meta.length ? `<small class="compare-option-meta">${escapeHtml(meta.join(' · '))}</small>` : ''}</span></label>`;
      }).join('');
      showPicker();
    } catch {
      if (dialog.open && revision === requestRevision) status.textContent = 'Не удалось загрузить квартиры. Закройте окно и попробуйте ещё раз.';
    }
  });

  picker.addEventListener('change', event => {
    const input = event.target;
    if (input.checked && selected.size < 4) selected.add(input.value);
    if (!input.checked) selected.delete(input.value);
    updatePicker();
  });

  action.addEventListener('click', () => {
    if (!table.hidden) {
      showPicker();
      picker.querySelector('input:checked')?.focus();
      return;
    }
    const chosen = [...selected].map(url => apartments.find(item => item.source_url === url)).filter(Boolean);
    if (chosen.length < 2 || chosen.length > 4) return;
    const columns = chosen.map(item => {
      const url = safeExternalUrl(item.source_url);
      const link = Number.isSafeInteger(item.listing_id) && item.listing_id > 0
        ? `<a href="#listing-${item.listing_id}" data-compare-listing="${item.listing_id}">Карточка →</a>`
        : url ? `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">Объявление ↗</a>` : '';
      return `<th scope="col">${escapeHtml(apartmentName(item))}${link}</th>`;
    }).join('');
    table.innerHTML = `<table class="compare-table"><thead><tr><th scope="col">Параметр</th>${columns}</tr></thead><tbody>${fields.map(([label, value]) => `<tr><th scope="row">${label}</th>${chosen.map(item => `<td>${value(item)}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
    status.textContent = '';
    picker.hidden = true;
    table.hidden = false;
    action.textContent = 'Изменить выбор';
    table.focus();
  });

  table.addEventListener('click', event => {
    const link = event.target.closest('[data-compare-listing]');
    if (!link || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    dialog.close();
    jumpToSavedListing(Number(link.dataset.compareListing));
  });
}

const pendingLikes = new Map();

function likeControl(item, collection) {
  const url = item.url || item.source_url;
  const normalized = normalizeUrl(url);
  const key = normalized.startsWith('https://krisha.kz/a/show/') ? normalized.replace(/\/+$/, '') : url;
  const checked = pendingLikes.get(key) ?? item.liked;
  return `<div class="like-control"><label><input type="checkbox" data-like-key="${escapeHtml(key)}" data-like-path="/api/v1/${collection}/${Number(item.id)}/liked" ${checked ? 'checked' : ''} ${pendingLikes.has(key) ? 'disabled' : ''}> Понравилась</label><small role="status" hidden></small></div>`;
}

function updateLikeControls(key, liked, busy, error = '') {
  for (const input of document.querySelectorAll('input[data-like-key]')) {
    if (input.dataset.likeKey !== key) continue;
    input.checked = liked;
    input.disabled = busy;
    const status = input.closest('.like-control').querySelector('[role="status"]');
    status.textContent = error;
    status.hidden = !error;
  }
}

async function changeLike(event) {
  const input = event.target;
  if (!input.matches('input[data-like-path]')) return;
  const {likeKey, likePath} = input.dataset;
  if (pendingLikes.has(likeKey)) return;
  const desired = input.checked;
  const hadFocus = document.activeElement === input;
  let confirmed = !desired;
  let feedback = '';
  pendingLikes.set(likeKey, desired);
  updateLikeControls(likeKey, desired, true);
  try {
    const response = await fetch(likePath, {
      method:'PUT', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({liked:desired}), signal:AbortSignal.timeout(10000),
    });
    const result = await readJson(response);
    if (!response.ok || result?.liked !== desired) throw new Error('Отметка не подтверждена');
    confirmed = result.liked;
    await Promise.all([loadListings({reuseComparisons:true}), loadSearchResults()]);
  } catch {
    feedback = 'Не удалось сохранить отметку. Попробуйте ещё раз.';
  } finally {
    pendingLikes.delete(likeKey);
    updateLikeControls(likeKey, confirmed, false, feedback);
    if (hadFocus && document.activeElement === document.body) {
      const replacement = [...document.querySelectorAll('input[data-like-path]')].find(node => node.dataset.likePath === likePath);
      replacement?.focus({preventScroll:true});
    }
  }
}

function sortListings(listings) {
  const items = [...listings];
  const sort = listingSort?.value || 'newest';
  const pledgeOrder = item => item.is_pledged === false ? 0 : item.is_pledged === true ? 2 : 1;
  const sellerOrder = item => (sellerRoles.get(item.seller_category) || sellerRoles.get('unknown')).order;
  const newestFirst = (left, right) => String(right.created_at).localeCompare(String(left.created_at)) || right.id - left.id;
  const compare = (left, right) => {
    if (sort === 'pledge') return pledgeOrder(left) - pledgeOrder(right) || right.assessment.score - left.assessment.score || right.id - left.id;
    if (sort === 'seller') return sellerOrder(left) - sellerOrder(right) || newestFirst(left, right);
    if (sort === 'liked') return Number(right.liked === true) - Number(left.liked === true) || newestFirst(left, right);
    if (sort === 'score') return right.assessment.score - left.assessment.score || right.id - left.id;
    if (sort === 'price_m2') return left.assessment.price_per_m2 - right.assessment.price_per_m2 || left.id - right.id;
    if (sort === 'price') return left.price_kzt - right.price_kzt || left.id - right.id;
    return newestFirst(left, right);
  };
  return items.sort(compare);
}

function comparisonSummary(label, values, minSamples) {
  if (values?.status === 'city_unknown') {
    return `<div class="comparison-row"><span>${escapeHtml(label)}</span><span>Город не подтверждён как Астана</span></div>`;
  }
  if (!values || values.status === 'district_unknown') {
    return `<div class="comparison-row"><span>${escapeHtml(label)}</span><span>Район не указан</span></div>`;
  }
  if (values.status !== 'ok' || !Number.isFinite(values.average_price_per_m2) || values.average_price_per_m2 <= 0) {
    return `<div class="comparison-row"><span>${escapeHtml(label)}</span><span>Недостаточно данных (${Number(values.count) || 0} из ${minSamples})</span></div>`;
  }
  const diff = values.difference_percent;
  const description = Number.isFinite(diff)
    ? diff === 0 ? 'эта квартира на уровне выборки' : `эта квартира на ${number.format(Math.abs(diff))}% ${diff < 0 ? 'дешевле' : 'дороже'} выборки`
    : '';
  return `<div class="comparison-row"><span>${escapeHtml(label)} · ${values.count} объявл.</span><span><b>${escapeHtml(money.format(values.average_price_per_m2))}/м²</b>${description ? ` <small>(${escapeHtml(description)})</small>` : ''}</span></div>`;
}

function renderComparison(data) {
  const asOf = new Date(data.as_of);
  const dateLabel = Number.isNaN(asOf.getTime()) ? 'дата неизвестна' : dateTime.format(asOf);
  const district = data.district_label ? `Район: ${data.district_label}` : 'Район';
  const minSamples = Number(data.min_samples) || 5;
  const krisha = data.krisha || {};
  const hasKrisha = krisha.district?.status === 'ok' || krisha.city?.status === 'ok';
  const observed = new Date(krisha.observed_at);
  const observedLabel = !krisha.observed_at || Number.isNaN(observed.getTime()) ? 'неизвестно' : dateTime.format(observed);
  const referenceRow = (label, value) => {
    if (value?.status !== 'ok' || !Number.isFinite(value.average_price_per_m2)) return `<div class="comparison-row"><span>${escapeHtml(label)}</span><span>Не получено со страницы</span></div>`;
    const diff = value.difference_percent;
    const difference = Number.isFinite(diff) ? diff === 0 ? 'эта квартира на уровне среднего' : `эта квартира на ${number.format(Math.abs(diff))}% ${diff < 0 ? 'дешевле' : 'дороже'} среднего` : '';
    return `<div class="comparison-row"><span>${escapeHtml(label)}</span><span><b>${escapeHtml(money.format(value.average_price_per_m2))}/м²</b> <small>${escapeHtml(difference)}</small></span></div>`;
  };
  const sourceUrl = safeExternalUrl(krisha.source_url);
  const krishaBlock = hasKrisha ? `<div class="krisha-comparison"><b>Средние из блока Krisha</b>
    ${referenceRow(krisha.district_label ? `Район в блоке Krisha: ${krisha.district_label}` : 'Район в блоке Krisha', krisha.district)}
    ${referenceRow('Астана', krisha.city)}
    <small>Получено со страницы: ${escapeHtml(observedLabel)}. Период и размер выборки источником не переданы.${sourceUrl ? ` <a href="${escapeHtml(sourceUrl)}" target="_blank" rel="noopener noreferrer">Источник →</a>` : ''}</small></div>` : '<p>Средние Krisha со страницы объявления пока не получены.</p>';
  return `<b>Сравнение цены за м²</b>
    ${krishaBlock}
    <b>По сохранённым объявлениям</b>
    <p>Среднее по другим сохранённым объявлениям за ${Number(data.lookback_days) || 90} дней; это не средняя цена рынка.</p>
    ${comparisonSummary(district, data.district, minSamples)}
    ${comparisonSummary('Астана', data.city, minSamples)}
    <small>Срез: ${escapeHtml(dateLabel)}. Цена предложения; объявления могут быть подобраны по вашим фильтрам.</small>`;
}

function renderDuplicateOffers(offers, currentId) {
  return `<ul class="duplicate-offers">${offers.map(offer => {
    const url = safeExternalUrl(offer.source_url);
    const title = escapeHtml(offer.address || offer.title || 'Объявление');
    const price = Number.isFinite(offer.price_kzt) ? money.format(offer.price_kzt) : 'Цена не указана';
    const evidence = offer.evidence?.kind === 'shared_photos'
      ? `Общих фото: ${number.format(offer.evidence.shared_photos)}`
      : offer.evidence?.kind === 'same_house_floor_area' ? 'Тот же дом и этаж, близкая площадь' : '';
    return `<li>${url ? `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${title} ↗</a>` : title}
      <div class="duplicate-facts"><b>${escapeHtml(price)}</b>${sellerBadge(offer)}${availabilityBadge(offer)}
      ${offer.listing_id === currentId ? '<span class="meta">Это объявление</span>' : ''}</div>
      ${evidence ? `<small class="meta">${escapeHtml(evidence)}</small>` : ''}</li>`;
  }).join('')}</ul>`;
}

function renderClosestComparables(data) {
  const renderGroup = (heading, group, showDistance) => {
    const missing = {
      complex_unknown:'Название ЖК не указано.',
      coordinates_unknown:'Координаты квартиры не указаны.',
      city_unknown:'Подбор аналогов доступен для Астаны.',
    }[group.status];
    if (missing) return `<section class="comparable-group"><h4>${heading}</h4><p>${missing}</p></section>`;
    const items = group.items.map(item => {
      const url = safeExternalUrl(item.source_url);
      const title = escapeHtml(item.address || item.title || 'Объявление');
      const meta = [Number.isFinite(item.area_m2) ? `${number.format(item.area_m2)} м²` : null,
                    showDistance && Number.isFinite(item.distance_m) ? `${number.format(item.distance_m)} м` : null].filter(Boolean);
      const variants = Array.isArray(item.offers) && item.offers.length > 1
        ? `<details class="more-comparables"><summary>Все объявления (${number.format(item.offers.length)})</summary>${renderDuplicateOffers(item.offers, null)}</details>` : '';
      return `<li>${url ? `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${title} ↗</a>` : title}
        <div class="comparable-facts"><b>${escapeHtml(money.format(item.price_per_m2))}/м²</b>${meta.length ? `<span>${escapeHtml(meta.join(' · '))}</span>` : ''}</div>${variants}</li>`;
    });
    const rest = items.slice(5);
    return `<section class="comparable-group"><h4>${heading} <span>${number.format(group.count)} в выборке</span></h4>
      ${items.length ? `<ul>${items.slice(0, 5).join('')}</ul>` : '<p>Подходящих объявлений пока нет.</p>'}
      ${rest.length ? `<details class="more-comparables"><summary>Ещё ${number.format(rest.length)} объявл.</summary><ul>${rest.join('')}</ul></details>` : ''}</section>`;
  };
  const radius = data.radius_m >= 1000 ? `${number.format(data.radius_m / 1000)} км` : `${number.format(data.radius_m)} м`;
  return `<p class="comparable-criteria">Сохранённые объявления за ${number.format(data.lookback_days)} дней. Столько же комнат, площадь ±${number.format(data.area_tolerance_percent)}%. Соседние дома в радиусе ${radius}.</p>
    ${renderGroup('В том же ЖК', data.same_complex, false)}
    ${renderGroup('В соседних домах', data.nearby, true)}`;
}

async function loadClosestComparables(details) {
  const content = details.querySelector('[data-comparables-content]');
  if (!content || content.dataset.state === 'loading' || content.dataset.state === 'loaded') return;
  content.dataset.state = 'loading';
  content.innerHTML = '<p class="meta" role="status">Подбираю ближайшие аналоги…</p>';
  try {
    const id = encodeURIComponent(details.dataset.comparablesId);
    const response = await fetch(`/api/v1/listings/${id}/comparison?include_comparables=true`, {signal:AbortSignal.timeout(10000)});
    if (!response.ok) throw new Error('Не удалось загрузить аналоги');
    const data = await readJson(response);
    if (!details.isConnected) return;
    content.innerHTML = renderClosestComparables(data.comparables);
    content.dataset.state = 'loaded';
  } catch {
    if (!details.isConnected) return;
    content.dataset.state = 'error';
    content.innerHTML = '<p class="meta" role="alert">Не удалось загрузить аналоги.</p><button type="button" class="panel-action" data-comparables-retry>Повторить</button>';
  }
}

async function loadDuplicateOffers(details) {
  const content = details.querySelector('[data-duplicates-content]');
  if (!content || content.dataset.state === 'loading' || content.dataset.state === 'loaded') return;
  content.dataset.state = 'loading';
  content.innerHTML = '<p class="meta" role="status">Проверяю сохранённые объявления…</p>';
  try {
    const id = encodeURIComponent(details.dataset.duplicatesId);
    const response = await fetch(`/api/v1/listings/${id}/duplicates`, {signal:AbortSignal.timeout(10000)});
    if (!response.ok) throw new Error('Не удалось загрузить дубли');
    const data = await readJson(response);
    if (!details.isConnected) return;
    content.innerHTML = data.items.length > 1
      ? `<p class="comparable-criteria">Вероятное совпадение · ${number.format(data.items.length)} в группе</p>${renderDuplicateOffers(data.items, data.listing_id)}`
      : '<p class="comparable-criteria">В сохранённых карточках вероятных дублей пока нет.</p>';
    content.dataset.state = 'loaded';
  } catch {
    if (!details.isConnected) return;
    content.dataset.state = 'error';
    content.innerHTML = '<p class="meta" role="alert">Не удалось проверить дубли.</p><button type="button" class="panel-action" data-duplicates-retry>Повторить</button>';
  }
}

grid?.addEventListener('toggle', event => {
  const details = event.target;
  if (details.matches('details[data-comparables-id]') && details.open) void loadClosestComparables(details);
  if (details.matches('details[data-duplicates-id]') && details.open) void loadDuplicateOffers(details);
}, true);
grid?.addEventListener('click', event => {
  const retry = event.target.closest('[data-comparables-retry]');
  if (retry) void loadClosestComparables(retry.closest('details[data-comparables-id]'));
  const retryDuplicates = event.target.closest('[data-duplicates-retry]');
  if (retryDuplicates) void loadDuplicateOffers(retryDuplicates.closest('details[data-duplicates-id]'));
  const sale = event.target.closest('[data-sale-id]');
  if (sale) void changeSaleStatus(sale);
});

async function loadComparisons(previous = null) {
  const nodes = [...(grid?.querySelectorAll('.price-comparison[data-listing-id]') || [])];
  await Promise.all(nodes.map(async node => {
    const cached = previous?.get(node.dataset.listingId);
    if (cached) {
      node.replaceWith(cached);
      return;
    }
    try {
      const response = await fetch(`/api/v1/listings/${encodeURIComponent(node.dataset.listingId)}/comparison`);
      if (!response.ok) throw new Error('Не удалось загрузить сравнение');
      const data = await readJson(response);
      if (node.isConnected) {
        node.innerHTML = renderComparison(data);
        node.dataset.loaded = 'true';
      }
    } catch {
      if (node.isConnected) node.textContent = 'Не удалось загрузить сравнение цены.';
    }
  }));
}

function render(listings) {
  const openDetails = new Set();
  grid?.querySelectorAll('article[id^="listing-"] details[data-details-key][open]').forEach(detail => {
    openDetails.add(`${detail.closest('article').id}:${detail.dataset.detailsKey}`);
  });
  savedUrls = new Set(listings.map(item => normalizeUrl(item.source_url)));
  savedListingIds = new Map(listings.map(item => [normalizeUrl(item.source_url), item.id]));
  const minScore = Number(filter?.value || 0);
  const seller = listingSellerFilter?.value || 'all';
  const pinnedId = /^#listing-([1-9]\d*)$/.exec(location.hash)?.[1];
  const inactive = item => availabilityLabels.has(item.availability);
  const visible = sortListings(listings).filter(item => item.assessment.score >= minScore
    && (seller === 'all' || (item.seller_category || 'unknown') === seller)
    && (!listingLeftBankOnly?.checked || item.river_bank === 'left')
    && (listingShowInactive?.checked || !inactive(item) || String(item.id) === pinnedId)
    && (listingShowPledged?.checked || item.is_pledged !== true || String(item.id) === pinnedId));
  const hiddenInactive = listingShowInactive?.checked ? 0 : listings.filter(item => inactive(item) && String(item.id) !== pinnedId).length;
  if (listingAvailabilitySummary) {
    listingAvailabilitySummary.hidden = hiddenInactive === 0;
    listingAvailabilitySummary.textContent = hiddenInactive ? `Скрыто проданных и снятых с публикации: ${hiddenInactive}.` : '';
  }
  const hiddenPledged = listingShowPledged?.checked ? 0 : listings.filter(item => item.is_pledged === true && String(item.id) !== pinnedId).length;
  if (listingPledgeSummary) {
    listingPledgeSummary.hidden = hiddenPledged === 0;
    listingPledgeSummary.textContent = hiddenPledged ? `Скрыто квартир в залоге: ${hiddenPledged}.` : '';
  }
  const strong = listings.filter(item => !inactive(item) && item.assessment.score >= 85);
  document.querySelector('#totalCount').textContent = listings.length;
  document.querySelector('#strongCount').textContent = strong.length;
  if (!grid) return;
  if (!visible.length) {
    grid.innerHTML = hiddenInactive > 0 && hiddenInactive === listings.length
      ? '<section class="empty card"><strong>Все сохранённые квартиры проданы или сняты с публикации</strong><p>Включите «Показывать проданные и снятые», чтобы увидеть карточки.</p></section>'
      : listingLeftBankOnly?.checked && listings.length
      ? '<section class="empty card"><strong>По текущим условиям квартир на левом берегу нет</strong><p>Снимите галочку «Только левый берег», чтобы посмотреть остальные карточки.</p></section>'
      : seller !== 'all' && listings.length
      ? '<section class="empty card"><strong>Нет квартир с выбранным типом продавца</strong><p>Выберите «Все продавцы», чтобы увидеть остальные карточки.</p></section>'
      : hiddenPledged && hiddenPledged === listings.length
      ? '<section class="empty card"><strong>Все сохранённые квартиры в залоге</strong><p>Включите «Показать сохранённые квартиры в залоге», чтобы посмотреть их.</p></section>'
      : listings.length
      ? '<section class="empty card"><strong>Нет квартир с выбранным рейтингом</strong><p>Выберите «Все», чтобы увидеть остальные квартиры.</p></section>'
      : '<section class="empty card"><strong>Сохранённых квартир пока нет</strong><p>Добавьте квартиру вручную или импортируйте объявление.</p></section>';
    return;
  }
  grid.innerHTML = visible.map(item => {
    const sourceUrl = safeExternalUrl(item.source_url);
    const title = item.residential_complex || item.title;
    const location = [item.address, item.district, item.city].filter(Boolean).join(' · ');
    const hasCoordinates = Number.isFinite(item.latitude) && Number.isFinite(item.longitude)
      && item.latitude >= -90 && item.latitude <= 90
      && item.longitude >= -180 && item.longitude <= 180;
    const locationMissing = !item.address && !hasCoordinates;
    const mapUrl = hasCoordinates
      ? `https://yandex.ru/maps/?ll=${encodeURIComponent(`${item.longitude},${item.latitude}`)}&z=16&pt=${encodeURIComponent(`${item.longitude},${item.latitude},pm2rdm`)}`
      : item.address ? `https://yandex.ru/maps/?text=${encodeURIComponent([item.city, item.address].filter(Boolean).join(', '))}` : null;
    const mapWidgetUrl = hasCoordinates
      ? `https://yandex.ru/map-widget/v1/?ll=${encodeURIComponent(`${item.longitude},${item.latitude}`)}&z=16&pt=${encodeURIComponent(`${item.longitude},${item.latitude},pm2rdm`)}&l=map`
      : null;
    const scoreClass = item.assessment.score < 85 ? 'medium' : '';
    const photos = [...new Set((Array.isArray(item.photo_urls) ? item.photo_urls : []).map(safeExternalUrl).filter(Boolean))];
    const previewPhotos = photos.slice(0, 4);
    const ai = item.ai_analysis || {};
    const history = item.price_history || [];
    const latestChange = history[0]?.change_kzt;
    const propertyDetails = item.property_details && typeof item.property_details === 'object' && !Array.isArray(item.property_details)
      ? Object.entries(item.property_details).filter(([key, value]) => key.trim() && typeof value === 'string' && value.trim()) : [];
    const complexDescriptionUrl = safeExternalUrl(item.complex_description_source_url);
    const complexDescriptionSource = ({listing:'Объявление', developer:'Сайт застройщика', manual:'Ручной ввод'})[item.complex_description_source_kind] || 'Источник не указан';
    const twogisRating = Number(item.twogis_rating);
    const twogisReviews = Number(item.twogis_review_count || 0);
    const twogisLine = item.twogis_name ? `<div class="twogis-review"><b>2GIS</b> ${Number.isFinite(twogisRating) && twogisReviews ? `★ ${twogisRating.toFixed(1)}/5 · ${twogisReviews} отзывов` : 'оценка и отзывы не опубликованы'}${item.twogis_url ? ` · <a href="${escapeHtml(item.twogis_url)}" target="_blank" rel="noopener noreferrer">Открыть в 2GIS →</a>` : ''}</div>` : '';
    return `<article class="listing card" id="listing-${item.id}" tabindex="-1" aria-label="Карточка квартиры: ${escapeHtml(title)}">
      ${previewPhotos.length ? `<div class="listing-photos">${previewPhotos.map((photo, index) => `<img class="listing-photo ${index ? 'thumbnail' : ''}" src="${escapeHtml(photo)}" alt="Фото квартиры ${index + 1}" loading="lazy" referrerpolicy="no-referrer">`).join('')}</div>` : ''}
      ${photos.length ? `<details class="listing-all-photos" data-details-key="photos"><summary>Все фотографии (${photos.length})</summary><div class="listing-gallery">${photos.map((photo, index) => `<a href="${escapeHtml(photo)}" target="_blank" rel="noopener noreferrer" aria-label="Открыть фото ${index + 1} в новой вкладке"><img src="${escapeHtml(photo)}" alt="Фото квартиры ${index + 1}" loading="lazy" referrerpolicy="no-referrer"><span>Фото ${index + 1} ↗</span></a>`).join('')}</div></details>` : ''}
      <div class="listing-top"><div><h3>${escapeHtml(title)}</h3><div class="meta">${escapeHtml(location)}</div></div><div class="rating ${scoreClass}">${item.assessment.score}</div></div>
      <div class="price">${money.format(item.price_kzt)}</div>
      <div class="meta">${item.rooms} комн. · ${number.format(item.area_m2)} м² · ${money.format(item.assessment.price_per_m2)}/м² · этаж ${item.floor || '—'}/${item.floors_total || '—'}</div>
      <div class="listing-bank">${riverBankBadge(item)}${availabilityBadge(item)}</div>
      ${likeControl(item, 'listings')}
      <section class="price-comparison" data-listing-id="${Number(item.id)}" aria-label="Сравнение цены за квадратный метр">Загружаю сравнение цены…</section>
      <details class="listing-comparables" data-details-key="closest-comparables" data-comparables-id="${Number(item.id)}"><summary>Ближайшие аналоги</summary><div data-comparables-content></div></details>
      <details class="listing-comparables" data-details-key="duplicate-offers" data-duplicates-id="${Number(item.id)}"><summary>Вероятные дубли</summary><div data-duplicates-content></div></details>
      ${mapWidgetUrl ? `<div class="listing-map"><iframe src="${escapeHtml(mapWidgetUrl)}" title="Расположение квартиры: ${escapeHtml(title)}" loading="lazy" allowfullscreen></iframe></div>` : ''}
      ${mapUrl ? `<a class="location-link" href="${escapeHtml(mapUrl)}" target="_blank" rel="noopener noreferrer">${hasCoordinates ? 'Открыть большую карту' : 'Найти адрес на карте'} →</a>${hasCoordinates ? '<p class="meta map-fallback">Если карта не загрузилась, воспользуйтесь ссылкой выше.</p>' : '<p class="meta">Координаты не получены. Карта найдёт дом по указанному адресу.</p>'}` : ''}
      ${locationMissing ? '<div class="meta location-missing">Адрес и координаты не указаны</div>' : ''}
      <div class="listing-details"><div>${bathroomBadge(item)}</div>${repairDetails(item)}${mortgageDetails(item)}<div><b>Залог:</b> ${pledgeBadge(item)}</div><div class="listing-author"><b>Продавец</b>${sellerBadge(item)}${item.seller_name ? `<span>${escapeHtml(item.seller_name)}</span>` : ''}</div></div>
      <details class="purchase-checklist" data-details-key="purchase-checklist">
        <summary>Проверка перед покупкой · eGov</summary>
        <ul>
          <li>Уточните, кто собственник и на каком основании действует продавец.</li>
          <li>Проверьте документы на квартиру, зарегистрированные права и обременения.</li>
          <li>Сверьте адрес, площадь и планировку с техническими документами.</li>
          <li>Уточните, нужны ли согласия других собственников или супруга.</li>
        </ul>
        <a href="https://beta.egov.kz/situations/10/49?lang=ru" target="_blank" rel="noopener noreferrer">Открыть инструкцию eGov ↗</a>
      </details>
      ${item.building_year || item.building_type ? `<div class="meta">${item.building_year ? `Построен ${item.building_year}` : ''}${item.building_year && item.building_type ? ' · ' : ''}${escapeHtml(item.building_type || '')}</div>` : ''}
      ${twogisLine}
      <details class="seller-description" data-details-key="seller-description"><summary>Описание объявления</summary>${item.description ? `<p class="seller-description-text">${escapeHtml(item.description)}</p>` : '<p class="meta">Описание объявления не получено.</p>'}</details>
      <details class="property-details" data-details-key="property-details"><summary>Все характеристики (${propertyDetails.length})</summary>${propertyDetails.length ? `<dl class="property-details-list">${propertyDetails.map(([key, value]) => `<div><dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd></div>`).join('')}</dl>` : '<p class="meta">Дополнительные характеристики не получены.</p>'}${item.details_imported_at ? `<p class="meta">Характеристики получены ${escapeHtml(dateTime.format(new Date(item.details_imported_at)))}</p>` : ''}</details>
      <details class="complex-description" data-details-key="complex-description"><summary>Описание ЖК</summary>${item.complex_description ? `<p style="white-space:pre-wrap">${escapeHtml(item.complex_description)}</p><p class="meta">${escapeHtml(complexDescriptionSource)}${complexDescriptionUrl ? ` · <a href="${escapeHtml(complexDescriptionUrl)}" target="_blank" rel="noopener noreferrer">Источник →</a>` : ''}${item.complex_description_observed_at ? ` · получено ${escapeHtml(dateTime.format(new Date(item.complex_description_observed_at)))}` : ''}</p>` : '<p class="meta">Описание ЖК не получено. Его можно добавить из объявления, сайта застройщика или вручную.</p>'}</details>
      ${latestChange ? `<div class="price-change ${latestChange < 0 ? 'down' : 'up'}">${latestChange < 0 ? '↓' : '↑'} ${money.format(Math.abs(latestChange))} между последними записями</div>` : ''}
      <details class="price-history" data-details-key="price-history"><summary>История цены (${history.length})</summary>${history.length ? `<ol>${history.map(snapshot => `<li>${escapeHtml(dateTime.format(new Date(snapshot.observed_at)))} — ${escapeHtml(money.format(snapshot.price_kzt))}${snapshot.change_kzt ? ` (${snapshot.change_kzt > 0 ? '+' : '−'}${escapeHtml(money.format(Math.abs(snapshot.change_kzt)))})` : ''}</li>`).join('')}</ol>` : '<p>Пока нет записей о цене.</p>'}</details>
      <span class="verdict">${escapeHtml(item.assessment.verdict)}</span>
      ${ai.summary ? `<div class="ai-summary"><b>AI-анализ</b><p>${escapeHtml(ai.summary)}</p></div>` : ''}
      <div class="listing-actions"><button class="check-listing secondary-action" data-listing-id="${item.id}" type="button">↻ Проверить квартиру</button>
        <button class="panel-action sale-action" data-sale-id="${Number(item.id)}" data-mark-sold="${item.availability !== 'sold'}" type="button">${item.availability === 'sold' ? 'Снять отметку «Продана»' : 'Отметить проданной'}</button></div>
      ${sourceUrl ? `<a href="${escapeHtml(sourceUrl)}" target="_blank" rel="noopener noreferrer">Открыть объявление →</a>` : ''}
    </article>`;
  }).join('');
  grid.querySelectorAll('article[id^="listing-"] details[data-details-key]').forEach(detail => {
    detail.open = openDetails.has(`${detail.closest('article').id}:${detail.dataset.detailsKey}`);
  });
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

async function changeSaleStatus(button) {
  const id = Number(button.dataset.saleId);
  const sold = button.dataset.markSold === 'true';
  const label = button.textContent;
  button.disabled = true;
  button.textContent = 'Сохраняю…';
  try {
    const response = await fetch(`/api/v1/listings/${id}/sold`, {
      method:'PUT', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({sold}), signal:AbortSignal.timeout(10000),
    });
    if (!response.ok) throw new Error('Не удалось сохранить статус');
    if (listingAvailabilityMessage) listingAvailabilityMessage.textContent = sold ? 'Квартира отмечена как проданная.' : 'Отметка «Продана» снята.';
    await Promise.all([loadListings(), loadSearchResults(), loadSearchStatus()]);
  } catch {
    if (listingAvailabilityMessage) listingAvailabilityMessage.textContent = 'Не удалось изменить статус квартиры.';
  } finally {
    button.disabled = false;
    button.textContent = label;
  }
}

async function loadListings({reuseComparisons = false} = {}) {
  const revision = ++listingsRevision;
  if (refreshButton) refreshButton.disabled = true;
  try {
    const response = await fetch('/api/v1/listings');
    if (!response.ok) throw new Error('Не удалось загрузить квартиры');
    const listings = await readJson(response);
    if (revision !== listingsRevision) return false;
    const loaded = reuseComparisons ? [...(grid?.querySelectorAll('.price-comparison[data-loaded]') || [])] : [];
    const previous = new Map(loaded.map(node => [node.dataset.listingId, node]));
    render(listings);
    grid?.querySelectorAll('.check-listing').forEach(button => {
      button.addEventListener('click', () => checkListingNow(Number(button.dataset.listingId), button));
    });
    await loadComparisons(previous);
    return true;
  } catch (error) {
    if (revision === listingsRevision && message) message.textContent = error.message;
    return false;
  } finally {
    if (revision === listingsRevision && refreshButton) refreshButton.disabled = false;
  }
}

async function openSavedListingFromHash() {
  const match = /^#listing-([1-9]\d*)$/.exec(location.hash);
  if (!match) return;
  // Reveal a directly linked card even when the current filters hide it.
  if (filter) filter.value = '';
  if (listingSellerFilter) listingSellerFilter.value = 'all';
  if (listingLeftBankOnly) listingLeftBankOnly.checked = false;
  if (!await loadListings()) return;
  const card = document.getElementById(`listing-${match[1]}`);
  if (!card) {
    if (searchMessage) searchMessage.textContent = 'Сохранённая карточка не найдена. Обновите результаты поиска.';
    return;
  }
  const history = card.querySelector('.price-history');
  if (history) history.open = true;
  card.focus({preventScroll:true});
  card.scrollIntoView({behavior:'auto', block:'start'});
}

function jumpToSavedListing(id) {
  if (!Number.isSafeInteger(id) || id < 1) return;
  const hash = `#listing-${id}`;
  if (location.hash === hash) void openSavedListingFromHash();
  else location.hash = hash;
}

window.addEventListener('hashchange', () => { void openSavedListingFromHash(); });
searchResults?.addEventListener('click', event => {
  const link = event.target.closest('a.open-saved-card');
  if (!link || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
  event.preventDefault();
  if (location.hash !== link.hash) history.pushState(null, '', link.hash);
  void openSavedListingFromHash();
});

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
    await loadSearchResults();
  } catch (error) {
    button.textContent = error.message;
    button.classList.add('button-error');
    setTimeout(() => { button.textContent = 'Повторить сохранение'; button.disabled = false; button.classList.remove('button-error'); }, 3500);
  }
}

async function loadSearchResults() {
  if (!searchResults) return;
  const revision = ++searchResultsRevision;
  const seller = searchSellerFilter?.value || 'all';
  const params = new URLSearchParams({limit:'200', only_new:'false', sort:searchResultsSort?.value || 'newest', seller});
  const leftBankOnly = searchLeftBankOnly?.checked;
  if (leftBankOnly) params.set('bank', 'left');
  try {
    const [response, statusResponse] = await Promise.all([
      fetch(`/api/v1/search/results?${params}`),
      fetch('/api/v1/search/status')
    ]);
    if (!response.ok) throw new Error('Не удалось загрузить найденные ссылки');
    if (!statusResponse.ok) throw new Error('Не удалось получить параметры поиска');
    const found = await readJson(response);
    const status = await readJson(statusResponse);
    if (revision !== searchResultsRevision) return;
    const results = status.exclude_pledged === false ? found : found.filter(item => item.analysis?.is_pledged !== true);
    const strictBathrooms = status.min_bathrooms === 2;
    const items = strictBathrooms
      ? results.filter(item => Number.isInteger(item.analysis?.bathrooms_count) && item.analysis.bathrooms_count >= 2)
      : results;
    if (!items.length) {
      searchResults.innerHTML = leftBankOnly
        ? '<section class="empty card"><strong>По текущим условиям объявлений на левом берегу нет</strong><p>Берег определяется по координатам в карточке. Снимите галочку, чтобы увидеть остальные объявления.</p></section>'
        : seller === 'owner'
        ? '<section class="empty card"><strong>По текущим условиям объявлений от собственников пока нет</strong><p>Нажмите «Искать сейчас», чтобы проверить выдачу Krisha с фильтром «От хозяев».</p></section>'
        : seller !== 'all'
        ? '<section class="empty card"><strong>Нет объявлений с выбранным типом продавца</strong></section>'
        : strictBathrooms
        ? '<section class="empty card"><strong>Квартир с подтверждёнными двумя санузлами пока нет</strong><p>При отборе «От 2 санузлов» объявления без подтверждённого количества скрыты. Подходящие квартиры появятся после получения данных объявления.</p></section>'
        : '<section class="empty card"><strong>Новых ссылок пока нет</strong><p>Новые объявления появятся после следующей публикации на Krisha.</p></section>';
      return;
    }
    searchResults.innerHTML = items.map(item => {
      const sourceUrl = safeExternalUrl(item.url);
      const a = item.analysis || {};
      const facts = [
        a.price_is_lower_bound ? `от ${money.format(a.price_lower_bound_kzt)} · точная цена не подтверждена` : a.price_kzt ? money.format(a.price_kzt) : 'Цена не подтверждена',
        a.area_m2 ? `${number.format(a.area_m2)} м²` : null,
        a.mortgage_supported === false ? 'Ипотека: недоступна' : null,
        a.price_per_m2 ? `${a.price_is_lower_bound ? 'от ' : ''}${money.format(a.price_per_m2)}/м²` : null,
        a.floor ? `${a.floor}/${a.floors_total || '—'} этаж` : null
      ].filter(Boolean);
      const reasons = (a.reasons || []).slice(0, 3).map(reason => `<li>✓ ${escapeHtml(reason)}</li>`).join('');
      const isSaved = savedUrls.has(normalizeUrl(item.url));
      const savedId = savedListingIds.get(normalizeUrl(item.url)) || item.imported_listing_id;
      const hasSavedCard = Number.isSafeInteger(savedId) && savedId > 0;
      const importStatus = item.import_status || 'pending';
      const imported = importStatus === 'imported' || isSaved;
      const importLabel = imported ? 'Сохранено в карточку' : importStatus === 'queued' ? 'Мониторинг запущен' : importStatus === 'blocked' ? 'Источник ограничил импорт' : importStatus === 'error' ? 'Импорт не удался — повторить' : 'Сохранить объявление';
      const seenLabel = item.status === 'seen' ? 'Просмотрено' : priorityLabel(item.priority);
      return `<article class="listing card ${priorityClass(item.priority)} ${item.status === 'seen' ? 'result-seen' : ''}">
        <div class="listing-top">
          <div><span class="verdict">${escapeHtml(seenLabel)}</span><h3>${escapeHtml(cleanSearchTitle(item.title))}</h3></div>
          <div class="rating ${a.score < 85 ? 'medium' : ''}">${a.score ?? '—'}</div>
        </div>
        <div class="search-facts">${facts.map(value => `<span>${escapeHtml(value)}</span>`).join('')}${typeof a.is_pledged === 'boolean' ? (a.is_pledged ? pledgeBadge(a) : '<span>Нет залога</span>') : ''}${bathroomBadge(a, true)}${sellerBadge(item)}${riverBankBadge(item)}</div>
        ${likeControl(item, 'search/results')}
        ${reasons ? `<ul class="reason-list">${reasons}</ul>` : `<p class="meta">${escapeHtml(item.snippet || 'Откройте объявление для проверки параметров.')}</p>`}
        <div class="meta source-meta">Источник: ${escapeHtml(item.search_engine)} · обнаружено ${dateTime.format(new Date(item.first_seen))}</div>
        <div class="card-actions">
          ${imported && hasSavedCard ? `<a class="open-saved-card secondary-action" href="#listing-${savedId}">Открыть карточку →</a>` : `<button class="enrich-result primary secondary-action ${imported ? 'saved' : ''}" data-url="${escapeHtml(item.url)}" type="button" ${imported ? 'disabled' : ''}>${importLabel}</button>`}
          ${sourceUrl ? `<a class="search-open" data-result-id="${item.id}" href="${escapeHtml(sourceUrl)}" target="_blank" rel="noopener noreferrer">Открыть и мониторить →</a>` : ''}
        </div>
      </article>`;
    }).join('');
    searchResults.querySelectorAll('.search-open').forEach(link => link.addEventListener('click', event => {
      void monitorBeforeOpen(link);
    }));
    searchResults.querySelectorAll('.enrich-result:not(.saved)').forEach(button => button.addEventListener('click', () => importSearchResult(button.dataset.url, button)));
  } catch (error) {
    if (revision === searchResultsRevision && searchMessage) searchMessage.textContent = error.message;
  }
}

searchResultsSort?.addEventListener('change', () => { void loadSearchResults(); });
searchSellerFilter?.addEventListener('change', () => { void loadSearchResults(); });
searchLeftBankOnly?.addEventListener('change', () => { void loadSearchResults(); });
searchResults?.addEventListener('change', changeLike);
grid?.addEventListener('change', changeLike);

function monitorBeforeOpen(link) {
  const resultId = link.dataset.resultId;
  link.classList.add('is-monitoring');
  link.setAttribute('aria-busy', 'true');
  link.textContent = 'Ставлю на мониторинг…';
  // Let the browser open the link in a new tab without waiting for monitoring.
  const monitorRequest = fetch(`/api/v1/search/results/${resultId}/monitor`, {
    method:'POST',
    keepalive:true,
  }).then(response => {
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
  }).catch(() => markSeen(resultId).catch(() => {}));

  void monitorRequest;
}

async function loadSearchStatus() {
  try {
    const response = await fetch('/api/v1/search/status');
    if (!response.ok) throw new Error('Не удалось получить статус поиска');
    const status = await readJson(response);
    if (searchExcludePledged && !searchPledgeInitialized && typeof status.exclude_pledged === 'boolean') {
      searchExcludePledged.checked = status.exclude_pledged;
      searchPledgeInitialized = true;
    }
    if (searchBathrooms && !searchBathroomsInitialized && [0, 2].includes(status.min_bathrooms)) {
      searchBathrooms.checked = status.min_bathrooms === 2;
      searchBathroomsInitialized = true;
    }
    if (searchRooms && !searchRoomsInitialized && [2, 3, 4].includes(status.rooms)) {
      searchRooms.value = String(status.rooms);
      searchRoomsInitialized = true;
      updateCriteriaRooms();
    }
    if (searchMinPrice && !searchMinPriceInitialized && (status.min_price_kzt === null || Number.isSafeInteger(status.min_price_kzt) && status.min_price_kzt > 0)) {
      searchMinPrice.value = status.min_price_kzt === null ? '' : String(status.min_price_kzt);
      searchMinPriceInitialized = true;
    }
    if (searchMaxPrice && !searchPriceInitialized && Number.isInteger(status.max_price_kzt) && status.max_price_kzt > 0) {
      searchMaxPrice.value = String(status.max_price_kzt);
      searchPriceInitialized = true;
    }
    updateCriteriaPrice();
    if (searchMinArea && !searchMinAreaInitialized && Number.isFinite(status.min_area_m2) && status.min_area_m2 > 0) {
      searchMinArea.value = String(status.min_area_m2);
      searchMinAreaInitialized = true;
    }
    if (searchMaxArea && !searchMaxAreaInitialized && (status.max_area_m2 === null || Number.isFinite(status.max_area_m2) && status.max_area_m2 > 0)) {
      searchMaxArea.value = status.max_area_m2 === null ? '' : String(status.max_area_m2);
      searchMaxAreaInitialized = true;
    }
    updateCriteriaArea();
    document.querySelector('#newSearchCount').textContent = status.new_results || 0;
    document.querySelector('#foundHour').textContent = status.found_last_hour || 0;
    document.querySelector('#foundToday').textContent = status.found_today || 0;
    document.querySelector('#urgentCount').textContent = status.urgent_results || 0;
    document.querySelector('#importedCount').textContent = status.imported_results || 0;
    const providers = (status.providers || []).join(', ') || 'нет';
    const lastRun = status.last_run ? dateTime.format(new Date(status.last_run)) : 'ещё не выполнялся';
    if (searchMeta) searchMeta.textContent = `Источники: ${providers}. Запросов за запуск: ${status.queries_per_run}. Последний поиск: ${lastRun}.${status.min_bathrooms === 2 ? ` Скрыто объявлений без данных о санузлах: ${queueCount(status.bathrooms_unconfirmed_results)}.` : ''}${status.exclude_pledged ? ` Скрыто квартир в залоге: ${queueCount(status.pledged_hidden_results)}.` : ''}`;
    const telegram = await fetch('/api/v1/notifications/status').then(readJson);
    const telegramNode = document.querySelector('#telegramStatus');
    if (telegramNode) telegramNode.textContent = telegram.configured ? `Telegram: подключён · отправлено ${telegram.sent_notifications || 0}` : 'Telegram: добавьте TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID для уведомлений';
    const health = await fetch('/health').then(readJson);
    const versionLabel = document.querySelector('#appVersion');
    if (versionLabel && typeof health.version === 'string') versionLabel.textContent = ` · v${health.version}`;
    const twogisNode = document.querySelector('#twogisStatus');
    if (twogisNode) twogisNode.textContent = health.twogis_configured ? '2GIS: автоматический поиск ЖК и отзывов включён' : '2GIS: добавьте TWOGIS_API_KEY для автоматического рейтинга ЖК';
  } catch (error) {
    if (searchMessage) searchMessage.textContent = error.message;
  }
}

runSearchButton?.addEventListener('click', async () => {
  if (!searchMinPrice?.checkValidity()) {
    if (searchMessage) searchMessage.textContent = 'Введите целую минимальную цену от 1 до 10 000 000 000 ₸ или оставьте поле пустым.';
    searchMinPrice?.reportValidity();
    return;
  }
  if (!searchMaxPrice?.checkValidity()) {
    if (searchMessage) searchMessage.textContent = 'Введите целую максимальную цену от 1 до 10 000 000 000 ₸.';
    searchMaxPrice?.reportValidity();
    return;
  }
  const minimumPrice = searchMinPrice.value === '' ? null : Number(searchMinPrice.value);
  const maximumPrice = Number(searchMaxPrice.value);
  if (minimumPrice !== null && minimumPrice > maximumPrice) {
    if (searchMessage) searchMessage.textContent = 'Минимальная цена не может быть выше максимальной.';
    return;
  }
  if (!searchMinArea?.checkValidity() || !searchMaxArea?.checkValidity()) {
    if (searchMessage) searchMessage.textContent = 'Введите площади от 0,1 до 10 000 м² с точностью до 0,1 м².';
    (!searchMinArea?.checkValidity() ? searchMinArea : searchMaxArea)?.reportValidity();
    return;
  }
  const maximumArea = searchMaxArea.value === '' ? null : Number(searchMaxArea.value);
  if (maximumArea !== null && Number(searchMinArea.value) > maximumArea) {
    if (searchMessage) searchMessage.textContent = 'Минимальная площадь не может быть больше максимальной.';
    return;
  }
  runSearchButton.disabled = true;
  const ownerOnly = searchSellerFilter?.value === 'owner';
  if (searchMessage) searchMessage.textContent = 'Проверяю поисковую выдачу…';
  try {
    const response = await fetch('/api/v1/search/run', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({rooms:Number(searchRooms?.value || 2), min_price_kzt:minimumPrice, max_price_kzt:maximumPrice, min_area_m2:Number(searchMinArea.value), max_area_m2:maximumArea, min_bathrooms:searchBathrooms?.checked ? 2 : 0, exclude_pledged:searchExcludePledged?.checked ?? true, ...(ownerOnly ? {seller:'owner'} : {})})
    });
    const body = await readJson(response);
    if (!response.ok) throw new Error(body.detail || 'Не удалось выполнить поиск');
    if (searchMessage) searchMessage.textContent = ownerOnly && body.blocked
      ? 'Krisha ограничила прямой поиск от хозяев. Ниже доступны ранее найденные объявления.'
      : `Запросов: ${body.queries}. Найдено ссылок: ${body.found}. Новых: ${body.new}. Автоимпортировано: ${body.imported || 0}. Ошибок: ${body.errors + (body.import_errors || 0)}.`;
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
  const browserImport = event.submitter?.id !== 'serverImportButton';
  if (browserImport && bathroomCheckBusy) return;
  if (browserImport) {
    bathroomCheckRevision += 1;
    bathroomCheckBusy = true;
    bathroomCheckFeedback = 'Ставлю объявление в очередь импорта…';
    if (bathroomCheckState) renderBathroomCheck(bathroomCheckState);
  }
  if (importMessage) importMessage.textContent = browserImport ? 'Ставлю объявление в очередь импорта…' : 'Загружаю объявление и определяю параметры…';
  const buttons = [...importForm.querySelectorAll('button[type="submit"]')];
  buttons.forEach(button => { button.disabled = true; });
  const data = new FormData(importForm);
  try {
    const options = {
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({source_url:String(data.get('source_url') || '').trim(), ...(browserImport ? {mode:'details'} : {})})
    };
    const response = await (browserImport ? fetchBathroomCheck('/start', options) : fetch('/api/v1/listings/import', options));
    const body = await readJson(response);
    if (!response.ok) {
      const detail = typeof body.detail === 'string' ? body.detail
        : Array.isArray(body.detail) ? body.detail.map(item => item.msg || 'Некорректный номер или ссылка').join('; ') : '';
      throw new Error(detail || 'Не удалось импортировать объявление');
    }
    if (browserImport) {
      updateBathroomCheck(body);
      window.postMessage({type:'apartment-agent-bathroom-queue'}, location.origin);
      if (importMessage) importMessage.textContent = 'Объявление поставлено в очередь. Карточка появится после подтверждения сохранения расширением; прогресс — в блоке «Автопроверка объявлений».';
      importForm.reset();
      return;
    }
    if (importMessage) importMessage.textContent = `Готово: ${money.format(body.price_kzt)}, ${number.format(body.area_m2)} м², рейтинг ${body.assessment.score}/100.`;
    importForm.reset();
    if (filter) filter.value = '0';
    await loadListings();
    await loadSearchResults();
  } catch (error) {
    if (importMessage) importMessage.textContent = error.name === 'TimeoutError' ? 'Сервер не подтвердил постановку в очередь за 10 секунд. Проверьте состояние автопроверки.' : error.message;
  } finally {
    buttons.forEach(button => { button.disabled = false; });
    if (browserImport) {
      bathroomCheckBusy = false;
      bathroomCheckFeedback = '';
      if (bathroomCheckState) renderBathroomCheck(bathroomCheckState);
    }
  }
});

form?.addEventListener('submit', async event => {
  event.preventDefault();
  if (message) message.textContent = 'Проверяю квартиру…';
  const data = new FormData(form);
  const payload = {
    source:'manual', source_url:data.get('source_url'), title:data.get('title') || '2-комнатная квартира в Астане', city:'Астана',
    description:data.get('description')?.trim() || null,
    repair_condition:data.get('repair_condition')?.trim() || null,
    district:data.get('district') || null, residential_complex:data.get('residential_complex') || null,
    complex_description:data.get('complex_description')?.trim() || null,
    complex_description_source_kind:data.get('complex_description')?.trim() ? data.get('complex_description_source_kind') : null,
    complex_description_source_url:data.get('complex_description')?.trim() ? data.get('complex_description_source_url')?.trim() || null : null,
    address:data.get('address') || null,
    seller_name:data.get('seller_name')?.trim() || null,
    seller_type:data.get('seller_type')?.trim() || null,
    latitude:data.get('latitude') ? Number(data.get('latitude')) : null,
    longitude:data.get('longitude') ? Number(data.get('longitude')) : null,
    price_kzt:Number(data.get('price_kzt')), area_m2:Number(data.get('area_m2')), rooms:Number(data.get('rooms')),
    bathrooms_count:data.get('bathrooms_count') ? Number(data.get('bathrooms_count')) : null,
    floor:data.get('floor') ? Number(data.get('floor')) : null, floors_total:data.get('floors_total') ? Number(data.get('floors_total')) : null,
    building_year:data.get('building_year') ? Number(data.get('building_year')) : null, building_type:data.get('building_type') || null,
    is_full_two_room:data.get('is_full_two_room') === 'on', mortgage_supported:data.get('mortgage_supported') === '' ? null : data.get('mortgage_supported') === 'true',
    is_pledged:data.get('is_pledged') === 'true' ? true : data.get('is_pledged') === 'false' ? false : null
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
listingSellerFilter?.addEventListener('change', () => { void loadListings({reuseComparisons:true}); });
listingLeftBankOnly?.addEventListener('change', () => { void loadListings({reuseComparisons:true}); });
listingShowPledged?.addEventListener('change', loadListings);
listingShowInactive?.addEventListener('change', () => { void loadListings({reuseComparisons:true}); });
setupLikedComparison();
document.querySelector('#openListingsMap')?.addEventListener('click', () => {
  if (!window.ApartmentAgentMap) {
    if (searchMessage) searchMessage.textContent = 'Не удалось загрузить карту. Обновите страницу и попробуйте ещё раз.';
    return;
  }
  window.ApartmentAgentMap.open({
    seller: searchSellerFilter?.value || 'all',
    bank: searchLeftBankOnly?.checked ? 'left' : 'all',
    openCard: jumpToSavedListing,
  });
});
refreshButton?.addEventListener('click', async () => { await loadListings(); await Promise.all([loadSearchStatus(), loadSearchResults()]); });
let lastReturnRefresh = 0;
function refreshAfterReturn() {
  if (document.visibilityState !== 'visible' || Date.now() - lastReturnRefresh < 1000) return;
  lastReturnRefresh = Date.now();
  void (async () => { await loadListings(); await Promise.all([loadSearchStatus(), loadSearchResults()]); })();
}
document.addEventListener('visibilitychange', refreshAfterReturn);
window.addEventListener('focus', refreshAfterReturn);
if ('serviceWorker' in navigator) window.addEventListener('load', () => {
  void navigator.serviceWorker.register('/service-worker.js', {updateViaCache:'none'})
    .then(registration => registration.update()).catch(() => {});
});
(async () => { if (/^#listing-[1-9]\d*$/.test(location.hash)) await openSavedListingFromHash(); else await loadListings(); await Promise.all([loadSearchStatus(), loadSearchResults()]); })();
