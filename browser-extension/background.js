importScripts('queue-worker.js');

const DEFAULTS = {enabled: true, apiOrigin: ''};
const importError = (detail, status) => {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return detail.map(item => {
    const field = Array.isArray(item?.loc) ? item.loc.filter(part => part !== 'body').join('.') : '';
    return `${field ? `${field}: ` : ''}${typeof item?.msg === 'string' ? item.msg : 'некорректное значение'}`;
  }).join('; ').slice(0,1000);
  return `HTTP ${status}: сервер отклонил данные объявления`;
};

const importSource = value => {
  if (typeof value !== 'string') return null;
  const canonical = ApartmentAgentBathroomQueue.canonical(value);
  if (canonical) return canonical;
  // The API preserves the URL of numbered adverts on other Krisha subdomains.
  try {
    const url = new URL(value);
    if (url.protocol === 'https:' && url.hostname.endsWith('.krisha.kz') && !url.port && !url.username && !url.password
      && /^\/a\/show\/\d+\/?$/.test(url.pathname)) return url.href;
  } catch (_) {}
  return null;
};

const importEndpoint = value => {
  try {
    const url = new URL(value);
    if (['http:', 'https:'].includes(url.protocol) && !url.username && !url.password && !url.search && !url.hash) {
      return `${url.href.replace(/\/$/, '')}/api/v1/listings/browser-import`;
    }
  } catch (_) {}
  return null;
};

const readImportConfirmation = async (response, endpoint, sourceUrl) => {
  if (response.redirected || response.type === 'opaqueredirect' || (response.status >= 300 && response.status < 400)
    || (response.url && response.url !== endpoint)) {
    throw new Error('Адрес импорта перенаправил запрос. Проверьте адрес ApartmentAgent в настройках расширения.');
  }
  const mediaType = response.headers.get('content-type')?.split(';', 1)[0].trim().toLowerCase();
  if (mediaType !== 'application/json') {
    throw new Error(`Сервер вернул ответ без JSON (HTTP ${response.status}). Проверьте адрес ApartmentAgent в настройках расширения.`);
  }
  const body = await response.json().catch(() => {
    throw new Error('Сервер вернул повреждённый JSON. Сохранение не подтверждено.');
  });
  if (!response.ok) throw new Error(importError(body?.detail, response.status));
  if (response.status !== 201) throw new Error(`Сервер вернул HTTP ${response.status} вместо подтверждения сохранения (HTTP 201).`);
  const score = body?.assessment?.score;
  if (body?.source !== 'browser' || !Number.isSafeInteger(body.id) || body.id <= 0
    || !Number.isInteger(score) || score < 0 || score > 100) {
    throw new Error('В ответе ApartmentAgent нет подтверждённой карточки объявления. Сохранение не подтверждено.');
  }
  if (importSource(body.source_url) !== sourceUrl) {
    throw new Error('Сервер вернул карточку другого объявления. Сохранение открытого объявления не подтверждено.');
  }
  return body;
};

chrome.runtime.onInstalled.addListener(() => {
  chrome.storage.sync.get(DEFAULTS, current => {
    chrome.storage.sync.set({enabled: current.enabled !== false, apiOrigin: current.apiOrigin || ''});
  });
});

chrome.action.onClicked.addListener(() => chrome.runtime.openOptionsPage());

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type === 'apartment-agent-bathroom-queue-poll') {
    ApartmentAgentBathroomQueue.kick(sender).then(() => sendResponse({ok:true}));
    return true;
  }
  if (message?.type === 'apartment-agent-queue-finished') {
    ApartmentAgentBathroomQueue.finished(sender, message.outcome, message.error, message.reason).then(() => sendResponse({ok:true}));
    return true;
  }
  if (!['apartment-agent-import', 'apartment-agent-availability'].includes(message?.type)) return false;
  chrome.storage.sync.get(DEFAULTS, async settings => {
    if (!settings.enabled) {
      sendResponse({ok: false, error: 'Автоматический импорт отключён'});
      return;
    }
    const endpoint = importEndpoint(settings.apiOrigin);
    if (!endpoint) {
      sendResponse({ok: false, error: 'Укажите адрес ApartmentAgent в настройках расширения, например http://127.0.0.1:8000.'});
      return;
    }
    const sourceUrl = importSource(message.payload?.source_url);
    if (!sourceUrl) {
      sendResponse({ok: false, error: 'Не удалось определить номер открытого объявления Krisha.'});
      return;
    }
    if (message.type === 'apartment-agent-availability') {
      if (message.payload?.availability_status !== 'archived' || importSource(sender.url) !== sourceUrl) {
        sendResponse({ok:false, error:'Статус не соответствует открытой странице объявления.'});
        return;
      }
      const availabilityEndpoint = endpoint.replace(/\/browser-import$/, '/availability');
      try {
        const response = await fetch(availabilityEndpoint, {
          method:'POST', headers:{'Content-Type':'application/json'}, redirect:'manual',
          body:JSON.stringify({source_url:sourceUrl, availability_status:'archived'}),
        });
        if (response.redirected || response.type === 'opaqueredirect' || (response.url && response.url !== availabilityEndpoint)
          || response.status !== 200 || !/^application\/json(?:\s*;|$)/i.test(response.headers.get('content-type') || '')) {
          throw new Error('Сервер не подтвердил статус объявления.');
        }
        const body = await response.json();
        if (importSource(body?.source_url) !== sourceUrl || body?.availability_status !== 'archived'
          || !(body.id === null || Number.isSafeInteger(body.id) && body.id > 0)) {
          throw new Error('Сервер не подтвердил статус этого объявления.');
        }
        sendResponse({ok:true, id:body.id});
      } catch (error) {
        sendResponse({ok:false, error:error instanceof Error ? error.message : 'Статус не подтверждён'});
      }
      return;
    }
    try {
      const response = await fetch(endpoint, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(message.payload),
        redirect: 'manual',
      }).catch(() => {
        throw new Error('Не удалось связаться с ApartmentAgent. Проверьте, что приложение запущено и адрес в настройках расширения верный.');
      });
      const body = await readImportConfirmation(response, endpoint, sourceUrl);
      const queue = await ApartmentAgentBathroomQueue.imported(sender, message.payload);
      sendResponse({ok: true, listing: body, waiting_details:queue?.waiting_details === true, observing:queue?.observing === true});
    } catch (error) {
      sendResponse({ok: false, error: error instanceof Error ? error.message : 'Ошибка импорта'});
    }
  });
  return true;
});
