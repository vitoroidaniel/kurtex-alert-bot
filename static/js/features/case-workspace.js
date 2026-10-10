/* Independent, read-only case workspace. Cases keeps its existing list and filters. */
var cwView=['board','grid','table','card'].includes(preferences.get('kurtex-workspace-view'))?preferences.get('kurtex-workspace-view'):'board',cwState={rows:[],total:0,hasMore:false,busy:false,serial:0,selected:'',detail:null,detailSerial:0,tab:'details',initialSelection:false};
function cwEscape(s){return String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function cwId(c){return c.full_id||c.id||'';}
function cwTone(name){return Array.from(name).reduce((value,char)=>(value+char.charCodeAt(0))%6,0);}
function cwField(c,name,other){return c[name]||c[other]||'';}
function cwSetView(view){if(!['board','grid','table','card'].includes(view))return;cwView=view;preferences.set('kurtex-workspace-view',view);cwRender();}
function cwData(){
 const q=(document.getElementById('cw-search')?.value||'').trim().toLowerCase(),status=document.getElementById('cw-status')?.value||'';
 return cwState.rows.filter(c=>(!status||c.status===status)&&(!q||[c.unit_number,c.issue_text,c.description,c.driver,c.report_driver,c.agent,c.group,c.status,c.vehicle_type,c.location].some(x=>String(x||'').toLowerCase().includes(q))));
}
function cwPriority(c){const p=String(c.priority||'normal').toLowerCase();return ['high','medium','low','normal'].includes(p)?p:'normal';}
function cwCard(c){
 const id=cwId(c),unit=c.unit_number||'No unit',p=cwPriority(c);
 return `<button type="button" class="cw-case ${cwState.selected===id?'selected':''}" data-id="${cwEscape(id)}" onclick="cwSelect(this.dataset.id)" aria-label="View case ${cwEscape(unit)}" aria-pressed="${cwState.selected===id}"><strong class="cw-unit">${cwEscape(unit)}</strong><div class="cw-issue" title="${cwEscape(c.issue_text||c.description||'Maintenance case')}">${cwEscape(c.issue_text||c.description||'Maintenance case')}</div><div class="cw-location" title="${cwEscape(c.location||c.group||'Location not provided')}">${cwEscape(c.location||c.group||'Location not provided')}</div><span class="cw-priority cw-${p}">${p}</span><div class="cw-case-bottom"><span class="cw-card-counts"><span aria-label="${Number(c.notes_count||0)} notes"><i class="ph ph-chat-circle" aria-hidden="true"></i> ${Number(c.notes_count||0)}</span><span aria-label="${Number(c.attachment_count||0)} attachments"><i class="ph ph-paperclip" aria-hidden="true"></i> ${Number(c.attachment_count||0)}</span></span><span>${cwEscape(c.opened_raw||c.opened||'')}</span></div></button>`;
}
function cwUpdateContent(root,html){const board=root.querySelector('.cw-board'),left=board?.scrollLeft||0,tops=Array.from(root.querySelectorAll('.cw-stack'),el=>el.scrollTop);updateHTML(root,html);const next=root.querySelector('.cw-board');if(next)next.scrollLeft=left;root.querySelectorAll('.cw-stack').forEach((el,i)=>el.scrollTop=tops[i]||0);}
function cwRender(){
 const root=document.getElementById('cw-content');if(!root)return;
 document.querySelectorAll('[data-cw-view]').forEach(b=>{b.classList.toggle('active',b.dataset.cwView===cwView);b.setAttribute('aria-pressed',String(b.dataset.cwView===cwView));});
 const rows=cwData();if(typeof uiWorkspaceFilters==='function')uiWorkspaceFilters(rows);const count=document.getElementById('cw-count');if(count)count.textContent=`${rows.length} matching · ${cwState.rows.length} of ${cwState.total} cases loaded`;
 const more=document.querySelector('.cw-footer button');if(more){more.hidden=!cwState.hasMore;more.disabled=cwState.busy;more.textContent=cwState.busy?'Loading…':'Load more cases';}
 if(!rows.length){root.innerHTML=`<div class="cw-empty">${cwState.busy?'Loading cases…':'No cases match these filters.'}</div>`;return;}
 if(cwView==='table'){
 cwUpdateContent(root,`<div class="cw-table-scroll"><table class="cw-table"><thead><tr><th>Unit</th><th>Issue</th><th>Driver</th><th>Assigned to</th><th>Status</th><th>Priority</th><th></th></tr></thead><tbody>${rows.map(c=>`<tr class="${cwState.selected===cwId(c)?'selected':''}"><td>${cwEscape(c.unit_number)}</td><td>${cwEscape(c.issue_text||c.description)}</td><td>${cwEscape(c.report_driver||c.driver)}</td><td>${cwEscape(c.agent)}</td><td>${cwEscape(c.status)}</td><td>${cwEscape(cwPriority(c))}</td><td><button type="button" class="btn secondary" data-id="${cwEscape(cwId(c))}" onclick="cwSelect(this.dataset.id)">Details</button></td></tr>`).join('')}</tbody></table></div>`);return;
 }
 if(cwView!=='board'){cwUpdateContent(root,`<div class="cw-${cwView}">${rows.map(cwCard).join('')}</div>`);return;}
 const field=document.getElementById('cw-group')?.value||'agent',groups=new Map();
 rows.forEach(c=>{const value=c[field],name=String(!value||value==='—'?(field==='agent'?'Unassigned':'Not specified'):value);if(!groups.has(name))groups.set(name,[]);groups.get(name).push(c);});
 cwUpdateContent(root,`<div class="cw-board">${Array.from(groups,([name,cases],i)=>`<section class="cw-column" data-tone="${cwTone(name)}"><header><strong>${cwEscape(name)}</strong><span>${cases.length}</span></header><div class="cw-stack">${cases.map(cwCard).join('')}</div></section>`).join('')}</div>`);
}
async function cwLoad(append){
 if(cwState.busy)return;const serial=++cwState.serial;let completed=false;cwState.busy=true;if(!cwState.rows.length)cwRender();
 try{
  let incoming=[],d,offset=append?cwState.rows.length:0,target=append?500:Math.max(500,cwState.rows.length);
  do{const r=await apiFetch('/api/cases?filter=all&limit='+Math.min(500,target-incoming.length)+'&offset='+(offset+incoming.length),'case-workspace');if(!r.ok)throw Error('Unable to load workspace');d=await r.json();if(serial!==cwState.serial)return;incoming.push(...(d.cases||[]));}while(d.has_more&&incoming.length<target&&d.cases?.length);
  cwState.rows=append?cwState.rows.concat(incoming):incoming;cwState.total=d.total||0;cwState.hasMore=!!d.has_more;completed=true;if(!cwState.initialSelection&&cwState.rows.length){cwState.initialSelection=true;if(!window.matchMedia('(max-width:760px)').matches)cwSelect(cwId(cwState.rows[0]));}
 }catch(e){if(e.name!=='AbortError'){const root=document.getElementById('cw-content');if(!cwState.rows.length)root.innerHTML='<div class="cw-empty">Unable to load cases. <button type="button" onclick="cwRefresh()">Retry</button></div>';}}
 finally{if(serial===cwState.serial){cwState.busy=false;if(completed||cwState.rows.length)cwRender();const more=document.querySelector('.cw-footer button');if(more)more.disabled=false;}}
}
function cwRefresh(){return cwLoad(false);}
function cwLoadMore(){return cwLoad(true);}
async function cwSelect(id){
 if(!cwState.selected)cwState.returnScroll=window.scrollY;
 cwState.selected=id;cwState.tab='details';cwState.detail=null;const serial=++cwState.detailSerial;
 const drawer=document.getElementById('cw-detail');drawer.hidden=false;drawer.innerHTML='<div class="loading">Loading case details…</div>';document.querySelector('.cw-work-area').classList.add('has-detail');cwRender();
 try{const r=await apiFetch('/api/case?id='+encodeURIComponent(id),'workspace-detail');if(!r.ok)throw Error('Unable to load case');const c=await r.json();if(serial!==cwState.detailSerial)return;cwState.detail=c;cwRenderDetail();document.getElementById('cw-detail-close').focus({preventScroll:true});if(window.matchMedia('(max-width:760px)').matches)drawer.scrollIntoView({block:'start'});}
 catch(e){if(serial!==cwState.detailSerial||e.name==='AbortError')return;drawer.innerHTML='<div class="cw-empty">Unable to load case. <button type="button" onclick="cwSelect(cwState.selected)">Retry</button><button type="button" onclick="cwCloseDetail()">Close</button></div>';}
}
function cwCloseDetail(){const id=cwState.selected;cwState.selected='';cwState.detailSerial++;document.getElementById('cw-detail').hidden=true;document.querySelector('.cw-work-area').classList.remove('has-detail');cwRender();Array.from(document.querySelectorAll('#cw-content button[data-id]')).find(b=>b.dataset.id===id)?.focus({preventScroll:true});if(window.matchMedia('(max-width:760px)').matches)window.scrollTo(0,cwState.returnScroll||0);}
function cwDetailTab(tab){cwState.tab=tab;cwRenderDetail();document.getElementById('cw-tab-'+tab)?.focus({preventScroll:true});}
function cwRenderDetail(){
 const c=cwState.detail;if(!c)return;const p=cwPriority(c),tab=cwState.tab,attachments=c.attachments||[];
 const field=(label,value)=>`<div class="cw-info-row"><dt>${label}</dt><dd>${label==='Status'?statusBadge(value||'open'):label==='Priority'?`<span class="cw-priority cw-${p}">${cwEscape(p)}</span>`:cwEscape(value||'Not provided')}</dd></div>`;
 const related=cwState.rows.filter(x=>cwId(x)!==cwState.selected&&x.unit_number&&x.unit_number===c.unit_number).slice(0,4);
 let content='';
 if(tab==='details')content=`<section><h3>Information</h3><dl>${field('Unit',c.unit_number)}${field('Driver',c.report_driver||c.driver)}${field('Issue',c.issue_text||c.description)}${field('Status',c.status)}${field('Assigned to',c.agent)}${field('Location',c.location)}${field('Priority',p)}${field('Reported',c.opened)}${field('Closed',c.closed==='—'?'':c.closed)}</dl></section><section><h3>Description</h3><p>${cwEscape(c.full_description||c.issue_text||c.description||'No description provided.')}</p></section><section><h3>Related cases <span>${related.length}</span></h3>${related.length?related.map(x=>`<button type="button" class="cw-related" data-id="${cwEscape(cwId(x))}" onclick="cwSelect(this.dataset.id)"><strong>${cwEscape(x.unit_number)}</strong><span>${cwEscape(x.issue_text||x.description)}</span></button>`).join(''):'<p>No other cases for this unit in the loaded history.</p>'}</section>`;
 if(tab==='notes')content=`<section><h3>Case notes</h3><p>${cwEscape(c.comments||c.full_notes||c.notes||'No notes recorded.')}</p></section>`;
 if(tab==='attachments')content=`<section><h3>Attachments</h3>${attachments.length?attachments.map(a=>`<div class="cw-attachment"><i class="ph ph-paperclip"></i><span>${cwEscape(a.name)}</span></div>`).join(''):'<p>No attachments recorded.</p>'}</section>`;
 if(tab==='history')content=`<section><h3>Case history</h3>${typeof buildTimeline==='function'?buildTimeline(c):`<p>Reported ${cwEscape(c.opened)}</p>`}</section>`;
 document.getElementById('cw-detail').innerHTML=`<header class="cw-detail-head"><h2>${cwEscape(c.unit_number||'Case')} <span class="cw-priority cw-${p}">${p}</span></h2><button id="cw-detail-close" type="button" onclick="cwCloseDetail()" aria-label="Close case details"><i class="ph ph-x"></i></button></header><div class="cw-detail-tabs" role="group" aria-label="Case information">${[['details','Details'],['notes','Notes'],['attachments','Attachments'],['history','History']].map(([key,label])=>`<button type="button" id="cw-tab-${key}" class="${tab===key?'active':''}" aria-pressed="${tab===key}" onclick="cwDetailTab('${key}')">${label}${key==='attachments'?` <span>${attachments.length}</span>`:''}</button>`).join('')}</div><div class="cw-detail-body">${content}</div><footer><button type="button" class="btn" data-id="${cwEscape(cwState.selected)}" onclick="openCase(this)">Open full case <i class="ph ph-arrow-right"></i></button></footer>`;
}
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&cwState.selected&&currentPage==='case_workspace'&&document.getElementById('mobile-more-sheet').hidden&&!document.querySelector('.modal-overlay.open'))cwCloseDetail();});
function cwExport(){
 const fields=['unit_number','issue_text','report_driver','agent','status','priority','location'];
 const quote=value=>'"'+String(value||'').replace(/^[=+@-]/,"'$&").replace(/"/g,'""')+'"';
 const csv=[fields.join(',')].concat(cwData().map(c=>fields.map(k=>quote(c[k]||(k==='issue_text'?c.description:''))).join(','))).join('\r\n');
 const url=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'})),a=document.createElement('a');a.href=url;a.download='kurtex-case-workspace.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
function cwPrint(){window.print();}

