(function () {
  function create(options) {
    let currentPageId = options.initialPageId || null;
    let historyStack = currentPageId ? [currentPageId] : [];
    let pending = null;
    let sequence = 0;

    function snapshot() {
      return {
        currentPageId,
        historyStack: historyStack.slice(),
        pendingPageId: pending?.pageId || null,
      };
    }

    function notifyState() {
      options.onStateChange?.(snapshot());
    }

    function finishPending(request, outcome) {
      if (pending === request) pending = null;
      options.onSettled?.(request, outcome);
      notifyState();
    }

    function cancelPending() {
      if (!pending) return false;
      const request = pending;
      pending = null;
      request.abortController.abort();
      options.onSettled?.(request, 'cancelled');
      notifyState();
      return true;
    }

    async function run(request) {
      let prepared = null;
      let committed = false;
      try {
        prepared = await options.prepare(request.pageId, request.abortController.signal);
        if (pending !== request || request.abortController.signal.aborted) {
          options.discard?.(prepared);
          return false;
        }

        options.commit(prepared, {
          fromPageId: currentPageId,
          mode: request.mode,
          toPageId: request.pageId,
        });
        committed = true;
        currentPageId = request.pageId;
        if (request.mode === 'back') {
          historyStack.pop();
        } else if (request.mode === 'replace') {
          historyStack = [request.pageId];
        } else {
          historyStack.push(request.pageId);
        }
        finishPending(request, 'committed');
        options.onCommit?.(snapshot(), request);
        return true;
      } catch (error) {
        if (prepared && !committed) options.discard?.(prepared);
        const cancelled = request.abortController.signal.aborted || error?.name === 'AbortError';
        if (pending === request) {
          finishPending(request, cancelled ? 'cancelled' : 'failed');
          if (!cancelled) options.onError?.(error, request);
        }
        return false;
      }
    }

    function start(pageId, mode) {
      const targetPageId = String(pageId || '');
      if (!targetPageId || !options.isValidPage(targetPageId)) return Promise.resolve(false);
      if (pending?.pageId === targetPageId) return pending.promise;
      if (targetPageId === currentPageId) {
        cancelPending();
        return Promise.resolve(false);
      }

      cancelPending();
      const request = {
        abortController: new AbortController(),
        id: ++sequence,
        mode,
        pageId: targetPageId,
        promise: null,
      };
      pending = request;
      options.onPending?.(request);
      notifyState();
      request.promise = run(request);
      return request.promise;
    }

    function navigate(pageId) {
      return start(pageId, 'push');
    }

    function replace(pageId) {
      return start(pageId, 'replace');
    }

    function goBack() {
      if (pending) {
        cancelPending();
        return Promise.resolve(false);
      }
      if (historyStack.length <= 1) return Promise.resolve(false);
      return start(historyStack[historyStack.length - 2], 'back');
    }

    notifyState();
    return {
      cancelPending,
      destroy: cancelPending,
      getState: snapshot,
      goBack,
      navigate,
      replace,
    };
  }

  window.UIPMPlayerNavigation = {create};
})();
