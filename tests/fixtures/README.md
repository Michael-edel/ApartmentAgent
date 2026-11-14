# Public page parser fixtures

These reduced fragments preserve the markup or JSON structure of public pages already saved locally during integration checks. Listing IDs, addresses, coordinates, asking prices, titles and descriptions have been replaced. User, contact, tracking, analytics, photo and unrelated ad data were removed. The values are illustrative. The originals are kept outside the repository.

- `krisha-catalog-redacted.html` retains the catalog card structure, localized price markup, street subtitle and owner label from a Krisha search page.
- `olx-offer-redacted.html` retains the escaped `window.__PRERENDERED_STATE__` assignment and selected fields from an OLX detail page, including the approximate-map indicator.

The tests use these fragments offline. A passing test confirms compatibility with the captured structures.
