const root = document.getElementById('app');
const projectId = root.dataset.projectId;
const pageList = document.getElementById('pageList');
const canvasArea = document.getElementById('canvasArea');
const selectionPanel = document.getElementById('selectionPanel');
const interactionList = document.getElementById('interactionList');
const pageCount = document.getElementById('pageCount');
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
let state = {pages:[], interactions:[]};
let config = {storage_backends:['local'], s3:{configured:false}};
let currentPageId = null;
let pendingSelection = null;
let drawing = null;
let renameTarget = null;
let pendingFiles = [];

function esc(s='') { const d=document.createElement('div'); d.textContent=s; return d.innerHTML; }
function currentPage(){ return state.pages.find(p => p.id === currentPageId); }
function pageName(id){ return state.pages.find(p => p.id === id)?.name || '已删除页面'; }
function actionLabel(i){ return i.action === 'back' ? '↩ 返回上一页' : `→ ${pageName(i.target_page_id)}`; }
function currentInteractions(){ return state.interactions.filter(i => i.source_page_id === currentPageId); }
function storageLabel(p){ return (p.storage_backend || 'local') === 's3' ? 'S3' : 'LOCAL'; }
function norm(s){ return String(s||'').trim().replace(/\s+/g,' ').toLocaleLowerCase(); }

function goLogin(){ location.href=`/login?next=${encodeURIComponent(location.pathname+location.search)}`; }
async function api(url, options={}) {
  const res = await fetch(url, options);
  if (res.status === 401) { goLogin(); throw new Error('登录已过期'); }
  if (!res.ok) {
    let msg = '操作失败'; try { const data=await res.json(); msg=data.detail || msg; } catch {}
    throw new Error(msg);
  }
  return res.status === 204 ? null : res.json();
}

async function loadConfig(){
  config = await api('/api/config');
  storageSelect.innerHTML = config.storage_backends.map(b => `<option value="${b}">${b === 's3' ? 'S3' : '本地'}</option>`).join('');
  storageSelect.value = config.default_storage_backend || 'local';
  storageSelect.title = config.s3?.configured ? `S3 已配置：${config.s3.bucket}` : `本地目录：${config.data_dir}`;
}

async function reload(keepCurrent=true) {
  state = await api(`/api/projects/${projectId}`);
  if (!keepCurrent || !state.pages.some(p => p.id === currentPageId)) currentPageId = state.pages[0]?.id || null;
  pendingSelection = null;
  renderAll();
}

function renderAll(){ renderPageList(); renderCanvas(); renderInteractions(); renderSelection(); }

function renderPageList(){
  pageCount.textContent = state.pages.length;
  if (!state.pages.length) { pageList.innerHTML = '<div class="interaction-empty">暂无页面</div>'; return; }
  pageList.innerHTML = state.pages.map(p => `
    <div class="page-item ${p.id===currentPageId?'active':''}" data-id="${p.id}">
      <div class="page-type-icon">${p.type==='html'?'HTML':'IMG'}</div>
      <div class="page-main"><div class="page-name" title="${esc(p.name)}">${esc(p.name)}</div><div class="page-storage">${storageLabel(p)}</div></div>
      <button class="mini-action rename-page" type="button" title="重命名">✎</button>
      <button class="mini-danger delete-page" type="button" title="删除页面">✕</button>
    </div>`).join('');
  pageList.querySelectorAll('.page-item').forEach(row => {
    row.addEventListener('click', e => {
      if (e.target.closest('button')) return;
      currentPageId = row.dataset.id; pendingSelection = null; renderAll();
    });
    row.querySelector('.rename-page').addEventListener('click', e => { e.stopPropagation(); openRename('page', row.dataset.id); });
    row.querySelector('.delete-page').addEventListener('click', e => { e.stopPropagation(); askDeletePage(row.dataset.id); });
  });
}

function renderCanvas(){
  const page = currentPage();
  if (!page) {
    currentPageName.textContent='请选择页面'; currentPageMeta.textContent=''; modeHelp.textContent='';
    canvasArea.innerHTML = `<div class="empty-state"><div class="empty-icon">↗</div><h2>先上传页面</h2><p>HTML：点击元素配置跳转；图片：拖拽框选区域配置跳转。</p><label class="primary-btn">上传页面<input class="emptyUpload" type="file" multiple accept=".html,.htm,.png,.jpg,.jpeg,.webp,.gif" hidden /></label></div>`;
    canvasArea.querySelector('.emptyUpload').addEventListener('change', e => prepareUpload(e.target.files));
    return;
  }
  currentPageName.textContent = page.name;
  currentPageMeta.textContent = `${page.type.toUpperCase()} · ${storageLabel(page)}`;
  if (page.type === 'html') {
    modeHelp.textContent = '点击页面内任意元素 → 命名交互 → 选择“跳转页面”或“返回上一页”';
    canvasArea.innerHTML = `<div class="html-frame-wrap"><iframe id="htmlFrame" class="html-frame" sandbox="allow-scripts" src="/api/pages/${page.id}/render?mode=edit"></iframe></div>`;
  } else {
    modeHelp.textContent = '按住鼠标拖拽框选 → 命名交互 → 选择“跳转页面”或“返回上一页”';
    canvasArea.innerHTML = `<div id="imageStage" class="image-stage"><img id="pageImage" src="/api/pages/${page.id}/file" alt="${esc(page.name)}" /></div>`;
    const img = document.getElementById('pageImage');
    if (img.complete) initImageStage(); else img.addEventListener('load', initImageStage, {once:true});
  }
}

function initImageStage(){
  const stage = document.getElementById('imageStage');
  if (!stage) return;
  currentInteractions().filter(i => i.kind==='region').forEach(i => {
    const p=i.payload; const el=document.createElement('div'); el.className='hotspot'; el.dataset.id=i.id;
    Object.assign(el.style,{left:`${p.x*100}%`,top:`${p.y*100}%`,width:`${p.width*100}%`,height:`${p.height*100}%`});
    el.innerHTML=`<span>${esc(i.name)}</span>`; stage.appendChild(el);
  });
  stage.addEventListener('pointerdown', startDraw);
}

function startDraw(e){
  if (e.button !== 0 || e.target.closest('.hotspot')) return;
  const stage = e.currentTarget; const r=stage.getBoundingClientRect();
  const sx=Math.max(0,Math.min(r.width,e.clientX-r.left)); const sy=Math.max(0,Math.min(r.height,e.clientY-r.top));
  const draft=document.createElement('div'); draft.className='hotspot draft'; stage.appendChild(draft);
  drawing={stage,r,sx,sy,draft}; stage.setPointerCapture(e.pointerId);
  stage.addEventListener('pointermove', drawMove); stage.addEventListener('pointerup', endDraw, {once:true}); stage.addEventListener('pointercancel', endDraw, {once:true});
}
function drawMove(e){
  if(!drawing) return; const {r,sx,sy,draft}=drawing; const ex=Math.max(0,Math.min(r.width,e.clientX-r.left)); const ey=Math.max(0,Math.min(r.height,e.clientY-r.top));
  const left=Math.min(sx,ex), top=Math.min(sy,ey), w=Math.abs(ex-sx), h=Math.abs(ey-sy);
  Object.assign(draft.style,{left:`${left}px`,top:`${top}px`,width:`${w}px`,height:`${h}px`});
}
function endDraw(e){
  if(!drawing) return; const {stage,r,sx,sy,draft}=drawing; stage.removeEventListener('pointermove', drawMove);
  const ex=Math.max(0,Math.min(r.width,e.clientX-r.left)); const ey=Math.max(0,Math.min(r.height,e.clientY-r.top));
  const left=Math.min(sx,ex), top=Math.min(sy,ey), w=Math.abs(ex-sx), h=Math.abs(ey-sy); draft.remove(); drawing=null;
  if(w<10 || h<10) return;
  pendingSelection={kind:'region', payload:{x:left/r.width,y:top/r.height,width:w/r.width,height:h/r.height}, label:`区域 ${Math.round(left)}×${Math.round(top)} / ${Math.round(w)}×${Math.round(h)}`, name:suggestInteractionName()};
  renderSelection();
}

window.addEventListener('message', e => {
  const d=e.data; if(!d || d.type!=='uipm-element-click' || d.pageId!==currentPageId) return;
  const existing=currentInteractions().find(i=>i.kind==='element' && i.payload.elementId===d.elementId);
  pendingSelection={kind:'element',payload:{elementId:d.elementId},label:`<${d.tag}> ${d.text || d.elementId}`,name:existing?.name || suggestInteractionName(),action:existing?.action || 'navigate',targetId:existing?.target_page_id || ''};
  renderSelection();
});

function suggestInteractionName(){
  const base=`${currentPage()?.name || '页面'}-交互`;
  const used=new Set(state.interactions.map(i=>norm(i.name)));
  let n=1, value=`${base}${n}`;
  while(used.has(norm(value))) value=`${base}${++n}`;
  return value;
}

function renderSelection(){
  if(!pendingSelection){ selectionPanel.className='selection-panel muted-panel'; selectionPanel.textContent='选择 HTML 元素或框选图片区域后，在这里命名，并设置“跳转页面”或“返回上一页”。'; return; }
  selectionPanel.className='selection-panel';
  const options=state.pages.filter(p=>p.id!==currentPageId).map(p=>`<option value="${p.id}" ${p.id===pendingSelection.targetId?'selected':''}>${esc(p.name)}</option>`).join('');
  const selectedAction=pendingSelection.action || 'navigate';
  selectionPanel.innerHTML=`
    <div class="selection-kicker">已选择 ${pendingSelection.kind==='element'?'HTML 元素':'图片区域'}</div>
    <div class="selection-title">${esc(pendingSelection.label)}</div>
    <label>交互名称（项目内唯一）</label><input id="interactionNameInput" maxlength="120" value="${esc(pendingSelection.name || '')}" />
    <label>动作</label><select id="actionSelect"><option value="navigate" ${selectedAction==='navigate'?'selected':''}>跳转到指定页面</option><option value="back" ${selectedAction==='back'?'selected':''}>返回上一页</option></select>
    <div id="targetField"><label>目标页面</label><select id="targetSelect"><option value="">请选择目标页面</option>${options}</select></div>
    <div id="backHint" class="dialog-note" style="display:none">预览时调用真实访问历史，效果与顶部“← 返回”完全一致。</div>
    <div class="selection-actions"><button id="cancelSelection" class="ghost-btn" type="button">取消</button><button id="saveInteraction" class="primary-btn" type="button">保存交互</button></div>`;
  const actionSelect=document.getElementById('actionSelect');
  const syncAction=()=>{const isBack=actionSelect.value==='back';document.getElementById('targetField').style.display=isBack?'none':'';document.getElementById('backHint').style.display=isBack?'block':'none';};
  actionSelect.addEventListener('change',syncAction); syncAction();
  document.getElementById('cancelSelection').addEventListener('click',()=>{pendingSelection=null;renderSelection();});
  document.getElementById('saveInteraction').addEventListener('click',saveInteraction);
}

async function saveInteraction(){
  const action=document.getElementById('actionSelect').value;
  const target=document.getElementById('targetSelect').value;
  const name=document.getElementById('interactionNameInput').value.trim();
  if(!name) return alert('请输入交互名称');
  if(action==='navigate' && !target) return alert('请选择目标页面');
  try{
    await api('/api/interactions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,source_page_id:currentPageId,action,target_page_id:action==='navigate'?target:null,kind:pendingSelection.kind,payload:pendingSelection.payload})});
    await reload(true);
  }catch(e){alert(e.message);}
}

function renderInteractions(){
  const items=currentInteractions(); interactionCount.textContent=items.length;
  if(!items.length){interactionList.innerHTML='<div class="interaction-empty">当前页面还没有交互</div>';return;}
  interactionList.innerHTML=items.map(i=>`<div class="interaction-card"><div class="interaction-top"><div class="interaction-main"><div class="interaction-label">${esc(i.name)}</div><div class="interaction-detail">${i.kind==='element'?'元素 '+esc(i.payload.elementId):'图片热点'} · ${esc(actionLabel(i))}</div></div><div class="interaction-actions"><button class="mini-action rename-interaction" data-id="${i.id}" type="button" title="重命名">✎</button><button class="mini-danger delete-interaction" data-id="${i.id}" type="button" title="删除">✕</button></div></div></div>`).join('');
  interactionList.querySelectorAll('.rename-interaction').forEach(btn=>btn.addEventListener('click',()=>openRename('interaction',btn.dataset.id)));
  interactionList.querySelectorAll('.delete-interaction').forEach(btn=>btn.addEventListener('click',async()=>{await api(`/api/interactions/${btn.dataset.id}`,{method:'DELETE'});await reload(true);}));
}

function prepareUpload(files){
  pendingFiles=Array.from(files||[]);
  document.getElementById('fileInput').value='';
  if(!pendingFiles.length) return;
  uploadRows.innerHTML=pendingFiles.map((f,idx)=>`<div class="upload-name-row"><div><strong>${esc(f.name)}</strong><span>${Math.ceil(f.size/1024)} KB</span></div><input class="upload-name-input" data-index="${idx}" maxlength="120" value="${esc(f.name.replace(/\.[^.]+$/,''))}" /></div>`).join('');
  uploadDialog.showModal();
}

document.getElementById('fileInput').addEventListener('change',e=>prepareUpload(e.target.files));
document.getElementById('uploadCancel').addEventListener('click',()=>{pendingFiles=[];uploadDialog.close();});
uploadForm.addEventListener('submit', async e=>{
  e.preventDefault();
  const inputs=Array.from(uploadRows.querySelectorAll('.upload-name-input'));
  const names=inputs.map(i=>i.value.trim().replace(/\s+/g,' '));
  if(names.some(n=>!n)) return alert('页面名称不能为空');
  const folded=names.map(norm);
  if(new Set(folded).size!==folded.length) return alert('本次上传的页面名称存在重复');
  const existing=new Set(state.pages.map(p=>norm(p.name)));
  const collision=names.find(n=>existing.has(norm(n)));
  if(collision) return alert(`页面名称“${collision}”在当前项目中已存在`);
  const fd=new FormData(); fd.append('storage_backend',storageSelect.value||'local'); fd.append('names_json',JSON.stringify(names));
  pendingFiles.forEach(f=>fd.append('files',f));
  try{await api(`/api/projects/${projectId}/pages`,{method:'POST',body:fd});uploadDialog.close();pendingFiles=[];await reload(false);}catch(err){alert(err.message);}
});

function openRename(kind,id){
  const item=kind==='page'?state.pages.find(p=>p.id===id):state.interactions.find(i=>i.id===id);
  if(!item) return;
  renameTarget={kind,id}; renameTitle.textContent=kind==='page'?'重命名页面':'重命名交互'; renameInput.value=item.name; renameDialog.showModal();
  requestAnimationFrame(()=>{renameInput.focus();renameInput.select();});
}
document.getElementById('renameCancel').addEventListener('click',()=>{renameTarget=null;renameDialog.close();});
renameForm.addEventListener('submit', async e=>{
  e.preventDefault(); if(!renameTarget) return;
  const name=renameInput.value.trim().replace(/\s+/g,' '); if(!name) return alert('请输入名称');
  const url=renameTarget.kind==='page'?`/api/pages/${renameTarget.id}`:`/api/interactions/${renameTarget.id}`;
  try{await api(url,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({name})});renameDialog.close();renameTarget=null;await reload(true);}catch(err){alert(err.message);}
});

function askDeletePage(id){
  const p=state.pages.find(p=>p.id===id); if(!p)return;
  document.getElementById('confirmText').textContent=`删除“${p.name}”？所有指向该页面、以及从该页面发出的跳转都会自动清理，底层资源也会从 ${storageLabel(p)} 删除。`;
  confirmDialog.showModal();
  const ok=document.getElementById('confirmOk'); const cancel=document.getElementById('confirmCancel');
  const cleanup=()=>{ok.onclick=null;cancel.onclick=null};
  cancel.onclick=()=>{cleanup();confirmDialog.close();};
  ok.onclick=async()=>{cleanup();confirmDialog.close();await api(`/api/pages/${id}`,{method:'DELETE'});await reload(false);};
}

(async()=>{await loadConfig();await reload(false);})();
