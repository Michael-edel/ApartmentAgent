const apiOrigin = document.querySelector('#apiOrigin');
const enabled = document.querySelector('#enabled');
const message = document.querySelector('#message');

chrome.storage.sync.get({apiOrigin: '', enabled: true}, settings => {
  apiOrigin.value = settings.apiOrigin;
  enabled.checked = settings.enabled;
});

document.querySelector('#save').addEventListener('click', () => {
  const value = apiOrigin.value.trim().replace(/\/$/, '');
  if (!/^https?:\/\//i.test(value)) {
    message.textContent = 'Укажите полный адрес, например https://...-8000.app.github.dev';
    return;
  }
  chrome.storage.sync.set({apiOrigin: value, enabled: enabled.checked}, () => {
    message.textContent = 'Настройки сохранены.';
  });
});
