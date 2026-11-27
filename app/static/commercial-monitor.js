(() => {
  const control = document.querySelector('#commercialMonitorEnabled');
  if (!control) return;

  const lastCheck = document.querySelector('#commercialMonitorLastCheck');
  const krishaCount = document.querySelector('#commercialMonitorKrisha');
  const krishaCheck = document.querySelector('#commercialMonitorKrishaCheck');
  const olxCount = document.querySelector('#commercialMonitorOlx');
  const olxCheck = document.querySelector('#commercialMonitorOlxCheck');
  const etagiCount = document.querySelector('#commercialMonitorEtagi');
  const etagiCheck = document.querySelector('#commercialMonitorEtagiCheck');
  const trackingStart = document.querySelector('#commercialMonitorTrackingStart');
  const message = document.querySelector('#commercialMonitorMessage');
  const dateTime = new Intl.DateTimeFormat('ru-RU', {dateStyle: 'short', timeStyle: 'short'});
  const number = new Intl.NumberFormat('ru-RU');
  let confirmed = null;
  let reading = false;
  let saving = false;
  let revision = 0;

  function setText(node, value) {
    if (node.textContent !== value) node.textContent = value;
  }

  function sourceCheckTime(value) {
    return value ? ` · проверено ${dateTime.format(new Date(value))}` : ' · успешных проверок пока не было';
  }

  function render(status) {
    confirmed = status;
    control.checked = status.enabled;
    control.disabled = saving;
    setText(lastCheck, status.last_checked_at ? dateTime.format(new Date(status.last_checked_at)) : 'ещё не было');
    setText(krishaCount, number.format(status.sources.krisha.count));
    setText(krishaCheck, sourceCheckTime(status.sources.krisha.last_checked_at));
    setText(olxCount, number.format(status.sources.olx.count));
    setText(olxCheck, sourceCheckTime(status.sources.olx.last_checked_at));
    setText(etagiCount, number.format(status.sources.etagi.count));
    setText(etagiCheck, sourceCheckTime(status.sources.etagi.last_checked_at));
    const tracking = status.tracking_started_at;
    setText(trackingStart, tracking ? `Отсчёт обнаружений — ${dateTime.format(new Date(tracking))}` : '');
    trackingStart.hidden = !tracking;
  }

  async function request(path, options) {
    const response = await fetch('/api/v1/commercial/' + path, {
      ...options,
      cache: 'no-store',
      signal: AbortSignal.timeout(10000),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }

  async function loadStatus() {
    if (reading || saving || document.hidden) return;
    reading = true;
    const requestRevision = revision;
    try {
      const status = await request('status');
      if (requestRevision === revision) {
        render(status);
        setText(message, '');
      }
    } catch (_) {
      if (requestRevision === revision) setText(message, 'Не удалось обновить состояние мониторинга.');
    } finally {
      reading = false;
    }
  }

  control.addEventListener('change', async () => {
    if (!confirmed || saving) return;
    saving = true;
    revision++;
    control.disabled = true;
    setText(message, 'Сохраняю настройку…');
    try {
      const status = await request('settings', {
        method: 'PUT',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({enabled: control.checked}),
      });
      render(status);
      setText(message, '');
    } catch (_) {
      control.checked = confirmed.enabled;
      setText(message, 'Не удалось подтвердить сохранение настройки. Попробуйте ещё раз.');
    } finally {
      saving = false;
      control.disabled = false;
    }
  });

  void loadStatus();
  document.addEventListener('visibilitychange', loadStatus);
  window.setInterval(loadStatus, 30000);
})();
