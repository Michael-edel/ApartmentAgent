/* Open regular catalog tabs, pause on Krisha verification, and resume when the
 * user completes it. Keep state across MV3 worker suspension. */
(() => {
  const ALARM = 'apartment-agent-commercial';
  const KEY = 'apartmentAgentCommercialScan';
  const PREFIX = '/api/v1/commercial/browser';
  const MAX_PAGES = 10;
  const MAX_GALLERY_CHECKS = 3;
  const PHOTO_HASH = '#apartment-agent-commercial-photo';
  let serial = Promise.resolve();
  let pendingPhoto = null;
  const run = fn => {
    const job = serial.then(fn);
    serial = job.catch(() => {});
    return job;
  };
  const load = async () => (await chrome.storage.session.get(KEY))[KEY] || {};
  const save = state => chrome.storage.session.set({[KEY]: state});
  const planOrigin = async () => {
    const settings = await chrome.storage.sync.get({enabled: true, apiOrigin: ''});
    if (!settings.enabled) return null;
    const url = new URL(settings.apiOrigin || 'http://localhost:8000');
    return ['http://localhost:8000', 'http://127.0.0.1:8000'].includes(url.origin) ? url.origin : null;
  };
  const api = async (origin, path, body) => {
    const response = await fetch(origin + PREFIX + path, {
      ...(body ? {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)} : {}),
      redirect: 'error', signal: AbortSignal.timeout(path === '/scan' ? 45000 : 10000),
    });
    if (!response.ok) throw new Error(`ApartmentAgent HTTP ${response.status}`);
    return response.json();
  };
  const catalogPath = /^\/((?:arenda\/(?:kommercheskaya-nedvizhimost|prombazy))|(?:prodazha\/(?:kommercheskaya-nedvizhimost|biznes|prombazy)))\/ekibastuz\/$/;
  const catalogOK = value => {
    const url = new URL(value);
    return url.origin === 'https://krisha.kz' && catalogPath.test(url.pathname) && !url.search && !url.hash;
  };
  const target = state => state.nextUrl || state.catalogs[state.index];
  const matches = (address, state, page = state.page) => {
    try {
      const actual = new URL(address);
      const catalog = new URL(state.catalogs[state.index]);
      return actual.origin === catalog.origin && !actual.username && !actual.password
        && actual.pathname.replace(/\/$/, '') === catalog.pathname.replace(/\/$/, '')
        && Number(actual.searchParams.get('page') || '1') === page;
    } catch (_) { return false; }
  };
  const report = async (state, status) => {
    try {
      await api(state.origin, '/report', {catalog: state.catalogs[state.index], status});
    } catch (error) {
      console.warn(`ApartmentAgent: не удалось передать состояние каталога (${status}).`, error);
    }
  };
  const renderedPhoto = async url => {
    let tab;
    let timeout;
    const taggedUrl = url + PHOTO_HASH;
    try {
      tab = await chrome.tabs.create({url: 'about:blank', active: false});
      return await new Promise(resolve => {
        pendingPhoto = {tabId: tab.id, url: taggedUrl, canonicalUrl: url, resolve};
        timeout = setTimeout(() => resolve({status: 'render_timeout'}), 18000);
        chrome.tabs.update(tab.id, {url: taggedUrl}).catch(() => resolve({status: 'content_unavailable'}));
      });
    } catch (_) {
      return {status: 'content_unavailable'};
    } finally {
      pendingPhoto = null;
      if (timeout) clearTimeout(timeout);
      const current = await getTab(tab?.id);
      const address = current?.pendingUrl || current?.url;
      if (address === 'about:blank' || address === taggedUrl || address === url) {
        await chrome.tabs.remove(current.id).catch(() => {});
      }
    }
  };
  const enrichPhoto = async (state, card) => {
    const url = card.url;
    let result;
    try {
      result = await chrome.tabs.sendMessage(state.tabId, {type: 'apartment-agent-commercial-photo', url}, {frameId: 0});
    } catch (_) {
      return {status: 'content_unavailable', url};
    }
    const usedRendered = result?.status === 'metadata_missing' && result.http_status === 200;
    if (usedRendered) result = await renderedPhoto(url);
    const detailStatuses = ['content_unavailable', 'http_unavailable', 'redirect_or_fetch_error', 'verification',
      'metadata_missing', 'canonical_mismatch', 'render_timeout', 'found'];
    const status = detailStatuses.includes(result?.status) ? result.status : 'content_unavailable';
    const outcome = {status, url};
    if (usedRendered) outcome.rendered = true;
    if (Number.isInteger(result?.http_status)) outcome.http_status = result.http_status;
    if (result?.photo_diagnostics) outcome.diagnostics = result.photo_diagnostics;
    if (status === 'verification' || [403, 429, 468].includes(outcome.http_status)) {
      state.detailPhotosBlocked = true;
      console.warn('ApartmentAgent: Krisha ограничила загрузку фото. Каталоги сохраняются, запросы карточек отложены до следующей проверки.');
    }
    if (status === 'found' && typeof result.photo_url === 'string') {
      card.photo_url = result.photo_url;
      if (Array.isArray(result.photo_urls)) card.photo_urls = result.photo_urls.slice(0, 30);
    }
    return outcome;
  };
  const enrichCatalogPhotos = async (state, payload) => {
    if (state.detailPhotosBlocked || !state.cards.length) return [{status: 'not_needed'}];
    let candidates;
    try {
      candidates = await api(state.origin, '/photo-candidates', payload);
    } catch (_) {
      return [{status: 'preflight_unavailable'}];
    }
    if (!Array.isArray(candidates?.urls)) return [{status: 'candidate_missing'}];
    const catalogCards = new Map(state.cards.map(card => [card.url, card]));
    const outcomes = [];
    for (const url of new Set(candidates.urls.slice(0, MAX_GALLERY_CHECKS))) {
      if (typeof url !== 'string' || !/^https:\/\/krisha\.kz\/a\/show\/[1-9]\d{0,14}$/.test(url)) continue;
      const card = catalogCards.get(url);
      if (!card) continue;
      if (outcomes.length) await new Promise(resolve => setTimeout(resolve, 1500));
      outcomes.push(await enrichPhoto(state, card));
      if (state.detailPhotosBlocked) break;
    }
    return outcomes.length ? outcomes : [{status: 'candidate_missing'}];
  };
  const saveCatalog = async (state, complete = true) => {
    const catalog = state.catalogs[state.index];
    const payload = {catalog, cards: state.cards};
    if (!complete) payload.complete = false;
    const photoLookups = await enrichCatalogPhotos(state, payload);
    payload.photo_lookup = photoLookups[0];
    if (photoLookups.length > 1) payload.photo_lookups = photoLookups;
    const result = await api(state.origin, '/scan', payload);
    if (!result.ok || result.catalog !== catalog) throw new Error('Catalog scan was not confirmed');
  };
  const getTab = async id => {
    try { return id ? await chrome.tabs.get(id) : null; } catch (_) { return null; }
  };
  const stop = async (state, {close = false} = {}) => {
    const tab = await getTab(state.tabId);
    if (close && tab && matches(tab.pendingUrl || tab.url, state)) await chrome.tabs.remove(tab.id).catch(() => {});
    await save({nextAt: Date.now() + state.interval});
  };
  const advance = async state => {
    if (state.index + 1 === state.catalogs.length) {
      await stop(state, {close: true});
      return false;
    }
    state.index++;
    state.page = 1;
    state.nextUrl = null;
    state.cards = [];
    return true;
  };
  const tick = async () => {
    const state = await load();
    if (state.tabId) {
      const tab = await getTab(state.tabId);
      if (!tab || (state.status === 'loading' && Date.now() - state.stepAt > 90000)) {
        try {
          if (state.cards.length) await saveCatalog(state, false);
        } finally {
          await report(state, 'error');
          await stop(state, {close: true});
        }
      }
      return;
    }
    if ((state.nextAt || 0) > Date.now()) return;
    const origin = await planOrigin();
    if (!origin) return;
    const plan = await api(origin, '/plan');
    if (!plan.enabled || !Array.isArray(plan.catalogs) || plan.catalogs.length !== 5 || !plan.catalogs.every(catalogOK)) return;
    const next = {origin, catalogs: plan.catalogs, index: 0, page: 1, cards: [], status: 'loading',
      stepAt: Date.now(), interval: Math.max(5, Number(plan.interval_minutes) || 5) * 60000};
    const tab = await chrome.tabs.create({url: target(next), active: false});
    next.tabId = tab.id;
    await save(next);
  };
  const arrived = async (message, sender) => {
    const state = await load();
    if (sender.frameId !== 0 || sender.tab?.id !== state.tabId || !matches(sender.url, state)) return false;
    if (message.status === 'verification') {
      if (state.status !== 'verification') {
        state.status = 'verification';
        await save(state);
        await chrome.tabs.update(state.tabId, {active: true});
        await report(state, 'verification');
      }
      return true;
    }
    if (message.status !== 'ready' || !Array.isArray(message.cards)) {
      // Failed or empty-looking challenge pages never create a baseline.
      if (state.cards.length) await saveCatalog(state, false);
      await report(state, 'unavailable');
      if (!await advance(state)) return true;
    } else {
      state.cards.push(...message.cards);
      const hasNext = matches(message.nextPage, state, state.page + 1);
      if (state.page < MAX_PAGES && hasNext) {
        state.page++;
        state.nextUrl = message.nextPage;
      } else {
        await saveCatalog(state, !hasNext);
        if (hasNext) await report(state, 'unavailable');
        if (!await advance(state)) return true;
      }
    }
    state.status = 'loading';
    state.stepAt = Date.now();
    await save(state);
    await new Promise(resolve => setTimeout(resolve, 2500));
    await chrome.tabs.update(state.tabId, {url: target(state)});
    return true;
  };
  const runScheduled = () => {
    void run(async () => {
      if (!await chrome.alarms.get(ALARM)) {
        await chrome.alarms.create(ALARM, {periodInMinutes: 1});
      }
      await tick();
    }).catch(error => console.warn('ApartmentAgent: не удалось запустить проверку коммерции.', error));
  };
  chrome.runtime.onInstalled.addListener(runScheduled);
  chrome.runtime.onStartup.addListener(runScheduled);
  chrome.alarms.onAlarm.addListener(alarm => {
    if (alarm.name === ALARM) runScheduled();
  });
  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (['apartment-agent-commercial-photo-claim', 'apartment-agent-commercial-photo-rendered'].includes(message?.type)) {
      // This reply must remain independent of the catalog job waiting for it.
      const lookup = pendingPhoto;
      const tracked = lookup && sender.frameId === 0 && sender.tab?.id === lookup.tabId
        && [lookup.url, lookup.canonicalUrl].includes(sender.url);
      if (tracked && message.type === 'apartment-agent-commercial-photo-rendered') {
        lookup.resolve({status: message.status, photo_url: message.photo_url, photo_urls: message.photo_urls,
          photo_diagnostics: message.photo_diagnostics, http_status: message.http_status});
      }
      sendResponse({tracked: Boolean(tracked)});
      return false;
    }
    if (message?.type !== 'apartment-agent-commercial-page') return false;
    void run(async () => {
      try {
        return await arrived(message, sender);
      } catch (error) {
        console.warn('ApartmentAgent: не удалось завершить проверку каталога.', error);
        const state = await load();
        if (sender.tab?.id === state.tabId) {
          await report(state, 'error');
          await stop(state, {close: true});
        }
        return false;
      }
    }).then(tracked => sendResponse({tracked})).catch(() => sendResponse({tracked: false}));
    return true;
  });
  runScheduled();
})();
