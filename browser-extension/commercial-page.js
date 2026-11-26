// Read the catalog the browser actually displays. Krisha verification is left to the user.
(() => {
  const catalogPage = /^\/(?:arenda\/(?:kommercheskaya-nedvizhimost|prombazy)|prodazha\/(?:kommercheskaya-nedvizhimost|biznes|prombazy))\/ekibastuz\/?$/.test(location.pathname);
  const renderedLookup = /^\/a\/show\/[1-9]\d{0,14}$/.test(location.pathname) && location.hash === '#apartment-agent-commercial-photo';
  if (!catalogPage && !renderedLookup) return;
  const POST_LOAD_WAIT_MS = 15000;
  let deadline = Date.now() + POST_LOAD_WAIT_MS;
  let verificationStarted = null;

  const send = payload => chrome.runtime.sendMessage({type: 'apartment-agent-commercial-page', ...payload}).catch(() => null);
  const text = (node, max) => node?.textContent?.replace(/\s+/g, ' ').trim().slice(0, max) || null;
  const hasVerification = (root = document) => Boolean(
    root.querySelector('#sl-check, title#slg-title, link#kg-favicon[href*="/.safeline/"]')
    || /проверка браузера|подтвердите, что вы не робот|checking your browser|security verification/i
      .test(root.title || root.querySelector('title')?.textContent || '')
  );
  const sameSiteUrl = value => {
    try {
      const address = new URL(value, location.href);
      return address.origin === location.origin ? address : null;
    } catch (_) { return null; }
  };
  const normalizedPath = path => path.replace(/\/$/, '');
  const photoHosts = new Set(['krisha-photos.kcdn.online', 'krisha-photos.kcdn.kz', 'photos.krisha.kz', 'kzala2-photos-kr.kcdn.kz']);
  const publicPhotoUrl = value => {
    if (typeof value !== 'string' || !value.trim()) return null;
    try {
      const url = new URL(value, location.href);
      if (url.protocol !== 'https:' || !photoHosts.has(url.hostname) || url.port || url.username || url.password
        || !/\.(?:jpe?g|png|webp)$/i.test(url.pathname)
        || /(?:^|\/)(?:empty-photo|no-photo|nophoto|placeholder)(?:[._/-]|$)/i.test(url.pathname)
        || url.href.length > 2048) return null;
      url.hash = '';
      return url.href;
    } catch (_) { return null; }
  };

  const telegramImage = url => /\.(?:jpe?g|png)(?:\?|$)/i.test(url);
  const photoHostPattern = [...photoHosts].map(host => host.replaceAll('.', '\\.')).join('|');
  const cdnPhotoPattern = new RegExp(String.raw`(?:^|[^:\w])((?:https:)?//(?:${photoHostPattern})/[^\s"'<>\\[\]{}(),]+)`, 'gi');
  function photoLinks(value) {
    const source = String(value || '').replace(/\\u002f/gi, '/').replace(/\\\//g, '/').replace(/&amp;/g, '&');
    return Array.from(source.matchAll(cdnPhotoPattern), match => publicPhotoUrl(match[1])).filter(Boolean);
  }
  const jsonPhoto = node => [node.image, node.images, node.photo, node.photos, node.photoUrls, node.photo_urls, node.imageUrls, node.gallery, node.pictures]
    .filter(Boolean).flatMap(value => {
      if (typeof value !== 'string') return photoLinks(JSON.stringify(value));
      const direct = publicPhotoUrl(value);
      return direct ? [direct] : photoLinks(value);
    });
  const secondaryBranch = key => /^(?:recommend|related|similar|suggest|nearby|other|seller|author|owner)/i.test(key);

  function imageChoices(elements) {
    const previews = [];
    const fullPhotos = [];
    const ordered = [];
    for (const image of elements) {
      const sources = ['data-srcset', 'srcset', 'data-lazy-srcset'].flatMap(name =>
        (image.getAttribute(name) || '').split(',').map(value => value.trim().split(/\s+/, 1)[0]));
      const background = image.style?.backgroundImage?.match(/^url\((["']?)(.*?)\1\)$/i)?.[2];
      const values = ['data-src', 'data-original', 'data-lazy-src', 'src', 'data-image', 'data-photo']
        .map(name => image.getAttribute(name)).concat(image.currentSrc, background, ...sources);
      const publicValues = values.map(publicPhotoUrl).filter(Boolean);
      previews.push(...publicValues);
      ordered.push(...publicValues);
      for (const name of ['data-full-src', 'data-full', 'data-large', 'href', 'content']) {
        const full = publicPhotoUrl(image.getAttribute(name));
        if (full) {
          fullPhotos.push(full);
          ordered.push(full);
        }
      }
    }
    return {previews, fullPhotos, ordered};
  }

  function galleryPhotos(values) {
    const best = new Map();
    for (const url of values) {
      const path = new URL(url).pathname;
      const key = path.replace(/^\/webp\//i, '/').replace(/(?:-(?:full|\d+x\d+))?\.[a-z]+$/i, '');
      const size = path.match(/-(\d+)x(\d+)\./i);
      const score = (telegramImage(url) ? 1000000000 : 0) + (size ? Number(size[1]) * Number(size[2]) : 100000000);
      if (!best.has(key) || score > best.get(key).score) best.set(key, {url, score});
    }
    return [...best.values()].slice(0, 30).map(photo => photo.url);
  }

  function cardPhoto(card) {
    const {previews, fullPhotos} = imageChoices(card.querySelectorAll('img, picture source, picture[data-full-src], .a-card__image, .a-card__image [style]'));
    const image = previews.find(telegramImage) || fullPhotos.find(telegramImage) || previews[0] || fullPhotos[0];
    return image || photoLinks(card.querySelector('.a-card__image')?.outerHTML)[0] || null;
  }

  function jsonScript(script) {
    const source = script.textContent?.trim() || '';
    const assignment = script.id === 'jsdata' ? source.match(/^window\.data\s*=\s*([\s\S]+?);?$/) : null;
    try { return JSON.parse(assignment?.[1] || source); } catch (_) { return null; }
  }

  function structuredDetailPhotos(parsed, path) {
    const photos = [];
    const targetId = path.split('/').at(-1);
    const collect = (node, key) => {
      if (Array.isArray(node)) {
        node.forEach(value => collect(value, key));
        return;
      }
      if (!node || typeof node !== 'object') return;
      const ids = [node.advertId, node.advert_id, node.listingId, node.listing_id, /^(?:advert|offer|listing)$/.test(key) ? node.id : null]
        .filter(value => typeof value === 'string' || typeof value === 'number').map(String).filter(value => /^\d+$/.test(value));
      for (const value of [node.url, node['@id'], node.canonicalUrl, node.source_url]) {
        if (typeof value !== 'string') continue;
        const match = sameSiteUrl(value)?.pathname.match(/^\/a\/show\/(\d+)\/?$/);
        if (match) ids.push(match[1]);
      }
      if (ids.some(id => id !== targetId)) return;
      if (ids.length) photos.push(...jsonPhoto(node));
      for (const [name, child] of Object.entries(node)) {
        if (!secondaryBranch(name)) collect(child, name);
      }
    };
    for (const script of parsed.querySelectorAll('script[type="application/ld+json"], script[type="application/json"], script#jsdata')) collect(jsonScript(script), '');
    return photos;
  }

  const gallery = '.offer__photos, .offer__gallery, .gallery, .a-gallery, [class^="gallery__"], [class*=" gallery__"], [data-gallery], [data-photos], [data-testid$="gallery"], [data-name="photos"], [itemprop="image"]';
  const primary = '.offer, .offer__content, .offer__container, .offer__advert, main:has(.offer__price)';
  const secondary = '.a-card, [class*="similar" i], [class*="recommend" i], [class*="related" i], [class*="seller" i], [class*="author" i]';
  const linkedListing = 'a[href*="/a/show/"], [data-advert-id], [data-listing-id]';
  const mediaTypes = 'img, picture, source, a[href], [data-src], [data-full-src], [data-photos], [style]';
  function belongsToListing(element, path) {
    if (element.closest?.(secondary)) return false;
    const linked = element.closest?.(linkedListing);
    if (!linked) return true;
    const id = linked.getAttribute('data-advert-id') || linked.getAttribute('data-listing-id');
    if (id && id !== path.split('/').at(-1)) return false;
    const href = linked.getAttribute('href');
    return !href || normalizedPath(sameSiteUrl(href)?.pathname || '') === path;
  }

  function detailPagePhotos(root, path) {
    const galleryRoots = [...root.querySelectorAll(gallery)].filter(node => belongsToListing(node, path));
    const galleryMedia = `:is(${gallery}) :is(${mediaTypes}), img.gallery__main-image, picture[itemprop="image"], img[itemprop="image"]`;
    const galleryElements = [...root.querySelectorAll(galleryMedia)].filter(node => belongsToListing(node, path));
    const primaryElements = [...root.querySelectorAll(`:is(${primary}) :is(${mediaTypes})`)].filter(node => belongsToListing(node, path));
    const galleryChoices = imageChoices(galleryElements);
    const primaryChoices = imageChoices(primaryElements);
    const heading = text(root.querySelector('h1'), 180)?.toLowerCase();
    const altImages = heading && heading.length >= 12 ? [...root.querySelectorAll('img[alt]')].filter(node => {
      const alt = node.getAttribute('alt')?.toLowerCase().replace(/\s+/g, ' ');
      return /^(?:аренда|продажа) коммерческой недвижимости(?:\s|[.:—-]|$)/i.test(alt)
        && alt.includes(heading) && belongsToListing(node, path);
    }) : [];
    const metaPhoto = ['og:image', 'og:image:secure_url', 'twitter:image']
      .flatMap(name => [...root.querySelectorAll(`meta[property="${name}"], meta[name="${name}"]`)])
      .map(element => publicPhotoUrl(element.getAttribute('content'))).filter(Boolean);
    // Some gallery widgets embed their images in HTML attributes or inert content.
    // Keep the raw lookup inside gallery roots without related listing branches.
    const galleryMarkup = galleryRoots.filter(node => !node.parentElement?.closest(gallery))
      .flatMap(node => photoLinks(node.querySelector?.(`${secondary}, ${linkedListing}`) ? '' : node.outerHTML));
    const structured = structuredDetailPhotos(root, path);
    const own = [...galleryChoices.ordered, ...galleryMarkup, ...structured, ...imageChoices(altImages).ordered, ...metaPhoto];
    // The whole offer can include advertiser images. Use its media only as a fallback.
    const primaryPhotos = primaryChoices.ordered;
    const photos = galleryPhotos(own.length ? own : primaryPhotos);
    return {photos, gallery_roots: galleryRoots.length, primary_images: primaryPhotos.length};
  }

  function photoResult(extracted, raw) {
    const {photos, gallery_roots, primary_images} = extracted;
    if (photos.length) return {status: 'found', photo_url: photos.find(telegramImage) || photos[0], photo_urls: photos};
    return {
      status: 'metadata_missing',
      photo_diagnostics: {gallery_roots, primary_images, trusted_links: new Set(photoLinks(raw)).size},
    };
  }

  function canonicalMatches(root, path) {
    const value = root.querySelector('link[rel="canonical"]')?.getAttribute('href');
    if (!value) return true;
    try {
      const canonical = new URL(value, location.href);
      return canonical.protocol === 'https:' && ['krisha.kz', 'www.krisha.kz'].includes(canonical.hostname)
        && normalizedPath(canonical.pathname) === path;
    } catch (_) { return false; }
  }

  async function detailPhoto(url) {
    if (typeof url !== 'string' || !/^https:\/\/krisha\.kz\/a\/show\/[1-9]\d{0,14}$/.test(url) || !sameSiteUrl(url)) return {status: 'content_unavailable'};
    if (hasVerification()) return {status: 'verification'};
    const response = await fetch(url, {
      mode: 'same-origin', credentials: 'same-origin', redirect: 'error', signal: AbortSignal.timeout(8000),
    });
    const http_status = response.status;
    if (!response.ok || !response.headers.get('content-type')?.toLowerCase().includes('text/html')) return {status: 'http_unavailable', http_status};
    const html = await response.text();
    if (html.length > 4000000) return {status: 'content_unavailable', http_status};
    const template = document.createElement('template');
    template.innerHTML = html;
    const parsed = template.content;
    if (hasVerification(parsed)) return {status: 'verification', http_status};
    const path = new URL(url).pathname;
    if (!canonicalMatches(parsed, path)) return {status: 'canonical_mismatch', http_status};

    return {...photoResult(detailPagePhotos(parsed, path), html), http_status};
  }

  if (renderedLookup) {
    window.ApartmentAgentReadCommercialPhoto = async challengeDetected => {
      const claim = await chrome.runtime.sendMessage({type: 'apartment-agent-commercial-photo-claim'}).catch(() => null);
      if (!claim?.tracked) return false;
      let extracted;
      let result;
      let photos = [];
      // Let an ordinary page render lazy photos, with a total wait of eight seconds.
      for (const delay of [0, 700, 1500, 2300, 3500]) {
        if (delay) await new Promise(resolve => setTimeout(resolve, delay));
        const navigationStatus = globalThis.performance?.getEntriesByType?.('navigation')?.[0]?.responseStatus;
        if ([403, 429, 468].includes(navigationStatus) || hasVerification() || challengeDetected()) {
          result = {status: 'verification', ...(navigationStatus ? {http_status: navigationStatus} : {})};
          break;
        }
        if (!canonicalMatches(document, location.pathname)) {
          result = {status: 'canonical_mismatch'};
          break;
        }
        extracted = detailPagePhotos(document, location.pathname);
        const previousCount = photos.length;
        photos = galleryPhotos([...photos, ...extracted.photos]);
        if (photos.length >= 30 || (photos.length >= 2 && photos.length === previousCount)) break;
      }
      if (!result) {
        if (!photos.length) {
          let known = [];
          try { known = window.ApartmentAgentExtract?.()?.photo_urls || []; } catch (_) { /* Optional extractor. */ }
          photos = galleryPhotos(known.map(publicPhotoUrl).filter(Boolean));
        }
        result = photoResult({...extracted, photos}, photos.length ? '' : document.documentElement?.outerHTML);
      }
      await chrome.runtime.sendMessage({type: 'apartment-agent-commercial-photo-rendered', ...result}).catch(() => null);
      return true;
    };
    return;
  }

  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message?.type !== 'apartment-agent-commercial-photo' || sender.id !== chrome.runtime.id) return false;
    void detailPhoto(message.url).then(sendResponse).catch(() => sendResponse({status: 'redirect_or_fetch_error'}));
    return true;
  });

  const readCatalog = () => {
    const root = document.querySelector('.a-search-list');
    const empty = /найдено\s+0\s+объявлен|по вашему запросу ничего не найдено|нет подходящих объявлений/i
      .test(document.querySelector('main')?.innerText || document.body?.innerText || '');
    if (!root) return empty ? {status: 'ready', cards: [], nextPage: null} : null;

    const cards = [];
    const published = new Map();
    const photos = new Map();
    const collectMetadata = (node) => {
      if (Array.isArray(node)) {
        node.forEach(collectMetadata);
        return;
      }
      if (!node || typeof node !== 'object') return;
      if (typeof node.url === 'string') {
        const url = sameSiteUrl(node.url);
        if (url && /^\/a\/show\/\d+\/?$/.test(url.pathname)) {
          const path = normalizedPath(url.pathname);
          if (typeof node.dateCreated === 'string') published.set(path, node.dateCreated);
          const photo = jsonPhoto(node)[0];
          if (photo && !photos.has(path)) photos.set(path, photo);
        }
      }
      for (const [key, value] of Object.entries(node)) {
        if (!secondaryBranch(key)) collectMetadata(value);
      }
    };
    for (const script of document.querySelectorAll('script[type="application/ld+json"],script#jsdata')) collectMetadata(jsonScript(script));
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
        photo_url: cardPhoto(card) || photos.get(path) || null,
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
