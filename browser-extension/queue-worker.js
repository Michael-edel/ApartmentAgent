/* Sequential, persistent MV3 worker. Listing pages are opened as ordinary tabs;
 * this worker never fetches Krisha pages or attempts to solve browser checks. */
(() => {
  const KEY = 'apartmentAgentBathroomQueue';
  const HEARTBEAT = 'apartment-agent-bathroom-heartbeat';
  const WAKE = 'apartment-agent-bathroom-wake';
  const PREFIX = '/api/v1/search/bathroom-check';
  const TIMEOUT = 60000;
  let state;
  let serial = Promise.resolve();
  const canonical = value => {
    try {
      const url = new URL(value);
      if (url.protocol !== 'https:' || !['krisha.kz', 'www.krisha.kz'].includes(url.hostname)
        || url.port || url.username || url.password || !/^\/a\/show\/\d+\/?$/.test(url.pathname)) return null;
      return `https://krisha.kz${url.pathname.replace(/\/$/, '')}`;
    } catch (_) { return null; }
  };
  const load = async () => {
    if (!state) {
      state = (await chrome.storage.local.get(KEY))[KEY];
      if (!state?.worker_id) state = {worker_id:crypto.randomUUID(), job_id:null, task:null, next_wake:0, halted:false};
      await save();
    }
    return state;
  };
  const save = () => chrome.storage.local.set({[KEY]:state});
  const settings = async () => {
    const value = await chrome.storage.sync.get({enabled:true, apiOrigin:''});
    if (!value.enabled) throw new Error('Автоматический импорт отключён');
    const url = new URL(value.apiOrigin);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) throw new Error('Укажите адрес ApartmentAgent в настройках расширения');
    return url.origin;
  };
  const api = async (path = '', body) => {
    const origin = await settings();
    const response = await fetch(`${origin}${PREFIX}${path}`, {
      method: body === undefined ? 'GET' : 'POST',
      headers:{'Content-Type':'application/json'},
      ...(body === undefined ? {} : {body:JSON.stringify(body)}),
      ...(typeof AbortSignal !== 'undefined' && AbortSignal.timeout ? {signal:AbortSignal.timeout(10000)} : {}),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : `ApartmentAgent: HTTP ${response.status}`);
    return result;
  };
  const schedule = async delay => {
    // Chromium may round alarms below 30 seconds; the server enforces its own gap.
    state.next_wake = Date.now() + Math.max(30000, delay || 0);
    await save();
    await chrome.alarms.create(WAKE, {when:state.next_wake});
  };
  const getTab = async id => {
    try { return await chrome.tabs.get(id); } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      if (/no tab with id|^no tab$|no tab found|invalid tab id|tab.*not found/i.test(message)) return null;
      // Permission/transient read failures are unknown addresses, not proof
      // that the user closed or navigated the extension-owned tab.
      return {id};
    }
  };
  const placeholder = () => `${chrome.runtime.getURL('queue-tab.html')}#${encodeURIComponent(state.worker_id)}`;
  // Chrome can report the previous URL while a new navigation is pending.
  const effectiveURL = tab => tab?.pendingUrl || tab?.url || '';
  const ownedUnchanged = (tab, task) => !!tab && (canonical(effectiveURL(tab)) === task.url || effectiveURL(tab) === placeholder());
  const waitForTabAddress = async (task, tab) => {
    if (!task.placeholder_wait_until) {
      task.placeholder_wait_until = Date.now() + 5000;
      await save();
    }
    while (tab && ['', 'about:blank'].includes(effectiveURL(tab)) && Date.now() < task.placeholder_wait_until) {
      await new Promise(resolve => setTimeout(resolve, 250));
      tab = await getTab(task.tab_id);
    }
    return tab;
  };
  const addressFailure = tab => {
    const address = effectiveURL(tab);
    if (!address || address === 'about:blank') return 'Нет доступа к адресу служебной вкладки. Обновите расширение и разрешения для вкладок.';
    let origin = '';
    try { const url = new URL(address); origin = `${url.protocol}//${url.host}`; } catch (_) {}
    return `Адрес вкладки изменился${origin ? ` (${origin})` : ''}. Проверьте страницу вручную.`;
  };
  const closeOwned = async task => {
    if (!task?.tab_id) return;
    const tab = await getTab(task.tab_id);
    // A tab the user navigated elsewhere now belongs to the user.
    if (ownedUnchanged(tab, task)) await chrome.tabs.remove(tab.id).catch(() => {});
  };
  const halt = async error => {
    await load();
    state.halted = true;
    state.message = error instanceof Error ? error.message : String(error);
    state.next_wake = 0;
    await save();
    await chrome.alarms.clear(WAKE);
  };
  const run = operation => {
    const result = serial.then(async () => { await load(); return operation(); });
    serial = result.catch(halt);
    return serial;
  };
  const complete = async (outcome, error = '') => {
    const task = state.task;
    if (!task) return;
    await api('/complete', {worker_id:state.worker_id, task_id:task.id, lease_token:task.lease_token, outcome, ...(error ? {error} : {})});
    state.task = null;
    state.message = error;
    if (outcome === 'blocked' || outcome === 'error') {
      // Keep the real browser page open for the user. Never retry a challenge.
      state.halted = true;
      state.kept_tab_id = task.tab_id;
      state.kept_tab_url = task.url;
      state.next_wake = 0;
      await save();
      await chrome.alarms.clear(WAKE);
      return;
    }
    await save(); // Clear ownership before tabs.onRemoved can run.
    await closeOwned(task);
    await schedule(30000);
  };
  const openTask = async () => {
    const task = state.task;
    if (!task) return;
    let tab = task.tab_id ? await getTab(task.tab_id) : null;
    if (!task.tab_id) {
      // Recover only our extension placeholder after a worker crash between
      // creation and storage. Never claim an existing user-owned Krisha tab.
      // Only inspect our placeholder page, never enumerate user browsing URLs.
      tab = (await chrome.tabs.query({url:chrome.runtime.getURL('queue-tab.html')})).find(candidate => effectiveURL(candidate) === placeholder());
      if (!tab) tab = await chrome.tabs.create({url:placeholder(), active:false});
      task.tab_id = tab.id;
      task.placeholder_wait_until = Date.now() + 5000;
      await save();
    }
    tab = await waitForTabAddress(task, tab);
    if (!tab) { await complete('blocked', 'Вкладка проверки была закрыта. Продолжение требует пользователя.'); return; }
    if (!ownedUnchanged(tab, task)) { await complete('blocked', addressFailure(tab)); return; }
    if (effectiveURL(tab) === placeholder()) {
      const latest = await waitForTabAddress(task, await getTab(tab.id));
      if (!ownedUnchanged(latest, task)) { await complete('blocked', addressFailure(latest)); return; }
      if (effectiveURL(latest) === placeholder()) await chrome.tabs.update(tab.id, {url:task.url, active:false});
    }
    await schedule(Math.max(0, task.started_at + TIMEOUT - Date.now()));
  };
  const poll = async () => {
    const status = await api();
    const sameLease = state.task && canonical(status.current_url) === state.task.url;
    if (status.status === 'paused' && state.task && status.job_id === state.job_id) {
      if (!sameLease) {
        // The server may have completed/expired the task before the worker
        // persisted its response. Keep the page for the explicit human pause.
        state.kept_tab_id = state.task.tab_id;
        state.kept_tab_url = state.task.url;
        state.task = null;
        state.halted = true;
        state.next_wake = 0;
        await save();
        await chrome.alarms.clear(WAKE);
        return;
      }
      // A manual pause finishes the leased page and only stops the next claim.
      if (Date.now() >= state.task.started_at + TIMEOUT) await complete('blocked', 'Страница не подтвердила импорт за 60 секунд. Проверьте вкладку вручную.');
      else await openTask();
      return;
    }
    if (status.status !== 'running') {
      const task = state.task;
      state.task = null;
      state.job_id = status.job_id;
      state.next_wake = 0;
      await save();
      await closeOwned(task);
      await chrome.alarms.clear(WAKE);
      return;
    }
    if (status.job_id !== state.job_id) {
      const oldTask = state.task;
      state.task = null;
      state.unreadable_streak = 0;
      state.halted = false;
      state.message = '';
      state.next_wake = 0;
      state.job_id = status.job_id;
      await save();
      await closeOwned(oldTask);
    }
    if (state.halted) return;
    state.job_id = status.job_id;
    await save();
    if (state.task) {
      if (!sameLease) {
        const staleTask = state.task;
        state.task = null;
        await save();
        await closeOwned(staleTask);
        await schedule(30000);
        return;
      }
      if (Date.now() >= state.task.started_at + TIMEOUT) {
        await complete('blocked', 'Страница не подтвердила импорт за 60 секунд. Проверьте вкладку вручную.');
        return;
      }
      await openTask();
      return;
    }
    if (state.next_wake > Date.now()) { await schedule(state.next_wake - Date.now()); return; }
    const claim = await api('/claim', {worker_id:state.worker_id, extension_version:chrome.runtime.getManifest().version});
    if (!claim.task) {
      if (claim.status === 'running') await schedule((claim.wait_seconds || 30) * 1000);
      return;
    }
    const url = canonical(claim.task.url);
    if (!url) {
      state.job_id = claim.job_id;
      state.task = {...claim.task, tab_id:null, started_at:Date.now()};
      await save();
      await complete('error', 'Сервер вернул неподдерживаемый адрес объявления');
      return;
    }
    state.job_id = claim.job_id;
    state.task = {...claim.task, url, mode:claim.mode || status.mode || 'bathrooms', tab_id:null, started_at:Date.now()};
    state.next_wake = 0;
    const kept = state.kept_tab_id ? await getTab(state.kept_tab_id) : null;
    const reuseAfterExplicitResume = kept && canonical(effectiveURL(kept)) === url && state.kept_tab_url === url;
    if (reuseAfterExplicitResume) {
      state.task.tab_id = kept.id;
      state.kept_tab_id = null;
      state.kept_tab_url = null;
    }
    await save();
    if (reuseAfterExplicitResume) await chrome.tabs.reload(kept.id);
    await openTask();
  };
  const senderIsTask = sender => state.task && sender?.tab?.id === state.task.tab_id;
  const apiForBridge = sender => {
    try {
      const url = new URL(sender?.url || sender?.tab?.url || '');
      return url.protocol === 'http:' && ['localhost', '127.0.0.1'].includes(url.hostname) && url.port === '8000';
    } catch (_) { return false; }
  };
  const worker = {
    kick: sender => run(async () => {
      if (sender && !apiForBridge(sender)) return;
      state.halted = false;
      state.message = '';
      // A UI command can cancel/pause immediately, but cannot shorten server gap.
      await save();
      await poll();
    }),
    imported: (sender, payload) => run(async () => {
      if (!senderIsTask(sender)) return;
      const tab = await getTab(state.task.tab_id);
      if (!tab || canonical(effectiveURL(tab)) !== state.task.url) { await complete('blocked', 'Адрес вкладки изменился во время импорта'); return; }
      state.task.last_imported = {
        bathrooms_known:Number.isInteger(payload?.bathrooms_count) && payload.bathrooms_count >= 0,
        details_extracted:payload?.details_extracted === true,
      };
      await save();
      // Keep the page open until the content script's full observation period:
      // author, description and gallery can arrive after the first valid save.
      return {observing:true, waiting_details:state.task.mode === 'details' && payload?.details_extracted !== true};
    }),
    finished: (sender, outcome, error, reason) => run(async () => {
      if (!senderIsTask(sender)) return;
      if (outcome === 'imported') {
        const latest = state.task.last_imported;
        const accepted = state.task.mode === 'details' ? latest?.details_extracted : latest?.bathrooms_known;
        if (accepted) { state.unreadable_streak = 0; await save(); await complete('imported'); return; }
        outcome = 'unknown';
        if (state.task.mode === 'details') {
          reason = 'unreadable';
          error = 'Не удалось прочитать полный блок характеристик, описания или автора объявления';
        }
      }
      if (outcome === 'unknown') {
        state.unreadable_streak = reason === 'unreadable' ? (state.unreadable_streak || 0) + 1 : 0;
        await save();
        if (state.unreadable_streak >= 3) {
          const detail = String(error || '').slice(0, 600);
          await complete('error', `Три страницы подряд не передали обязательные данные. Проверьте доступ к объявлениям и настройки расширения.${detail ? ` Последняя страница: ${detail}` : ''}`);
        }
        else await complete('unknown', String(error || '').slice(0, 1000));
      }
      else await complete(outcome === 'blocked' ? 'blocked' : 'error', String(error || 'Не удалось прочитать объявление').slice(0, 1000));
    }),
    removed: id => run(async () => {
      if (state.task?.tab_id === id) await complete('blocked', 'Вкладка проверки была закрыта пользователем');
    }),
    updated: (id, change) => run(async () => {
      if (state.task?.tab_id !== id || !change.url) return;
      if (change.url !== placeholder() && canonical(change.url) !== state.task.url) await complete('blocked', 'Объявление перенаправило браузер. Проверьте открытую вкладку.');
    }),
    start: () => run(async () => {
      await chrome.alarms.create(HEARTBEAT, {periodInMinutes:1});
      if (state.next_wake) await chrome.alarms.create(WAKE, {when:Math.max(Date.now() + 30000, state.next_wake)});
      await poll();
    }),
    canonical,
  };
  globalThis.ApartmentAgentBathroomQueue = worker;
  chrome.alarms.onAlarm.addListener(alarm => {
    if ([HEARTBEAT, WAKE].includes(alarm.name)) void run(poll);
  });
  chrome.tabs.onRemoved.addListener(id => { void worker.removed(id); });
  chrome.tabs.onUpdated.addListener((id, change) => { void worker.updated(id, change); });
  void worker.start();
})();
