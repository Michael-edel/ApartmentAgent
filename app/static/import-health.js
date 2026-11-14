(() => {
  const panel = document.querySelector('#importHealth');
  if (!panel) return;
  const body = panel.querySelector('[data-import-health-body]');
  const refresh = panel.querySelector('[data-import-health-refresh]');
  const labels = {success:'Данные получены', blocked:'Доступ ограничен', error:'Ошибка', skipped:'Архив или недоступно'};
  const methods = {search:'Выдача', auto:'Автоимпорт', browser:'Расширение', manual:'По ссылке', check:'Мониторинг', queue:'Очередь'};
  const sites = {krisha:'Krisha', olx:'OLX'};
  const time = value => value ? new Date(value).toLocaleString('ru-RU') : '—';

  function paragraph(message) {
    const node = document.createElement('p');
    node.className = 'microcopy';
    node.textContent = message;
    return node;
  }

  async function request(path) {
    const response = await fetch('/api/v1/import-observability' + path);
    if (!response.ok) throw Error('Журнал недоступен');
    return response.json();
  }

  async function load() {
    refresh.disabled = true;
    body.replaceChildren(paragraph('Загружаю журнал…'));
    try {
      const [status, journal] = await Promise.all([request(''), request('/events?limit=12')]);
      const rows = Object.entries(status.sources).map(([source, state]) => {
        const counts = state.counts;
        const current = state.in_cooldown ? `Пауза до ${time(state.retry_at)}` : labels[state.last_status] || 'Ожидает проверки';
        return paragraph(`${sites[source]} · ${current}. Успешных: ${counts.success || 0}. Ограничений: ${counts.blocked || 0}, из них проверок браузера: ${state.browser_verifications || 0}. Ошибок: ${counts.error || 0}.`);
      });
      rows.push(paragraph(status.browser_connected ? `Расширение подключено · ${status.browser_version}` :
        'Нет свежего сигнала от расширения. Для проверки связи нужна версия 0.4.14.'));
      if (status.last_browser_activity_at) rows.push(paragraph(`Последний импорт из браузера: ${time(status.last_browser_activity_at)}`));
      for (const item of journal.items) {
        const details = item.error || (item.fields.length ? `Получено полей: ${item.fields.length}` : '');
        const entry = paragraph([` ${time(item.observed_at)} · ${methods[item.method] || 'Импорт'} · ${labels[item.status]}`, details].filter(Boolean).join(' · '));
        if (item.url?.startsWith('https://')) {
          const link = document.createElement('a');
          link.href = item.url;
          link.target = '_blank';
          link.rel = 'noopener noreferrer';
          link.textContent = sites[item.source];
          entry.prepend(link);
        } else {
          entry.prepend(sites[item.source] || '');
        }
        if (item.fields.length) entry.title = item.fields.join(', ');
        rows.push(entry);
      }
      body.replaceChildren(...rows);
    } catch (error) {
      body.replaceChildren(paragraph(error.message || 'Журнал недоступен'));
    } finally {
      refresh.disabled = false;
    }
  }
  panel.addEventListener('toggle', () => { if (panel.open) void load(); });
  refresh.addEventListener('click', () => { void load(); });
})();
