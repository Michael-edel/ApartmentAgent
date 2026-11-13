(() => {
  const stages = new Map([
    ['new', 'Новая'], ['call', 'Позвонить'], ['viewing', 'Просмотр назначен'],
    ['viewed', 'Осмотрена'], ['rejected', 'Отказ'],
  ]);
  const drafts = new Map();
  const escape = value => String(value ?? '').replace(/[&<>"']/g, char => (
    {'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[char]
  ));

  async function request(url, method = 'GET', body) {
    const response = await fetch(url, {
      method, ...(body ? {headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)} : {}),
    });
    if (response.status === 204) return null;
    const data = await response.json();
    if (!response.ok) throw Error(typeof data.detail === 'string' ? data.detail : 'Проверьте введённые данные.');
    return data;
  }

  function render(item) {
    const stage = stages.get(item.workflow_stage) || stages.get('new');
    const hasCoordinates = Number.isFinite(item.latitude) && Number.isFinite(item.longitude);
    const origin = hasCoordinates ? `${item.latitude},${item.longitude}` :
      item.address?.trim() ? [item.city, item.address].filter(Boolean).join(', ') : '';
    return `<details class="listing-comparables listing-workflow" data-workflow-id="${Number(item.id)}" data-details-key="viewing-workflow">
      <summary>Заметки и просмотр <span class="meta" data-stage-label>${escape(stage)}</span></summary>
      <form data-workflow-form hidden>
        <label>Этап<select name="stage">${[...stages].map(([value, label]) => `<option value="${value}">${escape(label)}</option>`).join('')}</select></label>
        <label>Дата и время просмотра<input name="viewing_at" type="datetime-local"></label>
        <label>Заметка<textarea name="note" rows="3" maxlength="4000"></textarea></label>
        <button class="panel-action" type="submit">Сохранить заметку и этап</button>
      </form><p class="meta" data-workflow-status role="status"></p>
    </details>
    <details class="listing-comparables listing-workflow" data-route-origin="${escape(origin)}" data-route-listing-id="${Number(item.id)}" data-details-key="saved-routes">
      <summary>Маршруты до важных мест</summary>
      <div data-destination-list></div>
      <form data-destination-form hidden>
        <label>Название<input name="name" maxlength="100" required placeholder="Работа, школа или детсад"></label>
        <label>Адрес с городом<input name="address" maxlength="500" required></label>
        <button class="panel-action" type="submit">Добавить место</button>
      </form><p class="meta" data-destination-status role="status"></p>
    </details>`;
  }

  function viewingLocal(value) {
    if (!value) return '';
    const date = new Date(value);
    return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  }

  function draftKey(form) {
    const details = form.closest('details');
    return details.dataset.workflowId ? `workflow-${details.dataset.workflowId}` :
      `destination-${details.dataset.routeListingId}`;
  }

  function restoreDraft(form) {
    for (const [name, value] of Object.entries(drafts.get(draftKey(form)) || {})) {
      form.elements[name].value = value;
    }
  }

  async function loadWorkflow(details) {
    const status = details.querySelector('[data-workflow-status]');
    const form = details.querySelector('form');
    status.textContent = 'Загрузка…';
    try {
      const data = await request(`/api/v1/listings/${details.dataset.workflowId}/workflow`);
      form.elements.stage.value = data.stage;
      form.elements.note.value = data.note;
      form.elements.viewing_at.value = viewingLocal(data.viewing_at);
      restoreDraft(form);
      form.hidden = false;
      status.textContent = '';
    } catch (error) { status.textContent = error.message; }
  }

  function routeHref(origin, destination) {
    const route = new URL('https://yandex.ru/maps/');
    route.searchParams.set('mode', 'routes');
    route.searchParams.set('rtext', [origin, destination].map(point => point.replaceAll('~', ' ')).join('~'));
    return route.href;
  }

  async function loadDestinations(details) {
    const status = details.querySelector('[data-destination-status]');
    const list = details.querySelector('[data-destination-list]');
    const origin = details.dataset.routeOrigin;
    status.textContent = 'Загрузка…';
    try {
      const data = await request('/api/v1/destinations');
      list.replaceChildren();
      for (const item of data) {
        const row = document.createElement('div');
        row.className = 'destination-row';
        const address = document.createElement(origin ? 'a' : 'span');
        address.textContent = `${item.name} · ${item.address}`;
        if (origin) {
          address.href = routeHref(origin, item.address);
          address.target = '_blank';
          address.rel = 'noopener noreferrer';
        }
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'panel-action';
        remove.textContent = 'Удалить';
        remove.setAttribute('aria-label', `Удалить место «${item.name}»`);
        remove.addEventListener('click', async () => {
          remove.disabled = true;
          try { await request(`/api/v1/destinations/${item.id}`, 'DELETE'); await refreshDestinations(); }
          catch (error) { status.textContent = error.message; remove.disabled = false; }
        });
        row.append(address, remove);
        list.append(row);
      }
      status.textContent = !origin ? 'Для маршрута нужен адрес или координаты квартиры.' :
        data.length ? '' : 'Добавьте место для построения маршрута.';
      const form = details.querySelector('form');
      restoreDraft(form);
      form.hidden = false;
    } catch (error) { status.textContent = error.message; }
  }

  async function refreshDestinations() {
    await Promise.all([...document.querySelectorAll('[data-route-origin][open]')].map(loadDestinations));
  }

  document.addEventListener('toggle', event => {
    const details = event.target;
    if (!details.open) return;
    if (details.matches('[data-workflow-id]') && details.querySelector('form').hidden) void loadWorkflow(details);
    if (details.matches('[data-route-origin]')) void loadDestinations(details);
  }, true);

  document.addEventListener('input', event => {
    const form = event.target.form;
    if (!form?.matches('[data-workflow-form], [data-destination-form]')) return;
    drafts.set(draftKey(form), Object.fromEntries(new FormData(form)));
  });

  document.addEventListener('submit', async event => {
    const form = event.target;
    const isWorkflow = form.matches('[data-workflow-form]');
    if (!isWorkflow && !form.matches('[data-destination-form]')) return;
    event.preventDefault();
    const key = draftKey(form);
    const submittedDraft = drafts.get(key);
    const details = form.closest('details');
    const status = details.querySelector('[role="status"]');
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    status.textContent = 'Сохраняется…';
    try {
      if (isWorkflow) {
        const listingId = Number(details.dataset.workflowId);
        const viewing = form.elements.viewing_at.value;
        const workflow = await request(`/api/v1/listings/${listingId}/workflow`, 'PUT', {
          stage:form.elements.stage.value, note:form.elements.note.value,
          viewing_at:viewing ? new Date(viewing).toISOString() : null,
        });
        if (drafts.get(key) === submittedDraft) drafts.delete(key);
        details.querySelector('[data-stage-label]').textContent = stages.get(workflow.stage);
        details.closest('article')?.setAttribute('data-workflow-stage', workflow.stage);
        status.textContent = 'Сохранено';
        document.dispatchEvent(new CustomEvent('apartmentagent:workflow-updated', {detail:{listingId,workflow}}));
      } else {
        await request('/api/v1/destinations', 'POST', {name:form.elements.name.value, address:form.elements.address.value});
        if (drafts.get(key) === submittedDraft) {
          drafts.delete(key);
          const currentForm = document.querySelector(`[data-route-listing-id="${details.dataset.routeListingId}"] form`);
          currentForm?.reset();
        }
        await refreshDestinations();
      }
    } catch (error) { status.textContent = error.message; }
    finally { button.disabled = false; }
  });

  window.ApartmentAgentWorkflow = {render};
})();
