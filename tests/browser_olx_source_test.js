const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('app/static/app.js', 'utf8');
const helpers = source.slice(source.indexOf('function listingPlatform('), source.indexOf('function olxBadge('));
const {isOlxUrl, sourceFrameClass} = vm.runInNewContext(`${helpers}\n({isOlxUrl, sourceFrameClass})`, {URL});
for (const url of ['https://olx.kz/a', 'https://www.olx.kz/a', 'HTTPS://OLX.KZ/a']) assert.equal(isOlxUrl(url), true, url);
for (const url of ['https://olx.kz.example/a', 'https://example.com/olx.kz', 'https://example.com/?url=olx.kz', 'https://sub.olx.kz/a', 'https://user@olx.kz/a', 'javascript://olx.kz/a', '/olx.kz', '', null]) assert.equal(isOlxUrl(url), false, String(url));
assert.match(source, /olxBadge\(item\.source_url\)/);
assert.match(source, /olxBadge\(item\.url\)/);
assert.match(source, /duplicateSourceBadge\(offer\.source_url\)/);
assert.equal(sourceFrameClass('https://krisha.kz/a/show/123', false), 'krisha-source');
assert.equal(sourceFrameClass('https://www.olx.kz/d/obyavlenie/a-ID123.html', false), 'olx-source');
for (const url of ['https://krisha.kz/a/show/123', 'https://www.olx.kz/d/obyavlenie/a-ID123.html']) {
  assert.equal(sourceFrameClass(url, true), 'cross-platform-source');
}
assert.equal(sourceFrameClass('https://krisha.kz.example/a', true), '');
console.log('Listing source identification and frames: passed');
