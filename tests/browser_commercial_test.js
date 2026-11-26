// Test catalog observation and the worker protocol without requesting Krisha.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {test} = require('node:test');

const pageSource = fs.readFileSync('browser-extension/commercial-page.js', 'utf8');
const workerSource = fs.readFileSync('browser-extension/commercial-worker.js', 'utf8');
const catalog = 'https://krisha.kz/arenda/kommercheskaya-nedvizhimost/ekibastuz/';
const page2 = catalog + '?das%5Brent.square%5D=2&page=2';
const catalogs = [
  catalog,
  'https://krisha.kz/prodazha/kommercheskaya-nedvizhimost/ekibastuz/',
  'https://krisha.kz/prodazha/biznes/ekibastuz/',
  'https://krisha.kz/arenda/prombazy/ekibastuz/',
  'https://krisha.kz/prodazha/prombazy/ekibastuz/',
];
const listing = {url: 'https://krisha.kz/a/show/123', title: 'Офис · 100 м²', price: '150 000 ₸', description: null, photo_url: null, published_at: null};
const photo = 'https://krisha-photos.kcdn.online/webp/c3/c3290962-7a14-489a-8e48-15b214009973/1-120x90.jpg';
const realCommercialPhoto = 'https://krisha-photos.kcdn.online/webp/93/93daefc9-3562-4e31-a268-f9ea667d4cfb/1-750x470.jpg';
const noPhotos = {gallery_roots: 0, primary_images: 0, trusted_links: 0};
const photoHash = '#apartment-agent-commercial-photo';
const clone = value => JSON.parse(JSON.stringify(value));
const flush = () => new Promise(setImmediate);

const imageElements = images => images.map(attributes => ({
  currentSrc: attributes.currentSrc,
  style: attributes.style,
  closest(selector) {
    if (attributes.secondary && selector.includes('recommend')) return {};
    if (attributes.hrefToListing && selector.includes('a[href*="/a/show/"]')) {
      return {getAttribute: name => name === 'href' ? attributes.hrefToListing : null};
    }
    return null;
  },
  getAttribute: name => attributes[name] || null,
}));

function pageEnvironment({challenge = false, ready = false, empty = false, tracked = true, images = [], metadata = [], detail = {}, rendered = false} = {}) {
  const env = {challenge, ready, empty, now: 0, posted: [], timers: [], detailRequests: []};
  const location = new URL(rendered ? listing.url + photoHash : catalog);
  const detailNodes = selector => {
    if (selector.startsWith('.offer__photos,')) return (detail.galleryMarkup || []).map(outerHTML => ({
      outerHTML, querySelector: () => detail.galleryHasRelated ? {} : null,
    }));
    if (selector.includes('img.gallery__main-image')) return imageElements(detail.images || []);
    if (selector === 'img[alt]') return imageElements(detail.altImages || []);
    if (selector.includes('main:has(.offer__price)')) return imageElements(detail.primaryImages || []);
    if (selector.includes('meta[name="og:image"]')) return imageElements((detail.photos || []).map(content => ({content})));
    if (selector.includes('script#jsdata')) return detail.scripts || [];
    return [];
  };
  const detailNode = selector => {
    if (selector === 'link[rel="canonical"]' && detail.canonical) return {getAttribute: () => detail.canonical};
    if (selector === 'h1' && detail.heading) return {textContent: detail.heading};
    return null;
  };
  const card = {
    getAttribute: name => name === 'data-id' ? '123' : '',
    querySelector(selector) {
      if (selector === 'a.a-card__title[href]') return {href: listing.url, textContent: listing.title};
      if (selector === '.a-card__price') return {textContent: listing.price};
      return null;
    },
    querySelectorAll(selector) {
      return selector.includes('picture[data-full-src]') ? imageElements(images) : [];
    },
  };
  const document = {
    // The actual SafeLine response includes this title element before it gets text.
    title: '',
    body: {get innerText() { return env.empty ? 'По вашему запросу ничего не найдено' : ''; }},
    documentElement: {get outerHTML() { return detail.html || ''; }},
    querySelector(selector) {
      if (selector.includes('title#slg-title')) return env.challenge ? {} : null;
      if (selector === '.a-search-list' && env.ready) return {querySelectorAll: () => [card]};
      return detailNode(selector);
    },
    querySelectorAll(selector) {
      if (selector.includes('a[href*="page="]')) return [
        {href: 'https://other.example/ekibastuz/?page=2'},
        {href: page2},
      ];
      if (!rendered && selector.includes('application/ld+json')) return metadata.map(value => ({textContent: JSON.stringify(value)}));
      return detailNodes(selector);
    },
    createElement(tag) {
      assert.equal(tag, 'template');
      return {
        set innerHTML(html) { assert.equal(html, detail.html || '<html></html>'); },
        content: {
          querySelector(selector) {
            if (selector.includes('title#slg-title')) return detail.challenge ? {} : null;
            return detailNode(selector);
          },
          querySelectorAll: detailNodes,
        },
      };
    },
  };
  const chrome = {runtime: {
    id: 'extension-id',
    async sendMessage(message) { env.posted.push(clone(message)); return {tracked}; },
    onMessage: {addListener(listener) { env.receive = listener; }},
  }};
  const fetch = async (url, options) => {
    env.detailRequests.push({url, options});
    const status = detail.status || 200;
    return {
      status, ok: status === 200,
      headers: {get: () => detail.type || 'text/html; charset=utf-8'},
      async text() { return detail.html || '<html></html>'; },
    };
  };
  const VMDate = class extends Date { static now() { return env.now; } };
  const window = {ApartmentAgentExtract: () => ({photo_urls: detail.extractedPhotos || []})};
  vm.runInNewContext(pageSource, {
    document, location, chrome, fetch, AbortSignal, URL, Date: VMDate, window,
    performance: {getEntriesByType: () => [{responseStatus: detail.navigationStatus}]},
    setTimeout(callback, delay) { env.timers.push({at: env.now + delay, callback}); },
  });
  env.render = () => window.ApartmentAgentReadCommercialPhoto(() => false);
  env.photo = url => new Promise(resolve => {
    assert.equal(env.receive({type: 'apartment-agent-commercial-photo', url}, {id: chrome.runtime.id}, reply => resolve(clone(reply))), true);
  });
  env.advance = async time => {
    await flush();
    while (env.timers.length && env.timers[0].at <= time) {
      const timer = env.timers.shift();
      env.now = timer.at;
      await timer.callback();
    }
    env.now = time;
  };
  return env;
}

function workerEnvironment() {
  const env = {state: {}, requests: [], updates: [], created: [], handlers: {}, tab: null, now: 0, alarm: null,
    detailMessages: [], detailResponse: {}, photoCandidates: [], warnings: [], renderedTabs: [], removedTabs: []};
  const chrome = {
    storage: {
      sync: {async get() { return {enabled: true, apiOrigin: 'http://localhost:8000'}; }},
      session: {async get() { return clone(env.state); }, async set(values) { env.state = clone(values); }},
    },
    tabs: {
      async create(options) {
        env.created.push(clone(options));
        if (options.url === 'about:blank') {
          env.photoTab = {id: 12 + env.renderedTabs.length, ...options};
          env.renderedTabs.push(env.photoTab);
          return clone(env.photoTab);
        }
        env.tab = {id: 11, ...options};
        return clone(env.tab);
      },
      async get(id) { return clone(id === env.photoTab?.id ? env.photoTab : env.tab); },
      async update(id, changed) {
        if (id === env.photoTab?.id) {
          Object.assign(env.photoTab, changed);
          return;
        }
        assert.equal(id, env.tab.id);
        env.updates.push(clone(changed));
        Object.assign(env.tab, changed);
      },
      async remove(id) {
        env.removedTabs.push(id);
        if (id === env.photoTab?.id) {
          env.photoTab = null;
          return;
        }
        assert.equal(id, env.tab.id);
        env.tab = null;
      },
      async sendMessage(id, message, options) {
        assert.equal(id, env.tab.id);
        env.detailMessages.push(clone({message, options}));
        return clone(env.detailResponse);
      },
    },
    runtime: {
      onInstalled: {addListener(callback) { env.handlers.install = callback; }},
      onStartup: {addListener(callback) { env.handlers.startup = callback; }},
      onMessage: {addListener(callback) { env.handlers.message = callback; }},
    },
    alarms: {
      async get() { return env.alarm; },
      async create(name, options) { env.alarm = {name, ...options}; },
      onAlarm: {addListener(callback) { env.handlers.alarm = callback; }},
    },
  };
  const fetch = async (url, options) => {
    assert.ok(url.startsWith('http://localhost:8000/api/v1/commercial/browser/'));
    const path = new URL(url).pathname.split('/').at(-1);
    const body = options.body ? JSON.parse(options.body) : null;
    env.requests.push({path, body});
    if (path === 'photo-candidates') {
      if (env.photoCandidatesUnavailable) return {ok: false, status: 404};
      return {ok: true, async json() { return {urls: env.photoCandidates}; }};
    }
    const response = path === 'plan'
      ? {enabled: true, catalogs, interval_minutes: 5}
      : {ok: true, catalog: body.catalog};
    return {ok: true, async json() { return response; }};
  };
  vm.runInNewContext(workerSource, {
    chrome, fetch, URL, AbortSignal, console: {warn: (...message) => env.warnings.push(message)},
    Date: {now: () => env.now},
    setTimeout(callback, delay) {
      if (delay === 18000) {
        env.renderTimeout = callback;
        return 1;
      }
      env.now += delay;
      callback();
    },
    clearTimeout() { env.renderTimeout = null; },
  });
  env.start = async () => {
    env.handlers.install();
    await flush();
  };
  env.send = (payload, overrides = {}) => new Promise(resolve => {
    const sender = {frameId: 0, tab: {id: env.tab.id}, url: env.tab.url, ...overrides};
    assert.equal(env.handlers.message({type: 'apartment-agent-commercial-page', ...clone(payload)}, sender, reply => resolve(clone(reply))), true);
  });
  env.renderSend = (type, payload = {}, overrides = {}) => new Promise(resolve => {
    const sender = {frameId: 0, tab: {id: env.photoTab.id}, url: env.photoTab.url, ...overrides};
    assert.equal(env.handlers.message({type: 'apartment-agent-commercial-photo-' + type, ...clone(payload)}, sender, reply => resolve(clone(reply))), false);
  });
  return env;
}

test('detects a SafeLine challenge with an empty title and resumes only when the DOM shows results', async () => {
  const env = pageEnvironment({challenge: true});
  await env.advance(5000);
  assert.deepEqual(env.posted.map(event => event.status), ['verification']);
  env.challenge = false;
  env.ready = true;
  await env.advance(10000);
  assert.deepEqual(env.posted.map(event => event.status), ['verification', 'ready']);
  assert.deepEqual(env.posted[1].cards, [listing]);
  assert.equal(env.posted[1].nextPage, page2);
});

test('passes through the lazy-loaded catalog photo and ignores placeholder and unrelated images', async () => {
  const env = pageEnvironment({ready: true, images: [
    {src: '/static/frontend/images/empty-photo-750.png'},
    {src: 'https://krisha-photos.kcdn.online/webp/empty-photo-120.jpg'},
    {src: 'https://krisha-photos.kcdn.online.other.example/1-120x90.jpg'},
    {src: 'data:image/png;base64,AA==', 'data-src': photo.replace(/^https:/, '')},
  ]});
  await env.advance(0);
  assert.equal(env.posted[0].cards[0].photo_url, photo);
});

test('reads a catalog picture or background and prefers a JPEG thumbnail', async () => {
  const full = photo.replace('kcdn.online', 'kcdn.kz').replace('120x90', 'full');
  const fromPicture = pageEnvironment({ready: true, images: [
    {'data-full-src': full}, {'data-srcset': photo.replace('.jpg', '.webp') + ' 120w'}, {srcset: photo + ' 120w'},
  ]});
  await fromPicture.advance(0);
  assert.equal(fromPicture.posted[0].cards[0].photo_url, photo);

  const fullOnly = pageEnvironment({ready: true, images: [{'data-full-src': full}]});
  await fullOnly.advance(0);
  assert.equal(fullOnly.posted[0].cards[0].photo_url, full);

  const background = pageEnvironment({ready: true, images: [{style: {backgroundImage: `url("${photo}")`}}]});
  await background.advance(0);
  assert.equal(background.posted[0].cards[0].photo_url, photo);
});

test('recovers a source-set or listing-matched structured photo without mixing in recommended listings', async () => {
  const fromSource = pageEnvironment({ready: true, images: [{srcset: photo + ' 120w'}]});
  await fromSource.advance(0);
  assert.equal(fromSource.posted[0].cards[0].photo_url, photo);

  const fromMetadata = pageEnvironment({ready: true, metadata: [{
    recommendations: [{url: listing.url, image: photo.replace('1-120x90', '2-120x90')}],
    itemListElement: [
      {url: '/a/show/999', image: photo.replace('1-120x90', '3-120x90')},
      {url: '/a/show/123', image: [{contentUrl: photo}]},
    ],
  }]});
  await fromMetadata.advance(0);
  assert.equal(fromMetadata.posted[0].cards[0].photo_url, photo);
});

test('reads a public photo from the canonical detail page using one same-origin request', async () => {
  const env = pageEnvironment({ready: true, detail: {
    canonical: listing.url,
    photos: ['https://not-krisha.example/other.jpg', photo],
  }});
  assert.equal(env.receive({type: 'apartment-agent-commercial-photo', url: listing.url}, {id: 'other-extension'}, () => {}), false);
  assert.deepEqual(await env.photo('https://other.example/a/show/123'), {status: 'content_unavailable'});
  assert.deepEqual(await env.photo(listing.url + '?foreign=1'), {status: 'content_unavailable'});
  assert.deepEqual(await env.photo(listing.url), {status: 'found', photo_url: photo, photo_urls: [photo], http_status: 200});
  assert.equal(env.detailRequests.length, 1);
  const [{url, options}] = env.detailRequests;
  assert.equal(url, listing.url);
  assert.equal(options.mode, 'same-origin');
  assert.equal(options.credentials, 'same-origin');
  assert.equal(options.redirect, 'error');
  assert.ok(options.signal instanceof AbortSignal);
});

test('does not use photos from inaccessible, unexpected or browser-verification detail pages', async () => {
  const gallery = {images: [{'data-full-src': photo}]};
  assert.deepEqual(await pageEnvironment({detail: gallery}).photo(listing.url), {status: 'found', photo_url: photo, photo_urls: [photo], http_status: 200});
  const redirected = pageEnvironment({detail: {...gallery, canonical: listing.url.replace('/123', '/456')}});
  assert.deepEqual(await redirected.photo(listing.url), {status: 'canonical_mismatch', http_status: 200});
  for (const status of [403, 429, 468]) {
    const env = pageEnvironment({detail: {status, ...gallery}});
    assert.deepEqual(await env.photo(listing.url), {status: 'http_unavailable', http_status: status});
    assert.equal(env.detailRequests.length, 1);
  }
  const detailChallenge = pageEnvironment({detail: {challenge: true, ...gallery}});
  assert.deepEqual(await detailChallenge.photo(listing.url), {status: 'verification', http_status: 200});
  const catalogChallenge = pageEnvironment({challenge: true});
  assert.deepEqual(await catalogChallenge.photo(listing.url), {status: 'verification'});
  assert.equal(catalogChallenge.detailRequests.length, 0);
});

test('prefers the full detail image and uses structured photos only for the requested listing', async () => {
  const highRes = photo.replace('120x90', 'full');
  const ogImage = pageEnvironment({detail: {images: [{src: photo}], photos: [highRes]}});
  assert.deepEqual(await ogImage.photo(listing.url), {status: 'found', photo_url: highRes, photo_urls: [highRes], http_status: 200});
  const scripts = [
    {textContent: JSON.stringify({
      similarAds: [{url: listing.url, image: photo.replace('1-', '2-')}],
      itemListElement: [{url: '/a/show/456', image: photo.replace('1-', '3-')}],
    })},
    {id: 'jsdata', textContent: 'window.data = ' + JSON.stringify({advert: {id: 123, images: [{url: highRes}]}}) + ';'},
  ];
  const fromJson = pageEnvironment({detail: {scripts}});
  assert.deepEqual(await fromJson.photo(listing.url), {status: 'found', photo_url: highRes, photo_urls: [highRes], http_status: 200});
  const unrelated = pageEnvironment({detail: {scripts: scripts.slice(0, 1)}});
  assert.deepEqual(await unrelated.photo(listing.url), {status: 'metadata_missing', photo_diagnostics: noPhotos, http_status: 200});
});

test('reads Krisha commercial alt text and joins full and thumbnail variants without adding other listings', async () => {
  const heading = 'Свободное назначение · 380 м²';
  const alt = 'Аренда коммерческой недвижимости в Павлодарской обл.: ' + heading + ' за 1.5 млн ₸';
  const thumbnail = realCommercialPhoto.replace('750x470', '120x90');
  const second = thumbnail.replace('/1-', '/2-');
  const foreignPhoto = thumbnail.replace('/1-', '/3-');
  const env = pageEnvironment({detail: {heading, altImages: [
    {alt: 'empty photo', src: '/static/frontend/images/empty-photo-750.png'},
    {alt, src: thumbnail},
    {alt, 'data-lazy-src': realCommercialPhoto},
    {alt: alt + ' — фото 2', src: second},
    {alt, src: foreignPhoto, hrefToListing: '/a/show/456'},
    {alt, src: foreignPhoto, secondary: true},
  ]}});
  assert.deepEqual(await env.photo(listing.url), {
    status: 'found', photo_url: realCommercialPhoto, photo_urls: [realCommercialPhoto, second], http_status: 200,
  });
});

test('reads CDN links from an owned gallery widget and only reports counts for unbound markup', async () => {
  const html = `<html><div data-photos='["${realCommercialPhoto}"]'></div></html>`;
  const galleryMarkup = [`<div data-photos='["${realCommercialPhoto.replaceAll('/', '\\/')}"]'></div>`];
  const own = pageEnvironment({detail: {html, galleryMarkup}});
  assert.deepEqual(await own.photo(listing.url), {
    status: 'found', photo_url: realCommercialPhoto, photo_urls: [realCommercialPhoto], http_status: 200,
  });
  const related = pageEnvironment({detail: {html, galleryMarkup, galleryHasRelated: true}});
  assert.deepEqual(await related.photo(listing.url), {
    status: 'metadata_missing', photo_diagnostics: {gallery_roots: 1, primary_images: 0, trusted_links: 1}, http_status: 200,
  });
});

test('recognizes the CDN hostname observed in the commercial gallery and rejects lookalike hosts', async () => {
  const commercialCdn = 'https://kzala2-photos-kr.kcdn.kz/webp/82/8287575c-025b-41e9-88da-51970eb69f79/1-750x470.webp';
  const lookalike = commercialCdn.replace('.kcdn.kz/', '.kcdn.kz.other.example/').replace('/1-', '/2-');
  const galleryMarkup = [`<div data-photos='["${commercialCdn}","${lookalike}"]'></div>`];
  const env = pageEnvironment({detail: {images: [{srcset: commercialCdn + ' 750w'}, {src: lookalike}], galleryMarkup}});
  assert.deepEqual(await env.photo(listing.url), {
    status: 'found', photo_url: commercialCdn, photo_urls: [commercialCdn], http_status: 200,
  });
  const widgetOnly = pageEnvironment({detail: {galleryMarkup}});
  assert.deepEqual(await widgetOnly.photo(listing.url), {
    status: 'found', photo_url: commercialCdn, photo_urls: [commercialCdn], http_status: 200,
  });
});

test('a claimed rendered listing picks a real public photo without issuing another document fetch', async () => {
  const env = pageEnvironment({rendered: true, detail: {images: [
    {src: '/static/frontend/images/empty-photo-750.png'},
    {src: photo},
  ]}});
  const render = env.render();
  await env.advance(8000);
  assert.equal(await render, true);
  assert.deepEqual(env.posted, [
    {type: 'apartment-agent-commercial-photo-claim'},
    {type: 'apartment-agent-commercial-photo-rendered', status: 'found', photo_url: photo, photo_urls: [photo]},
  ]);
  assert.equal(env.detailRequests.length, 0);
  const unowned = pageEnvironment({rendered: true, tracked: false, detail: {images: [{src: photo}]}});
  assert.equal(await unowned.render(), false);
  assert.deepEqual(unowned.posted, [{type: 'apartment-agent-commercial-photo-claim'}]);
});

test('rendered lookup uses approved photos from the shared extractor after waiting for the gallery', async () => {
  const env = pageEnvironment({rendered: true, detail: {extractedPhotos: ['https://other.example/photo.jpg', photo]}});
  const render = env.render();
  await env.advance(8000);
  assert.equal(await render, true);
  assert.equal(env.posted.at(-1).photo_url, photo);
});

test('rendered lookup collects the late gallery and recognizes navigation-level restrictions', async () => {
  const detail = {images: [{src: '/static/frontend/images/empty-photo-750.png'}]};
  const env = pageEnvironment({rendered: true, detail});
  const render = env.render();
  await env.advance(4500);
  const second = realCommercialPhoto.replace('/1-', '/2-');
  detail.images = [{src: realCommercialPhoto}, {'data-full-src': second}];
  await env.advance(8000);
  assert.equal(await render, true);
  assert.deepEqual(env.posted.at(-1).photo_urls, [realCommercialPhoto, second]);

  const blocked = pageEnvironment({rendered: true, detail: {navigationStatus: 468, images: detail.images}});
  assert.equal(await blocked.render(), true);
  assert.deepEqual(blocked.posted.at(-1), {
    type: 'apartment-agent-commercial-photo-rendered', status: 'verification', http_status: 468,
  });

  const wrongId = pageEnvironment({rendered: true, detail: {canonical: 'https://krisha.kz/a/show/321', images: detail.images}});
  assert.equal(await wrongId.render(), true);
  assert.deepEqual(wrongId.posted.at(-1), {type: 'apartment-agent-commercial-photo-rendered', status: 'canonical_mismatch'});
});
test('does not observe a challenge after the worker rejects an unowned tab', async () => {
  const env = pageEnvironment({challenge: true, tracked: false});
  await env.advance(30000);
  assert.deepEqual(env.posted.map(event => event.status), ['verification']);
  assert.equal(env.timers.length, 0);
});

test('permits delayed catalog DOM and distinguishes an explicit empty result from a load failure', async () => {
  const pending = pageEnvironment();
  await pending.advance(8000);
  assert.deepEqual(pending.posted, []);
  pending.ready = true;
  await pending.advance(9000);
  assert.equal(pending.posted[0].status, 'ready');

  const unavailable = pageEnvironment();
  await unavailable.advance(15000);
  assert.deepEqual(unavailable.posted.map(event => event.status), ['unavailable']);

  const empty = pageEnvironment({empty: true});
  await empty.advance(0);
  assert.deepEqual(empty.posted.map(event => [event.status, event.cards]), [['ready', []]]);
});

test('uses canonical page 1, pauses on verification, and follows the catalog’s actual next URL', async () => {
  const env = workerEnvironment();
  await env.start();
  assert.deepEqual(env.created, [{url: catalog, active: false}]);

  const unowned = await env.send({status: 'verification'}, {tab: {id: 55}});
  assert.equal(unowned.tracked, false);
  assert.deepEqual(env.requests.map(request => request.path), ['plan']);

  const owned = await env.send({status: 'verification'});
  assert.equal(owned.tracked, true);
  assert.deepEqual(env.updates, [{active: true}]);
  assert.deepEqual(env.requests.at(-1), {path: 'report', body: {catalog, status: 'verification'}});
  await env.send({status: 'verification'});
  assert.equal(env.requests.filter(request => request.path === 'report').length, 1);

  await env.send({status: 'ready', cards: [listing], nextPage: page2});
  assert.equal(env.tab.url, page2);
  assert.equal(env.requests.filter(request => request.path === 'scan').length, 0);
  await env.send({status: 'ready', cards: [], nextPage: null});
  assert.deepEqual(env.requests.at(-1), {path: 'scan', body: {catalog, cards: [listing], photo_lookup: {status: 'candidate_missing'}}});
  assert.equal(env.tab.url, catalogs[1]);
});

test('starts after an unpacked extension reload even when onInstalled is not dispatched', async () => {
  const env = workerEnvironment();
  await flush();
  assert.deepEqual(env.created, [{url: catalog, active: false}]);
  assert.equal(env.alarm.periodInMinutes, 1);
  await env.start();
  assert.equal(env.created.length, 1);
  assert.equal(env.requests.filter(request => request.path === 'plan').length, 1);
});

test('preserves the first verified page if the next page fails without claiming a complete scan', async () => {
  const env = workerEnvironment();
  await env.start();
  await env.send({status: 'ready', cards: [listing], nextPage: page2});
  await env.send({status: 'unavailable'});
  assert.deepEqual(env.requests.slice(1), [
    {path: 'photo-candidates', body: {catalog, cards: [listing], complete: false}},
    {path: 'scan', body: {catalog, cards: [listing], complete: false, photo_lookup: {status: 'candidate_missing'}}},
    {path: 'report', body: {catalog, status: 'unavailable'}},
  ]);
  assert.equal(env.tab.url, catalogs[1]);
});

test('never records a failed first page as an empty successful catalog', async () => {
  const env = workerEnvironment();
  await env.start();
  await env.send({status: 'unavailable'});
  assert.deepEqual(env.requests.slice(1), [{path: 'report', body: {catalog, status: 'unavailable'}}]);
  assert.equal(env.tab.url, catalogs[1]);
});

test('loads at most three selected listing galleries per catalog before saving the scan', async () => {
  const env = workerEnvironment();
  await env.start();
  const cards = [listing, ...[456, 789, 987].map(id => ({...listing, url: `https://krisha.kz/a/show/${id}`}))];
  env.photoCandidates = cards.map(card => card.url);
  env.detailResponse = {status: 'found', photo_url: photo, photo_urls: [photo], http_status: 200};
  await env.send({status: 'ready', cards, nextPage: null});
  const selected = cards.slice(0, 3);
  assert.deepEqual(env.detailMessages, selected.map(({url}) => ({
    message: {type: 'apartment-agent-commercial-photo', url}, options: {frameId: 0},
  })));
  const photo_lookups = selected.map(({url}) => ({status: 'found', url, http_status: 200}));
  assert.deepEqual(env.requests.slice(1), [
    {path: 'photo-candidates', body: {catalog, cards}},
    {path: 'scan', body: {catalog, cards: [
      ...selected.map(card => ({...card, photo_url: photo, photo_urls: [photo]})), cards[3],
    ], photo_lookup: photo_lookups[0], photo_lookups}},
  ]);
  assert.equal(env.tab.url, catalogs[1]);
});

test('ignores candidates from outside the catalog and saves when the preflight endpoint is unavailable', async () => {
  const env = workerEnvironment();
  await env.start();
  env.photoCandidates = ['https://krisha.kz/a/show/456'];
  await env.send({status: 'ready', cards: [listing], nextPage: null});
  assert.equal(env.detailMessages.length, 0);
  assert.deepEqual(env.requests.at(-1), {path: 'scan', body: {catalog, cards: [listing], photo_lookup: {status: 'candidate_missing'}}});

  env.photoCandidatesUnavailable = true;
  await env.send({status: 'ready', cards: [listing], nextPage: null});
  assert.deepEqual(env.requests.at(-1), {path: 'scan', body: {catalog: catalogs[1], cards: [listing], photo_lookup: {status: 'preflight_unavailable'}}});
  assert.equal(env.tab.url, catalogs[2]);
});

test('stops optional detail requests for the remaining cycle when Krisha restricts photos', async () => {
  const env = workerEnvironment();
  await env.start();
  env.photoCandidates = [listing.url];
  env.detailResponse = {status: 'http_unavailable', http_status: 468};
  for (const catalogUrl of catalogs) {
    await env.send({status: 'ready', cards: [listing], nextPage: null});
    const photo_lookup = catalogUrl === catalog
      ? {status: 'http_unavailable', url: listing.url, http_status: 468} : {status: 'not_needed'};
    assert.deepEqual(env.requests.at(-1), {path: 'scan', body: {catalog: catalogUrl, cards: [listing], photo_lookup}});
  }
  assert.equal(env.detailMessages.length, 1);
  assert.equal(env.requests.filter(request => request.path === 'photo-candidates').length, 1);
  assert.equal(env.warnings.length, 1);
  assert.equal(env.tab, null);
});

test('fills a photo from one owned rendered tab when the catalog detail HTML had no photo', async () => {
  const env = workerEnvironment();
  await env.start();
  env.photoCandidates = [listing.url];
  env.detailResponse = {status: 'metadata_missing', http_status: 200};
  const catalogJob = env.send({status: 'ready', cards: [listing], nextPage: null});
  await flush();
  assert.equal(env.photoTab.url, listing.url + photoHash);
  assert.equal(env.photoTab.active, false);
  assert.deepEqual(await env.renderSend('claim', {}, {frameId: 1}), {tracked: false});
  assert.deepEqual(await env.renderSend('claim', {}, {url: listing.url.replace('/123', '/321')}), {tracked: false});
  assert.deepEqual(await env.renderSend('claim', {}, {url: listing.url}), {tracked: true});
  assert.deepEqual(await env.renderSend('rendered', {status: 'found', photo_url: photo, photo_urls: [photo]}), {tracked: true});
  assert.deepEqual(await catalogJob, {tracked: true});
  assert.equal(env.photoTab, null);
  assert.deepEqual(env.removedTabs, [12]);
  assert.deepEqual(env.requests.at(-1), {path: 'scan', body: {
    catalog, cards: [{...listing, photo_url: photo, photo_urls: [photo]}],
    photo_lookup: {status: 'found', url: listing.url, rendered: true},
  }});
  assert.equal(env.tab.url, catalogs[1]);
});

test('an unreadable rendered tab closes and saves its catalog with no retries', async () => {
  const env = workerEnvironment();
  await env.start();
  env.photoCandidates = [listing.url];
  env.detailResponse = {status: 'metadata_missing', http_status: 200};
  const catalogJob = env.send({status: 'ready', cards: [listing], nextPage: null});
  await flush();
  env.renderTimeout();
  assert.deepEqual(await catalogJob, {tracked: true});
  assert.equal(env.photoTab, null);
  assert.equal(env.detailMessages.length, 1);
  assert.equal(env.renderedTabs.length, 1);
  assert.equal(env.requests.at(-1).body.photo_lookup.status, 'render_timeout');
  assert.equal(env.requests.at(-1).body.photo_lookup.rendered, true);
});

test('verification on a rendered listing closes the temporary tab and skips later detail lookups', async () => {
  const detail = pageEnvironment({rendered: true, challenge: true, detail: {images: [{src: photo}]}});
  assert.equal(await detail.render(), true);
  assert.deepEqual(detail.posted.at(-1), {type: 'apartment-agent-commercial-photo-rendered', status: 'verification'});

  const env = workerEnvironment();
  await env.start();
  env.photoCandidates = [listing.url];
  env.detailResponse = {status: 'metadata_missing', http_status: 200};
  const catalogJob = env.send({status: 'ready', cards: [listing], nextPage: null});
  await flush();
  await env.renderSend('rendered', {status: 'verification'});
  assert.deepEqual(await catalogJob, {tracked: true});
  assert.equal(env.requests.at(-1).body.photo_lookup.status, 'verification');
  assert.equal(env.photoTab, null);
  await env.send({status: 'ready', cards: [listing], nextPage: null});
  assert.deepEqual(env.requests.at(-1).body.photo_lookup, {status: 'not_needed'});
  assert.equal(env.detailMessages.length, 1);
});
