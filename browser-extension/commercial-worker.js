/* Open regular catalog tabs, pause on Krisha verification, and resume when the
 * user completes it. Keep state across MV3 worker suspension. */
(() => {
  const ALARM = 'apartment-agent-commercial';
  const KEY = 'apartmentAgentCommercialScan';
  const PREFIX = '/api/v1/commercial/browser';
  const MAX_PAGES = 10;
  let serial = Promise.resolve();
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
  const saveCatalog = async (state, complete = true) => {
    const catalog = state.catalogs[state.index];
    const payload = {catalog, cards: state.cards};
    if (!complete) payload.complete = false;
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
