(() => {
  'use strict';

  window.UIPMContext = {
    getPageId() {
      const url = new URL(window.location.href);
      return (
        url.searchParams.get('page_id') ||
        window.currentPageId ||
        document.getElementById('app')?.dataset.pageId ||
        localStorage.getItem('uipm:last_page') ||
        null
      );
    },

    rememberPage(pageId) {
      if (pageId) {
        localStorage.setItem('uipm:last_page', pageId);
      }
    },

    keepPage(url = window.location.href) {
      const pageId = this.getPageId();
      const target = new URL(url, window.location.origin);
      if (pageId) {
        target.searchParams.set('page_id', pageId);
        this.rememberPage(pageId);
      }
      return target.toString();
    },

    reload() {
      window.location.href = this.keepPage();
    },

    navigate(url) {
      window.location.href = this.keepPage(url);
    },
  };
})();
