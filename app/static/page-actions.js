(() => {
  'use strict';

  const root = document.getElementById('app');
  const pageList = document.getElementById('pageList');
  if (!root || !pageList) return;

  const projectId = root.dataset.projectId;
  const renameDialog = document.getElementById('renameDialog');
  const renameForm = document.getElementById('renameForm');
  const renameInput = document.getElementById('renameInput');
  const renameTitle = document.getElementById('renameTitle');
  const renameNote = renameForm?.querySelector('.dialog-note');
  const defaultRenameNote = renameNote?.textContent || '';
  let pageActionDialog = null;

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

  function openNameDialog({mode, title, value, note, submit}) {
    if (!renameDialog || !renameForm || !renameInput || !renameTitle) return false;
    pageActionDialog = {mode, submit};
    renameDialog.dataset.pageActionMode = mode;
    renameTitle.textContent = title;
    renameInput.value = value;
    if (renameNote) renameNote.textContent = note || defaultRenameNote;
    renameDialog.showModal();
    requestAnimationFrame(() => {
      renameInput.focus();
      renameInput.select();
    });
    return true;
  }

  if (renameForm && renameDialog && renameInput) {
    // editor.js already owns this dialog for normal page/interaction renaming.
    // Intercept submit only while one of the extra page actions opened it.
    renameForm.addEventListener('submit', async (event) => {
      if (!renameDialog.dataset.pageActionMode || !pageActionDialog) return;
      event.preventDefault();
      event.stopImmediatePropagation();

      const name = cleanName(renameInput.value);
      if (!name) {
        alert('请输入名称');
        renameInput.focus();
        return;
      }
      if (name.length > 120) {
        alert('名称不能超过 120 个字符');
        renameInput.focus();
        return;
      }

      const submitButton = renameForm.querySelector('button[type="submit"]');
      if (submitButton) submitButton.disabled = true;
      try {
        await pageActionDialog.submit(name);
        renameDialog.close();
      } catch (error) {
        alert(error.message);
      } finally {
        if (submitButton) submitButton.disabled = false;
      }
    }, true);

    renameDialog.addEventListener('close', () => {
      if (!renameDialog.dataset.pageActionMode) return;
      delete renameDialog.dataset.pageActionMode;
      pageActionDialog = null;
      if (renameTitle) renameTitle.textContent = '重命名';
      if (renameNote) renameNote.textContent = defaultRenameNote;
    });
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

    button.addEventListener('click', () => {
      const currentName = cleanName(title.textContent);
      openNameDialog({
        mode: 'project-rename',
        title: '修改项目名称',
        value: currentName,
        note: '修改后会同步更新当前编辑页的项目标题。',
        submit: async (name) => {
          if (name === currentName) return;
          const project = await requestJson(`/api/projects/${projectId}`, {
            method: 'PATCH',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({name}),
          });
          title.textContent = project.name;
          document.title = `${project.name} · UI Prototype Manager`;
        },
      });
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

    button.addEventListener('click', (event) => {
      event.stopPropagation();
      const originalName = cleanName(pageNameNode.textContent);
      openNameDialog({
        mode: 'page-copy',
        title: '复制页面',
        value: `${originalName} copy`,
        note: '将复制图片页面及其交互和页面元素配置，复制后与原页面相互独立。',
        submit: async (name) => {
          button.disabled = true;
          button.classList.add('is-busy');
          try {
            await requestJson(`/api/pages/${pageId}/duplicate`, {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({name}),
            });
            location.reload();
          } finally {
            button.disabled = false;
            button.classList.remove('is-busy');
          }
        },
      });
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
