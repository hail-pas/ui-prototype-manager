(() => {
  const bridgeRoot = document.getElementById('player');
  const bridgeExternalFrame = document.getElementById('externalFrame');
  if (!bridgeRoot || !bridgeExternalFrame) return;

  window.addEventListener('message', (event) => {
    const data = event.data;
    if (!data || data.type !== 'uipm-external-navigate') return;
    if (event.source !== bridgeExternalFrame.contentWindow) return;

    const targetProjectId = String(data.projectId || '');
    const targetPageId = String(data.pageId || '');
    if (targetProjectId !== bridgeRoot.dataset.projectId || !targetPageId) return;
    if (typeof page !== 'function' || !page(targetPageId)) return;
    if (typeof closeExternalPage !== 'function' || !closeExternalPage()) return;
    if (typeof navigate === 'function') navigate(targetPageId);
  });
})();
