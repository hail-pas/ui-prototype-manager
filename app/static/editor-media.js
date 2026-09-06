(() => {
  'use strict';

  const VIDEO_PATTERN = /\.(mp4|webm)$/i;
  const replaceVideoInput = document.getElementById('replaceVideoInput');
  let replaceVideoPageId = null;
  let regionGesture = null;
  const savingRegionIds = new Set();

  function isVideoFile(file) {
    return Boolean(file && VIDEO_PATTERN.test(file.name || ''));
  }

  const renderBaseCanvas = renderCanvas;
  renderCanvas = async function renderCanvasWithVideoSupport() {
    const page = currentPage();
    if (!page || page.type !== 'video') {
      await renderBaseCanvas();
      const emptyUpload = canvasArea.querySelector('.emptyUpload');
      if (emptyUpload) {
        emptyUpload.accept = '.html,.htm,.zip,.png,.jpg,.jpeg,.webp,.gif,.mp4,.webm';
        const copy = canvasArea.querySelector('.empty-state p');
        if (copy) copy.textContent = 'HTML：点击元素配置跳转；图片/视频：拖拽框选区域配置跳转。';
      }
      return;
    }

    const renderVersion = ++canvasRenderVersion;
    if (frameController) {
      frameController.destroy();
      frameController = null;
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
    currentPageMeta.textContent = `VIDEO · ${storageLabel(page)}`;
    modeHelp.textContent = '拖拽创建新区域；已有区域可直接拖动调整位置';
    canvasArea.innerHTML = `
      <div id="imageStage" class="image-stage media-video-stage">
        <video id="pageVideo" aria-label="${esc(page.name)}"></video>
        <div id="imageOverlayLayer" class="overlay-layer"></div>
        <div id="imageGuideLayer" class="editor-guide-layer"></div>
      </div>`;
    renderImagePageOverlays();

    const video = document.getElementById('pageVideo');
    let initialized = false;
    const initialize = () => {
      if (initialized || page.id !== currentPageId || renderVersion !== canvasRenderVersion) return;
      initialized = true;
      initImageStage();
      const playback = video.play();
      if (playback) playback.catch(() => {});
    };
    const loadSource = () => {
      video.src = pageContentUrl(page);
      video.load();
    };
    video.autoplay = true;
    video.loop = true;
    video.muted = true;
    video.defaultMuted = true;
    video.controls = false;
    video.playsInline = true;
    video.preload = 'auto';
    video.addEventListener('loadedmetadata', initialize, {once: true});
    video.addEventListener('error', async () => {
      if (video.dataset.contentUrlRetried === 'true') return;
      video.dataset.contentUrlRetried = 'true';
      try {
        await refreshPageContentUrl(page);
        if (page.id === currentPageId) loadSource();
      } catch (error) {
        alert(error.message);
      }
    });
    loadSource();
    if (video.readyState >= 1) initialize();
  };

  function enhanceVideoRows() {
    state.pages.filter((page) => page.type === 'video').forEach((page) => {
      const row = pageList.querySelector(`.page-item[data-id="${page.id}"]`);
      if (!row) return;
      const icon = row.querySelector('.page-type-icon');
      if (icon) icon.textContent = 'VID';
      const storage = row.querySelector('.page-storage');
      if (storage?.dataset.typeMerged === '1') {
        storage.textContent = storage.textContent.replace(/\s·\sIMG$/, ' · VID');
      }

      if (row.querySelector('.replace-page-video')) return;
      const rename = row.querySelector('.rename-page');
      if (!rename || !replaceVideoInput) return;
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'mini-action replace-page-video';
      button.title = '替换主体视频';
      button.setAttribute('aria-label', `替换 ${page.name} 的主体视频`);
      button.textContent = '↻';
      button.addEventListener('click', (event) => {
        event.stopPropagation();
        if (!confirmDiscardSelection()) return;
        replaceVideoPageId = page.id;
        replaceVideoInput.value = '';
        replaceVideoInput.click();
      });
      rename.before(button);
    });
  }

  enhanceVideoRows();
  new MutationObserver(enhanceVideoRows).observe(pageList, {childList: true});

  replaceVideoInput?.addEventListener('change', async () => {
    const page = state.pages.find((item) => item.id === replaceVideoPageId);
    const file = replaceVideoInput.files?.[0];
    replaceVideoInput.value = '';
    replaceVideoPageId = null;
    if (!page || page.type !== 'video' || !file) return;

    const data = new FormData();
    data.append('file', file);
    try {
      const updated = await api(`/api/pages/${page.id}/video`, {method: 'PUT', body: data});
      Object.assign(page, updated);
      contentUrlRefreshes.delete(page.id);
      if (page.id === currentPageId) renderCanvas();
      renderPageList();
    } catch (error) {
      alert(error.message);
    }
  });

  uploadForm.addEventListener('submit', async (event) => {
    if (!pendingFiles.some(isVideoFile)) return;
    event.preventDefault();
    event.stopImmediatePropagation();
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

    const groups = [];
    pendingFiles.forEach((file, index) => {
      const kind = isVideoFile(file) ? 'video' : 'regular';
      let group = groups[groups.length - 1];
      if (!group || group.kind !== kind) {
        group = {kind, items: []};
        groups.push(group);
      }
      group.items.push({file, name: names[index]});
    });

    const storageBackend = storageSelect.value || 'local';
    const makeForm = (items, includeRenderSettings) => {
      const formData = new FormData();
      formData.append('storage_backend', storageBackend);
      formData.append('names_json', JSON.stringify(items.map((item) => item.name)));
      if (includeRenderSettings) {
        formData.append('render_mode', uploadRenderMode.value || 'auto');
        formData.append('viewport_width', String(viewportWidth));
        formData.append('viewport_height', String(viewportHeight));
      }
      items.forEach((item) => formData.append('files', item.file));
      return formData;
    };

    const fileCount = pendingFiles.length;
    let completedGroup = false;
    setUploadInProgress(true, fileCount);
    try {
      for (const group of groups) {
        const videoGroup = group.kind === 'video';
        await api(
          videoGroup
            ? `/api/projects/${projectId}/video-pages`
            : `/api/projects/${projectId}/pages`,
          {
            method: 'POST',
            body: makeForm(group.items, !videoGroup),
          },
        );
        completedGroup = true;
      }
      uploadDialogTitle.textContent = '上传完成';
      uploadStatusTitle.textContent = '上传完成，正在刷新页面…';
      await reload(false);
      uploadDialog.close();
      pendingFiles = [];
      setUploadInProgress(false);
    } catch (error) {
      setUploadInProgress(false);
      if (completedGroup) {
        try {
          await reload(false);
        } catch {}
        alert(`${error.message}\n部分页面已上传，请检查页面列表。`);
      } else {
        alert(error.message);
      }
    }
  }, true);

  const renderBaseHotspots = renderImageHotspots;
  renderImageHotspots = function renderHotspotsWithDrag() {
    renderBaseHotspots();
    const stage = document.getElementById('imageStage');
    if (!stage) return;
    stage.querySelectorAll('.hotspot[data-id]').forEach((hotspot) => {
      if (hotspot.dataset.dragBound === '1') return;
      hotspot.dataset.dragBound = '1';
      hotspot.addEventListener('pointerdown', startRegionDrag, true);
    });
  };

  function startRegionDrag(event) {
    if (event.button !== 0 || regionGesture) return;
    const hotspot = event.currentTarget;
    const interaction = interactionById(hotspot.dataset.id);
    if (!interaction || interaction.kind !== 'region' || savingRegionIds.has(interaction.id)) return;
    if (selection?.interactionId !== interaction.id && !confirmDiscardSelection()) return;
    if (selection?.interactionId !== interaction.id) {
      selection = null;
      renderSelection();
      renderInteractions();
    }

    const stage = document.getElementById('imageStage');
    const rect = stage?.getBoundingClientRect();
    if (!stage || !rect || rect.width <= 0 || rect.height <= 0) return;

    event.preventDefault();
    event.stopPropagation();
    const original = {...interaction.payload};
    regionGesture = {
      hotspot,
      interaction,
      pointerId: event.pointerId,
      rect,
      original,
      startClientX: event.clientX,
      startClientY: event.clientY,
      moved: false,
    };
    hotspot.classList.add('is-dragging');
    hotspot.setPointerCapture(event.pointerId);
    hotspot.addEventListener('pointermove', moveRegionDrag);
    hotspot.addEventListener('pointerup', finishRegionDrag);
    hotspot.addEventListener('pointercancel', cancelRegionDrag);
  }

  function moveRegionDrag(event) {
    const gesture = regionGesture;
    if (!gesture || event.pointerId !== gesture.pointerId) return;
    event.preventDefault();
    const deltaX = event.clientX - gesture.startClientX;
    const deltaY = event.clientY - gesture.startClientY;
    const {original, rect, interaction} = gesture;
    const x = Math.max(0, Math.min(1 - original.width, original.x + deltaX / rect.width));
    const y = Math.max(0, Math.min(1 - original.height, original.y + deltaY / rect.height));
    Object.assign(interaction.payload, {x, y});
    if (selection?.interactionId === interaction.id) {
      Object.assign(selection.payload, {x, y});
    }
    gesture.hotspot.style.left = `${x * 100}%`;
    gesture.hotspot.style.top = `${y * 100}%`;
    gesture.moved = gesture.moved || Math.abs(deltaX) >= 1 || Math.abs(deltaY) >= 1;
  }

  function cleanupRegionDrag() {
    const gesture = regionGesture;
    if (!gesture) return null;
    const {hotspot, pointerId} = gesture;
    hotspot.removeEventListener('pointermove', moveRegionDrag);
    hotspot.removeEventListener('pointerup', finishRegionDrag);
    hotspot.removeEventListener('pointercancel', cancelRegionDrag);
    if (hotspot.hasPointerCapture(pointerId)) hotspot.releasePointerCapture(pointerId);
    hotspot.classList.remove('is-dragging');
    regionGesture = null;
    return gesture;
  }

  function restoreRegion(gesture) {
    Object.assign(gesture.interaction.payload, gesture.original);
    if (selection?.interactionId === gesture.interaction.id) {
      selection.payload = {...gesture.original};
    }
    renderImageHotspots();
  }

  function cancelRegionDrag() {
    const gesture = cleanupRegionDrag();
    if (gesture) restoreRegion(gesture);
  }

  function finishRegionDrag() {
    const gesture = cleanupRegionDrag();
    if (!gesture) return;
    if (!gesture.moved) {
      selectInteraction(gesture.interaction.id, {source: 'canvas'});
      return;
    }
    void saveRegionPosition(gesture);
  }

  async function saveRegionPosition(gesture) {
    const interaction = gesture.interaction;
    savingRegionIds.add(interaction.id);
    const payload = Object.fromEntries(
      ['x', 'y', 'width', 'height'].map((field) => [
        field,
        Number(interaction.payload[field].toFixed(8)),
      ]),
    );
    try {
      const saved = await api(`/api/interactions/${interaction.id}/region`, {
        method: 'PATCH',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload),
      });
      const index = state.interactions.findIndex((item) => item.id === saved.id);
      if (index >= 0) state.interactions[index] = saved;
      if (selection?.interactionId === saved.id) {
        selection.payload = {...saved.payload};
        renderSelection();
        renderInteractions();
        renderImageHotspots();
      } else {
        selectInteraction(saved.id, {source: 'canvas'});
      }
    } catch (error) {
      restoreRegion(gesture);
      alert(error.message);
    } finally {
      savingRegionIds.delete(interaction.id);
    }
  }

  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape' || !regionGesture) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    cancelRegionDrag();
  }, true);

  renderImageHotspots();
})();
