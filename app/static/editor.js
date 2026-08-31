const root = document.getElementById('app');
const projectId = root.dataset.projectId;
const pageList = document.getElementById('pageList');
const canvasArea = document.getElementById('canvasArea');
const selectionPanel = document.getElementById('selectionPanel');
const interactionList = document.getElementById('interactionList');
const pageCount = document.getElementById('pageCount');
const pageSearchInput = document.getElementById('pageSearchInput');
const pageSearchClear = document.getElementById('pageSearchClear');
const replaceImageInput = document.getElementById('replaceImageInput');
const interactionCount = document.getElementById('interactionCount');
const currentPageName = document.getElementById('currentPageName');
const currentPageMeta = document.getElementById('currentPageMeta');
const modeHelp = document.getElementById('modeHelp');
const confirmDialog = document.getElementById('confirmDialog');
const renameDialog = document.getElementById('renameDialog');
const renameForm = document.getElementById('renameForm');
const renameInput = document.getElementById('renameInput');
const renameTitle = document.getElementById('renameTitle');
const storageSelect = document.getElementById('storageSelect');
const uploadDialog = document.getElementById('uploadDialog');
const uploadForm = document.getElementById('uploadForm');
const uploadRows = document.getElementById('uploadRows');
const uploadRenderMode = document.getElementById('uploadRenderMode');
const uploadViewportWidth = document.getElementById('uploadViewportWidth');
const uploadViewportHeight = document.getElementById('uploadViewportHeight');
const uploadDialogTitle = document.getElementById('uploadDialogTitle');
const uploadCancel = document.getElementById('uploadCancel');
const uploadSubmit = document.getElementById('uploadSubmit');
const uploadSubmitLabel = document.getElementById('uploadSubmitLabel');
const uploadStatus = document.getElementById('uploadStatus');
const uploadStatusTitle = document.getElementById('uploadStatusTitle');
const uploadStatusDetail = document.getElementById('uploadStatusDetail');
const renderSettingsSection = document.getElementById('renderSettingsSection');
const renderSettingsPanel = document.getElementById('renderSettingsPanel');
const overlayElementsSection = document.getElementById('overlayElementsSection');
const overlayImageInput = document.getElementById('overlayImageInput');
const overlayVideoInput = document.getElementById('overlayVideoInput');
const overlayLinkButton = document.getElementById('overlayLinkButton');
const overlayLinkDialog = document.getElementById('overlayLinkDialog');
const overlayLinkForm = document.getElementById('overlayLinkForm');
const overlayLinkType = document.getElementById('overlayLinkType');
const overlayLinkUrl = document.getElementById('overlayLinkUrl');
const overlayLinkStatus = document.getElementById('overlayLinkStatus');
const overlayLinkCancel = document.getElementById('overlayLinkCancel');
const overlayLinkSubmit = document.getElementById('overlayLinkSubmit');
const overlayCount = document.getElementById('overlayCount');
const overlayList = document.getElementById('overlayList');
const overlaySelectionPanel = document.getElementById('overlaySelectionPanel');
const overlayUploadStatus = document.getElementById('overlayUploadStatus');
const overlayUploadTitle = document.getElementById('overlayUploadTitle');
const overlayUploadDetail = document.getElementById('overlayUploadDetail');

let state = {pages: [], interactions: [], overlays: []};
let config = {
  storage_backends: ['local'],
  s3: {configured: false},
  html_render_defaults: {render_mode: 'auto', viewport_width: 1920, viewport_height: 1080},
};
let currentPageId = null;
let selection = null;
let hoveredInteractionId = null;
let missingElementIds = new Set();
let htmlElementMeta = new Map();
let drawing = null;
let selectedOverlayId = null;
let hoveredOverlayId = null;
let overlayGesture = null;
let overlayUploadInProgress = false;
let overlayUploadStartedAt = 0;
let overlayUploadTimer = null;
let renameTarget = null;
let pendingFiles = [];
let frameController = null;
let canvasRenderVersion = 0;
const contentUrlRefreshes = new Map();
let uploadStartedAt = 0;
let uploadTimer = null;
let uploadInProgress = false;
let draggedPageId = null;
let pageOrderSaving = false;
let replaceImagePageId = null;

function esc(value = '') {
  const element = document.createElement('div');
  element.textContent = value;
  return element.innerHTML;
}

function norm(value) {
  return String(value || '').trim().replace(/\s+/g, ' ').toLocaleLowerCase();
}

function currentPage() {
  return state.pages.find((page) => page.id === currentPageId);
}

function currentInteractions() {
  return state.interactions.filter((interaction) => interaction.source_page_id === currentPageId);
}

function currentOverlays() {
  return state.overlays
    .filter((overlay) => overlay.page_id === currentPageId)
    .sort((left, right) => left.z_index - right.z_index || left.created_at.localeCompare(right.created_at));
}

function overlayById(overlayId) {
  return state.overlays.find((overlay) => overlay.id === overlayId);
}

function interactionById(interactionId) {
  return state.interactions.find((interaction) => interaction.id === interactionId);
}

function pageName(pageId) {
  return state.pages.find((page) => page.id === pageId)?.name || '已删除页面';
}

function storageLabel(page) {
  if (page.storage_backend === 'url') return 'LINK';
  if ((page.storage_backend || 'local') !== 's3') return 'LOCAL';
  return config.s3?.provider === 'oss' ? 'OSS' : 'S3';
}

function interactionView(interaction) {
  if (!selection || selection.interactionId !== interaction.id) return interaction;
  return {
    ...interaction,
    name: selection.name,
    action: selection.action,
    target_page_id: selection.targetId || null,
    target_url: selection.externalUrl || null,
  };
}

function actionLabel(interaction) {
  if (interaction.action === 'back') return '↩ 返回上一页';
  if (interaction.action === 'external') return `↗ ${interaction.target_url || '外部网页'}`;
  return `→ ${pageName(interaction.target_page_id)}`;
}

function elementDescription(interaction) {
  const meta = htmlElementMeta.get(interaction.id);
  if (!meta) return `HTML 元素 · ${interaction.payload.elementId}`;
  const text = meta.text ? ` ${meta.text}` : '';
  return `<${meta.tag}>${text}`;
}

function selectionForInteraction(interaction, label = '') {
  return {
    isNew: false,
    interactionId: interaction.id,
    kind: interaction.kind,
    payload: {...interaction.payload},
    label: label || (interaction.kind === 'element' ? elementDescription(interaction) : '已保存的图片区域'),
    name: interaction.name,
    action: interaction.action,
    targetId: interaction.target_page_id || '',
    externalUrl: interaction.target_url || '',
    dirty: false,
  };
}

function confirmDiscardSelection() {
  if (!selection || (!selection.isNew && !selection.dirty)) return true;
  return window.confirm('当前交互配置尚未保存，是否放弃修改？');
}

function uploadElapsedSeconds() {
  return Math.max(0, Math.floor((Date.now() - uploadStartedAt) / 1000));
}

function updateUploadStatusDetail(fileCount) {
  uploadStatusDetail.textContent = `${fileCount} 个文件 · 请保持当前页面打开 · 已耗时 ${uploadElapsedSeconds()} 秒`;
}

function setUploadInProgress(inProgress, fileCount = pendingFiles.length) {
  uploadInProgress = inProgress;
  uploadForm.setAttribute('aria-busy', String(inProgress));
  uploadDialog.classList.toggle('is-uploading', inProgress);
  uploadForm.querySelectorAll('input, select, button').forEach((control) => {
    control.disabled = inProgress;
  });
  uploadStatus.hidden = !inProgress;
  uploadDialogTitle.textContent = inProgress ? '正在上传页面…' : '确认上传页面';
  uploadSubmitLabel.textContent = inProgress ? '上传中…' : '上传';

  if (uploadTimer) {
    clearInterval(uploadTimer);
    uploadTimer = null;
  }
  if (!inProgress) return;

  uploadStartedAt = Date.now();
  uploadStatusTitle.textContent = '正在上传并处理文件…';
  updateUploadStatusDetail(fileCount);
  uploadTimer = setInterval(() => updateUploadStatusDetail(fileCount), 1000);
}

function goLogin() {
  location.href = `/login?next=${encodeURIComponent(location.pathname + location.search)}`;
}

async function api(url, options = {}) {
  const response = await fetch(url, options);
  if (response.status === 401) {
    goLogin();
    throw new Error('登录已过期');
  }
  if (!response.ok) {
    let message = '操作失败';
    try {
      const data = await response.json();
      message = data.detail || message;
    } catch {}
    throw new Error(message);
  }
  return response.status === 204 ? null : response.json();
}

function contentUrlExpiring(page, skewMs = 60_000) {
  if (!page.content_url_expires_at) return false;
  const expiresAt = Date.parse(page.content_url_expires_at);
  return !Number.isFinite(expiresAt) || expiresAt - Date.now() <= skewMs;
}

function pageContentUrl(page, mode = null) {
  const fallback = page.type === 'html'
    ? `/api/pages/${page.id}/render`
    : `/api/pages/${page.id}/file`;
  const url = new URL(page.content_url || fallback, location.href);
  if (mode) url.hash = new URLSearchParams({'uipm-mode': mode}).toString();
  return url.href;
}

async function refreshPageContentUrl(page) {
  if (!contentUrlRefreshes.has(page.id)) {
    const refresh = api(`/api/pages/${page.id}/content-url`)
      .then((data) => Object.assign(page, data))
      .finally(() => contentUrlRefreshes.delete(page.id));
    contentUrlRefreshes.set(page.id, refresh);
  }
  await contentUrlRefreshes.get(page.id);
}

async function loadConfig() {
  config = await api('/api/config');
  const remoteStorageLabel = config.s3?.provider === 'oss' ? 'OSS' : 'S3';
  storageSelect.innerHTML = config.storage_backends
    .map((backend) => `<option value="${backend}">${backend === 's3' ? remoteStorageLabel : '本地'}</option>`)
    .join('');
  storageSelect.value = config.default_storage_backend || 'local';
  storageSelect.title = config.s3?.configured
    ? `${remoteStorageLabel} 已配置：${config.s3.bucket}`
    : `本地目录：${config.data_dir}`;
}

async function reload(keepCurrent = true) {
  state = await api(`/api/projects/${projectId}`);
  state.overlays = Array.isArray(state.overlays) ? state.overlays : [];
  if (!keepCurrent || !state.pages.some((page) => page.id === currentPageId)) {
    currentPageId = state.pages[0]?.id || null;
  }
  selection = null;
  selectedOverlayId = null;
  hoveredOverlayId = null;
  hoveredInteractionId = null;
  missingElementIds = new Set();
  htmlElementMeta = new Map();
  renderAll();
}

function renderAll() {
  renderPageList();
  renderCanvas();
  renderOverlayElements();
  renderRenderSettings();
  renderInteractions();
  renderSelection();
}

function renderPageList() {
  const query = pageSearchInput.value;
  const visiblePages = window.UIPMPageList.filterByName(state.pages, query);
  const searching = Boolean(query.trim());
  pageSearchClear.hidden = !searching;
  pageCount.textContent = searching ? `${visiblePages.length}/${state.pages.length}` : state.pages.length;
  if (!state.pages.length) {
    pageList.innerHTML = '<div class="interaction-empty">暂无页面</div>';
    return;
  }
  if (!visiblePages.length) {
    pageList.innerHTML = '<div class="interaction-empty">没有匹配的页面</div>';
    return;
  }
  pageList.innerHTML = visiblePages.map((page) => `
    <div class="page-item ${page.id === currentPageId ? 'active' : ''}" data-id="${page.id}" draggable="${!searching && page.id === currentPageId && !pageOrderSaving}">
      <span class="page-drag-handle" title="选中后上下拖动排序" aria-hidden="true">⠿</span>
      <div class="page-type-icon">${page.type === 'html' ? 'HTML' : 'IMG'}</div>
      <div class="page-main">
        <div class="page-name" title="${esc(page.name)}">${esc(page.name)}</div>
        <div class="page-storage">${storageLabel(page)}</div>
      </div>
      ${page.type === 'image' ? `<button class="mini-action replace-page-image" type="button" title="替换主体图片" aria-label="替换 ${esc(page.name)} 的主体图片">↻</button>` : ''}
      <button class="mini-action rename-page" type="button" title="重命名" aria-label="重命名 ${esc(page.name)}">✎</button>
      <button class="mini-danger delete-page" type="button" title="删除页面" aria-label="删除 ${esc(page.name)}">✕</button>
    </div>`).join('');

  pageList.querySelectorAll('.page-item').forEach((row) => {
    row.addEventListener('click', (event) => {
      if (event.target.closest('button') || row.dataset.id === currentPageId) return;
      if (!confirmDiscardSelection()) return;
      currentPageId = row.dataset.id;
      selection = null;
      selectedOverlayId = null;
      hoveredOverlayId = null;
      hoveredInteractionId = null;
      missingElementIds = new Set();
      htmlElementMeta = new Map();
      renderAll();
    });
    row.querySelector('.replace-page-image')?.addEventListener('click', (event) => {
      event.stopPropagation();
      if (!confirmDiscardSelection()) return;
      replaceImagePageId = row.dataset.id;
      replaceImageInput.value = '';
      replaceImageInput.click();
    });
    row.querySelector('.rename-page').addEventListener('click', (event) => {
      event.stopPropagation();
      if (!confirmDiscardSelection()) return;
      openRename('page', row.dataset.id);
    });
    row.querySelector('.delete-page').addEventListener('click', (event) => {
      event.stopPropagation();
      askDeletePage(row.dataset.id);
    });
    row.addEventListener('dragstart', (event) => {
      if (searching || pageOrderSaving || row.dataset.id !== currentPageId) {
        event.preventDefault();
        return;
      }
      draggedPageId = row.dataset.id;
      row.classList.add('is-dragging');
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', draggedPageId);
    });
    row.addEventListener('dragover', (event) => {
      if (!draggedPageId || draggedPageId === row.dataset.id) return;
      event.preventDefault();
      const after = event.clientY >= row.getBoundingClientRect().top + row.offsetHeight / 2;
      row.classList.toggle('drop-after', after);
      row.classList.toggle('drop-before', !after);
    });
    row.addEventListener('dragleave', () => row.classList.remove('drop-before', 'drop-after'));
    row.addEventListener('drop', async (event) => {
      event.preventDefault();
      const sourceId = draggedPageId;
      const targetId = row.dataset.id;
      const after = event.clientY >= row.getBoundingClientRect().top + row.offsetHeight / 2;
      clearPageDragState();
      if (!sourceId || sourceId === targetId) return;
      await persistPageOrder(window.UIPMPageList.movePage(state.pages, sourceId, targetId, after));
    });
    row.addEventListener('dragend', clearPageDragState);
  });
}

function clearPageDragState() {
  draggedPageId = null;
  pageList.querySelectorAll('.page-item').forEach((row) => {
    row.classList.remove('is-dragging', 'drop-before', 'drop-after');
  });
}

async function persistPageOrder(nextPages) {
  const previousPages = state.pages;
  state.pages = nextPages;
  pageOrderSaving = true;
  renderPageList();
  try {
    await api(`/api/projects/${projectId}/pages/order`, {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({page_ids: nextPages.map((page) => page.id)}),
    });
    state.pages.forEach((page, index) => { page.sort_order = index; });
  } catch (error) {
    state.pages = previousPages;
    alert(error.message);
  } finally {
    pageOrderSaving = false;
    renderPageList();
  }
}

async function renderCanvas() {
  const renderVersion = ++canvasRenderVersion;
  if (frameController) {
    frameController.destroy();
    frameController = null;
  }
  const page = currentPage();
  if (!page) {
    currentPageName.textContent = '请选择页面';
    currentPageMeta.textContent = '';
    modeHelp.textContent = '';
    canvasArea.innerHTML = `
      <div class="empty-state">
        <div class="empty-icon">↗</div><h2>先上传页面</h2>
        <p>HTML：点击元素配置跳转；图片：拖拽框选区域配置跳转。</p>
        <label class="primary-btn">上传页面<input class="emptyUpload" type="file" multiple accept=".html,.htm,.zip,.png,.jpg,.jpeg,.webp,.gif" hidden></label>
      </div>`;
    canvasArea.querySelector('.emptyUpload').addEventListener('change', (event) => prepareUpload(event.target.files));
    return;
  }

  if (contentUrlExpiring(page)) {
    canvasArea.innerHTML = '<div class="empty-state"><p>正在刷新资源访问地址…</p></div>';
    try {
      await refreshPageContentUrl(page);
    } catch (error) {
      if (renderVersion === canvasRenderVersion) {
        canvasArea.innerHTML = `<div class="empty-state"><p>${esc(error.message)}</p></div>`;
      }
      return;
    }
    if (renderVersion !== canvasRenderVersion || page.id !== currentPageId) return;
  }

  currentPageName.textContent = page.name;
  const renderLabel = page.type === 'html'
    ? ` · ${{auto: '自动', fixed: '固定', responsive: '响应式'}[page.render_mode || 'auto'] || '自动'}`
    : '';
  currentPageMeta.textContent = `${page.type.toUpperCase()} · ${storageLabel(page)}${renderLabel}`;

  if (page.type === 'html') {
    modeHelp.textContent = '已保存交互常驻显示；点击元素或右侧条目可双向定位';
    frameController = window.UIPMFrameFit.create({
      host: canvasArea,
      pageId: page.id,
      iframeId: 'htmlFrame',
      title: page.name,
      src: pageContentUrl(page, 'edit'),
      variant: 'editor',
      responsiveHeight: 720,
      renderMode: page.render_mode || 'auto',
      viewportWidth: page.viewport_width || 1920,
      viewportHeight: page.viewport_height || 1080,
    });
    renderHtmlPageOverlays();
    frameController.iframe.addEventListener('load', () => postHtmlEditorState(), {once: true});
    return;
  }

  modeHelp.textContent = '拖拽创建新区域；点击已有区域或右侧条目可双向定位';
  canvasArea.innerHTML = `
    <div id="imageStage" class="image-stage">
      <img id="pageImage" src="${esc(pageContentUrl(page))}" alt="${esc(page.name)}">
      <div id="imageOverlayLayer" class="overlay-layer"></div>
      <div id="imageGuideLayer" class="editor-guide-layer"></div>
    </div>`;
  renderImagePageOverlays();
  const image = document.getElementById('pageImage');
  image.addEventListener('error', async () => {
    if (image.dataset.contentUrlRetried === 'true') return;
    image.dataset.contentUrlRetried = 'true';
    try {
      await refreshPageContentUrl(page);
      if (page.id === currentPageId) image.src = pageContentUrl(page);
    } catch (error) {
      alert(error.message);
    }
  });
  if (image.complete && image.naturalWidth > 0) initImageStage();
  else image.addEventListener('load', initImageStage, {once: true});
}

function overlayContentUrl(overlay) {
  return new URL(overlay.content_url || `/api/overlays/${overlay.id}/content-url`, location.href).href;
}

async function refreshOverlayContentUrl(overlay) {
  const refreshKey = `overlay:${overlay.id}`;
  if (!contentUrlRefreshes.has(refreshKey)) {
    const refresh = api(`/api/overlays/${overlay.id}/content-url`)
      .then((data) => Object.assign(overlay, data))
      .finally(() => contentUrlRefreshes.delete(refreshKey));
    contentUrlRefreshes.set(refreshKey, refresh);
  }
  await contentUrlRefreshes.get(refreshKey);
}

function applyOverlayGeometry(element, overlay) {
  Object.assign(element.style, {
    left: `${overlay.x * 100}%`,
    top: `${overlay.y * 100}%`,
    width: `${overlay.width * 100}%`,
    height: `${overlay.height * 100}%`,
  });
}

async function retryOverlayMedia(media, overlay) {
  if (media.dataset.contentUrlRetried === 'true') return;
  media.dataset.contentUrlRetried = 'true';
  try {
    await refreshOverlayContentUrl(overlay);
    if (overlay.page_id === currentPageId && media.isConnected) media.src = overlayContentUrl(overlay);
  } catch (error) {
    alert(error.message);
  }
}

function createEditorOverlayElement(overlay) {
  const element = document.createElement('div');
  const selected = selectedOverlayId === overlay.id;
  const hovered = hoveredOverlayId === overlay.id;
  const displayName = overlayDisplayName(overlay);
  element.className = `editor-overlay ${selected ? 'is-selected' : ''} ${hovered ? 'is-hovered' : ''}`;
  element.dataset.id = overlay.id;
  element.tabIndex = 0;
  element.setAttribute('role', 'button');
  element.setAttribute('aria-label', `选择媒体：${displayName}`);
  element.setAttribute('aria-pressed', String(selected));
  element.style.zIndex = selected ? '40' : hovered ? '35' : '30';
  applyOverlayGeometry(element, overlay);

  const media = document.createElement(overlay.type === 'video' ? 'video' : 'img');
  media.className = 'editor-overlay-media';
  media.draggable = false;
  media.style.objectFit = overlay.object_fit;
  if (overlay.storage_backend === 'url') media.referrerPolicy = 'no-referrer';
  if (overlay.type === 'video') {
    media.controls = false;
    media.playsInline = true;
    media.preload = 'metadata';
  } else {
    media.alt = '';
  }
  media.src = overlayContentUrl(overlay);
  media.addEventListener('error', () => retryOverlayMedia(media, overlay));
  element.appendChild(media);

  const label = document.createElement('span');
  label.className = 'editor-overlay-label';
  label.textContent = displayName;
  element.appendChild(label);

  const handle = document.createElement('span');
  handle.className = 'editor-overlay-handle';
  handle.setAttribute('aria-hidden', 'true');
  handle.addEventListener('pointerdown', (event) => startOverlayGesture(event, overlay.id, 'resize'));
  element.appendChild(handle);

  element.addEventListener('pointerdown', (event) => startOverlayGesture(event, overlay.id, 'drag'));
  element.addEventListener('pointerenter', () => setHoveredOverlay(overlay.id));
  element.addEventListener('pointerleave', () => setHoveredOverlay(null));
  element.addEventListener('focus', () => setHoveredOverlay(overlay.id));
  element.addEventListener('blur', () => setHoveredOverlay(null));
  element.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      selectOverlay(overlay.id, {source: 'canvas'});
    }
  });
  return element;
}

function renderEditorOverlays(layer) {
  if (!layer) return;
  layer.replaceChildren(...currentOverlays().map(createEditorOverlayElement));
}

function renderImagePageOverlays() {
  renderEditorOverlays(document.getElementById('imageOverlayLayer'));
}

function renderHtmlPageOverlays() {
  const viewport = frameController?.viewport;
  if (!viewport) return;
  const layer = document.createElement('div');
  layer.className = 'overlay-layer';
  layer.id = 'htmlOverlayLayer';
  viewport.appendChild(layer);
  const guides = document.createElement('div');
  guides.className = 'editor-guide-layer';
  guides.id = 'htmlGuideLayer';
  viewport.appendChild(guides);
  renderEditorOverlays(layer);
}

function currentOverlayLayer() {
  return currentPage()?.type === 'html'
    ? document.getElementById('htmlOverlayLayer')
    : document.getElementById('imageOverlayLayer');
}

function currentGuideLayer() {
  return currentPage()?.type === 'html'
    ? document.getElementById('htmlGuideLayer')
    : document.getElementById('imageGuideLayer');
}

function rerenderEditorOverlays() {
  renderEditorOverlays(currentOverlayLayer());
}

function applyOverlaySelectionState() {
  currentOverlayLayer()?.querySelectorAll('.editor-overlay').forEach((element) => {
    const selected = element.dataset.id === selectedOverlayId;
    const hovered = element.dataset.id === hoveredOverlayId;
    element.classList.toggle('is-selected', selected);
    element.classList.toggle('is-hovered', hovered);
    element.setAttribute('aria-pressed', String(selected));
    element.style.zIndex = selected ? '40' : hovered ? '35' : '30';
  });
  overlayList.querySelectorAll('.overlay-list-item').forEach((row) => {
    row.classList.toggle('is-hovered', row.dataset.id === hoveredOverlayId);
  });
}

function setHoveredOverlay(overlayId) {
  if (hoveredOverlayId === overlayId) return;
  hoveredOverlayId = overlayId;
  applyOverlaySelectionState();
}

function scrollOverlayListItemIntoView(overlayId) {
  requestAnimationFrame(() => {
    overlayList.querySelector(`.overlay-list-item[data-id="${overlayId}"]`)
      ?.scrollIntoView({block: 'nearest'});
  });
}

function scrollEditorOverlayIntoView(overlayId) {
  requestAnimationFrame(() => {
    currentOverlayLayer()?.querySelector(`.editor-overlay[data-id="${overlayId}"]`)
      ?.scrollIntoView({behavior: 'smooth', block: 'center', inline: 'center'});
  });
}

function selectOverlay(overlayId, {source = 'canvas'} = {}) {
  const overlay = overlayById(overlayId);
  if (!overlay || overlay.page_id !== currentPageId) return false;
  if (selectedOverlayId === overlayId) {
    if (source === 'list') scrollEditorOverlayIntoView(overlayId);
    return true;
  }
  if (!confirmDiscardSelection()) return false;
  selection = null;
  hoveredInteractionId = null;
  selectedOverlayId = overlayId;
  renderSelection();
  renderInteractions();
  renderOverlayElements();
  applyOverlaySelectionState();
  refreshCanvasAnnotations({rerenderImage: true});
  if (source === 'list') scrollEditorOverlayIntoView(overlayId);
  else scrollOverlayListItemIntoView(overlayId);
  return true;
}

function clearOverlaySelection() {
  if (!selectedOverlayId) return;
  selectedOverlayId = null;
  renderOverlayElements();
  applyOverlaySelectionState();
}

function snapAxis(position, size, threshold) {
  const candidates = [
    {position: 0, guide: 0},
    {position: 0.5 - size / 2, guide: 0.5},
    {position: 1 - size, guide: 1},
  ];
  let match = null;
  candidates.forEach((candidate) => {
    const distance = Math.abs(candidate.position - position);
    if (distance <= threshold && (!match || distance < match.distance)) {
      match = {...candidate, distance};
    }
  });
  return match || {position, guide: null};
}

function showSnapGuides(vertical, horizontal) {
  const layer = currentGuideLayer();
  if (!layer) return;
  const guides = [];
  if (vertical !== null) {
    const guide = document.createElement('span');
    guide.className = 'editor-guide is-vertical';
    guide.style.left = vertical === 1 ? 'calc(100% - 1px)' : `${vertical * 100}%`;
    guides.push(guide);
  }
  if (horizontal !== null) {
    const guide = document.createElement('span');
    guide.className = 'editor-guide is-horizontal';
    guide.style.top = horizontal === 1 ? 'calc(100% - 1px)' : `${horizontal * 100}%`;
    guides.push(guide);
  }
  layer.replaceChildren(...guides);
}

function clearSnapGuides() {
  currentGuideLayer()?.replaceChildren();
}

const savingOverlayIds = new Set();

function startOverlayGesture(event, overlayId, mode) {
  if (event.button !== 0 || overlayGesture || savingOverlayIds.has(overlayId)) return;
  if (mode === 'resize') event.stopPropagation();
  const overlay = overlayById(overlayId);
  if (!overlay || !selectOverlay(overlayId)) return;
  event.preventDefault();
  event.stopPropagation();
  const element = event.target.closest('.editor-overlay');
  const layer = currentOverlayLayer();
  const rect = layer?.getBoundingClientRect();
  if (!element || !rect || rect.width <= 0 || rect.height <= 0) return;
  const captureElement = event.currentTarget;
  const original = {
    x: overlay.x,
    y: overlay.y,
    width: overlay.width,
    height: overlay.height,
  };
  overlayGesture = {
    mode,
    overlay,
    element,
    captureElement,
    pointerId: event.pointerId,
    rect,
    original,
    startClientX: event.clientX,
    startClientY: event.clientY,
    moved: false,
  };
  captureElement.setPointerCapture(event.pointerId);
  captureElement.addEventListener('pointermove', moveOverlayGesture);
  captureElement.addEventListener('pointerup', finishOverlayGesture);
  captureElement.addEventListener('pointercancel', cancelOverlayGesture);
}

function moveOverlayGesture(event) {
  const gesture = overlayGesture;
  if (!gesture || event.pointerId !== gesture.pointerId) return;
  event.preventDefault();
  const deltaX = event.clientX - gesture.startClientX;
  const deltaY = event.clientY - gesture.startClientY;
  const {overlay, original, rect} = gesture;

  if (gesture.mode === 'drag') {
    let x = Math.max(0, Math.min(1 - original.width, original.x + deltaX / rect.width));
    let y = Math.max(0, Math.min(1 - original.height, original.y + deltaY / rect.height));
    const snappedX = snapAxis(x, original.width, 6 / rect.width);
    const snappedY = snapAxis(y, original.height, 6 / rect.height);
    x = snappedX.position;
    y = snappedY.position;
    Object.assign(overlay, {x, y});
    showSnapGuides(snappedX.guide, snappedY.guide);
  } else {
    const aspectRatio = Number(overlay.aspect_ratio) > 0
      ? Number(overlay.aspect_ratio)
      : (original.width * rect.width) / (original.height * rect.height);
    const minimumWidth = Math.min(40, (1 - original.x) * rect.width);
    const maximumWidth = Math.min(
      (1 - original.x) * rect.width,
      (1 - original.y) * rect.height * aspectRatio,
    );
    const pixelWidth = Math.min(maximumWidth, Math.max(minimumWidth, original.width * rect.width + deltaX));
    Object.assign(overlay, {
      width: pixelWidth / rect.width,
      height: (pixelWidth / aspectRatio) / rect.height,
    });
  }
  gesture.moved = gesture.moved || Math.abs(deltaX) >= 1 || Math.abs(deltaY) >= 1;
  applyOverlayGeometry(gesture.element, overlay);
}

function cleanupOverlayGesture() {
  const gesture = overlayGesture;
  if (!gesture) return null;
  const {captureElement, pointerId} = gesture;
  captureElement.removeEventListener('pointermove', moveOverlayGesture);
  captureElement.removeEventListener('pointerup', finishOverlayGesture);
  captureElement.removeEventListener('pointercancel', cancelOverlayGesture);
  if (captureElement.hasPointerCapture(pointerId)) captureElement.releasePointerCapture(pointerId);
  overlayGesture = null;
  clearSnapGuides();
  return gesture;
}

function cancelOverlayGesture() {
  const gesture = cleanupOverlayGesture();
  if (!gesture) return;
  Object.assign(gesture.overlay, gesture.original);
  applyOverlayGeometry(gesture.element, gesture.overlay);
}

function finishOverlayGesture() {
  const gesture = cleanupOverlayGesture();
  if (!gesture || !gesture.moved) return;
  void saveOverlayGeometry(gesture.overlay, gesture.original, gesture.element);
}

function roundedOverlayGeometry(overlay) {
  return Object.fromEntries(
    ['x', 'y', 'width', 'height'].map((field) => [field, Number(overlay[field].toFixed(8))]),
  );
}

async function saveOverlayGeometry(overlay, original, element) {
  savingOverlayIds.add(overlay.id);
  try {
    const saved = await api(`/api/overlays/${overlay.id}`, {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(roundedOverlayGeometry(overlay)),
    });
    const index = state.overlays.findIndex((item) => item.id === saved.id);
    if (index >= 0) state.overlays[index] = saved;
  } catch (error) {
    Object.assign(overlay, original);
    if (element.isConnected) applyOverlayGeometry(element, overlay);
    alert(error.message);
  } finally {
    savingOverlayIds.delete(overlay.id);
    if (overlay.page_id === currentPageId) {
      renderOverlayElements();
      rerenderEditorOverlays();
    }
  }
}

function overlayDisplayName(overlay, items = currentOverlays()) {
  const sameType = items.filter((item) => item.type === overlay.type);
  const index = sameType.findIndex((item) => item.id === overlay.id) + 1;
  return `${overlay.type === 'video' ? '视频' : '图片'} ${Math.max(1, index)}`;
}

function renderOverlayElements() {
  const page = currentPage();
  overlayElementsSection.hidden = !page;
  if (!page) {
    overlayCount.textContent = '0';
    overlayList.innerHTML = '';
    overlaySelectionPanel.hidden = true;
    return;
  }

  const items = currentOverlays();
  if (selectedOverlayId && !items.some((item) => item.id === selectedOverlayId)) {
    selectedOverlayId = null;
  }
  if (hoveredOverlayId && !items.some((item) => item.id === hoveredOverlayId)) {
    hoveredOverlayId = null;
  }
  overlayCount.textContent = items.length;
  if (!items.length) {
    overlayList.innerHTML = '<div class="overlay-list-empty">当前页面还没有媒体</div>';
  } else {
    overlayList.innerHTML = items.map((overlay) => {
      const name = overlayDisplayName(overlay, items);
      const active = overlay.id === selectedOverlayId;
      const hovered = overlay.id === hoveredOverlayId;
      return `
        <div class="overlay-list-item ${active ? 'is-active' : ''} ${hovered ? 'is-hovered' : ''}" data-id="${overlay.id}">
          <button class="overlay-list-select" type="button" aria-pressed="${active}">
            <span class="overlay-list-icon">${overlay.type === 'video' ? 'VID' : 'IMG'}</span>
            <span class="overlay-list-name">${name}</span>
          </button>
          <button class="mini-danger delete-overlay" type="button" title="删除" aria-label="删除 ${name}">✕</button>
        </div>`;
    }).join('');
    overlayList.querySelectorAll('.overlay-list-item').forEach((row) => {
      const selectButton = row.querySelector('.overlay-list-select');
      const deleteButton = row.querySelector('.delete-overlay');
      row.addEventListener('pointerenter', () => setHoveredOverlay(row.dataset.id));
      row.addEventListener('pointerleave', () => setHoveredOverlay(null));
      selectButton.addEventListener('click', () => selectOverlay(row.dataset.id, {source: 'list'}));
      selectButton.addEventListener('focus', () => setHoveredOverlay(row.dataset.id));
      selectButton.addEventListener('blur', () => setHoveredOverlay(null));
      deleteButton.addEventListener('focus', () => setHoveredOverlay(row.dataset.id));
      deleteButton.addEventListener('blur', () => setHoveredOverlay(null));
      deleteButton.addEventListener('click', () => deleteOverlay(row.dataset.id));
    });
  }

  const selected = overlayById(selectedOverlayId);
  if (!selected || selected.page_id !== currentPageId) {
    overlaySelectionPanel.hidden = true;
    overlaySelectionPanel.innerHTML = '';
    return;
  }
  overlaySelectionPanel.hidden = false;
  overlaySelectionPanel.innerHTML = `
    <div class="overlay-selection-meta">
      <span>${overlayDisplayName(selected, items)}</span>
      <span>${selected.type === 'video' ? '视频' : '图片'} · ${storageLabel(selected)}</span>
    </div>
    ${selected.storage_backend === 'url' ? `
      <a class="overlay-source-link" href="${esc(selected.source_url || selected.content_url)}" target="_blank" rel="noopener noreferrer" title="${esc(selected.source_url || selected.content_url)}">打开原始链接 ↗</a>` : ''}
    <label for="overlayObjectFit">填充方式</label>
    <select id="overlayObjectFit">
      <option value="cover" ${selected.object_fit === 'cover' ? 'selected' : ''}>裁切填充</option>
      <option value="contain" ${selected.object_fit === 'contain' ? 'selected' : ''}>完整显示</option>
    </select>
    ${selected.type === 'video' ? `
      <label class="overlay-controls-check">
        <input id="overlayVideoControls" type="checkbox" ${selected.video_controls ? 'checked' : ''}>
        <span>预览显示播放控件</span>
      </label>` : ''}
    <button id="deleteSelectedOverlay" class="danger-btn overlay-delete-btn" type="button">删除媒体</button>`;

  document.getElementById('overlayObjectFit').addEventListener('change', (event) => {
    void updateOverlayProperties(selected.id, {object_fit: event.target.value});
  });
  document.getElementById('overlayVideoControls')?.addEventListener('change', (event) => {
    void updateOverlayProperties(selected.id, {video_controls: event.target.checked});
  });
  document.getElementById('deleteSelectedOverlay').addEventListener('click', () => deleteOverlay(selected.id));
}

async function updateOverlayProperties(overlayId, updates) {
  const overlay = overlayById(overlayId);
  if (!overlay || savingOverlayIds.has(overlayId)) return;
  savingOverlayIds.add(overlayId);
  try {
    const saved = await api(`/api/overlays/${overlayId}`, {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(updates),
    });
    const index = state.overlays.findIndex((item) => item.id === saved.id);
    if (index >= 0) state.overlays[index] = saved;
  } catch (error) {
    alert(error.message);
  } finally {
    savingOverlayIds.delete(overlayId);
    if (overlay.page_id === currentPageId) {
      renderOverlayElements();
      rerenderEditorOverlays();
    }
  }
}

async function deleteOverlay(overlayId) {
  const overlay = overlayById(overlayId);
  if (!overlay || savingOverlayIds.has(overlayId)) return;
  const name = overlayDisplayName(overlay);
  const deleteMessage = overlay.storage_backend === 'url'
    ? `删除${name}？只会移除页面中的链接配置，原始媒体不会受影响。`
    : `删除${name}？底层媒体资源也会同步删除。`;
  if (!window.confirm(deleteMessage)) return;
  savingOverlayIds.add(overlayId);
  try {
    await api(`/api/overlays/${overlayId}`, {method: 'DELETE'});
    state.overlays = state.overlays.filter((item) => item.id !== overlayId);
    if (selectedOverlayId === overlayId) selectedOverlayId = null;
    if (hoveredOverlayId === overlayId) hoveredOverlayId = null;
    renderOverlayElements();
    rerenderEditorOverlays();
  } catch (error) {
    alert(error.message);
  } finally {
    savingOverlayIds.delete(overlayId);
  }
}

function updateOverlayUploadStatus(file) {
  const elapsed = Math.max(0, Math.floor((Date.now() - overlayUploadStartedAt) / 1000));
  overlayUploadDetail.textContent = `${file.name} · 已耗时 ${elapsed} 秒`;
}

function setOverlayControlsBusy(inProgress) {
  overlayUploadInProgress = inProgress;
  overlayElementsSection.classList.toggle('is-uploading', inProgress);
  overlayImageInput.disabled = inProgress;
  overlayVideoInput.disabled = inProgress;
  overlayLinkButton.disabled = inProgress;
}

function setOverlayUploadInProgress(inProgress, file = null) {
  setOverlayControlsBusy(inProgress);
  overlayUploadStatus.hidden = !inProgress;
  if (overlayUploadTimer) {
    clearInterval(overlayUploadTimer);
    overlayUploadTimer = null;
  }
  if (!inProgress || !file) return;

  overlayUploadStartedAt = Date.now();
  const isVideo = file.type.startsWith('video/') || /\.(mp4|webm)$/i.test(file.name);
  overlayUploadTitle.textContent = `正在上传并处理${isVideo ? '视频' : '图片'}…`;
  updateOverlayUploadStatus(file);
  overlayUploadTimer = setInterval(() => updateOverlayUploadStatus(file), 1000);
}

async function uploadOverlay(input) {
  const file = input.files?.[0];
  input.value = '';
  const page = currentPage();
  if (!file || !page || overlayUploadInProgress) return;
  if (!confirmDiscardSelection()) return;
  selection = null;
  selectedOverlayId = null;
  hoveredOverlayId = null;
  hoveredInteractionId = null;
  renderSelection();
  renderInteractions();
  refreshCanvasAnnotations({rerenderImage: true});

  const formData = new FormData();
  formData.append('file', file);
  formData.append('storage_backend', storageSelect.value || page.storage_backend || 'local');
  setOverlayUploadInProgress(true, file);
  try {
    const saved = await api(`/api/pages/${page.id}/overlays`, {method: 'POST', body: formData});
    state.overlays.push(saved);
    selectedOverlayId = saved.id;
    renderOverlayElements();
    rerenderEditorOverlays();
  } catch (error) {
    alert(error.message);
  } finally {
    setOverlayUploadInProgress(false);
  }
}

overlayImageInput.addEventListener('change', () => void uploadOverlay(overlayImageInput));
overlayVideoInput.addEventListener('change', () => void uploadOverlay(overlayVideoInput));

function normalizeOverlayLink(value) {
  const raw = String(value || '').trim();
  if (!raw) throw new Error('请输入媒体链接');
  if (raw.length > 2048) throw new Error('媒体链接不能超过 2048 个字符');
  let url;
  try {
    url = new URL(raw);
  } catch {
    throw new Error('请输入完整、有效的媒体链接');
  }
  if (!['http:', 'https:'].includes(url.protocol)) {
    throw new Error('媒体链接必须使用 HTTP 或 HTTPS');
  }
  if (url.username || url.password) throw new Error('媒体链接不能包含账号或密码');
  if (location.protocol === 'https:' && url.protocol !== 'https:') {
    throw new Error('HTTPS 页面只能使用 HTTPS 媒体链接');
  }
  return url.href;
}

function inspectOverlayLink(url, type) {
  const media = document.createElement(type === 'video' ? 'video' : 'img');
  media.referrerPolicy = 'no-referrer';
  if (type === 'video') {
    media.preload = 'metadata';
    media.muted = true;
    media.playsInline = true;
  }
  return new Promise((resolve, reject) => {
    let settled = false;
    const cleanup = () => {
      window.clearTimeout(timeout);
      media.onload = null;
      media.onerror = null;
      media.onloadedmetadata = null;
    };
    const finish = (error = null) => {
      if (settled) return;
      settled = true;
      const width = type === 'video' ? media.videoWidth : media.naturalWidth;
      const height = type === 'video' ? media.videoHeight : media.naturalHeight;
      cleanup();
      if (type === 'video') {
        media.pause();
        media.removeAttribute('src');
        media.load();
      } else {
        media.removeAttribute('src');
      }
      if (error) {
        reject(error);
        return;
      }
      if (!width || !height) {
        reject(new Error('媒体链接没有有效的宽高信息'));
        return;
      }
      resolve({width, height, aspectRatio: width / height});
    };
    const ready = () => finish();
    const failed = () => finish(new Error(`链接无法作为${type === 'video' ? '视频' : '图片'}加载`));
    const timeout = window.setTimeout(
      () => finish(new Error('媒体链接验证超时，请检查链接是否可公开访问')),
      15_000,
    );
    media.onerror = failed;
    if (type === 'video') media.onloadedmetadata = ready;
    else media.onload = ready;
    media.src = url;
    if (type === 'video') media.load();
  });
}

function setOverlayLinkStatus(message = '', state = '') {
  overlayLinkStatus.textContent = message;
  overlayLinkStatus.classList.toggle('is-error', state === 'error');
  overlayLinkStatus.classList.toggle('is-success', state === 'success');
}

function setOverlayLinkInProgress(inProgress) {
  setOverlayControlsBusy(inProgress);
  overlayUploadStatus.hidden = true;
  overlayLinkForm.setAttribute('aria-busy', String(inProgress));
  overlayLinkType.disabled = inProgress;
  overlayLinkUrl.disabled = inProgress;
  overlayLinkCancel.disabled = inProgress;
  overlayLinkSubmit.disabled = inProgress;
  overlayLinkSubmit.textContent = inProgress ? '正在验证…' : '验证并添加';
}

function selectCreatedOverlay(saved) {
  selection = null;
  hoveredInteractionId = null;
  selectedOverlayId = saved.id;
  hoveredOverlayId = null;
  state.overlays.push(saved);
  renderSelection();
  renderInteractions();
  refreshCanvasAnnotations({rerenderImage: true});
  renderOverlayElements();
  rerenderEditorOverlays();
}

overlayLinkButton.addEventListener('click', () => {
  if (!currentPage() || overlayUploadInProgress || !confirmDiscardSelection()) return;
  overlayLinkForm.reset();
  setOverlayLinkStatus();
  overlayLinkDialog.showModal();
  requestAnimationFrame(() => overlayLinkUrl.focus());
});

overlayLinkCancel.addEventListener('click', () => {
  if (!overlayUploadInProgress) overlayLinkDialog.close();
});

overlayLinkDialog.addEventListener('cancel', (event) => {
  if (overlayUploadInProgress) event.preventDefault();
});

overlayLinkForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const page = currentPage();
  if (!page || overlayUploadInProgress) return;
  let url;
  try {
    url = normalizeOverlayLink(overlayLinkUrl.value);
  } catch (error) {
    setOverlayLinkStatus(error.message, 'error');
    return;
  }

  const type = overlayLinkType.value;
  setOverlayLinkInProgress(true);
  setOverlayLinkStatus('正在加载链接并校验媒体类型与尺寸…');
  try {
    const media = await inspectOverlayLink(url, type);
    setOverlayLinkStatus(`验证通过：${media.width} × ${media.height}，正在保存配置…`, 'success');
    const saved = await api(`/api/pages/${page.id}/overlays/from-url`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        url,
        type,
        aspect_ratio: Number(media.aspectRatio.toFixed(8)),
      }),
    });
    selectCreatedOverlay(saved);
    overlayLinkDialog.close();
  } catch (error) {
    setOverlayLinkStatus(error.message, 'error');
  } finally {
    setOverlayLinkInProgress(false);
  }
});

function renderRenderSettings() {
  const page = currentPage();
  if (!page || page.type !== 'html') {
    renderSettingsSection.hidden = true;
    renderSettingsPanel.innerHTML = '';
    return;
  }
  renderSettingsSection.hidden = false;
  const mode = page.render_mode || 'auto';
  const width = page.viewport_width || 1920;
  const height = page.viewport_height || 1080;
  const label = {auto: '自动识别', fixed: '固定画布', responsive: '响应式'}[mode] || '自动识别';
  renderSettingsPanel.innerHTML = `
    <details>
      <summary>${label}<span>${width} × ${height}</span></summary>
      <div class="render-settings-form">
        <label>显示模式</label>
        <select id="pageRenderMode">
          <option value="auto" ${mode === 'auto' ? 'selected' : ''}>自动识别固定画布</option>
          <option value="fixed" ${mode === 'fixed' ? 'selected' : ''}>固定设计尺寸</option>
          <option value="responsive" ${mode === 'responsive' ? 'selected' : ''}>响应式页面</option>
        </select>
        <div class="render-size-grid">
          <div><label>设计宽度</label><input id="pageViewportWidth" type="number" min="240" max="10000" value="${width}"></div>
          <div><label>设计高度</label><input id="pageViewportHeight" type="number" min="240" max="10000" value="${height}"></div>
        </div>
        <p class="render-settings-note">默认配置已适用于常见 1920×1080 大屏，通常无需修改。</p>
        <button id="saveRenderSettings" class="primary-btn render-settings-save" type="button">保存显示配置</button>
      </div>
    </details>`;
  document.getElementById('saveRenderSettings').addEventListener('click', saveRenderSettings);
}

async function saveRenderSettings() {
  const page = currentPage();
  if (!page || page.type !== 'html') return;
  if (!confirmDiscardSelection()) return;
  const renderMode = document.getElementById('pageRenderMode').value;
  const viewportWidth = Number(document.getElementById('pageViewportWidth').value);
  const viewportHeight = Number(document.getElementById('pageViewportHeight').value);
  if (!Number.isInteger(viewportWidth) || viewportWidth < 240 || viewportWidth > 10000) {
    alert('设计宽度需在 240–10000 之间');
    return;
  }
  if (!Number.isInteger(viewportHeight) || viewportHeight < 240 || viewportHeight > 10000) {
    alert('设计高度需在 240–10000 之间');
    return;
  }
  try {
    await api(`/api/pages/${page.id}`, {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({render_mode: renderMode, viewport_width: viewportWidth, viewport_height: viewportHeight}),
    });
    await reload(true);
  } catch (error) {
    alert(error.message);
  }
}

function initImageStage() {
  const stage = document.getElementById('imageStage');
  if (!stage) return;
  renderImageHotspots();
  stage.addEventListener('pointerdown', startDraw);
}

function renderImageHotspots() {
  const stage = document.getElementById('imageStage');
  if (!stage) return;
  stage.querySelectorAll('.hotspot').forEach((hotspot) => hotspot.remove());

  currentInteractions().filter((interaction) => interaction.kind === 'region').forEach((interaction) => {
    const view = interactionView(interaction);
    const displayName = view.name || '未命名交互';
    const region = interaction.payload;
    const hotspot = document.createElement('button');
    hotspot.type = 'button';
    hotspot.className = 'hotspot';
    hotspot.dataset.id = interaction.id;
    hotspot.setAttribute('aria-label', `选择交互：${displayName}`);
    hotspot.setAttribute('aria-pressed', String(selection?.interactionId === interaction.id));
    Object.assign(hotspot.style, {
      left: `${region.x * 100}%`,
      top: `${region.y * 100}%`,
      width: `${region.width * 100}%`,
      height: `${region.height * 100}%`,
    });
    const label = document.createElement('span');
    label.textContent = displayName;
    hotspot.appendChild(label);
    hotspot.addEventListener('click', () => selectInteraction(interaction.id, {source: 'canvas'}));
    hotspot.addEventListener('pointerenter', () => setHoveredInteraction(interaction.id));
    hotspot.addEventListener('pointerleave', () => setHoveredInteraction(null));
    hotspot.addEventListener('focus', () => setHoveredInteraction(interaction.id));
    hotspot.addEventListener('blur', () => setHoveredInteraction(null));
    stage.appendChild(hotspot);
  });

  if (selection?.isNew && selection.kind === 'region') {
    const region = selection.payload;
    const draft = document.createElement('div');
    draft.className = 'hotspot draft is-active';
    Object.assign(draft.style, {
      left: `${region.x * 100}%`,
      top: `${region.y * 100}%`,
      width: `${region.width * 100}%`,
      height: `${region.height * 100}%`,
    });
    const label = document.createElement('span');
    label.textContent = selection.name || '未命名交互';
    draft.appendChild(label);
    stage.appendChild(draft);
  }
  applyImageHotspotState();
}

function applyImageHotspotState() {
  const stage = document.getElementById('imageStage');
  if (!stage) return;
  stage.querySelectorAll('.hotspot[data-id]').forEach((hotspot) => {
    const interactionId = hotspot.dataset.id;
    const isActive = selection?.interactionId === interactionId;
    hotspot.classList.toggle('is-active', isActive);
    hotspot.classList.toggle('is-hovered', hoveredInteractionId === interactionId);
    hotspot.setAttribute('aria-pressed', String(isActive));
  });
}

function startDraw(event) {
  if (event.button !== 0 || event.target.closest('.hotspot')) return;
  if (!confirmDiscardSelection()) return;
  selectedOverlayId = null;
  hoveredOverlayId = null;
  renderOverlayElements();
  applyOverlaySelectionState();
  const stage = event.currentTarget;
  const rect = stage.getBoundingClientRect();
  const startX = Math.max(0, Math.min(rect.width, event.clientX - rect.left));
  const startY = Math.max(0, Math.min(rect.height, event.clientY - rect.top));
  const draft = document.createElement('div');
  draft.className = 'hotspot draft';
  stage.appendChild(draft);
  drawing = {stage, rect, startX, startY, draft, pointerId: event.pointerId};
  stage.setPointerCapture(event.pointerId);
  stage.addEventListener('pointermove', drawMove);
  stage.addEventListener('pointerup', endDraw);
  stage.addEventListener('pointercancel', cancelDraw);
}

function drawMove(event) {
  if (!drawing) return;
  const {rect, startX, startY, draft} = drawing;
  const endX = Math.max(0, Math.min(rect.width, event.clientX - rect.left));
  const endY = Math.max(0, Math.min(rect.height, event.clientY - rect.top));
  const left = Math.min(startX, endX);
  const top = Math.min(startY, endY);
  Object.assign(draft.style, {
    left: `${left}px`, top: `${top}px`,
    width: `${Math.abs(endX - startX)}px`, height: `${Math.abs(endY - startY)}px`,
  });
}

function cleanupDrawing() {
  if (!drawing) return null;
  const current = drawing;
  current.stage.removeEventListener('pointermove', drawMove);
  current.stage.removeEventListener('pointerup', endDraw);
  current.stage.removeEventListener('pointercancel', cancelDraw);
  if (current.stage.hasPointerCapture(current.pointerId)) current.stage.releasePointerCapture(current.pointerId);
  drawing = null;
  return current;
}

function cancelDraw() {
  const current = cleanupDrawing();
  if (current) current.draft.remove();
}

function endDraw(event) {
  if (!drawing) return;
  const {rect, startX, startY, draft} = drawing;
  const endX = Math.max(0, Math.min(rect.width, event.clientX - rect.left));
  const endY = Math.max(0, Math.min(rect.height, event.clientY - rect.top));
  const left = Math.min(startX, endX);
  const top = Math.min(startY, endY);
  const width = Math.abs(endX - startX);
  const height = Math.abs(endY - startY);
  cleanupDrawing();
  draft.remove();
  if (width < 10 || height < 10) return;

  selection = {
    isNew: true,
    interactionId: null,
    kind: 'region',
    payload: {x: left / rect.width, y: top / rect.height, width: width / rect.width, height: height / rect.height},
    label: `区域 ${Math.round(left)}×${Math.round(top)} / ${Math.round(width)}×${Math.round(height)}`,
    name: suggestInteractionName(),
    action: 'navigate',
    targetId: '',
    externalUrl: '',
    dirty: true,
  };
  hoveredInteractionId = null;
  renderSelection();
  renderInteractions();
  renderImageHotspots();
}

function postHtmlEditorState(revealInteractionId = null) {
  if (!frameController || currentPage()?.type !== 'html') return;
  const interactions = currentInteractions()
    .filter((interaction) => interaction.kind === 'element')
    .map((interaction) => {
      const view = interactionView(interaction);
      return {
        interactionId: interaction.id,
        elementId: interaction.payload.elementId,
        name: view.name || '未命名交互',
      };
    });
  const draft = selection?.isNew && selection.kind === 'element'
    ? {elementId: selection.payload.elementId, name: selection.name || '未命名交互'}
    : null;
  frameController.send({
    type: 'uipm-editor-state',
    pageId: currentPageId,
    interactions,
    selectedInteractionId: selection?.interactionId || null,
    hoveredInteractionId,
    draft,
    revealInteractionId,
  });
}

function refreshCanvasAnnotations({revealInteractionId = null, rerenderImage = false} = {}) {
  if (currentPage()?.type === 'html') {
    postHtmlEditorState(revealInteractionId);
    return;
  }
  if (rerenderImage) renderImageHotspots();
  else applyImageHotspotState();
}

function setHoveredInteraction(interactionId) {
  if (hoveredInteractionId === interactionId) return;
  hoveredInteractionId = interactionId;
  interactionList.querySelectorAll('.interaction-card').forEach((card) => {
    card.classList.toggle('is-hovered', card.dataset.id === interactionId);
  });
  refreshCanvasAnnotations();
}

function scrollInteractionCardIntoView(interactionId) {
  window.requestAnimationFrame(() => {
    const card = interactionList.querySelector(`.interaction-card[data-id="${interactionId}"]`);
    card?.scrollIntoView({block: 'nearest'});
  });
}

function scrollImageHotspotIntoView(interactionId) {
  window.requestAnimationFrame(() => {
    const hotspot = document.querySelector(`#imageStage .hotspot[data-id="${interactionId}"]`);
    hotspot?.scrollIntoView({behavior: 'smooth', block: 'center', inline: 'center'});
  });
}

function selectInteraction(interactionId, {label = '', source = 'list'} = {}) {
  const interaction = interactionById(interactionId);
  if (!interaction || interaction.source_page_id !== currentPageId) return;
  if (selection?.interactionId === interactionId) {
    if (source === 'list' && interaction.kind === 'element') postHtmlEditorState(interactionId);
    return;
  }
  if (!confirmDiscardSelection()) return;
  selectedOverlayId = null;
  hoveredOverlayId = null;
  selection = selectionForInteraction(interaction, label);
  hoveredInteractionId = null;
  renderSelection();
  renderInteractions();
  renderOverlayElements();
  applyOverlaySelectionState();
  refreshCanvasAnnotations({
    revealInteractionId: source === 'list' && interaction.kind === 'element' ? interactionId : null,
    rerenderImage: interaction.kind === 'region',
  });
  if (source === 'list' && interaction.kind === 'region') scrollImageHotspotIntoView(interactionId);
  if (source === 'canvas') scrollInteractionCardIntoView(interactionId);
}

function createElementSelection(data) {
  if (!confirmDiscardSelection()) return;
  selectedOverlayId = null;
  hoveredOverlayId = null;
  selection = {
    isNew: true,
    interactionId: null,
    kind: 'element',
    payload: {elementId: data.elementId},
    label: `<${data.tag}> ${data.text || data.elementId}`,
    name: suggestInteractionName(),
    action: 'navigate',
    targetId: '',
    externalUrl: '',
    dirty: true,
  };
  hoveredInteractionId = null;
  renderSelection();
  renderInteractions();
  renderOverlayElements();
  applyOverlaySelectionState();
  postHtmlEditorState();
}

function sameSet(left, right) {
  return left.size === right.size && [...left].every((value) => right.has(value));
}

window.addEventListener('message', (event) => {
  const data = event.data;
  if (!data || data.pageId !== currentPageId || !frameController?.ownsMessage(event)) return;

  if (data.type === 'uipm-editor-ready') {
    postHtmlEditorState();
    return;
  }
  if (data.type === 'uipm-element-click') {
    const existing = currentInteractions().find(
      (interaction) => interaction.kind === 'element' && interaction.payload.elementId === data.elementId,
    );
    if (existing) selectInteraction(existing.id, {label: `<${data.tag}> ${data.text || data.elementId}`, source: 'canvas'});
    else createElementSelection(data);
    return;
  }
  if (data.type === 'uipm-element-hover') {
    const existing = currentInteractions().find(
      (interaction) => interaction.kind === 'element' && interaction.payload.elementId === data.elementId,
    );
    setHoveredInteraction(existing?.id || null);
    return;
  }
  if (data.type === 'uipm-overlay-status') {
    const nextMissing = new Set(Array.isArray(data.missingInteractionIds) ? data.missingInteractionIds : []);
    const nextMeta = new Map(
      (Array.isArray(data.elementMeta) ? data.elementMeta : []).map((item) => [item.interactionId, item]),
    );
    const statusChanged = !sameSet(missingElementIds, nextMissing);
    const metaChanged = JSON.stringify([...htmlElementMeta]) !== JSON.stringify([...nextMeta]);
    if (!statusChanged && !metaChanged) return;
    missingElementIds = nextMissing;
    htmlElementMeta = nextMeta;
    if (selection?.interactionId && selection.kind === 'element' && !selection.dirty) {
      const selected = interactionById(selection.interactionId);
      if (selected) selection.label = elementDescription(selected);
    }
    renderInteractions();
    if (!selection?.dirty) renderSelection();
  }
});

function suggestInteractionName() {
  const base = `${currentPage()?.name || '页面'}-交互`;
  const used = new Set(state.interactions.map((interaction) => norm(interaction.name)));
  let index = 1;
  let value = `${base}${index}`;
  while (used.has(norm(value))) value = `${base}${++index}`;
  return value;
}

function renderSelection() {
  if (!selection) {
    selectionPanel.className = 'selection-panel muted-panel';
    selectionPanel.textContent = '选择 HTML 元素或图片区域后，可在这里编辑名称、动作和跳转目标；画布与下方列表会同步高亮。';
    return;
  }

  selectionPanel.className = 'selection-panel';
  const options = state.pages
    .filter((page) => page.id !== currentPageId)
    .map((page) => `<option value="${page.id}" ${page.id === selection.targetId ? 'selected' : ''}>${esc(page.name)}</option>`)
    .join('');
  const isExisting = Boolean(selection.interactionId);
  const missing = isExisting && selection.kind === 'element' && missingElementIds.has(selection.interactionId);
  selectionPanel.innerHTML = `
    <div class="selection-kicker">${isExisting ? '编辑' : '新建'} ${selection.kind === 'element' ? 'HTML 元素' : '图片区域'}</div>
    <div class="selection-title">${esc(selection.label)}</div>
    ${missing ? '<div class="selection-warning">当前 HTML 中未找到该元素。可删除此交互，或重新选择其他元素创建绑定。</div>' : ''}
    <label for="interactionNameInput">交互名称（项目内唯一）</label>
    <input id="interactionNameInput" maxlength="120" autocomplete="off" value="${esc(selection.name)}">
    <label for="actionSelect">动作</label>
    <select id="actionSelect">
      <option value="navigate" ${selection.action === 'navigate' ? 'selected' : ''}>跳转到指定页面</option>
      <option value="external" ${selection.action === 'external' ? 'selected' : ''}>跳转到外部网页</option>
      <option value="back" ${selection.action === 'back' ? 'selected' : ''}>返回上一页</option>
    </select>
    <div id="targetField">
      <label for="targetSelect">目标页面</label>
      <select id="targetSelect"><option value="">请选择目标页面</option>${options}</select>
    </div>
    <div id="externalField" hidden>
      <label for="externalUrlInput">外部网页链接</label>
      <input id="externalUrlInput" type="url" maxlength="2048" autocomplete="off" placeholder="https://example.com" value="${esc(selection.externalUrl)}">
      <p class="dialog-note">预览时将在当前页面全屏打开；若目标网站禁止 iframe，页面可能为空白，仍可使用右上角返回控件。</p>
    </div>
    <div id="backHint" class="dialog-note" hidden>预览时调用真实访问历史，效果与顶部“← 返回”完全一致。</div>
    <div class="selection-actions">
      <button id="cancelSelection" class="ghost-btn" type="button">取消</button>
      <button id="saveInteraction" class="primary-btn" type="button">${isExisting ? '保存更改' : '创建交互'}</button>
    </div>`;

  const nameInput = document.getElementById('interactionNameInput');
  const actionSelect = document.getElementById('actionSelect');
  const targetSelect = document.getElementById('targetSelect');
  const targetField = document.getElementById('targetField');
  const externalField = document.getElementById('externalField');
  const externalUrlInput = document.getElementById('externalUrlInput');
  const backHint = document.getElementById('backHint');

  const syncActionVisibility = () => {
    const isBack = actionSelect.value === 'back';
    const isExternal = actionSelect.value === 'external';
    targetField.hidden = isBack || isExternal;
    externalField.hidden = !isExternal;
    backHint.hidden = !isBack;
  };

  nameInput.addEventListener('input', () => {
    selection.name = nameInput.value;
    selection.dirty = true;
    renderInteractions();
    refreshCanvasAnnotations({rerenderImage: selection.kind === 'region'});
  });
  actionSelect.addEventListener('change', () => {
    selection.action = actionSelect.value;
    selection.dirty = true;
    syncActionVisibility();
    renderInteractions();
  });
  targetSelect.addEventListener('change', () => {
    selection.targetId = targetSelect.value;
    selection.dirty = true;
    renderInteractions();
  });
  externalUrlInput.addEventListener('input', () => {
    selection.externalUrl = externalUrlInput.value;
    selection.dirty = true;
    renderInteractions();
  });
  syncActionVisibility();
  document.getElementById('cancelSelection').addEventListener('click', clearSelection);
  document.getElementById('saveInteraction').addEventListener('click', saveInteraction);
}

function clearSelection() {
  selection = null;
  hoveredInteractionId = null;
  renderSelection();
  renderInteractions();
  refreshCanvasAnnotations({rerenderImage: true});
}

async function saveInteraction() {
  const name = selection.name.trim().replace(/\s+/g, ' ');
  if (!name) {
    alert('请输入交互名称');
    return;
  }
  if (selection.action === 'navigate' && !selection.targetId) {
    alert('请选择目标页面');
    return;
  }
  if (selection.action === 'external') {
    try {
      const target = new URL(selection.externalUrl);
      if (!['http:', 'https:'].includes(target.protocol) || !target.hostname || target.username || target.password) {
        throw new Error('invalid URL');
      }
    } catch (_error) {
      alert('请输入有效的 HTTP(S) 外部网页链接');
      return;
    }
  }

  const button = document.getElementById('saveInteraction');
  button.disabled = true;
  const body = {
    name,
    action: selection.action,
    target_page_id: selection.action === 'navigate' ? selection.targetId : null,
    target_url: selection.action === 'external' ? selection.externalUrl.trim() : null,
  };
  const isNew = selection.isNew;
  let url = `/api/interactions/${selection.interactionId}`;
  let method = 'PATCH';
  if (isNew) {
    url = '/api/interactions';
    method = 'POST';
    Object.assign(body, {
      source_page_id: currentPageId,
      kind: selection.kind,
      payload: selection.payload,
    });
  }

  try {
    const saved = await api(url, {
      method,
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    const index = state.interactions.findIndex((interaction) => interaction.id === saved.id);
    if (index >= 0) state.interactions[index] = saved;
    else state.interactions.push(saved);
    selection = selectionForInteraction(saved, selection.label);
    renderSelection();
    renderInteractions();
    refreshCanvasAnnotations({rerenderImage: saved.kind === 'region'});
  } catch (error) {
    alert(error.message);
    button.disabled = false;
  }
}

function renderInteractions() {
  const items = currentInteractions();
  interactionCount.textContent = items.length;
  if (!items.length) {
    interactionList.innerHTML = '<div class="interaction-empty">当前页面还没有交互</div>';
    return;
  }

  interactionList.innerHTML = items.map((interaction) => {
    const view = interactionView(interaction);
    const active = selection?.interactionId === interaction.id;
    const hovered = hoveredInteractionId === interaction.id;
    const missing = interaction.kind === 'element' && missingElementIds.has(interaction.id);
    const kindLabel = interaction.kind === 'element' ? elementDescription(interaction) : '图片区域';
    return `
      <div class="interaction-card ${active ? 'is-active' : ''} ${hovered ? 'is-hovered' : ''} ${missing ? 'is-missing' : ''}" data-id="${interaction.id}">
        <button class="interaction-select" data-id="${interaction.id}" type="button" aria-pressed="${active}">
          <span class="interaction-label">${esc(view.name || '未命名交互')}</span>
          <span class="interaction-detail">${esc(kindLabel)} · ${esc(actionLabel(view))}</span>
          ${missing ? '<span class="interaction-warning">元素未找到</span>' : ''}
        </button>
        <div class="interaction-actions">
          <button class="mini-danger delete-interaction" data-id="${interaction.id}" type="button" title="删除" aria-label="删除 ${esc(view.name)}">✕</button>
        </div>
      </div>`;
  }).join('');

  interactionList.querySelectorAll('.interaction-card').forEach((card) => {
    card.addEventListener('pointerenter', () => setHoveredInteraction(card.dataset.id));
    card.addEventListener('pointerleave', () => setHoveredInteraction(null));
  });
  interactionList.querySelectorAll('.interaction-select').forEach((button) => {
    button.addEventListener('click', () => selectInteraction(button.dataset.id, {source: 'list'}));
    button.addEventListener('focus', () => setHoveredInteraction(button.dataset.id));
    button.addEventListener('blur', () => setHoveredInteraction(null));
  });
  interactionList.querySelectorAll('.delete-interaction').forEach((button) => {
    button.addEventListener('click', () => deleteInteraction(button.dataset.id));
  });
}

async function deleteInteraction(interactionId) {
  const interaction = interactionById(interactionId);
  if (!interaction || !window.confirm(`删除交互“${interaction.name}”？`)) return;
  try {
    await api(`/api/interactions/${interactionId}`, {method: 'DELETE'});
    state.interactions = state.interactions.filter((item) => item.id !== interactionId);
    missingElementIds.delete(interactionId);
    htmlElementMeta.delete(interactionId);
    if (selection?.interactionId === interactionId) selection = null;
    if (hoveredInteractionId === interactionId) hoveredInteractionId = null;
    renderSelection();
    renderInteractions();
    refreshCanvasAnnotations({rerenderImage: interaction.kind === 'region'});
  } catch (error) {
    alert(error.message);
  }
}

function prepareUpload(files) {
  pendingFiles = Array.from(files || []);
  document.getElementById('fileInput').value = '';
  if (!pendingFiles.length) return;
  if (!confirmDiscardSelection()) {
    pendingFiles = [];
    return;
  }
  const defaults = config.html_render_defaults || {};
  uploadRenderMode.value = defaults.render_mode || 'auto';
  uploadViewportWidth.value = defaults.viewport_width || 1920;
  uploadViewportHeight.value = defaults.viewport_height || 1080;
  uploadRows.innerHTML = pendingFiles.map((file, index) => `
    <div class="upload-name-row">
      <div><strong>${esc(file.name)}</strong><span>${/\.zip$/i.test(file.name) ? 'ZIP HTML 页面包 · ' : ''}${Math.ceil(file.size / 1024)} KB</span></div>
      <input class="upload-name-input" data-index="${index}" maxlength="120" value="${esc(file.name.replace(/\.[^.]+$/, ''))}">
    </div>`).join('');
  uploadDialog.showModal();
}

pageSearchInput.addEventListener('input', renderPageList);
pageSearchClear.addEventListener('click', () => {
  pageSearchInput.value = '';
  renderPageList();
  pageSearchInput.focus();
});

replaceImageInput.addEventListener('change', async () => {
  const page = state.pages.find((item) => item.id === replaceImagePageId);
  const file = replaceImageInput.files?.[0];
  replaceImageInput.value = '';
  replaceImagePageId = null;
  if (!page || page.type !== 'image' || !file) return;
  const data = new FormData();
  data.append('file', file);
  try {
    const updated = await api(`/api/pages/${page.id}/image`, {method: 'PUT', body: data});
    Object.assign(page, updated);
    contentUrlRefreshes.delete(page.id);
    if (page.id === currentPageId) renderCanvas();
    renderPageList();
  } catch (error) {
    alert(error.message);
  }
});

document.getElementById('fileInput').addEventListener('change', (event) => prepareUpload(event.target.files));
uploadCancel.addEventListener('click', () => {
  if (uploadInProgress) return;
  pendingFiles = [];
  uploadDialog.close();
});
uploadDialog.addEventListener('cancel', (event) => {
  if (uploadInProgress) {
    event.preventDefault();
    return;
  }
  pendingFiles = [];
});

uploadForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (uploadInProgress) return;
  const inputs = Array.from(uploadRows.querySelectorAll('.upload-name-input'));
  const names = inputs.map((input) => input.value.trim().replace(/\s+/g, ' '));
  if (names.some((name) => !name)) return alert('页面名称不能为空');
  const folded = names.map(norm);
  if (new Set(folded).size !== folded.length) return alert('本次上传的页面名称存在重复');
  const existing = new Set(state.pages.map((page) => norm(page.name)));
  const collision = names.find((name) => existing.has(norm(name)));
  if (collision) return alert(`页面名称“${collision}”在当前项目中已存在`);

  const viewportWidth = Number(uploadViewportWidth.value);
  const viewportHeight = Number(uploadViewportHeight.value);
  if (!Number.isInteger(viewportWidth) || viewportWidth < 240 || viewportWidth > 10000) {
    return alert('设计宽度需在 240–10000 之间');
  }
  if (!Number.isInteger(viewportHeight) || viewportHeight < 240 || viewportHeight > 10000) {
    return alert('设计高度需在 240–10000 之间');
  }

  const formData = new FormData();
  formData.append('storage_backend', storageSelect.value || 'local');
  formData.append('names_json', JSON.stringify(names));
  formData.append('render_mode', uploadRenderMode.value || 'auto');
  formData.append('viewport_width', String(viewportWidth));
  formData.append('viewport_height', String(viewportHeight));
  pendingFiles.forEach((file) => formData.append('files', file));
  const fileCount = pendingFiles.length;
  setUploadInProgress(true, fileCount);
  try {
    await api(`/api/projects/${projectId}/pages`, {method: 'POST', body: formData});
    uploadDialogTitle.textContent = '上传完成';
    uploadStatusTitle.textContent = '上传完成，正在刷新页面…';
    await reload(false);
    uploadDialog.close();
    pendingFiles = [];
    setUploadInProgress(false);
  } catch (error) {
    setUploadInProgress(false);
    alert(error.message);
  }
});

function openRename(kind, id) {
  const item = kind === 'page'
    ? state.pages.find((page) => page.id === id)
    : state.interactions.find((interaction) => interaction.id === id);
  if (!item) return;
  renameTarget = {kind, id};
  renameTitle.textContent = kind === 'page' ? '重命名页面' : '重命名交互';
  renameInput.value = item.name;
  renameDialog.showModal();
  requestAnimationFrame(() => {
    renameInput.focus();
    renameInput.select();
  });
}

document.getElementById('renameCancel').addEventListener('click', () => {
  renameTarget = null;
  renameDialog.close();
});

renameForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (!renameTarget) return;
  const name = renameInput.value.trim().replace(/\s+/g, ' ');
  if (!name) return alert('请输入名称');
  const url = renameTarget.kind === 'page'
    ? `/api/pages/${renameTarget.id}`
    : `/api/interactions/${renameTarget.id}`;
  try {
    await api(url, {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name}),
    });
    renameDialog.close();
    renameTarget = null;
    await reload(true);
  } catch (error) {
    alert(error.message);
  }
});

function askDeletePage(pageId) {
  const page = state.pages.find((item) => item.id === pageId);
  if (!page) return;
  document.getElementById('confirmText').textContent =
    `删除“${page.name}”？所有指向该页面、以及从该页面发出的跳转都会自动清理，底层资源也会从 ${storageLabel(page)} 删除。`;
  confirmDialog.showModal();
  const ok = document.getElementById('confirmOk');
  const cancel = document.getElementById('confirmCancel');
  const cleanup = () => {
    ok.onclick = null;
    cancel.onclick = null;
  };
  cancel.onclick = () => {
    cleanup();
    confirmDialog.close();
  };
  ok.onclick = async () => {
    cleanup();
    confirmDialog.close();
    await api(`/api/pages/${pageId}`, {method: 'DELETE'});
    await reload(false);
  };
}

document.addEventListener('keydown', (event) => {
  if (event.key !== 'Escape' || document.querySelector('dialog[open]')) return;
  if (overlayGesture) {
    cancelOverlayGesture();
    return;
  }
  if (selection) clearSelection();
  else clearOverlaySelection();
});

(async () => {
  await loadConfig();
  await reload(false);
})();
