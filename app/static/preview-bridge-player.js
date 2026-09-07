(() => {
  const bridgeRoot = document.getElementById('player');
  const bridgeExternalFrame = document.getElementById('externalFrame');
  if (!bridgeRoot || !bridgeExternalFrame) return;

  window.addEventListener('message', async (event) => {
    const data = event.data;
    if (!data || data.type !== 'uipm-external-navigate') return;
    if (event.source !== bridgeExternalFrame.contentWindow) return;

    const targetProjectId = String(data.projectId || '');
    const targetPageId = String(data.pageId || '');
    if (targetProjectId !== bridgeRoot.dataset.projectId || !targetPageId) return;
    if (typeof page !== 'function' || !page(targetPageId)) return;
    if (typeof externalOpen === 'undefined' || !externalOpen) return;
    if (typeof closeExternalPage !== 'function') return;

    if (typeof currentPageId !== 'undefined' && targetPageId === currentPageId) {
      closeExternalPage();
      return;
    }

    if (!navigation || typeof navigation.navigate !== 'function') return;
    const committed = await navigation.navigate(targetPageId);
    if (committed && externalOpen) closeExternalPage();
  });
})();
