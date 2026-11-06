(() => {
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

  // Each page reload can update an existing card, including after an upgrade.
  // Deduplicate successful payloads only during this bounded observation run.
  const deadline = Date.now() + 12000;
  const succeeded = new Set();
  let inFlight = false;
  let failures = 0;
  let retryAfter = 0;
  let lastError = '';
  let missing = [];
  let savedMessage = '';
  const bathroomLabel = payload => Number.isInteger(payload.bathrooms_count)
    ? `Санузлов: ${payload.bathrooms_count_is_lower_bound ? 'не менее ' : ''}${payload.bathrooms_count}.`
    : 'Санузлы: количество не определено.';
  const finish = () => {
    if (inFlight) show('ApartmentAgent: подтверждение сохранения не получено. Обновите страницу для повторной попытки.', true);
    else if (lastError) show(`ApartmentAgent: ${lastError}. Обновите страницу для повторной попытки.`, true);
    else if (savedMessage) show(savedMessage);
    else show(`ApartmentAgent: не удалось определить ${missing.length ? missing.join(', ') : 'данные объявления'}. Обновите страницу или используйте ручной ввод.`, true);
  };
  const observe = () => {
    if (Date.now() >= deadline) { finish(); return; }
    if (!inFlight && failures < 3 && Date.now() >= retryAfter) {
      try {
        const payload = window.ApartmentAgentExtract?.();
        missing = payload ? ['price_kzt', 'area_m2', 'rooms'].filter(key => !payload[key]) : [];
        if (payload && !missing.length) {
          const signature = JSON.stringify(payload);
          if (!succeeded.has(signature)) {
            inFlight = true;
            show('ApartmentAgent: импортирую открытое объявление…');
            chrome.runtime.sendMessage({type: 'apartment-agent-import', payload}, response => {
              inFlight = false;
              const error = chrome.runtime.lastError?.message || (!response?.ok ? response?.error || 'импорт не выполнен' : '');
              if (error) {
                failures += 1;
                retryAfter = Date.now() + 2000;
                lastError = error;
                if (failures >= 3) finish();
                return;
              }
              succeeded.add(signature);
              lastError = '';
              savedMessage = `ApartmentAgent: сохранено, рейтинг ${response.listing?.assessment?.score ?? '—'}/100. ${bathroomLabel(payload)}`;
              show(savedMessage);
            });
          }
        }
      } catch (error) {
        inFlight = false;
        failures += 1;
        retryAfter = Date.now() + 2000;
        lastError = error instanceof Error ? error.message : 'не удалось прочитать объявление';
      }
    }
    window.setTimeout(observe, 400);
  };
  window.setTimeout(observe, 1200);
})();
