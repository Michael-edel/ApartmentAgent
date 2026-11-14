(() => {
  'use strict';
  const groups = [
    ['Что сфотографировать', [['meters','Счётчики'], ['stairway','Подъезд'], ['yard','Двор'], ['parking','Парковка'], ['window_view','Вид из окон']]],
    ['Документы', [['documents','Документы продавца'], ['extract','Выписка и обременения']]],
    ['Что проверить', [['plumbing','Сантехника'], ['electricity','Электрика'], ['heating','Отопление'], ['ventilation','Вентиляция'], ['windows','Окна'], ['walls','Стены'], ['ceiling','Потолок'], ['noise','Шум'], ['water_pressure','Напор воды'], ['elevator','Лифт'], ['internet','Интернет']]],
  ];
  const categories = [['meters','Счётчики'], ['stairway','Подъезд'], ['yard','Двор'], ['parking','Парковка'], ['window_view','Вид из окон'], ['documents','Документы продавца'], ['extract','Выписка'], ['other','Другое']];
  const drafts = new Map();
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function render(item) {
    return `<details class="listing-comparables inspection" data-inspection-id="${Number(item.id)}" data-details-key="inspection">
      <summary>Чек-лист осмотра квартиры</summary><p class="meta">Отметьте проверенное, сохраните заметки и приложите фотографии или документы.</p>
      <form data-inspection-form hidden>
        ${groups.map(([title, checks]) => `<fieldset><legend>${title}</legend>${checks.map(([key,label]) => `<label class="inspection-check"><input type="checkbox" name="${key}"> ${label}</label>`).join('')}</fieldset>`).join('')}
        <label>Вопросы продавцу<textarea name="seller_questions" rows="4" maxlength="4000" placeholder="Причина продажи, срок владения, кто прописан, долги, перепланировка, залог"></textarea></label>
        <label>Заметки по осмотру<textarea name="notes" rows="3" maxlength="4000"></textarea></label>
        <label>Итог осмотра<select name="outcome"><option value="undecided">Пока не решено</option><option value="suitable">Годна к покупке</option><option value="needs_review">Нужны документы</option><option value="unsuitable">Отказ</option></select></label>
        <button class="panel-action" type="submit">Сохранить осмотр</button>
      </form>
      <div data-inspection-files hidden><h4>Фотографии и документы</h4><p class="meta">JPEG, PNG, WebP или PDF, до 5 МБ каждый, не больше 10 файлов. Файлы хранятся в базе приложения.</p>
        <label>Раздел<select data-inspection-category>${categories.map(([key,label]) => `<option value="${key}">${label}</option>`).join('')}</select></label>
        <label>Выбрать файл<input type="file" data-inspection-file accept="image/jpeg,image/png,image/webp,application/pdf"></label>
        <button class="panel-action" type="button" data-inspection-upload>Прикрепить файл</button><div data-inspection-attachments></div></div>
      <p class="meta" data-inspection-status role="status"></p>
    </details>`;
  }
  async function request(url, options = {}) {
    const response = await fetch(url, options);
    if (response.status === 204) return null;
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw Error(typeof body.detail === 'string' ? body.detail : 'Не удалось выполнить действие.');
    return body;
  }
  const endpoint = details => `/api/v1/listings/${encodeURIComponent(details.dataset.inspectionId)}/inspection`;
  const readForm = form => {
    const checks = {};
    for (const [, entries] of groups) for (const [key] of entries) checks[key] = form.elements[key].checked;
    return {checks, seller_questions:form.elements.seller_questions.value, notes:form.elements.notes.value, outcome:form.elements.outcome.value};
  };
  function applyForm(form, data) {
    for (const [, entries] of groups) for (const [key] of entries) form.elements[key].checked = data.checks?.[key] === true;
    form.elements.seller_questions.value = data.seller_questions || '';
    form.elements.notes.value = data.notes || '';
    form.elements.outcome.value = data.outcome || 'undecided';
  }
  function showAttachments(details, attachments) {
    const list = details.querySelector('[data-inspection-attachments]'); list.replaceChildren();
    for (const attachment of attachments) {
      const row = document.createElement('div'); row.className = 'inspection-file-row';
      const link = document.createElement('a'); link.textContent = `${categories.find(([key]) => key === attachment.category)?.[1] || 'Файл'}: ${attachment.name}`;
      link.href = `${endpoint(details)}/attachments/${encodeURIComponent(attachment.id)}`; link.download = '';
      const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'panel-action'; remove.textContent = 'Удалить';
      remove.addEventListener('click', async () => {
        if (!window.confirm('Удалить это вложение?')) return;
        remove.disabled = true;
        try { await request(link.href, {method:'DELETE'}); await load(details, true); }
        catch (error) { details.querySelector('[data-inspection-status]').textContent = error.message; remove.disabled = false; }
      });
      row.append(link, remove); list.append(row);
    }
  }
  async function load(details, keepForm = false) {
    const status = details.querySelector('[data-inspection-status]'), form = details.querySelector('[data-inspection-form]');
    status.textContent = 'Загрузка…';
    try {
      const data = await request(endpoint(details));
      if (!keepForm) applyForm(form, drafts.get(details.dataset.inspectionId) || data);
      showAttachments(details, data.attachments || []); form.hidden = false;
      details.querySelector('[data-inspection-files]').hidden = false; status.textContent = '';
    } catch (error) { status.textContent = error.message; }
  }
  document.addEventListener('toggle', event => {
    const details = event.target;
    if (details.matches?.('[data-inspection-id]') && details.open && details.querySelector('form').hidden) void load(details);
  }, true);
  document.addEventListener('input', event => {
    const form = event.target.form;
    if (form?.matches('[data-inspection-form]')) drafts.set(form.closest('[data-inspection-id]').dataset.inspectionId, readForm(form));
  });
  document.addEventListener('change', event => {
    const form = event.target.form;
    if (form?.matches('[data-inspection-form]')) drafts.set(form.closest('[data-inspection-id]').dataset.inspectionId, readForm(form));
  });
  document.addEventListener('submit', async event => {
    const form = event.target;
    if (!form.matches('[data-inspection-form]')) return;
    event.preventDefault();
    const details = form.closest('[data-inspection-id]'), id = details.dataset.inspectionId, data = readForm(form);
    const previous = drafts.get(id), status = details.querySelector('[data-inspection-status]'), button = form.querySelector('button[type=submit]');
    button.disabled = true; status.textContent = 'Сохраняется…';
    try {
      await request(endpoint(details), {method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
      if (drafts.get(id) === previous) drafts.delete(id);
      status.textContent = 'Осмотр сохранён';
    } catch (error) { status.textContent = error.message; } finally { button.disabled = false; }
  });
  document.addEventListener('click', async event => {
    const button = event.target.closest?.('[data-inspection-upload]');
    if (!button) return;
    const details = button.closest('[data-inspection-id]'), file = details.querySelector('[data-inspection-file]').files[0];
    const status = details.querySelector('[data-inspection-status]');
    if (!file) { status.textContent = 'Выберите файл.'; return; }
    if (file.size > 5 * 1024 * 1024) { status.textContent = 'Файл больше 5 МБ.'; return; }
    const params = new URLSearchParams({name:file.name, category:details.querySelector('[data-inspection-category]').value});
    button.disabled = true; status.textContent = 'Прикрепляется…';
    try { await request(`${endpoint(details)}/attachments?${params}`, {method:'POST', body:file}); details.querySelector('[data-inspection-file]').value = ''; await load(details, true); status.textContent = 'Файл прикреплён'; }
    catch (error) { status.textContent = error.message; } finally { button.disabled = false; }
  });
  window.ApartmentAgentInspection = {render};
})();
