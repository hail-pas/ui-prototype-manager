const root = document.getElementById('player');
const projectId = root.dataset.projectId;
const stage = document.getElementById('playerStage');
const backBtn = document.getElementById('backBtn');
const fullscreenBtn = document.getElementById('fullscreenBtn');
const pageList = document.getElementById('playerPageList');
const pageCount = document.getElementById('playerPageCount');
const pageSearchInput = document.getElementById('playerPageSearchInput');
const pageSearchClear = document.getElementById('playerPageSearchClear');
const pageNameElement = document.getElementById('playerPageName');
const menuLayer = document.getElementById('playerMenuLayer');
const menuBackdrop = document.getElementById('playerMenuBackdrop');
const menuCloseBtn = document.getElementById('menuCloseBtn');

const PAGE_READY_TIMEOUT_MS = 15_000;
const PAGE_LOADING_DELAY_MS = 250;
const PAGE_ERROR_VISIBLE_MS = 5_000;
const PAGE_TRANSITION_MS = 140;
const PAGE_CACHE_TTL_MS = 30_000;
const HOTSPOT_REVEAL_MS = 500;

let state = {pages: [], interactions: [], overlays: []};
let currentPageId = null;
let activeView = null;
let cachedView = null;
let cachedViewTimer = null;
let navigation = null;
let menuOpen = false;
let previewFocusTarget = null;
let transitionStatus = null;
let transitionStatusTimer = null;
let transitionStatusHideTimer = null;
const pageViews = new Set();
const contentUrlRefreshes = new Map();
const hotspotRevealTimers = new WeakMap();

function esc(value = '') {
  const element = document.createElement('div');
  element.textContent = value;
  return element.innerHTML;
}

function page(pageId) {
  return state.pages.find((item) => item.id === pageId);
}

function interactions(pageId) {
  return state.interactions.filter((item) => item.source_page_id === pageId);
}

function overlays(pageId) {
  return state.overlays
    .filter((item) => item.page_id === pageId)
    .sort((left, right) => left.z_index - right.z_index || left.created_at.localeCompare(right.created_at));
}

function focusableMenuElements() {
  return Array.from(menuLayer.querySelectorAll('a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])'))
    .filter((element) => !element.hidden);
}

function restorePreviewFocus() {
  requestAnimationFrame(() => {
    const target = activeView?.controller?.iframe || previewFocusTarget || stage;
    if (target?.isConnected) target.focus({preventScroll: true});
    previewFocusTarget = null;
  });
}

function setMenuOpen(open) {
  const nextOpen = Boolean(open);
  if (menuOpen === nextOpen) return;
  menuOpen = nextOpen;
  if (menuOpen) previewFocusTarget = document.activeElement;
  root.classList.toggle('menu-open', menuOpen);
  menuLayer.inert = !menuOpen;
  menuLayer.setAttribute('aria-hidden', String(!menuOpen));
  stage.inert = menuOpen;
  if (menuOpen) requestAnimationFrame(() => menuCloseBtn.focus({preventScroll: true}));
  else restorePreviewFocus();
}

function toggleMenu() {
  setMenuOpen(!menuOpen);
}

function setFullscreenButtonLabel(label) {
  fullscreenBtn.textContent = label;
}

function syncFullscreenState() {
  const active = document.fullscreenElement === root;
  setFullscreenButtonLabel(active ? '退出全屏' : '进入全屏');
  fullscreenBtn.setAttribute('aria-pressed', String(active));
  pageViews.forEach((view) => view.controller?.relayout());
}

async function toggleFullscreen() {
  const active = document.fullscreenElement === root;
  if (!active && (!document.fullscreenEnabled || typeof root.requestFullscreen !== 'function')) {
    setFullscreenButtonLabel('当前浏览器不支持全屏');
    fullscreenBtn.disabled = true;
    return;
  }

  fullscreenBtn.disabled = true;
  setFullscreenButtonLabel(active ? '正在退出全屏…' : '正在进入全屏…');
  try {
    if (active) await document.exitFullscreen();
    else await root.requestFullscreen({navigationUI: 'hide'});
    setMenuOpen(false);
  } catch (error) {
    console.warn(active ? '退出全屏失败' : '进入全屏失败', error);
    setFullscreenButtonLabel(active ? '退出全屏失败，请重试' : '进入全屏失败，请重试');
    window.setTimeout(syncFullscreenState, 1800);
  } finally {
    fullscreenBtn.disabled = false;
  }
}

async function api(url) {
  const response = await fetch(url);
  if (response.status === 401) {
    location.href = `/login?next=${encodeURIComponent(location.pathname + location.search)}`;
    throw new Error('登录已过期');
  }
  if (!response.ok) {
    let message = '加载失败';
    try {
      const data = await response.json();
      message = data.detail || message;
    } catch {}
    throw new Error(message);
  }
  return response.json();
}

function contentUrlExpiring(item, skewMs = 60_000) {
  if (!item.content_url_expires_at) return false;
  const expiresAt = Date.parse(item.content_url_expires_at);
  return !Number.isFinite(expiresAt) || expiresAt - Date.now() <= skewMs;
}

function pageContentUrl(item, mode = null) {
  const fallback = item.type === 'html'
    ? `/api/pages/${item.id}/render`
    : `/api/pages/${item.id}/file`;
  const url = new URL(item.content_url || fallback, location.href);
  if (mode) url.hash = new URLSearchParams({'uipm-mode': mode}).toString();
  return url.href;
}

async function refreshPageContentUrl(item) {
  if (!contentUrlRefreshes.has(item.id)) {
    const refresh = api(`/api/pages/${item.id}/content-url`)
      .then((data) => Object.assign(item, data))
      .finally(() => contentUrlRefreshes.delete(item.id));
    contentUrlRefreshes.set(item.id, refresh);
  }
  await contentUrlRefreshes.get(item.id);
}

function overlayContentUrl(item) {
  return new URL(item.content_url || `/api/overlays/${item.id}/content-url`, location.href).href;
}

async function refreshOverlayContentUrl(item) {
  const refreshKey = `overlay:${item.id}`;
  if (!contentUrlRefreshes.has(refreshKey)) {
    const refresh = api(`/api/overlays/${item.id}/content-url`)
      .then((data) => Object.assign(item, data))
      .finally(() => contentUrlRefreshes.delete(refreshKey));
    contentUrlRefreshes.set(refreshKey, refresh);
  }
  await contentUrlRefreshes.get(refreshKey);
}

function abortError() {
  return new DOMException('Navigation cancelled', 'AbortError');
}

function throwIfAborted(signal) {
  if (signal.aborted) throw abortError();
}

function abortable(promise, signal) {
  if (signal.aborted) return Promise.reject(abortError());
  return new Promise((resolve, reject) => {
    const handleAbort = () => reject(abortError());
    signal.addEventListener('abort', handleAbort, {once: true});
    Promise.resolve(promise).then(
      (value) => {
        signal.removeEventListener('abort', handleAbort);
        resolve(value);
      },
      (error) => {
        signal.removeEventListener('abort', handleAbort);
        reject(error);
      },
    );
  });
}

function withTimeout(promise, timeoutMs, message) {
  let timer = null;
  const timeout = new Promise((_, reject) => {
    timer = window.setTimeout(() => reject(new Error(message)), timeoutMs);
  });
  return Promise.race([promise, timeout]).finally(() => window.clearTimeout(timer));
}

function afterStablePaint(signal) {
  return abortable(new Promise((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(resolve));
  }), signal);
}

function waitForMedia(media, readyEvent, isReady, signal) {
  if (signal.aborted) return Promise.reject(abortError());
  if (isReady()) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const cleanup = () => {
      media.removeEventListener(readyEvent, handleReady);
      media.removeEventListener('error', handleError);
      signal.removeEventListener('abort', handleAbort);
    };
    const handleReady = () => {
      cleanup();
      resolve();
    };
    const handleError = () => {
      cleanup();
      reject(new Error('媒体资源加载失败'));
    };
    const handleAbort = () => {
      cleanup();
      reject(abortError());
    };
    media.addEventListener(readyEvent, handleReady, {once: true});
    media.addEventListener('error', handleError, {once: true});
    signal.addEventListener('abort', handleAbort, {once: true});
  });
}

async function loadImageMedia(image, url, signal) {
  image.src = url;
  await waitForMedia(
    image,
    'load',
    () => image.complete && image.naturalWidth > 0,
    signal,
  );
  if (typeof image.decode === 'function') {
    await abortable(image.decode().catch(() => {}), signal);
  }
}

async function loadVideoMedia(video, url, signal) {
  video.src = url;
  await waitForMedia(video, 'loadeddata', () => video.readyState >= 2, signal);
  const playback = video.play();
  if (playback) playback.catch(() => {});
}

function createPlayerOverlay(item, signal) {
  const media = document.createElement(item.type === 'video' ? 'video' : 'img');
  media.className = `player-overlay is-${item.type}`;
  media.draggable = false;
  media.style.objectFit = item.object_fit;
  if (item.storage_backend === 'url') media.referrerPolicy = 'no-referrer';
  Object.assign(media.style, {
    left: `${item.x * 100}%`,
    top: `${item.y * 100}%`,
    width: `${item.width * 100}%`,
    height: `${item.height * 100}%`,
  });
  if (item.type === 'video') {
    media.autoplay = true;
    media.loop = true;
    media.muted = true;
    media.defaultMuted = true;
    media.controls = Boolean(item.video_controls);
    media.playsInline = true;
    media.preload = 'auto';
  } else {
    media.alt = '';
    media.decoding = 'async';
  }

  const load = async () => {
    const loadSource = () => item.type === 'video'
      ? loadVideoMedia(media, overlayContentUrl(item), signal)
      : loadImageMedia(media, overlayContentUrl(item), signal);
    try {
      await loadSource();
    } catch (error) {
      if (signal.aborted) throw error;
      await abortable(refreshOverlayContentUrl(item), signal);
      await loadSource();
    }
  };
  return {media, ready: load()};
}

function createPlayerOverlayLayer(pageId, signal) {
  const layer = document.createElement('div');
  layer.className = 'overlay-layer player-overlay-layer';
  const mediaItems = overlays(pageId).map((item) => createPlayerOverlay(item, signal));
  layer.replaceChildren(...mediaItems.map((item) => item.media));
  return {
    layer,
    ready: Promise.all(mediaItems.map((item) => item.ready)),
  };
}

function createPageLayer(item) {
  const element = document.createElement('div');
  element.className = `player-page-view is-preparing ${item.type === 'image' ? 'is-image-view' : 'is-html-view'}`;
  element.dataset.pageId = item.id;
  element.inert = true;
  element.setAttribute('aria-hidden', 'true');
  stage.appendChild(element);
  return element;
}

function createReadySignal() {
  let settled = false;
  let resolveReady;
  const promise = new Promise((resolve) => {
    resolveReady = () => {
      if (settled) return;
      settled = true;
      resolve();
    };
  });
  return {promise, resolve: resolveReady};
}

function createHtmlPageView(item, signal) {
  const element = createPageLayer(item);
  const contentReady = createReadySignal();
  const frameLoaded = createReadySignal();
  const overlay = createPlayerOverlayLayer(item.id, signal);
  const view = {
    controller: null,
    destroyed: false,
    element,
    markContentReady: contentReady.resolve,
    pageId: item.id,
    ready: null,
    returnToCacheOnDiscard: false,
  };

  view.controller = window.UIPMFrameFit.create({
    host: element,
    pageId: item.id,
    title: item.name,
    src: pageContentUrl(item, 'play'),
    variant: 'player',
    renderMode: item.render_mode || 'auto',
    viewportWidth: item.viewport_width || 1920,
    viewportHeight: item.viewport_height || 1080,
  });
  view.controller.iframe.addEventListener('load', frameLoaded.resolve, {once: true});
  view.controller.attachViewportLayer(overlay.layer);
  view.ready = Promise.all([contentReady.promise, frameLoaded.promise, overlay.ready]);
  pageViews.add(view);
  return view;
}

function appendImageHotspots(container, pageId) {
  interactions(pageId).filter((item) => item.kind === 'region').forEach((interaction) => {
    const region = interaction.payload;
    const hotspot = document.createElement('button');
    hotspot.className = 'player-hotspot';
    hotspot.type = 'button';
    hotspot.title = interaction.action === 'back'
      ? '返回上一页'
      : `跳转到 ${page(interaction.target_page_id)?.name || ''}`;
    Object.assign(hotspot.style, {
      left: `${region.x * 100}%`,
      top: `${region.y * 100}%`,
      width: `${region.width * 100}%`,
      height: `${region.height * 100}%`,
    });
    const label = document.createElement('span');
    label.textContent = interaction.name || '交互区域';
    hotspot.appendChild(label);
    hotspot.addEventListener('click', () => executeInteraction(interaction));
    container.appendChild(hotspot);
  });
}

function revealImageHotspots(container) {
  window.clearTimeout(hotspotRevealTimers.get(container));
  container.classList.add('is-revealing-hotspots');
  const timer = window.setTimeout(() => {
    container.classList.remove('is-revealing-hotspots');
    hotspotRevealTimers.delete(container);
  }, HOTSPOT_REVEAL_MS);
  hotspotRevealTimers.set(container, timer);
}

function handleImageBlankClick(event, pageId) {
  const container = event.currentTarget;
  if (event.target.closest('.player-hotspot')) return;
  const rect = container.getBoundingClientRect();
  if (!rect.width || !rect.height) return;
  const x = (event.clientX - rect.left) / rect.width;
  const y = (event.clientY - rect.top) / rect.height;
  const regions = interactions(pageId)
    .filter((item) => item.kind === 'region')
    .map((item) => item.payload);
  const overInteraction = window.UIPMPageList.pointInsideRegions(regions, x, y);
  if (!overInteraction) revealImageHotspots(container);
}

function createImagePageView(item, signal) {
  const element = createPageLayer(item);
  const imageStage = document.createElement('div');
  imageStage.className = 'player-image-stage';
  const image = document.createElement('img');
  image.alt = item.name;
  image.decoding = 'async';
  imageStage.appendChild(image);
  const overlay = createPlayerOverlayLayer(item.id, signal);
  imageStage.appendChild(overlay.layer);
  appendImageHotspots(imageStage, item.id);
  imageStage.addEventListener('click', (event) => handleImageBlankClick(event, item.id));
  element.appendChild(imageStage);

  const loadBaseImage = async () => {
    try {
      await loadImageMedia(image, pageContentUrl(item), signal);
    } catch (error) {
      if (signal.aborted) throw error;
      await abortable(refreshPageContentUrl(item), signal);
      await loadImageMedia(image, pageContentUrl(item), signal);
    }
  };
  const view = {
    controller: null,
    destroyed: false,
    element,
    markContentReady: null,
    pageId: item.id,
    ready: Promise.all([loadBaseImage(), overlay.ready]),
    returnToCacheOnDiscard: false,
  };
  pageViews.add(view);
  return view;
}

function destroyPageView(view) {
  if (!view || view.destroyed) return;
  view.destroyed = true;
  view.abortController?.abort();
  view.controller?.destroy();
  const imageStage = view.element.querySelector('.player-image-stage');
  if (imageStage) window.clearTimeout(hotspotRevealTimers.get(imageStage));
  view.element.querySelectorAll('video, audio').forEach((media) => {
    media.pause();
    media.removeAttribute('src');
    media.load?.();
  });
  view.element.remove();
  pageViews.delete(view);
  if (cachedView === view) cachedView = null;
}

function pauseViewMedia(view) {
  view.element.querySelectorAll('video, audio').forEach((media) => media.pause());
}

function resumeViewMedia(view) {
  view.element.querySelectorAll('video[autoplay], audio[autoplay]').forEach((media) => {
    const playback = media.play();
    if (playback) playback.catch(() => {});
  });
}

function storeCachedView(view) {
  if (!view || view === activeView || view.destroyed) return;
  if (cachedView && cachedView !== view) destroyPageView(cachedView);
  window.clearTimeout(cachedViewTimer);
  view.returnToCacheOnDiscard = false;
  view.element.classList.remove('is-active', 'is-leaving', 'is-faded');
  view.element.classList.add('is-preparing', 'is-cached');
  view.element.inert = true;
  view.element.setAttribute('aria-hidden', 'true');
  pauseViewMedia(view);
  cachedView = view;
  cachedViewTimer = window.setTimeout(() => {
    if (cachedView === view) destroyPageView(view);
  }, PAGE_CACHE_TTL_MS);
}

async function takeCachedView(pageId, signal) {
  if (!cachedView || cachedView.pageId !== pageId) return null;
  const view = cachedView;
  cachedView = null;
  window.clearTimeout(cachedViewTimer);
  view.returnToCacheOnDiscard = true;
  view.element.classList.remove('is-cached');
  view.controller?.relayout();
  resumeViewMedia(view);
  try {
    await afterStablePaint(signal);
    return view;
  } catch (error) {
    storeCachedView(view);
    throw error;
  }
}

async function preparePageView(pageId, signal) {
  const item = page(pageId);
  if (!item) throw new Error('目标页面不存在');
  const cached = await takeCachedView(pageId, signal);
  if (cached) return cached;

  throwIfAborted(signal);
  if (contentUrlExpiring(item)) await abortable(refreshPageContentUrl(item), signal);
  throwIfAborted(signal);
  const viewAbortController = new AbortController();
  const relayAbort = () => viewAbortController.abort();
  signal.addEventListener('abort', relayAbort, {once: true});
  const view = item.type === 'html'
    ? createHtmlPageView(item, viewAbortController.signal)
    : createImagePageView(item, viewAbortController.signal);
  view.abortController = viewAbortController;
  try {
    await withTimeout(
      abortable(view.ready, signal),
      PAGE_READY_TIMEOUT_MS,
      `“${item.name}”加载超时`,
    );
    await afterStablePaint(signal);
    return view;
  } catch (error) {
    destroyPageView(view);
    throw error;
  } finally {
    signal.removeEventListener('abort', relayAbort);
  }
}

function activatePageView(view) {
  stage.classList.add('has-active-page');
  view.returnToCacheOnDiscard = false;
  view.element.classList.remove('is-preparing', 'is-cached', 'is-leaving', 'is-faded');
  view.element.classList.add('is-active');
  view.element.inert = false;
  view.element.setAttribute('aria-hidden', 'false');
  view.controller?.relayout();
  resumeViewMedia(view);
}

function commitPageView(view) {
  const outgoing = activeView;
  activeView = view;
  activatePageView(view);
  removeInitialEmpty();
  if (!outgoing || outgoing === view) return;

  if (outgoing.element.contains(document.activeElement)) {
    const focusTarget = view.controller?.iframe || stage;
    focusTarget.focus({preventScroll: true});
  }
  outgoing.element.inert = true;
  outgoing.element.setAttribute('aria-hidden', 'true');
  outgoing.element.classList.remove('is-active');
  outgoing.element.classList.add('is-leaving');
  requestAnimationFrame(() => {
    if (outgoing.destroyed || outgoing === activeView) return;
    outgoing.element.classList.add('is-faded');
    window.setTimeout(() => {
      if (outgoing !== activeView) storeCachedView(outgoing);
    }, PAGE_TRANSITION_MS);
  });
}

function discardPageView(view) {
  if (view?.returnToCacheOnDiscard) storeCachedView(view);
  else destroyPageView(view);
}

function ensureTransitionStatus() {
  if (transitionStatus) return transitionStatus;
  transitionStatus = document.createElement('div');
  transitionStatus.className = 'player-transition-status';
  transitionStatus.hidden = true;
  transitionStatus.setAttribute('role', 'status');
  transitionStatus.setAttribute('aria-live', 'polite');
  stage.appendChild(transitionStatus);
  return transitionStatus;
}

function clearTransitionStatus() {
  window.clearTimeout(transitionStatusTimer);
  window.clearTimeout(transitionStatusHideTimer);
  stage.removeAttribute('aria-busy');
  if (!transitionStatus) return;
  transitionStatus.hidden = true;
  transitionStatus.classList.remove('is-error');
  transitionStatus.textContent = '';
}

function beginTransitionStatus(request) {
  clearTransitionStatus();
  stage.setAttribute('aria-busy', 'true');
  transitionStatusTimer = window.setTimeout(() => {
    const item = page(request.pageId);
    const status = ensureTransitionStatus();
    status.textContent = `正在打开“${item?.name || '目标页面'}”…`;
    status.hidden = false;
  }, PAGE_LOADING_DELAY_MS);
}

function showTransitionError(error, request) {
  clearTransitionStatus();
  if (!activeView) {
    showInitialEmpty(error?.message || '页面加载失败');
    return;
  }
  const item = page(request.pageId);
  const status = ensureTransitionStatus();
  status.classList.add('is-error');
  status.textContent = `“${item?.name || '目标页面'}”加载失败：${error?.message || '请重试'}`;
  status.hidden = false;
  transitionStatusHideTimer = window.setTimeout(clearTransitionStatus, PAGE_ERROR_VISIBLE_MS);
}

function showInitialEmpty(message) {
  removeInitialEmpty();
  const empty = document.createElement('div');
  empty.className = 'player-empty player-initial-empty';
  empty.textContent = message;
  stage.appendChild(empty);
}

function removeInitialEmpty() {
  stage.querySelector('.player-initial-empty')?.remove();
}

function updateUrl(pageId) {
  const url = new URL(location.href);
  if (pageId) url.searchParams.set('page', pageId);
  else url.searchParams.delete('page');
  history.replaceState(null, '', url);
}

function renderPageList(pendingPageId = null) {
  if (!state.pages.length) {
    pageList.innerHTML = '<div class="player-nav-empty">暂无页面</div>';
    return;
  }
  const query = pageSearchInput.value;
  const visiblePages = window.UIPMPageList.filterByName(state.pages, query);
  const searching = Boolean(query.trim());
  pageSearchClear.hidden = !searching;
  pageCount.textContent = searching ? `${visiblePages.length}/${state.pages.length}` : state.pages.length;
  if (!visiblePages.length) {
    pageList.innerHTML = '<div class="player-nav-empty">没有匹配的页面</div>';
    return;
  }
  pageList.innerHTML = visiblePages.map((item) => `
    <button type="button" class="player-page-item ${item.id === currentPageId ? 'active' : ''} ${item.id === pendingPageId ? 'pending' : ''}" data-id="${item.id}">
      <span class="player-page-type">${item.type === 'html' ? 'HTML' : 'IMG'}</span>
      <span class="player-page-name">${esc(item.name)}</span>
    </button>`).join('');
  pageList.querySelectorAll('.player-page-item').forEach((button) => {
    button.addEventListener('click', () => {
      navigate(button.dataset.id);
      setMenuOpen(false);
    });
  });
}

function syncNavigationState(snapshot) {
  currentPageId = snapshot.currentPageId;
  backBtn.disabled = snapshot.historyStack.length <= 1 && !(snapshot.pendingPageId && activeView);
  renderPageList(snapshot.pendingPageId);
}

function handleNavigationCommit(snapshot) {
  currentPageId = snapshot.currentPageId;
  const current = page(currentPageId);
  pageNameElement.textContent = current?.name || '';
  updateUrl(currentPageId);
  renderPageList();
  if (!menuOpen) {
    requestAnimationFrame(() => {
      const target = activeView?.controller?.iframe || stage;
      if (target?.isConnected) target.focus({preventScroll: true});
    });
  }
}

function navigate(pageId) {
  if (!navigation) return;
  void navigation.navigate(pageId);
}

function goBack() {
  if (!navigation) return;
  void navigation.goBack();
}

function executeInteraction(interaction) {
  if (!interaction) return;
  if (interaction.action === 'back') {
    goBack();
    return;
  }
  if (interaction.action === 'navigate' && interaction.target_page_id) {
    navigate(interaction.target_page_id);
  }
}

async function load() {
  try {
    state = await api(`/api/projects/${projectId}`);
    state.overlays = Array.isArray(state.overlays) ? state.overlays : [];
  } catch (error) {
    showInitialEmpty(error.message);
    return;
  }

  pageCount.textContent = state.pages.length;
  navigation = window.UIPMPlayerNavigation.create({
    commit: commitPageView,
    discard: discardPageView,
    isValidPage: (pageId) => state.pages.some((item) => item.id === pageId),
    onCommit: handleNavigationCommit,
    onError: showTransitionError,
    onPending: beginTransitionStatus,
    onSettled: (_request, outcome) => {
      if (outcome !== 'failed') clearTransitionStatus();
    },
    onStateChange: syncNavigationState,
    prepare: preparePageView,
  });

  const requestedPageId = new URLSearchParams(location.search).get('page');
  const initialPageId = state.pages.some((item) => item.id === requestedPageId)
    ? requestedPageId
    : state.pages[0]?.id || null;
  if (!initialPageId) {
    showInitialEmpty('项目还没有页面。');
    return;
  }
  void navigation.replace(initialPageId);
}

window.addEventListener('message', (event) => {
  const data = event.data;
  if (!data) return;
  const owner = Array.from(pageViews).find((view) => view.controller?.ownsMessage(event));
  if (!owner || data.pageId !== owner.pageId) return;
  if (data.type === 'uipm-content-ready') {
    owner.markContentReady?.();
    return;
  }
  if (owner !== activeView || owner.pageId !== currentPageId) return;
  if (data.type === 'uipm-preview-key' && data.key === 'Escape') {
    toggleMenu();
    return;
  }
  if (data.type !== 'uipm-element-click') return;
  const interaction = interactions(currentPageId).find(
    (item) => item.kind === 'element' && item.payload.elementId === data.elementId,
  );
  executeInteraction(interaction);
});

document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') {
    if (event.repeat) return;
    event.preventDefault();
    toggleMenu();
    return;
  }
  if (!menuOpen || event.key !== 'Tab') return;
  const focusable = focusableMenuElements();
  if (!focusable.length) {
    event.preventDefault();
    return;
  }
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}, true);

backBtn.addEventListener('click', () => {
  goBack();
  setMenuOpen(false);
});
fullscreenBtn.addEventListener('click', () => void toggleFullscreen());
document.addEventListener('fullscreenchange', syncFullscreenState);
menuCloseBtn.addEventListener('click', () => setMenuOpen(false));
menuBackdrop.addEventListener('click', () => setMenuOpen(false));
pageSearchInput.addEventListener('input', () => renderPageList(navigation?.getState().pendingPageId));
pageSearchClear.addEventListener('click', () => {
  pageSearchInput.value = '';
  renderPageList(navigation?.getState().pendingPageId);
  pageSearchInput.focus();
});

void load();
