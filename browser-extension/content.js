(() => {
  const marker = 'apartment-agent-imported';
  if (sessionStorage.getItem(marker) === location.href.split('#')[0]) return;

  const show = (message, error = false) => {
    const old = document.querySelector('[data-apartment-agent-extension-message]');
    old?.remove();
    const node = document.createElement('div');
    node.dataset.apartmentAgentExtensionMessage = 'true';
    node.textContent = message;
    Object.assign(node.style, {
      position: 'fixed', zIndex: '2147483647', left: '16px', right: '16px', bottom: '20px',
      padding: '14px 16px', borderRadius: '12px', background: error ? '#fee4e2' : '#e5f5ef',
      color: error ? '#9b1c1c' : '#126b4d', boxShadow: '0 8px 24px #0003',
      font: '600 15px -apple-system,BlinkMacSystemFont,Segoe UI,sans-serif',
    });
    document.body.appendChild(node);
    window.setTimeout(() => node.remove(), 8000);
  };

  window.setTimeout(() => {
    const payload = window.ApartmentAgentExtract?.();
    if (!payload) return;
    const missing = ['price_kzt', 'area_m2', 'rooms'].filter(key => !payload[key]);
    if (missing.length) {
      show(`ApartmentAgent: не удалось определить ${missing.join(', ')}.`, true);
      return;
    }
    show('ApartmentAgent: импортирую открытое объявление…');
    chrome.runtime.sendMessage({type: 'apartment-agent-import', payload}, response => {
      if (chrome.runtime.lastError) {
        show(`ApartmentAgent: ${chrome.runtime.lastError.message}`, true);
        return;
      }
      if (!response?.ok) {
        show(`ApartmentAgent: ${response?.error || 'импорт не выполнен'}`, true);
        return;
      }
      sessionStorage.setItem(marker, payload.source_url);
      show(`ApartmentAgent: сохранено, рейтинг ${response.listing?.assessment?.score ?? '—'}/100`);
    });
  }, 1200);
})();
