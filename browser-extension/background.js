const DEFAULTS = {enabled: true, apiOrigin: ''};

chrome.runtime.onInstalled.addListener(() => {
  chrome.storage.sync.get(DEFAULTS, current => {
    chrome.storage.sync.set({enabled: current.enabled !== false, apiOrigin: current.apiOrigin || ''});
  });
});

chrome.action.onClicked.addListener(() => chrome.runtime.openOptionsPage());

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
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
      if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
      sendResponse({ok: true, listing: body});
    } catch (error) {
      sendResponse({ok: false, error: error instanceof Error ? error.message : 'Ошибка импорта'});
    }
  });
  return true;
});
