const grid=document.getElementById('projectGrid');
const dialog=document.getElementById('newProjectDialog');
const form=document.getElementById('newProjectForm');
const nameInput=document.getElementById('projectName');
document.getElementById('newProjectBtn').addEventListener('click',()=>{dialog.showModal();setTimeout(()=>nameInput.focus(),0);});
document.getElementById('cancelProjectBtn').addEventListener('click',()=>dialog.close());
function esc(s=''){const d=document.createElement('div');d.textContent=s;return d.innerHTML;}
function goLogin(){location.href=`/login?next=${encodeURIComponent(location.pathname+location.search)}`;}
async function api(url,options={}){const res=await fetch(url,options);if(res.status===401){goLogin();throw new Error('登录已过期');}if(!res.ok){let m='操作失败';try{m=(await res.json()).detail||m}catch{}throw new Error(m);}return res.json();}
async function loadProjects(){
  const projects=await api('/api/projects');
  if(!projects.length){grid.innerHTML='<div class="empty-projects">还没有项目。点击右上角“新建项目”开始。</div>';return;}
  grid.innerHTML=projects.map(p=>`<a class="project-card" href="/project/${p.id}"><button class="mini-danger project-delete" data-id="${p.id}" data-name="${esc(p.name)}" title="删除项目" type="button">✕</button><div class="eyebrow">PROJECT</div><h3>${esc(p.name)}</h3><p>${p.page_count} 个页面</p><div class="card-arrow">→</div></a>`).join('');
  grid.querySelectorAll('.project-delete').forEach(btn=>btn.addEventListener('click',async e=>{e.preventDefault();e.stopPropagation();if(!confirm(`删除项目“${btn.dataset.name}”？项目内页面、交互和本地/S3资源也会被删除。`))return;try{await api(`/api/projects/${btn.dataset.id}`,{method:'DELETE'});loadProjects();}catch(err){alert(err.message);}}));
}
form.addEventListener('submit',async e=>{e.preventDefault();const name=nameInput.value.trim();if(!name)return;try{const p=await api('/api/projects',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name})});location.href=`/project/${p.id}`;}catch(err){alert(err.message);}});
loadProjects();
