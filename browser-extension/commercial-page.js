// Read the catalog the browser actually displays. Krisha verification is left to the user.
(() => {
  if (!/^\/(?:arenda\/(?:kommercheskaya-nedvizhimost|prombazy)|prodazha\/(?:kommercheskaya-nedvizhimost|biznes|prombazy))\/ekibastuz\/?$/.test(location.pathname)) return;
  const POST_LOAD_WAIT_MS = 15000;
  let deadline = Date.now() + POST_LOAD_WAIT_MS;
  let verificationStarted = null;

  const send = payload => chrome.runtime.sendMessage({type: 'apartment-agent-commercial-page', ...payload}).catch(() => null);
  const text = (node, max) => node?.textContent?.replace(/\s+/g, ' ').trim().slice(0, max) || null;
  const hasVerification = () => Boolean(
    document.querySelector('#sl-check, title#slg-title, link#kg-favicon[href*="/.safeline/"]')
    || /проверка браузера|подтвердите, что вы не робот|checking your browser|security verification/i.test(document.title)
  );
  const sameSiteUrl = value => {
    try {
      const address = new URL(value, location.href);
      return address.origin === location.origin ? address : null;
    } catch (_) { return null; }
  };
  const normalizedPath = path => path.replace(/\/$/, '');

  const readCatalog = () => {
    const root = document.querySelector('.a-search-list');
    const empty = /найдено\s+0\s+объявлен|по вашему запросу ничего не найдено|нет подходящих объявлений/i
      .test(document.querySelector('main')?.innerText || document.body?.innerText || '');
    if (!root) return empty ? {status: 'ready', cards: [], nextPage: null} : null;

    const cards = [];
    const published = new Map();
    const collectDates = (node) => {
      if (Array.isArray(node)) {
        node.forEach(collectDates);
        return;
      }
      if (!node || typeof node !== 'object') return;
      if (typeof node.dateCreated === 'string' && typeof node.url === 'string') {
        const url = sameSiteUrl(node.url);
        if (url && /^\/a\/show\/\d+\/?$/.test(url.pathname)) {
          published.set(normalizedPath(url.pathname), node.dateCreated);
        }
      }
      for (const [key, value] of Object.entries(node)) {
        if (!/^(?:recommend|related|similar|suggest|nearby|other)/i.test(key)) collectDates(value);
      }
    };
    for (const script of document.querySelectorAll('script[type="application/ld+json"],script#jsdata')) {
      try { collectDates(JSON.parse(script.textContent)); } catch (_) { /* Optional metadata. */ }
    }
    for (const card of root.querySelectorAll('.a-card[data-id]')) {
      const id = card.getAttribute('data-id');
      const link = card.querySelector('a.a-card__title[href]');
      const title = text(link, 300);
      if (!title || !/^[1-9]\d{0,14}$/.test(id || '')) continue;
      const address = sameSiteUrl(link.href);
      const path = `/a/show/${id}`;
      if (!address || normalizedPath(address.pathname) !== path) continue;
      const date = published.get(path) || card.querySelector('time[itemprop="dateCreated"]')?.getAttribute('datetime');
      cards.push({
        url: `https://krisha.kz${path}`, title,
        price: text(card.querySelector('.a-card__price'), 200),
        description: text(card.querySelector('.a-card__text'), 1200),
        published_at: typeof date === 'string' && /T.*(?:Z|[+-]\d{2}:\d{2})$/.test(date) && Number.isFinite(Date.parse(date))
          ? new Date(date).toISOString() : null,
      });
    }
    if (!cards.length && !empty) return null;

    const current = Number(new URL(location.href).searchParams.get('page') || '1');
    const nextPage = [...document.querySelectorAll('a[href*="page="], [rel="next"][href]')]
      .map(link => sameSiteUrl(link.href))
      .find(url => url && normalizedPath(url.pathname) === normalizedPath(location.pathname)
        && Number(url.searchParams.get('page')) === current + 1);
    return {status: 'ready', cards, nextPage: nextPage?.href ?? null};
  };

  const observe = async () => {
    if (hasVerification()) {
      if (verificationStarted === null) {
        verificationStarted = Date.now();
        // Manually opened catalogs do not belong to the automatic scan.
        if (!(await send({status: 'verification'}))?.tracked) return;
      }
      const delay = Date.now() - verificationStarted < 15 * 60000 ? 5000 : 30000;
      setTimeout(observe, delay);
      return;
    }
    if (verificationStarted !== null) {
      verificationStarted = null;
      deadline = Date.now() + POST_LOAD_WAIT_MS;
    }
    const catalog = readCatalog();
    if (catalog) {
      await send(catalog);
    } else if (Date.now() >= deadline) {
      await send({status: 'unavailable'});
    } else {
      setTimeout(observe, 1000);
    }
  };

  void observe();
})();
