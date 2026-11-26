const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('app/static/app.js', 'utf8');
const helpers = source.slice(source.indexOf('function listingPlatform('), source.indexOf('function duplicateSourceBadge('));
const {isOlxUrl, sourceFrameClass, sourceBadge} = vm.runInNewContext(`${helpers}\n({isOlxUrl, sourceFrameClass, sourceBadge})`, {URL});
for (const url of ['https://olx.kz/a', 'https://www.olx.kz/a', 'HTTPS://OLX.KZ/a']) assert.equal(isOlxUrl(url), true, url);
for (const url of ['https://olx.kz.example/a', 'https://example.com/olx.kz', 'https://example.com/?url=olx.kz', 'https://sub.olx.kz/a', 'https://user@olx.kz/a', 'javascript://olx.kz/a', '/olx.kz', '', null]) assert.equal(isOlxUrl(url), false, String(url));
assert.match(source, /sourceBadge\(item\.source_url\)/);
assert.match(source, /sourceBadge\(item\.url\)/);
assert.match(source, /duplicateSourceBadge\(offer\.source_url\)/);
assert.equal(sourceFrameClass('https://krisha.kz/a/show/123', false), 'krisha-source');
assert.equal(sourceFrameClass('https://www.olx.kz/d/obyavlenie/a-ID123.html', false), 'olx-source');
for (const url of ['https://krisha.kz/a/show/123', 'https://www.olx.kz/d/obyavlenie/a-ID123.html']) {
  assert.equal(sourceFrameClass(url, true), 'cross-platform-source');
}
assert.equal(sourceFrameClass('https://krisha.kz.example/a', true), '');
for (const city of ['astana', 'ekibastuz']) {
  const url = `https://${city}.etagi.com/realty/object/`;
  assert.equal(sourceFrameClass(url, false), 'etagi-source');
  assert.equal(sourceFrameClass(url, true), 'etagi-source');
  assert.match(sourceBadge(url), /Этажи/);
}
for (const url of [
  'https://astana.etagi.com.example/realty/', 'https://example.com/?url=ekibastuz.etagi.com',
  'https://www.etagi.com/realty/', 'https://astana.etagi.com:8080/realty/',
  'https://user@ekibastuz.etagi.com/realty/', 'http://astana.etagi.com/realty/',
]) {
  assert.equal(sourceFrameClass(url, false), '', url);
  assert.equal(sourceBadge(url), '', url);
}
console.log('Listing source identification and frames: passed');
