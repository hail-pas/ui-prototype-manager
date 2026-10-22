(() => {
  const BRIDGE_COOKIE_NAME = 'uipm_preview_bridge';
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

  function waitForTargetPaint(pageId) {
    return new Promise((resolve) => {
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          const targetView = findPageView(pageId);
          resolve(Boolean(
            targetView
            && targetView.isConnected
            && targetView.classList.contains('is-active')
            && !targetView.classList.contains('is-preparing')
            && !targetView.classList.contains('is-cached')
          ));
        });
      });
    });
  }

  function readBridgeCookie() {
    const prefix = `${BRIDGE_COOKIE_NAME}=`;
    const item = document.cookie.split('; ').find((entry) => entry.startsWith(prefix));
    return item ? item.slice(prefix.length) : '';
  }

  function clearBridgeCookie() {
    document.cookie = `${BRIDGE_COOKIE_NAME}=; Path=/; Max-Age=0; SameSite=Lax`;
  }

  function decodeBridgeCommand(raw) {
    try {
      const normalized = raw.replace(/-/g, '+').replace(/_/g, '/');
      const padded = normalized + '='.repeat((4 - (normalized.length % 4)) % 4);
      return JSON.parse(atob(padded));
    } catch (_) {
      return null;
    }
  }

  async function handleBridgeNavigation(data) {
    if (!data || data.type !== 'uipm-external-navigate') return;

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
    if (!committed || !externalOpen || currentPageId !== targetPageId) return;

    await waitForOutgoingPageToSettle(sourcePageId);
    if (!externalOpen || currentPageId !== targetPageId) return;

    const painted = await waitForTargetPaint(targetPageId);
    if (painted && externalOpen && currentPageId === targetPageId) closeExternalPage();
  }

  window.addEventListener('message', (event) => {
    if (event.source !== bridgeExternalFrame.contentWindow) return;
    void handleBridgeNavigation(event.data);
  });

  window.setInterval(() => {
    if (typeof externalOpen === 'undefined' || !externalOpen) return;
    const raw = readBridgeCookie();
    if (!raw) return;
    clearBridgeCookie();
    const command = decodeBridgeCommand(raw);
    if (command) void handleBridgeNavigation(command);
  }, 50);
})();
