(() => {
  'use strict';

  function waitForPresentedVideoFrame(video, signal) {
    if (typeof video.requestVideoFrameCallback !== 'function') {
      return afterStablePaint(signal);
    }
    return abortable(new Promise((resolve) => {
      video.requestVideoFrameCallback(() => resolve());
    }), signal);
  }

  const createBaseImagePageView = createImagePageView;
  createImagePageView = function createMediaPageView(item, signal) {
    if (item.type !== 'video') return createBaseImagePageView(item, signal);

    const element = createPageLayer(item);
    element.classList.remove('is-html-view');
    element.classList.add('is-image-view', 'is-video-view');
    const mediaStage = document.createElement('div');
    mediaStage.className = 'player-image-stage player-video-stage';
    const video = document.createElement('video');
    video.autoplay = true;
    video.loop = true;
    video.muted = true;
    video.defaultMuted = true;
    video.controls = false;
    video.playsInline = true;
    video.preload = 'auto';
    mediaStage.appendChild(video);

    const overlay = createPlayerOverlayLayer(item.id, signal);
    mediaStage.appendChild(overlay.layer);
    appendImageHotspots(mediaStage, item.id);
    mediaStage.addEventListener('click', (event) => handleImageBlankClick(event, item.id));
    element.appendChild(mediaStage);

    const loadBaseVideo = async () => {
      const load = () => loadVideoMedia(video, pageContentUrl(item), signal);
      try {
        await load();
      } catch (error) {
        if (signal.aborted) throw error;
        await abortable(refreshPageContentUrl(item), signal);
        await load();
      }
      await waitForPresentedVideoFrame(video, signal);
    };
    const view = {
      controller: null,
      destroyed: false,
      element,
      markContentReady: null,
      pageId: item.id,
      ready: Promise.all([loadBaseVideo(), overlay.ready]),
      returnToCacheOnDiscard: false,
    };
    pageViews.add(view);
    return view;
  };

  const renderBasePageList = renderPageList;
  renderPageList = function renderPageListWithVideo(pendingPageId = null) {
    renderBasePageList(pendingPageId);
    state.pages.filter((item) => item.type === 'video').forEach((item) => {
      pageList.querySelector(`.player-page-item[data-id="${item.id}"] .player-page-type`)
        ?.replaceChildren('VID');
    });
  };
})();
