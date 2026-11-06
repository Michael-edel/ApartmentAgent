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
  if (message?.type !== 'apartment-agent-import') return false;
  chrome.storage.sync.get(DEFAULTS, async settings => {
    if (!settings.enabled) {
      sendResponse({ok: false, error: 'Автоматический импорт отключён'});
      return;
    }
    const apiOrigin = String(settings.apiOrigin || '').replace(/\/$/, '');
    if (!/^https?:\/\//i.test(apiOrigin)) {
      sendResponse({ok: false, error: 'Сначала укажите адрес ApartmentAgent в настройках расширения'});
      return;
    }
    try {
      const response = await fetch(`${apiOrigin}/api/v1/listings/browser-import`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(message.payload),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(importError(body.detail, response.status));
      const queue = await ApartmentAgentBathroomQueue.imported(sender, message.payload);
      sendResponse({ok: true, listing: body, waiting_details:queue?.waiting_details === true, observing:queue?.observing === true});
    } catch (error) {
      sendResponse({ok: false, error: error instanceof Error ? error.message : 'Ошибка импорта'});
    }
  });
  return true;
});
