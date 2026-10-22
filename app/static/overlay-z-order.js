(() => {
  'use strict';

  const CONTROL_CLASS = 'overlay-z-order-controls';
  let reordering = false;

  function clampOverlayZIndex(value) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return 0;
    return Math.max(0, Math.min(1000, Math.trunc(numeric)));
  }

  function applyEditorOverlayZIndex(element) {
    if (!element) return;
    const overlay = overlayById(element.dataset.id);
    if (!overlay) return;
    element.style.zIndex = String(clampOverlayZIndex(overlay.z_index));
  }

  function applyEditorOverlayZIndexes() {
    currentOverlayLayer()?.querySelectorAll('.editor-overlay').forEach(applyEditorOverlayZIndex);
  }

  const createEditorOverlayElementBase = createEditorOverlayElement;
  createEditorOverlayElement = function createEditorOverlayElementWithZIndex(overlay) {
    const element = createEditorOverlayElementBase(overlay);
    element.style.zIndex = String(clampOverlayZIndex(overlay.z_index));
    return element;
  };

  const applyOverlaySelectionStateBase = applyOverlaySelectionState;
  applyOverlaySelectionState = function applyOverlaySelectionStateWithZIndex() {
    applyOverlaySelectionStateBase();
    applyEditorOverlayZIndexes();
  };

  function moveItem(items, fromIndex, toIndex) {
    const reordered = [...items];
    const [item] = reordered.splice(fromIndex, 1);
    reordered.splice(toIndex, 0, item);
    return reordered;
  }

  function targetIndexFor(mode, index, length) {
    if (mode === 'bottom') return 0;
    if (mode === 'lower') return Math.max(0, index - 1);
    if (mode === 'raise') return Math.min(length - 1, index + 1);
    if (mode === 'top') return length - 1;
    return index;
  }

  function updateStateOverlay(saved) {
    const index = state.overlays.findIndex((item) => item.id === saved.id);
    if (index >= 0) state.overlays[index] = saved;
  }

  async function reorderOverlay(overlayId, mode) {
    if (reordering) return;
    const items = currentOverlays();
    const currentIndex = items.findIndex((item) => item.id === overlayId);
    if (currentIndex < 0 || items.length < 2) return;

    const targetIndex = targetIndexFor(mode, currentIndex, items.length);
    if (targetIndex === currentIndex) return;

    const reordered = moveItem(items, currentIndex, targetIndex);
    const updates = reordered
      .map((overlay, zIndex) => ({overlay, zIndex}))
      .filter(({overlay, zIndex}) => Number(overlay.z_index) !== zIndex);

    reordering = true;
    updates.forEach(({overlay}) => savingOverlayIds.add(overlay.id));
    renderOverlayZOrderControls();

    try {
      const savedItems = await Promise.all(updates.map(({overlay, zIndex}) => api(`/api/overlays/${overlay.id}`, {
        method: 'PATCH',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({z_index: zIndex}),
      })));
      savedItems.forEach(updateStateOverlay);
      renderOverlayElements();
      rerenderEditorOverlays();
      applyOverlaySelectionState();
    } catch (error) {
      await reload(true);
      void showMessage(`调整层级失败：${error.message}`);
    } finally {
      updates.forEach(({overlay}) => savingOverlayIds.delete(overlay.id));
      reordering = false;
      renderOverlayZOrderControls();
    }
  }

  function orderButton({mode, text, title, disabled}) {
    return `<button type="button" class="ghost-btn overlay-z-order-btn" data-overlay-z-action="${mode}" title="${title}" ${disabled || reordering ? 'disabled' : ''}>${text}</button>`;
  }

  function renderOverlayZOrderControls() {
    overlaySelectionPanel.querySelector(`.${CONTROL_CLASS}`)?.remove();

    const selected = overlayById(selectedOverlayId);
    if (!selected || selected.page_id !== currentPageId || overlaySelectionPanel.hidden) return;

    const items = currentOverlays();
    const index = items.findIndex((item) => item.id === selected.id);
    if (index < 0) return;

    const controls = document.createElement('div');
    controls.className = CONTROL_CLASS;
    controls.innerHTML = `
      <div class="overlay-z-order-heading">
        <span>层级</span>
        <span class="overlay-z-order-position">${index + 1} / ${items.length}</span>
      </div>
      <div class="overlay-z-order-actions" role="group" aria-label="调整媒体层级">
        ${orderButton({mode: 'bottom', text: '置底', title: '移到所有媒体下方', disabled: index === 0})}
        ${orderButton({mode: 'lower', text: '下移', title: '向下移动一层', disabled: index === 0})}
        ${orderButton({mode: 'raise', text: '上移', title: '向上移动一层', disabled: index === items.length - 1})}
        ${orderButton({mode: 'top', text: '置顶', title: '移到所有媒体上方', disabled: index === items.length - 1})}
      </div>
      <p class="overlay-z-order-note">仅调整媒体之间的前后关系；媒体整体始终位于页面内容之上、交互层之下。</p>`;

    controls.querySelectorAll('[data-overlay-z-action]').forEach((button) => {
      button.addEventListener('click', () => void reorderOverlay(selected.id, button.dataset.overlayZAction));
    });

    const deleteButton = overlaySelectionPanel.querySelector('#deleteSelectedOverlay');
    if (deleteButton) overlaySelectionPanel.insertBefore(controls, deleteButton);
    else overlaySelectionPanel.appendChild(controls);
  }

  const renderOverlayElementsBase = renderOverlayElements;
  renderOverlayElements = function renderOverlayElementsWithZOrder() {
    const result = renderOverlayElementsBase();
    renderOverlayZOrderControls();
    return result;
  };

  renderOverlayZOrderControls();
  applyEditorOverlayZIndexes();
})();
