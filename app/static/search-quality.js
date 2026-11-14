(() => {
  'use strict';
  let dirty = false;
  const parent = document.querySelector('#searchPreferences');
  if (!parent) { window.ApartmentAgentSearchQuality = {read:() => ({}), apply:() => {}}; return; }
  const details = document.createElement('details');
  details.className = 'search-preferences';
  details.id = 'qualityPreferences';
  details.innerHTML = `<summary>Район, планировка и слова в объявлении</summary>
    <p class="microcopy">Неизвестные данные отмечаются отдельно. Точная фраза ищется в заголовке и описании.</p>
    <label>Районы через запятую<input id="qualityDistricts" maxlength="1000" placeholder="Например, Есильский район, Нура"></label>
    <label>Планировка<select id="qualityLayout"><option value="all">Любая</option><option value="separate">Раздельные комнаты</option><option value="adjoining">Смежные комнаты</option><option value="studio">Студия</option><option value="cross_ventilated">Распашонка</option></select></label>
    <label class="checkbox"><input id="qualityKitchenLiving" type="checkbox"> Кухня-гостиная</label>
    <label class="checkbox"><input id="qualityCloset" type="checkbox"> Гардеробная</label>
    <label class="checkbox"><input id="qualityBalcony" type="checkbox"> Балкон или лоджия</label>
    <fieldset><legend>Радиус от важного места</legend><p class="microcopy">Расстояние по прямой, не время пути. Нужны точные координаты квартиры и места.</p>
      <label>Широта места<input id="qualityRadiusLat" type="number" min="-90" max="90" step="any"></label>
      <label>Долгота места<input id="qualityRadiusLon" type="number" min="-180" max="180" step="any"></label>
      <label>Максимум, метров<input id="qualityRadiusMax" type="number" min="1" max="100000" step="1"></label>
    </fieldset>
    <label>Фраза в объявлении<input id="qualityPhrase" maxlength="200" placeholder="Например, рядом школа"></label>
    <details id="qualityMapPanel">
      <summary>Нарисовать область на карте</summary>
      <p class="microcopy">Поставьте хотя бы три точки по порядку вдоль границы. Затем нажмите «Завершить рисование»: последняя точка соединится с первой.</p>
      <p class="microcopy">При поиске по области в результатах и на карте видны только квартиры с точными координатами внутри границы.</p>
      <div id="qualityMap" class="search-area-map" aria-label="Карта выбора области"></div>
      <div class="search-area-actions">
        <button type="button" id="qualityMapFinish" class="panel-action" disabled>Завершить рисование</button>
        <button type="button" id="qualityMapEdit" class="panel-action" hidden>Продолжить рисование</button>
        <button type="button" id="qualityMapUndo" class="panel-action" disabled>Убрать последнюю точку</button>
        <button type="button" id="qualityMapClear" class="panel-action" disabled>Очистить область</button>
      </div>
      <p id="qualityMapMessage" class="microcopy" role="status"></p>
      <details><summary>Ввести координаты вручную</summary>
        <label>Широта, долгота каждой точки с новой строки<textarea id="qualityPolygon" rows="3" placeholder="51.12, 71.41&#10;51.13, 71.41&#10;51.13, 71.43"></textarea></label>
      </details>
    </details>
    <label class="checkbox"><input id="qualityLower" type="checkbox"> Исключать цену «от»</label>
    <label class="checkbox"><input id="qualityCash" type="checkbox"> Исключать только наличный расчёт, если подтверждено</label>
    <label class="checkbox"><input id="qualityStrict" type="checkbox"> Скрывать объявления с неизвестными значениями выбранных условий</label>`;
  parent.after(details);
  const el = id => details.querySelector(`#${id}`);
  details.addEventListener('input', () => { dirty = true; });
  details.addEventListener('change', () => { dirty = true; });
  const polygonInput = el('qualityPolygon');
  const finishButton = el('qualityMapFinish');
  const editButton = el('qualityMapEdit');
  let map = null, drawn = null, points = null, finished = false;
  const centers = {astana:[51.128,71.43], ekibastuz:[51.718,75.322]};
  function readVertices() {
    return polygonInput.value.split(/\r?\n/).map(line => line.trim()).filter(Boolean).map(line => {
      const parts = line.split(',').map(value => value.trim());
      if (parts.length !== 2 || parts.some(value => value === '' || !Number.isFinite(Number(value)))) {
        throw Error('В каждой строке укажите широту и долготу через запятую.');
      }
      const [lat, lon] = parts.map(Number);
      if (lat < -90 || lat > 90 || lon < -180 || lon > 180) throw Error('Проверьте координаты области.');
      return [lat, lon];
    });
  }

  const cross = (a, b, c) => (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);
  function intersects(a, b, c, d) {
    const side = (p, q, r) => Math.abs(cross(p, q, r)) <= 1e-12 ? 0 : Math.sign(cross(p, q, r));
    const onSegment = (p, q, r) => p.every((value, i) => Math.min(value, q[i]) <= r[i] && r[i] <= Math.max(value, q[i]));
    const ac = side(a, b, c), ad = side(a, b, d), ca = side(c, d, a), cb = side(c, d, b);
    return (ac * ad < 0 && ca * cb < 0) || (!ac && onSegment(a, b, c)) || (!ad && onSegment(a, b, d)) ||
      (!ca && onSegment(c, d, a)) || (!cb && onSegment(c, d, b));
  }

  function polygonError(vertices) {
    const count = vertices.length;
    if (count < 3 || count > 200 || new Set(vertices.map(point => point.join(','))).size !== count) {
      return 'Для области нужны от 3 до 200 разных точек.';
    }
    for (let i = 0; i < count; i += 1) {
      for (let j = i + 2; j < count; j += 1) {
        if (i === 0 && j === count - 1) continue;
        if (intersects(vertices[i], vertices[i + 1], vertices[j], vertices[(j + 1) % count])) {
          return 'Граница пересекает сама себя. Уберите точку и продолжите по периметру.';
        }
      }
    }
    const area = vertices.slice(1, -1).reduce((sum, point, i) => sum + cross(vertices[0], point, vertices[i + 2]), 0);
    return Math.abs(area) <= 1e-12 ? 'Точки лежат на одной линии. Выберите точки по периметру.' : '';
  }

  function drawCurrent() {
    let vertices = [];
    let error = '';
    try { vertices = readVertices(); } catch (failure) { error = failure.message; }
    if (!error && vertices.length >= 3) error = polygonError(vertices);
    finishButton.hidden = finished;
    finishButton.disabled = vertices.length < 3 || Boolean(error);
    editButton.hidden = !finished;
    el('qualityMapUndo').disabled = !polygonInput.value.trim();
    el('qualityMapClear').disabled = el('qualityMapUndo').disabled;
    el('qualityMapMessage').textContent = error || (finished
      ? `Область готова. Точек: ${vertices.length}. Нажмите «Искать сейчас», чтобы применить её.`
      : vertices.length >= 3 ? `Точек: ${vertices.length}. Можно завершить рисование.`
        : `Точек: ${vertices.length}. Добавьте ещё ${3 - vertices.length}.`);
    if (map) {
      points.clearLayers();
      if (drawn) { map.removeLayer(drawn); drawn = null; }
      vertices.forEach((point, index) => L.circleMarker(point, {
        radius:index === 0 ? 6 : 5, color:'#17644d', fillColor:'#fff', fillOpacity:1, interactive:false,
      }).addTo(points));
      if (vertices.length > 1) drawn = (finished
        ? L.polygon(vertices, {color:'#197b62', interactive:false})
        : L.polyline(vertices, {color:'#2474ca', interactive:false})).addTo(map);
    }
  }

  function setVertices(vertices) {
    polygonInput.value = vertices.map(point => point.join(', ')).join('\n');
    dirty = true;
    finished = false;
    drawCurrent();
  }
  el('qualityMapPanel').addEventListener('toggle', () => {
    if (!el('qualityMapPanel').open) return;
    if (!window.L) { el('qualityMapMessage').textContent = 'Карта недоступна. Введите координаты вручную.'; return; }
    if (!map) {
      const city = document.querySelector('#searchCity')?.value || 'astana';
      map = L.map(el('qualityMap'), {zoomAnimation:false}).setView(centers[city] || centers.astana, 11);
      L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom:19, attribution:'&copy; OpenStreetMap contributors'}).addTo(map);
      points = L.layerGroup().addTo(map);
      map.on('click', event => {
        if (finished) return;
        let vertices;
        try { vertices = readVertices(); } catch { return; }
        if (vertices.length >= 200) {
          el('qualityMapMessage').textContent = 'Достигнут предел в 200 точек. Завершите рисование или уберите последнюю точку.';
          return;
        }
        const point = [event.latlng.lat, event.latlng.lng].map(value => Number(value.toFixed(6)));
        if (!vertices.some(vertex => vertex[0] === point[0] && vertex[1] === point[1])) setVertices([...vertices, point]);
      });
      drawCurrent();
      if (finished) map.fitBounds(drawn.getBounds(), {padding:[20,20], maxZoom:15});
    }
    requestAnimationFrame(() => map.invalidateSize());
  });
  finishButton.addEventListener('click', () => {
    finished = true;
    drawCurrent();
    editButton.focus();
  });
  editButton.addEventListener('click', () => { finished = false; drawCurrent(); el('qualityMap').focus(); });
  el('qualityMapUndo').addEventListener('click', () => {
    polygonInput.value = polygonInput.value.trim().split(/\r?\n/).slice(0, -1).join('\n');
    dirty = true;
    finished = false;
    drawCurrent();
  });
  el('qualityMapClear').addEventListener('click', () => setVertices([]));
  polygonInput.addEventListener('input', () => { finished = false; drawCurrent(); });
  document.querySelector('#searchCity')?.addEventListener('change', event => {
    if (map && centers[event.target.value]) map.setView(centers[event.target.value], 11);
  });
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function renderEvidence(quality) {
    const entry = quality?.reasons?.find(item => item.criterion === 'phrase' && item.passed && item.evidence);
    if (!entry) return '';
    const source = entry.evidence.source === 'title' ? 'заголовок' : 'описание';
    return `<p class="meta">Совпадение фразы (${source}): ${escape(entry.evidence.evidence)}</p>`;
  }
  function read() {
    const districtText = el('qualityDistricts').value.trim();
    const districts = districtText ? districtText.split(',').map(x => x.trim()).filter(Boolean) : [];
    if (districts.length > 20 || districts.some(x => x.length > 100)) throw Error('Укажите до 20 районов, до 100 символов каждый.');
    const vertices = readVertices();
    const polygon = vertices.length ? vertices : null;
    const error = polygon && polygonError(polygon);
    if (error) throw Error(error);
    const radiusValues = [el('qualityRadiusLat'), el('qualityRadiusLon'), el('qualityRadiusMax')];
    let radius = null;
    if (radiusValues.some(input => input.value !== '')) {
      if (radiusValues.some(input => input.value === '' || !input.checkValidity() || !Number.isFinite(Number(input.value)))) throw Error('Для радиуса укажите широту, долготу и расстояние от 1 до 100 000 метров.');
      radius = {latitude:Number(radiusValues[0].value), longitude:Number(radiusValues[1].value), max_m:Number(radiusValues[2].value)};
    }
    const features = [['kitchen_living','qualityKitchenLiving'], ['closet','qualityCloset'], ['balcony','qualityBalcony']].filter(([,id]) => el(id).checked).map(([key]) => key);
    return {districts, polygon, radius, features, layout:el('qualityLayout').value, phrase:el('qualityPhrase').value.trim(), exclude_pledged:false,
      exclude_lower_bound:el('qualityLower').checked, exclude_cash_only:el('qualityCash').checked, require_confirmed:el('qualityStrict').checked};
  }
  function apply(value, force = false) {
    if (!value || (dirty && !force)) return;
    el('qualityDistricts').value = (Array.isArray(value.districts) ? value.districts : []).join(', ');
    const vertices = Array.isArray(value.polygon) ? value.polygon : [];
    polygonInput.value = vertices.map(point => point.join(', ')).join('\n');
    finished = !polygonError(vertices);
    el('qualityLayout').value = ['all','separate','adjoining','studio','cross_ventilated'].includes(value.layout) ? value.layout : 'all';
    el('qualityPhrase').value = value.phrase || '';
    el('qualityRadiusLat').value = value.radius?.latitude ?? '';
    el('qualityRadiusLon').value = value.radius?.longitude ?? '';
    el('qualityRadiusMax').value = value.radius?.max_m ?? '';
    for (const [key, id] of [['kitchen_living','qualityKitchenLiving'], ['closet','qualityCloset'], ['balcony','qualityBalcony']]) el(id).checked = Array.isArray(value.features) && value.features.includes(key);
    el('qualityLower').checked = value.exclude_lower_bound === true;
    el('qualityCash').checked = value.exclude_cash_only === true;
    el('qualityStrict').checked = value.require_confirmed === true;
    dirty = false;
    drawCurrent();
  }
  drawCurrent();
  window.ApartmentAgentSearchQuality = {read, apply, renderEvidence};
})();
