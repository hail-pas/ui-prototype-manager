const grid=document.getElementById('projectGrid');
const dialog=document.getElementById('newProjectDialog');
const form=document.getElementById('newProjectForm');
const nameInput=document.getElementById('projectName');
const copyDialog=document.getElementById('projectCopyDialog');
const copyText=document.getElementById('projectCopyText');
const copyCancel=document.getElementById('cancelProjectCopyBtn');
const copyConfirm=document.getElementById('confirmProjectCopyBtn');
let copyTarget=null;
document.getElementById('newProjectBtn').addEventListener('click',()=>{dialog.showModal();setTimeout(()=>nameInput.focus(),0);});
document.getElementById('cancelProjectBtn').addEventListener('click',()=>dialog.close());
function esc(s=''){const d=document.createElement('div');d.textContent=s;return d.innerHTML;}
function goLogin(){location.href=`/login?next=${encodeURIComponent(location.pathname+location.search)}`;}
function formatCreatedAt(value){const date=new Date(value);if(Number.isNaN(date.getTime()))return value||'';return new Intl.DateTimeFormat('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(date);}
async function api(url,options={}){const res=await fetch(url,options);if(res.status===401){goLogin();throw new Error('登录已过期');}if(!res.ok){let m='操作失败';try{m=(await res.json()).detail||m}catch{}throw new Error(m);}return res.json();}
async function loadProjects(){
  const projects=await api('/api/projects');
  if(!projects.length){grid.innerHTML='<div class="empty-projects">还没有项目。点击右上角“新建项目”开始。</div>';return;}
  grid.innerHTML=projects.map(p=>`<a class="project-card" href="/project/${p.id}"><button class="project-copy" data-id="${p.id}" data-name="${esc(p.name)}" title="复制项目" type="button" aria-label="复制项目">⧉</button><button class="mini-danger project-delete" data-id="${p.id}" data-name="${esc(p.name)}" title="删除项目" type="button">✕</button><div class="eyebrow">PROJECT</div><h3>${esc(p.name)}</h3><p>${p.page_count} 个页面</p><p class="project-created">创建时间：${esc(formatCreatedAt(p.created_at))}</p><div class="card-arrow">→</div></a>`).join('');
  grid.querySelectorAll('.project-copy').forEach(btn=>btn.addEventListener('click',e=>{e.preventDefault();e.stopPropagation();copyTarget={id:btn.dataset.id,name:btn.dataset.name};copyText.textContent=`确认复制项目“${copyTarget.name}”？将完整复制项目下的页面、交互、页面元素和资源，复制后与原项目相互独立。`;copyDialog.showModal();}));
  grid.querySelectorAll('.project-delete').forEach(btn=>btn.addEventListener('click',async e=>{e.preventDefault();e.stopPropagation();if(!confirm(`删除项目“${btn.dataset.name}”？项目内页面、交互和本地/S3资源也会被删除。`))return;try{await api(`/api/projects/${btn.dataset.id}`,{method:'DELETE'});loadProjects();}catch(err){alert(err.message);}}));
}
form.addEventListener('submit',async e=>{e.preventDefault();const name=nameInput.value.trim();if(!name)return;try{const p=await api('/api/projects',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name})});location.href=`/project/${p.id}`;}catch(err){alert(err.message);}});
loadProjects();

copyCancel.addEventListener('click',()=>{copyTarget=null;copyDialog.close();});
copyDialog.addEventListener('cancel',()=>{copyTarget=null;});
copyConfirm.addEventListener('click',async()=>{if(!copyTarget)return;copyConfirm.disabled=true;try{await api(`/api/projects/${copyTarget.id}/duplicate`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:`${copyTarget.name} copy`})});copyTarget=null;copyDialog.close();await loadProjects();}catch(err){alert(err.message);}finally{copyConfirm.disabled=false;}});
