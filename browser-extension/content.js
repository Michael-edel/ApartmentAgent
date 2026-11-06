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
  let queueNotified = false;
  let lastSavedBathrooms = null;
  const notifyQueue = (outcome, error = '', reason = '') => {
    if (queueNotified) return;
    queueNotified = true;
    chrome.runtime.sendMessage({type:'apartment-agent-queue-finished', outcome, error, reason}, () => { void chrome.runtime.lastError; });
  };
  const pageText = () => {
    let text = String(document.body?.innerText || '');
    for (const notice of document.querySelectorAll('[data-apartment-agent-extension-message], [data-apartment-agent-message]')) {
      text = text.replace(notice.innerText || notice.textContent || '', '');
    }
    return text;
  };
  const challengeDetected = () => {
    const title = String(document.title || '');
    const text = pageText();
    const check = /captcha|проверка\s+(?:безопасности|браузера)|подтвердите.{0,30}(?:человек|робот)|checking\s+your\s+browser|are\s+you\s+(?:a\s+)?robot|robot\s+check|доступ\s+ограничен/i;
    const httpBlock = /\b(?:Forbidden|Access\s+denied|Too\s+many\s+requests)\b|(?:HTTP(?:\s+Error)?|Error|Ошибка)\s*[:—-]?\s*(?:403|429|468)(?!\d)|(?:^|\n)\s*(?:403|429|468)(?=\s|:|$)/i;
    return check.test(title) || httpBlock.test(title)
      || text.length < 5000 && (check.test(text) || httpBlock.test(text));
  };
  const unavailableDetected = () => {
    const text = `${document.title || ''} ${pageText()}`;
    return /объявление(?:\s+(?:было|больше|уже|сейчас))?\s+(?:не\s*актуально|не\s*активно|удалено|снято\s+с\s+публикации|в\s+архиве|перенесено\s+в\s+архив|не\s+найдено)|архивное\s+объявление|такое\s+объявление\s+не\s+существует/i.test(text);
  };
  const bathroomLabel = payload => Number.isInteger(payload.bathrooms_count)
    ? `Санузлов: ${payload.bathrooms_count_is_lower_bound ? 'не менее ' : ''}${payload.bathrooms_count}.`
    : 'Санузлы: количество не определено.';
  const finish = () => {
    if (inFlight) {
      show('ApartmentAgent: подтверждение сохранения не получено. Обновите страницу для повторной попытки.', true);
      notifyQueue('error', 'Подтверждение сохранения не получено');
    } else if (lastError) {
      show(`ApartmentAgent: ${lastError}. Обновите страницу для повторной попытки.`, true);
      notifyQueue('error', lastError);
    } else if (savedMessage) {
      show(savedMessage);
      if (!Number.isInteger(lastSavedBathrooms)) notifyQueue('unknown');
    } else {
      show(`ApartmentAgent: не удалось определить ${missing.length ? missing.join(', ') : 'данные объявления'}. Обновите страницу или используйте ручной ввод.`, true);
      const fields = missing.length ? missing.join(', ') : 'price_kzt, area_m2, rooms (данные недоступны)';
      const title = String(document.title || '').replace(/\s+/g, ' ').trim().slice(0, 120);
      notifyQueue('unknown', `Не удалось прочитать: ${fields}.${title ? ` Заголовок: ${title}` : ''}`, 'unreadable');
    }
  };
  const observe = () => {
    if (challengeDetected()) {
      show('ApartmentAgent: сайт запросил проверку браузера. Пройдите её самостоятельно и продолжите очередь в приложении.', true);
      notifyQueue('blocked', 'Сайт запросил проверку браузера');
      return;
    }
    if (unavailableDetected()) {
      show('ApartmentAgent: объявление удалено или неактуально. Очередь пропускает эту страницу.');
      notifyQueue('unknown', 'Объявление удалено или неактуально', 'unavailable');
      return;
    }
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
              lastSavedBathrooms = payload.bathrooms_count;
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
