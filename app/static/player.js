const root=document.getElementById('player');
const projectId=root.dataset.projectId;
const stage=document.getElementById('playerStage');
const backBtn=document.getElementById('backBtn');
const navToggle=document.getElementById('navToggle');
const nav=document.getElementById('playerNav');
const pageList=document.getElementById('playerPageList');
const pageCount=document.getElementById('playerPageCount');
const pageNameEl=document.getElementById('playerPageName');
let state={pages:[],interactions:[]};
let historyStack=[];
let currentPageId=null;
function esc(s=''){const d=document.createElement('div');d.textContent=s;return d.innerHTML;}
function page(id){return state.pages.find(p=>p.id===id)}
function interactions(id){return state.interactions.filter(i=>i.source_page_id===id)}

async function load(){
  const res=await fetch(`/api/projects/${projectId}`);
  if(res.status===401){location.href=`/login?next=${encodeURIComponent(location.pathname+location.search)}`;return;}
  if(!res.ok){stage.innerHTML='<div class="player-empty">加载失败</div>';return;}
  state=await res.json();
  const q=new URLSearchParams(location.search).get('page');
  currentPageId=state.pages.some(p=>p.id===q)?q:state.pages[0]?.id||null;
  if(currentPageId)historyStack=[currentPageId];
  pageCount.textContent=state.pages.length;
  render();
}
function updateUrl(id){
  const url=new URL(location.href);
  if(id) url.searchParams.set('page',id); else url.searchParams.delete('page');
  history.replaceState(null,'',url);
}
function navigate(id){
  if(!state.pages.some(p=>p.id===id) || id===currentPageId)return;
  currentPageId=id;historyStack.push(id);updateUrl(id);render();
}
function goBack(){
  if(historyStack.length<=1)return;
  historyStack.pop();
  currentPageId=historyStack[historyStack.length-1];
  updateUrl(currentPageId);
  render();
}
function executeInteraction(i){
  if(!i)return;
  if(i.action==='back'){goBack();return;}
  if(i.action==='navigate' && i.target_page_id)navigate(i.target_page_id);
}
function renderPageList(){
  if(!state.pages.length){pageList.innerHTML='<div class="player-nav-empty">暂无页面</div>';return;}
  pageList.innerHTML=state.pages.map(p=>`<button type="button" class="player-page-item ${p.id===currentPageId?'active':''}" data-id="${p.id}"><span class="player-page-type">${p.type==='html'?'HTML':'IMG'}</span><span class="player-page-name">${esc(p.name)}</span></button>`).join('');
  pageList.querySelectorAll('.player-page-item').forEach(btn=>btn.addEventListener('click',()=>navigate(btn.dataset.id)));
}
function render(){
  backBtn.disabled=historyStack.length<=1;
  const p=page(currentPageId);pageNameEl.textContent=p?`/ ${p.name}`:'';
  renderPageList();
  if(!p){stage.innerHTML='<div class="player-empty">项目还没有页面。</div>';return;}
  if(p.type==='html'){
    stage.innerHTML=`<iframe class="player-html" sandbox="allow-scripts" src="/api/pages/${p.id}/render?mode=play"></iframe>`;
  }else{
    stage.innerHTML=`<div id="playImageStage" class="player-image-stage"><img src="/api/pages/${p.id}/file" alt="${esc(p.name)}" /></div>`;
    const s=document.getElementById('playImageStage');
    interactions(p.id).filter(i=>i.kind==='region').forEach(i=>{const r=i.payload;const b=document.createElement('button');b.className='player-hotspot';b.type='button';b.title=i.action==='back'?'返回上一页':`跳转到 ${page(i.target_page_id)?.name||''}`;Object.assign(b.style,{left:`${r.x*100}%`,top:`${r.y*100}%`,width:`${r.width*100}%`,height:`${r.height*100}%`});b.addEventListener('click',()=>executeInteraction(i));s.appendChild(b);});
  }
}
window.addEventListener('message',e=>{const d=e.data;if(!d||d.type!=='uipm-element-click'||d.pageId!==currentPageId)return;const hit=interactions(currentPageId).find(i=>i.kind==='element'&&i.payload.elementId===d.elementId);executeInteraction(hit);});
backBtn.addEventListener('click',goBack);
navToggle.addEventListener('click',()=>{
  const collapsed=root.classList.toggle('nav-collapsed');
  navToggle.setAttribute('aria-expanded', String(!collapsed));
  navToggle.textContent=collapsed?'☰ 页面':'☰ 页面';
});
load();
