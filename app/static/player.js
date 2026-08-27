const root = document.getElementById('player');
const projectId = root.dataset.projectId;
const stage = document.getElementById('playerStage');
const backBtn = document.getElementById('backBtn');
const pageList = document.getElementById('playerPageList');
const pageCount = document.getElementById('playerPageCount');
const pageNameElement = document.getElementById('playerPageName');
const menuLayer = document.getElementById('playerMenuLayer');
const menuBackdrop = document.getElementById('playerMenuBackdrop');
const menuCloseBtn = document.getElementById('menuCloseBtn');

let state = {pages: [], interactions: []};
let historyStack = [];
let currentPageId = null;
let frameController = null;
let renderVersion = 0;
let menuOpen = false;
let previewFocusTarget = null;
const contentUrlRefreshes = new Map();

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

function focusableMenuElements() {
  return Array.from(menuLayer.querySelectorAll('a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])'))
    .filter((element) => !element.hidden);
}

function restorePreviewFocus() {
  requestAnimationFrame(() => {
    const target = frameController?.iframe || previewFocusTarget || stage;
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

async function load() {
  try {
    state = await api(`/api/projects/${projectId}`);
  } catch (error) {
    stage.innerHTML = `<div class="player-empty">${esc(error.message)}</div>`;
    return;
  }
  const requestedPageId = new URLSearchParams(location.search).get('page');
  currentPageId = state.pages.some((item) => item.id === requestedPageId)
    ? requestedPageId
    : state.pages[0]?.id || null;
  if (currentPageId) historyStack = [currentPageId];
  pageCount.textContent = state.pages.length;
  void render();
}

function updateUrl(pageId) {
  const url = new URL(location.href);
  if (pageId) url.searchParams.set('page', pageId);
  else url.searchParams.delete('page');
  history.replaceState(null, '', url);
}

function navigate(pageId) {
  if (!state.pages.some((item) => item.id === pageId) || pageId === currentPageId) return;
  currentPageId = pageId;
  historyStack.push(pageId);
  updateUrl(pageId);
  void render();
}

function goBack() {
  if (historyStack.length <= 1) return;
  historyStack.pop();
  currentPageId = historyStack[historyStack.length - 1];
  updateUrl(currentPageId);
  void render();
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

function renderPageList() {
  if (!state.pages.length) {
    pageList.innerHTML = '<div class="player-nav-empty">暂无页面</div>';
    return;
  }
  pageList.innerHTML = state.pages.map((item) => `
    <button type="button" class="player-page-item ${item.id === currentPageId ? 'active' : ''}" data-id="${item.id}">
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

async function render() {
  const currentRenderVersion = ++renderVersion;
  if (frameController) {
    frameController.destroy();
    frameController = null;
  }
  backBtn.disabled = historyStack.length <= 1;
  const currentPage = page(currentPageId);
  pageNameElement.textContent = currentPage?.name || '';
  stage.classList.toggle('is-image-page', currentPage?.type === 'image');
  renderPageList();
  if (!currentPage) {
    stage.innerHTML = '<div class="player-empty">项目还没有页面。</div>';
    return;
  }

  if (contentUrlExpiring(currentPage)) {
    stage.innerHTML = '<div class="player-empty">正在刷新资源访问地址…</div>';
    try {
      await refreshPageContentUrl(currentPage);
    } catch (error) {
      if (currentRenderVersion === renderVersion) {
        stage.innerHTML = `<div class="player-empty">${esc(error.message)}</div>`;
      }
      return;
    }
    if (currentRenderVersion !== renderVersion || currentPage.id !== currentPageId) return;
  }

  if (currentPage.type === 'html') {
    frameController = window.UIPMFrameFit.create({
      host: stage,
      pageId: currentPage.id,
      title: currentPage.name,
      src: pageContentUrl(currentPage, 'play'),
      variant: 'player',
      renderMode: currentPage.render_mode || 'auto',
      viewportWidth: currentPage.viewport_width || 1920,
      viewportHeight: currentPage.viewport_height || 1080,
    });
    return;
  }

  stage.innerHTML = `
    <div id="playImageStage" class="player-image-stage">
      <img id="playerPageImage" src="${esc(pageContentUrl(currentPage))}" alt="${esc(currentPage.name)}">
    </div>`;
  const imageStage = document.getElementById('playImageStage');
  const image = document.getElementById('playerPageImage');
  image.addEventListener('error', async () => {
    if (image.dataset.contentUrlRetried === 'true') return;
    image.dataset.contentUrlRetried = 'true';
    try {
      await refreshPageContentUrl(currentPage);
      if (currentPage.id === currentPageId) image.src = pageContentUrl(currentPage);
    } catch (error) {
      stage.innerHTML = `<div class="player-empty">${esc(error.message)}</div>`;
    }
  });
  interactions(currentPage.id).filter((item) => item.kind === 'region').forEach((interaction) => {
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
    hotspot.addEventListener('click', () => executeInteraction(interaction));
    imageStage.appendChild(hotspot);
  });
}

window.addEventListener('message', (event) => {
  const data = event.data;
  if (!data || data.pageId !== currentPageId || !frameController?.ownsMessage(event)) return;
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
menuCloseBtn.addEventListener('click', () => setMenuOpen(false));
menuBackdrop.addEventListener('click', () => setMenuOpen(false));

void load();
