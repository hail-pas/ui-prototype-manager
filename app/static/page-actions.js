(() => {
  'use strict';

  const root = document.getElementById('app');
  const pageList = document.getElementById('pageList');
  if (!root || !pageList) return;

  const projectId = root.dataset.projectId;

  function cleanName(value) {
    return String(value || '').trim().replace(/\s+/g, ' ');
  }

  async function requestJson(url, options = {}) {
    const response = await fetch(url, options);
    if (response.status === 401) {
      location.href = `/login?next=${encodeURIComponent(location.pathname + location.search)}`;
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

  function installProjectRename() {
    const wrap = document.querySelector('.project-title-wrap');
    const title = wrap?.querySelector('strong');
    if (!wrap || !title || wrap.querySelector('.project-rename-action')) return;

    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'project-rename-action';
    button.title = '修改项目名称';
    button.setAttribute('aria-label', '修改项目名称');
    button.textContent = '✎';
    wrap.appendChild(button);

    button.addEventListener('click', async () => {
      const currentName = cleanName(title.textContent);
      const input = window.prompt('修改项目名称', currentName);
      if (input === null) return;
      const name = cleanName(input);
      if (!name) return alert('请输入项目名称');
      if (name === currentName) return;
      if (name.length > 120) return alert('项目名称不能超过 120 个字符');

      button.disabled = true;
      try {
        const project = await requestJson(`/api/projects/${projectId}`, {
          method: 'PATCH',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({name}),
        });
        title.textContent = project.name;
        document.title = `${project.name} · UI Prototype Manager`;
      } catch (error) {
        alert(error.message);
      } finally {
        button.disabled = false;
      }
    });
  }

  function pageType(row) {
    return cleanName(row.querySelector('.page-type-icon')?.textContent).toUpperCase();
  }

  function mergePageType(row) {
    const storage = row.querySelector('.page-storage');
    if (!storage || storage.dataset.typeMerged === '1') return;
    const typeLabel = pageType(row);
    if (!typeLabel) return;
    storage.textContent = `${cleanName(storage.textContent)} · ${typeLabel}`;
    storage.dataset.typeMerged = '1';
  }

  function installCopyButton(row) {
    // HTML packages contain page-specific instrumentation, so copying is
    // intentionally limited to image pages.
    if (pageType(row) !== 'IMG' || row.querySelector('.copy-page')) return;

    const pageId = row.dataset.id;
    const rename = row.querySelector('.rename-page');
    const pageNameNode = row.querySelector('.page-name');
    if (!pageId || !rename || !pageNameNode) return;

    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'mini-action copy-page';
    button.title = '复制页面（深拷贝）';
    button.setAttribute('aria-label', `复制 ${cleanName(pageNameNode.textContent)}`);
    button.innerHTML = '<span aria-hidden="true">⧉</span>';

    button.addEventListener('click', async (event) => {
      event.stopPropagation();
      const originalName = cleanName(pageNameNode.textContent);
      const input = window.prompt('复制页面名称', `${originalName} copy`);
      if (input === null) return;
      const name = cleanName(input);
      if (!name) return alert('请输入页面名称');
      if (name.length > 120) return alert('页面名称不能超过 120 个字符');

      button.disabled = true;
      button.classList.add('is-busy');
      try {
        await requestJson(`/api/pages/${pageId}/duplicate`, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({name}),
        });
        location.reload();
      } catch (error) {
        alert(error.message);
      } finally {
        button.disabled = false;
        button.classList.remove('is-busy');
      }
    });

    rename.before(button);
  }

  function enhancePageRow(row) {
    const pageName = row.querySelector('.page-name');
    if (pageName) pageName.title = cleanName(pageName.textContent);
    installCopyButton(row);
    mergePageType(row);
  }

  function enhancePageList() {
    pageList.querySelectorAll('.page-item').forEach(enhancePageRow);
  }

  installProjectRename();
  enhancePageList();
  new MutationObserver(enhancePageList).observe(pageList, {childList: true});
})();
