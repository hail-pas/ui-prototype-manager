(() => {
  const bridgeRoot = document.getElementById('player');
  const bridgeStage = document.getElementById('playerStage');
  const bridgeExternalFrame = document.getElementById('externalFrame');
  if (!bridgeRoot || !bridgeStage || !bridgeExternalFrame) return;

  function findPageView(pageId) {
    return Array.from(bridgeStage.querySelectorAll('.player-page-view'))
      .find((view) => view.dataset.pageId === pageId) || null;
  }

  function pageViewSettled(view) {
    return !view || !view.isConnected || view.classList.contains('is-cached');
  }

  function waitForOutgoingPageToSettle(pageId) {
    const outgoingView = findPageView(pageId);
    if (pageViewSettled(outgoingView)) return Promise.resolve();

    return new Promise((resolve) => {
      const finishIfSettled = () => {
        if (!pageViewSettled(outgoingView)) return false;
        observer.disconnect();
        resolve();
        return true;
      };
      const observer = new MutationObserver(finishIfSettled);
      observer.observe(bridgeStage, {
        attributes: true,
        attributeFilter: ['class'],
        childList: true,
        subtree: true,
      });
      finishIfSettled();
    });
  }

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
    const sourcePageId = currentPageId;
    const committed = await navigation.navigate(targetPageId);
    if (!committed || !externalOpen) return;

    await waitForOutgoingPageToSettle(sourcePageId);
    if (externalOpen) closeExternalPage();
  });
})();
