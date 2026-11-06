(() => {
  window.addEventListener('message', event => {
    if (event.source !== window || event.origin !== location.origin || event.data?.type !== 'apartment-agent-bathroom-queue') return;
    chrome.runtime.sendMessage({type:'apartment-agent-bathroom-queue-poll'}, response => {
      const error = chrome.runtime.lastError?.message;
      window.postMessage({type:'apartment-agent-bathroom-queue-ack', ok:!error && response?.ok === true}, location.origin);
    });
  });
})();
