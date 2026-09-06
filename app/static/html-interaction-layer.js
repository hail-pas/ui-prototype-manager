(() => {
  'use strict';

  let renderFrame = 0;
  let boundFrame = null;
  let boundWindow = null;
  let resizeObserver = null;

  function escapeSelectorValue(value) {
    if (window.CSS?.escape) return window.CSS.escape(String(value));
    return String(value).replace(/["\\]/g, '\\$&');
  }

  function htmlFrame() {
    return currentPage()?.type === 'html' ? frameController?.iframe || null : null;
  }

  function interactionLayer() {
    const viewport = frameController?.viewport;
    if (!viewport || currentPage()?.type !== 'html') return null;
    let layer = viewport.querySelector('#htmlInteractionLayer');
    if (layer) return layer;

    layer = document.createElement('div');
    layer.id = 'htmlInteractionLayer';
    layer.className = 'html-interaction-layer';
    const guides = viewport.querySelector('#htmlGuideLayer');
    viewport.insertBefore(layer, guides || null);
    return layer;
  }

  function clearInteractionLayer() {
    document.getElementById('htmlInteractionLayer')?.remove();
  }

  function disconnectFrameWindow() {
    if (boundWindow) {
      boundWindow.removeEventListener('scroll', scheduleRender, true);
      boundWindow.removeEventListener('resize', scheduleRender);
    }
    resizeObserver?.disconnect();
    resizeObserver = null;
    boundWindow = null;
  }

  function disconnectFrame() {
    disconnectFrameWindow();
    boundFrame?.removeEventListener('load', handleFrameLoad);
    boundFrame = null;
  }

  function bindFrameWindow() {
    disconnectFrameWindow();
    if (!boundFrame?.contentWindow) return;
    boundWindow = boundFrame.contentWindow;
    boundWindow.addEventListener('scroll', scheduleRender, true);
    boundWindow.addEventListener('resize', scheduleRender);
    try {
      const doc = boundFrame.contentDocument;
      if (doc && window.ResizeObserver) {
        resizeObserver = new ResizeObserver(scheduleRender);
        resizeObserver.observe(doc.documentElement);
        if (doc.body) resizeObserver.observe(doc.body);
      }
    } catch (_error) {
      // HTML pages are normally same-origin; if that changes, the iframe markers still remain available.
    }
  }

  function handleFrameLoad() {
    bindFrameWindow();
    scheduleRender();
  }

  function bindFrame() {
    const frame = htmlFrame();
    if (frame === boundFrame) return;
    disconnectFrame();
    if (!frame) return;
    boundFrame = frame;
    boundFrame.addEventListener('load', handleFrameLoad);
    bindFrameWindow();
  }

  function markerItems() {
    const items = currentInteractions()
      .filter((interaction) => interaction.kind === 'element')
      .map((interaction) => {
        const view = interactionView(interaction);
        return {
          interactionId: interaction.id,
          elementId: interaction.payload.elementId,
          name: view.name || '未命名交互',
          draft: false,
        };
      });

    if (selection?.isNew && selection.kind === 'element' && selection.payload?.elementId) {
      items.push({
        interactionId: '__draft__',
        elementId: selection.payload.elementId,
        name: selection.name || '未命名交互',
        draft: true,
      });
    }
    return items;
  }

  function renderHtmlInteractionLayer() {
    renderFrame = 0;
    bindFrame();
    const frame = htmlFrame();
    if (!frame) {
      clearInteractionLayer();
      return;
    }

    const layer = interactionLayer();
    if (!layer) return;

    let doc;
    let view;
    try {
      doc = frame.contentDocument;
      view = frame.contentWindow;
    } catch (_error) {
      layer.replaceChildren();
      return;
    }
    if (!doc || !view) return;

    const frameRect = frame.getBoundingClientRect();
    const layerRect = layer.getBoundingClientRect();
    const viewportWidth = view.innerWidth || frame.clientWidth;
    const viewportHeight = view.innerHeight || frame.clientHeight;
    if (!frameRect.width || !frameRect.height || !layerRect.width || !layerRect.height || !viewportWidth || !viewportHeight) {
      layer.replaceChildren();
      return;
    }

    const scaleX = frameRect.width / viewportWidth;
    const scaleY = frameRect.height / viewportHeight;
    const markers = [];

    markerItems().forEach((item) => {
      const target = doc.querySelector(`[data-ui-id="${escapeSelectorValue(item.elementId)}"]`);
      if (!target) return;
      const rect = target.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) return;

      const marker = document.createElement('div');
      const active = item.draft || selection?.interactionId === item.interactionId;
      const hovered = !item.draft && hoveredInteractionId === item.interactionId;
      marker.className = `html-interaction-marker ${active ? 'is-active' : ''} ${hovered ? 'is-hovered' : ''} ${item.draft ? 'is-draft' : ''}`;
      Object.assign(marker.style, {
        left: `${frameRect.left + rect.left * scaleX - layerRect.left}px`,
        top: `${frameRect.top + rect.top * scaleY - layerRect.top}px`,
        width: `${rect.width * scaleX}px`,
        height: `${rect.height * scaleY}px`,
      });

      const label = document.createElement('span');
      label.textContent = item.name;
      marker.appendChild(label);
      markers.push(marker);
    });

    layer.replaceChildren(...markers);
  }

  function scheduleRender() {
    window.cancelAnimationFrame(renderFrame);
    renderFrame = window.requestAnimationFrame(renderHtmlInteractionLayer);
  }

  const renderCanvasBase = renderCanvas;
  renderCanvas = async function renderCanvasWithHtmlInteractionLayer() {
    const result = await renderCanvasBase();
    scheduleRender();
    return result;
  };

  const postHtmlEditorStateBase = postHtmlEditorState;
  postHtmlEditorState = function postHtmlEditorStateWithOuterLayer(...args) {
    const result = postHtmlEditorStateBase(...args);
    scheduleRender();
    return result;
  };

  window.addEventListener('message', (event) => {
    const frame = htmlFrame();
    if (!frame || event.source !== frame.contentWindow) return;
    const type = event.data?.type;
    if (type === 'uipm-element-selected' || type === 'uipm-element-hover' || type === 'uipm-overlay-status' || type === 'uipm-size') {
      scheduleRender();
    }
  });
  window.addEventListener('resize', scheduleRender);

  scheduleRender();
})();
