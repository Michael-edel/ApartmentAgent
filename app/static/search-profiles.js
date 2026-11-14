(() => {
  'use strict';
  const el = id => document.getElementById(id);
  const modes = new Set(['off','hard','preference']);
  let dirty = false, items = [], active = null;
  let sellerInitialized = false, sellerChanged = false;
  el('searchSellerFilter')?.addEventListener('change', () => { sellerChanged = true; });
  function applySeller(seller) {
    if (sellerInitialized || sellerChanged || !['all', 'owner'].includes(seller)) return;
    const control = el('searchSellerFilter');
    if (!control) return;
    control.value = seller;
    sellerInitialized = true;
  }
  const fieldIds = ['conditionFirstFloor','conditionLastFloor','conditionMinYear','conditionMinYearMode','conditionMaxYear','conditionMaxYearMode','conditionKitchen','conditionKitchenMode'];
  for (const id of fieldIds) el(id)?.addEventListener('input', () => { dirty = true; });
  for (const id of fieldIds) el(id)?.addEventListener('change', () => { dirty = true; });
  const pair = (field, modeId, label) => {
    const input = el(field), mode = el(modeId)?.value || 'off';
    if (!modes.has(mode) || !input?.checkValidity()) throw Error(`Проверьте поле «${label}».`);
    if (mode !== 'off' && !input.value) throw Error(`Укажите значение «${label}» или выберите «Не учитывать».`);
    return {value:input.value === '' ? null : Number(input.value), mode};
  };
  function read() {
    const min = pair('conditionMinYear','conditionMinYearMode','Год постройки от');
    const max = pair('conditionMaxYear','conditionMaxYearMode','Год постройки до');
    if (min.mode !== 'off' && max.mode !== 'off' && min.value > max.value) throw Error('Год постройки «от» не может быть больше года «до».');
    const quality = window.ApartmentAgentSearchQuality?.read();
    return {first_floor:el('conditionFirstFloor')?.value || 'off', last_floor:el('conditionLastFloor')?.value || 'off', min_build_year:min, max_build_year:max, min_kitchen_m2:pair('conditionKitchen','conditionKitchenMode','Площадь кухни'), ...(quality ? {quality} : {})};
  }
  function apply(conditions, force = false) {
    window.ApartmentAgentSearchQuality?.apply(conditions?.quality, force);
    if (!conditions || (dirty && !force)) return;
    for (const [id, value] of [['conditionFirstFloor',conditions.first_floor],['conditionLastFloor',conditions.last_floor]]) if (el(id) && modes.has(value)) el(id).value=value;
    for (const [id, modeId, value] of [['conditionMinYear','conditionMinYearMode',conditions.min_build_year],['conditionMaxYear','conditionMaxYearMode',conditions.max_build_year],['conditionKitchen','conditionKitchenMode',conditions.min_kitchen_m2]]) {
      if (el(id)) el(id).value=value?.value ?? '';
      if (el(modeId)) el(modeId).value=modes.has(value?.mode) ? value.mode : 'off';
    }
    dirty = false;
  }
  function criteria() {
    const rooms=Number(el('searchRooms')?.value), price=el('searchMaxPrice'), minPrice=el('searchMinPrice'), minArea=el('searchMinArea'), maxArea=el('searchMaxArea');
    if (![price,minPrice,minArea,maxArea].every(input => input?.checkValidity())) throw Error('Проверьте цену и площадь в критериях поиска.');
    if (minPrice.value && Number(minPrice.value) > Number(price.value)) throw Error('Минимальная цена больше максимальной.');
    if (maxArea.value && Number(minArea.value) > Number(maxArea.value)) throw Error('Минимальная площадь больше максимальной.');
    return {seller:el('searchSellerFilter')?.value === 'owner' ? 'owner' : 'all', city:el('searchCity')?.value || 'astana', market_type:el('searchMarketType')?.value || 'all', rooms, min_price_kzt:minPrice.value ? Number(minPrice.value) : null, max_price_kzt:Number(price.value), min_area_m2:Number(minArea.value), max_area_m2:maxArea.value ? Number(maxArea.value) : null, min_bathrooms:el('searchBathrooms')?.checked ? 2 : 0, exclude_pledged:!!el('searchExcludePledged')?.checked, conditions:read()};
  }
  const select=el('profileSelect'), name=el('profileName'), enabled=el('profileEnabled'), message=el('profileMessage');
  const selected = () => items.find(item => String(item.id) === select?.value);
  function updateEnabledPolicy() {
    const profileCity = selected()?.criteria?.city;
    const formCity = el('searchCity')?.value;
    const isManualOnly = profileCity ? profileCity !== 'astana' : formCity === 'ekibastuz';
    enabled.disabled = isManualOnly;
    if (isManualOnly) enabled.checked = false;
    const hint = el('profileCityHint');
    if (hint) hint.hidden = !isManualOnly && formCity !== 'ekibastuz';
  }
  function updateSelected() {
    const item=selected();
    if (item) { name.value=item.name; enabled.checked=item.enabled === true; }
    else { name.value=''; enabled.checked=false; }
    for (const id of ['profileUpdate','profileUpdateCriteria','profileActivate','profileDelete']) if (el(id)) el(id).disabled=!item;
    if (item && !['astana','ekibastuz'].includes(item.criteria?.city)) el('profileActivate').disabled = true;
    updateEnabledPolicy();
  }
  select?.addEventListener('change', updateSelected);
  document.addEventListener('apartmentagent:city-changed', updateEnabledPolicy);
  async function request(url, method='GET', body) {
    const response=await fetch(url, {method,...(body ? {headers:{'Content-Type':'application/json'},body:JSON.stringify(body)} : {})});
    if (response.status === 204) return null;
    const data=await response.json().catch(() => ({}));
    if (!response.ok) throw Error(typeof data.detail === 'string' ? data.detail : 'Не удалось выполнить действие.');
    return data;
  }
  async function refresh(preferred) {
    const data=await request('/api/v1/search/profiles');
    items=Array.isArray(data.items) ? data.items : []; active=data.active_profile_id;
    const previous=preferred ?? select.value;
    select.replaceChildren(new Option('Выберите профиль',''));
    for (const item of items) select.add(new Option(`${item.name}${item.id === active ? ' · активный' : ''}${item.enabled ? ' · фон' : ''}`,String(item.id)));
    select.value=items.some(item => String(item.id) === String(previous)) ? String(previous) : '';
    updateSelected();
  }
  async function act(action) {
    const current=selected();
    if (action !== 'create' && !current) { message.textContent='Выберите профиль.'; return; }
    if (action === 'delete' && !window.confirm(`Удалить профиль «${current.name}»?`)) return;
    message.textContent='Выполняю…';
    try {
      if (action === 'activate') { await request(`/api/v1/search/profiles/${current.id}/activate`,'POST'); if (/^#listing-[1-9]\d*$/.test(location.hash)) history.replaceState(null, '', location.pathname + location.search); location.reload(); return; }
      if (action === 'delete') { await request(`/api/v1/search/profiles/${current.id}`,'DELETE'); await refresh(''); message.textContent='Профиль удалён.'; return; }
      const profileName=name.value.trim();
      if (!profileName) throw Error('Введите название профиля.');
      const chosenCriteria = action === 'update' ? current.criteria : criteria();
      const body={name:profileName,enabled:chosenCriteria.city === 'astana' && enabled.checked,criteria:chosenCriteria};
      const result=await request(action === 'create' ? '/api/v1/search/profiles' : `/api/v1/search/profiles/${current.id}`,action === 'create' ? 'POST' : 'PUT',body);
      await refresh(result.id); message.textContent=chosenCriteria.city === 'ekibastuz' ? 'Профиль сохранён для ручного поиска.' : 'Профиль сохранён.';
    } catch (error) { message.textContent=error.message || 'Не удалось выполнить действие.'; }
  }
  for (const [id, action] of [['profileCreate','create'],['profileUpdate','update'],['profileUpdateCriteria','updateCriteria'],['profileActivate','activate'],['profileDelete','delete']]) el(id)?.addEventListener('click', () => void act(action));
  updateSelected();
  refresh().catch(() => { if (message) message.textContent='Не удалось загрузить профили.'; });
  window.ApartmentAgentProfiles={read,apply,applySeller,refresh};
})();
