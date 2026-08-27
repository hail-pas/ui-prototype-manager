(function () {
  const DEFAULT_WIDTH = 1920;
  const DEFAULT_HEIGHT = 1080;
  const MIN_DIMENSION = 240;
  const MAX_DIMENSION = 10000;

  function numberInRange(value, fallback) {
    const parsed = Math.round(Number(value));
    return Number.isFinite(parsed) && parsed >= MIN_DIMENSION && parsed <= MAX_DIMENSION ? parsed : fallback;
  }

  function availableSize(host) {
    const style = getComputedStyle(host);
    const horizontalPadding = parseFloat(style.paddingLeft) + parseFloat(style.paddingRight);
    const verticalPadding = parseFloat(style.paddingTop) + parseFloat(style.paddingBottom);
    return {
      width: Math.max(1, host.clientWidth - horizontalPadding),
      height: Math.max(1, host.clientHeight - verticalPadding),
    };
  }

  function create(options) {
    const host = options.host;
    const pageId = String(options.pageId || '');
    const variant = options.variant === 'player' ? 'player' : 'editor';
    const requestedMode = ['auto', 'responsive', 'fixed'].includes(options.renderMode) ? options.renderMode : 'auto';
    const configuredWidth = numberInRange(options.viewportWidth, DEFAULT_WIDTH);
    const configuredHeight = numberInRange(options.viewportHeight, DEFAULT_HEIGHT);
    const maxWidth = 1400;
    let activeMode = requestedMode === 'fixed' ? 'fixed' : 'responsive';
    let designWidth = configuredWidth;
    let designHeight = configuredHeight;
    let destroyed = false;

    const wrap = document.createElement('div');
    wrap.className = `html-frame-wrap ${variant === 'player' ? 'player-html-frame-wrap' : ''}`;
    const viewport = document.createElement('div');
    viewport.className = 'html-frame-viewport';
    const iframe = document.createElement('iframe');
    iframe.className = variant === 'player' ? 'html-frame player-html' : 'html-frame';
    if (options.iframeId) iframe.id = options.iframeId;
    iframe.setAttribute('sandbox', 'allow-scripts');
    iframe.setAttribute('title', options.title || 'HTML 页面');
    viewport.appendChild(iframe);
    wrap.appendChild(viewport);
    host.replaceChildren(wrap);

    function layoutResponsive() {
      if (destroyed) return;
      const available = availableSize(host);
      const outerWidth = variant === 'player'
        ? available.width
        : Math.max(1, Math.min(available.width, maxWidth));
      const innerWidth = variant === 'player' ? outerWidth : Math.max(1, outerWidth - 2);
      const innerHeight = variant === 'player'
        ? available.height
        : Math.max(1, Number(options.responsiveHeight) || 720);
      wrap.classList.remove('is-fixed');
      wrap.classList.add('is-responsive');
      wrap.style.width = `${outerWidth}px`;
      wrap.style.height = `${variant === 'player' ? innerHeight : innerHeight + 2}px`;
      viewport.style.width = `${innerWidth}px`;
      viewport.style.height = `${innerHeight}px`;
      iframe.style.position = 'static';
      iframe.style.width = '100%';
      iframe.style.height = '100%';
      iframe.style.transform = 'none';
    }

    function layoutFixed() {
      if (destroyed) return;
      const available = availableSize(host);
      if (variant === 'player') {
        // Keep the configured layout width stable while using every visible pixel.
        const scale = Math.max(0.01, available.width / designWidth);
        const virtualHeight = Math.max(1, available.height / scale);
        wrap.classList.remove('is-responsive');
        wrap.classList.add('is-fixed');
        wrap.style.width = `${available.width}px`;
        wrap.style.height = `${available.height}px`;
        viewport.style.width = `${available.width}px`;
        viewport.style.height = `${available.height}px`;
        iframe.style.position = 'absolute';
        iframe.style.width = `${designWidth}px`;
        iframe.style.height = `${virtualHeight}px`;
        iframe.style.transform = `scale(${scale})`;
        return;
      }
      const innerAvailableWidth = Math.max(1, Math.min(available.width, maxWidth) - 2);
      const innerAvailableHeight = Math.max(1, available.height - 2);
      const scale = Math.max(0.01, Math.min(
        innerAvailableWidth / designWidth,
        innerAvailableHeight / designHeight,
        1,
      ));
      const visualWidth = designWidth * scale;
      const visualHeight = designHeight * scale;
      wrap.classList.remove('is-responsive');
      wrap.classList.add('is-fixed');
      wrap.style.width = `${visualWidth + 2}px`;
      wrap.style.height = `${visualHeight + 2}px`;
      viewport.style.width = `${visualWidth}px`;
      viewport.style.height = `${visualHeight}px`;
      iframe.style.position = 'absolute';
      iframe.style.width = `${designWidth}px`;
      iframe.style.height = `${designHeight}px`;
      iframe.style.transform = `scale(${scale})`;
    }

    function layout() {
      if (activeMode === 'fixed') layoutFixed(); else layoutResponsive();
    }

    function handleMessage(event) {
      if (destroyed || requestedMode !== 'auto' || event.source !== iframe.contentWindow) return;
      const data = event.data;
      if (!data || data.type !== 'uipm-render-size' || String(data.pageId || '') !== pageId) return;
      const viewportWidth = Number(data.viewportWidth);
      const contentWidth = Number(data.contentWidth);
      const contentHeight = Number(data.contentHeight);
      if (!Number.isFinite(viewportWidth) || !Number.isFinite(contentWidth) || !Number.isFinite(contentHeight)) return;
      const overflowThreshold = Math.max(8, viewportWidth * 0.02);
      if (contentWidth > viewportWidth + overflowThreshold) {
        designWidth = numberInRange(contentWidth, configuredWidth);
        designHeight = numberInRange(contentHeight, configuredHeight);
        if (activeMode !== 'fixed') {
          activeMode = 'fixed';
          layoutFixed();
        }
      }
    }

    window.addEventListener('message', handleMessage);
    const resizeObserver = window.ResizeObserver ? new ResizeObserver(layout) : null;
    if (resizeObserver) resizeObserver.observe(host); else window.addEventListener('resize', layout);
    layout();
    iframe.src = options.src;

    return {
      wrap,
      viewport,
      iframe,
      ownsMessage(event) { return event.source === iframe.contentWindow; },
      send(message) {
        if (!destroyed && iframe.contentWindow) iframe.contentWindow.postMessage(message, '*');
      },
      relayout: layout,
      destroy() {
        if (destroyed) return;
        destroyed = true;
        window.removeEventListener('message', handleMessage);
        if (resizeObserver) resizeObserver.disconnect(); else window.removeEventListener('resize', layout);
      },
    };
  }

  window.UIPMFrameFit = { create };
})();
