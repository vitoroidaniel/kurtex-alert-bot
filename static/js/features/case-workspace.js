/* Maintenance reports have independent list state; Cases retains complete history. */
var cwView=['board','grid','table','card'].includes(preferences.get('kurtex-workspace-view'))?preferences.get('kurtex-workspace-view'):'board';
var cwState={rows:[],total:0,hasMore:false,busy:false,serial:0,selected:'',detail:null,detailSerial:0,tab:'details',initialSelection:false,full:false,dialog:null,writeBusy:false};
function cwEscape(s){return String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function cwId(c){return c.full_id||c.id||'';}
function cwTone(name){return Array.from(name).reduce((v,c)=>(v+c.charCodeAt(0))%6,0);}
function cwPriority(c){const p=String(c.priority||'normal').toLowerCase();return ['high','medium','low','normal'].includes(p)?p:'normal';}
function cwLabel(s){return String(s||'').replace(/_/g,' ').replace(/^./,c=>c.toUpperCase());}
function cwDate(s){if(!s)return '';const date=new Date(s);return Number.isNaN(date.getTime())?s:date.toLocaleString('en-US',{timeZone:'America/Chicago',month:'short',day:'numeric',hour:'numeric',minute:'2-digit'});}
function cwIsActive(c){return ['open','assigned','reported'].includes(c.status);}
function cwCanEdit(c){return c.can_manage&&cwIsActive(c);}
function cwStatus(c){return `<span class="cw-status-pill ${c.status==='done'?'is-done':''}"><span aria-hidden="true">●</span> ${cwEscape(cwLabel(c.status))}</span>`;}
function cwNotice(message){const el=document.getElementById('cw-notice');el.hidden=!message;el.textContent=message;}
function cwSetView(view){if(!['board','grid','table','card'].includes(view))return;cwView=view;preferences.set('kurtex-workspace-view',view);cwRender();}
function cwToggleFilters(){const panel=document.getElementById('cw-filters');panel.hidden=!panel.hidden;document.getElementById('cw-filter-toggle').setAttribute('aria-expanded',String(!panel.hidden));}
function cwData(){
 const value=id=>document.getElementById(id)?.value||'',q=value('cw-search').trim().toLowerCase(),status=value('cw-status'),priority=value('cw-priority-filter'),equipment=value('cw-equipment-filter');
 const rows=cwState.rows.filter(c=>(!status||c.status===status)&&(!priority||cwPriority(c)===priority)&&(!equipment||c.vehicle_type===equipment)&&(!q||[c.unit_number,c.issue_text,c.description,c.driver,c.report_driver,c.agent,c.group,c.location].some(x=>String(x||'').toLowerCase().includes(q))));
 const sort=value('cw-sort'),rank={high:0,medium:1,normal:2,low:3};
 if(sort==='oldest')rows.reverse();
 if(sort==='priority')rows.sort((a,b)=>rank[cwPriority(a)]-rank[cwPriority(b)]);
 return rows;
}
function cwCard(c){
 const id=cwId(c),unit=c.unit_number||'No unit',p=cwPriority(c),date=(c.reported||c.opened||'').split(' ').slice(0,2).join(' ');
 return `<button type="button" class="cw-case ${cwState.selected===id?'selected':''}" data-priority="${p}" data-id="${cwEscape(id)}" onclick="cwSelect(this.dataset.id)" aria-label="View ${cwEscape(unit)}, ${cwEscape(c.issue_text||c.description)}, ${p} priority" aria-pressed="${cwState.selected===id}"><strong class="cw-unit">${cwEscape(unit)}</strong><span class="cw-issue" title="${cwEscape(c.issue_text||c.description)}">${cwEscape(c.issue_text||c.description||'Maintenance report')}</span><span class="cw-location" title="${cwEscape(c.location||'Location not provided')}">${cwEscape(c.location||'Location not provided')}</span><span class="cw-case-bottom"><span class="cw-priority cw-${p}">${cwLabel(p)}</span><span class="cw-card-counts"><span aria-label="${Number(c.notes_count||0)} notes"><i class="ph ph-chat-circle" aria-hidden="true"></i>${Number(c.notes_count||0)}</span><span aria-label="${Number(c.attachment_count||0)} attachments"><i class="ph ph-paperclip" aria-hidden="true"></i>${Number(c.attachment_count||0)}</span></span><time>${cwEscape(date)}</time></span></button>`;
}
function cwUpdateContent(root,html){const left=root.querySelector('.cw-board')?.scrollLeft||0,tops=Array.from(root.querySelectorAll('.cw-stack'),el=>el.scrollTop);updateHTML(root,html);const board=root.querySelector('.cw-board');if(board)board.scrollLeft=left;root.querySelectorAll('.cw-stack').forEach((el,i)=>el.scrollTop=tops[i]||0);cwUpdatePanControls();}
function cwRender(){
 if(cwState.panning){cwState.panRenderPending=true;return;}
 const root=document.getElementById('cw-content');if(!root)return;
 document.querySelectorAll('[data-cw-view]').forEach(b=>{b.classList.toggle('active',b.dataset.cwView===cwView);b.setAttribute('aria-pressed',String(b.dataset.cwView===cwView));});
 const rows=cwData();if(typeof uiWorkspaceFilters==='function')uiWorkspaceFilters(rows);
 document.getElementById('cw-count').textContent=`${rows.length} matching · ${cwState.rows.length} of ${cwState.total} active reports loaded`;
 const more=document.getElementById('cw-load-more');more.hidden=!cwState.hasMore;more.disabled=cwState.busy;more.textContent=cwState.busy?'Loading…':'Load more reports';
 if(!rows.length){const filtered=cwState.rows.length>0;cwUpdateContent(root,`<div class="cw-empty"><i class="ph ${filtered?'ph-magnifying-glass':'ph-check-circle'}" aria-hidden="true"></i><h3>${cwState.busy?'Loading maintenance reports…':filtered?'No reports match these filters':'No active maintenance reports'}</h3><p>${cwState.busy?'':filtered?'Try another search or reset your filters.':'New reports appear here automatically. Closed reports stay in Cases.'}</p>${!cwState.busy?`<button type="button" class="cw-secondary" onclick="${filtered?'cwClearFilters()':'cwOpenDialog(\'new\')'}">${filtered?'Reset filters':'New case'}</button>`:''}</div>`);return;}
 if(cwView==='table'){
  cwUpdateContent(root,`<div class="cw-table-scroll"><table class="cw-table"><thead><tr><th>Unit / Issue</th><th>Driver</th><th>Assigned agent</th><th>Status</th><th>Priority</th><th>Reported</th><th>Actions</th></tr></thead><tbody>${rows.map(c=>`<tr class="${cwState.selected===cwId(c)?'selected':''}"><td><strong>${cwEscape(c.unit_number)}</strong><small>${cwEscape(c.issue_text||c.description)}</small></td><td>${cwEscape(c.report_driver||c.driver)}</td><td>${cwEscape(c.agent)}</td><td>${cwStatus(c)}</td><td><span class="cw-priority cw-${cwPriority(c)}">${cwLabel(cwPriority(c))}</span></td><td>${cwEscape(c.reported||c.opened)}</td><td><button type="button" class="cw-secondary" data-id="${cwEscape(cwId(c))}" onclick="cwSelect(this.dataset.id)">View case</button></td></tr>`).join('')}</tbody></table></div>`);return;
 }
 if(cwView!=='board'){cwUpdateContent(root,`<div class="cw-${cwView}">${rows.map(cwCard).join('')}</div>`);return;}
 const field=document.getElementById('cw-group').value,groups=new Map();
 rows.forEach(c=>{const v=c[field],name=String(!v||v==='—'?(field==='agent'?'Unassigned':'Not specified'):v);if(!groups.has(name))groups.set(name,[]);groups.get(name).push(c);});
 cwUpdateContent(root,`<div class="cw-board" tabindex="0" role="region" aria-label="Maintenance board. Drag or use left and right arrow keys to move across groups.">${Array.from(groups,([name,cases])=>`<section class="cw-column" data-tone="${cwTone(name)}"><header><strong title="${cwEscape(name)}">${cwEscape(field==='agent'?name:cwLabel(name))}</strong><span>${cases.length}</span></header><div class="cw-stack">${cases.map(cwCard).join('')}</div><footer><button type="button" onclick="cwOpenDialog('new')"><i class="ph ph-plus"></i> Add case</button></footer></section>`).join('')}</div>`);
}
function cwUpdatePanControls(){
 const board=document.querySelector('#cw-content .cw-board'),nav=document.getElementById('cw-board-navigation');
 nav.hidden=!board||cwView!=='board'||cwState.full||(window.matchMedia('(max-width:760px)').matches&&!!cwState.selected);
 if(!board)return;
 document.getElementById('cw-pan-left').disabled=board.scrollLeft<=1;
 document.getElementById('cw-pan-right').disabled=board.scrollLeft>=board.scrollWidth-board.clientWidth-1;
}
function cwPan(direction){
 const board=document.querySelector('#cw-content .cw-board');if(!board)return;
 const reduce=document.documentElement.classList.contains('reduce-motion')||window.matchMedia('(prefers-reduced-motion: reduce)').matches;
 board.scrollBy({left:direction*Math.max(240,board.clientWidth*.75),behavior:reduce?'auto':'smooth'});
}
// Horizontal panning is navigation only. Reports keep their assignee and status.
(function(){
 const root=document.getElementById('cw-content');let drag=null,suppressClickUntil=0;
 root.addEventListener('pointerdown',event=>{
  const board=event.target.closest('.cw-board');
  if(!board||event.pointerType!=='mouse'||event.button!==0||event.target.closest('input,textarea,select,a,[contenteditable],.cw-column>footer'))return;
  drag={board,id:event.pointerId,x:event.clientX,y:event.clientY,left:board.scrollLeft,active:false};
 });
 root.addEventListener('pointermove',event=>{
  if(!drag||event.pointerId!==drag.id)return;
  const dx=event.clientX-drag.x,dy=event.clientY-drag.y;
  if(!drag.active&&Math.abs(dx)>6&&Math.abs(dx)>Math.abs(dy)){
   drag.active=true;cwState.panning=true;drag.board.classList.add('is-panning');drag.board.setPointerCapture(event.pointerId);
  }
  if(!drag.active)return;
  event.preventDefault();drag.board.scrollLeft=drag.left-dx;
 });
 function finish(event){if(!drag||event.pointerId!==drag.id)return;const current=drag;drag=null;cwState.panning=false;current.board.classList.remove('is-panning');if(current.active){suppressClickUntil=Date.now()+250;if(current.board.hasPointerCapture(event.pointerId))current.board.releasePointerCapture(event.pointerId);}if(cwState.panRenderPending){cwState.panRenderPending=false;setTimeout(cwRender,0);}}
 root.addEventListener('pointerup',finish);root.addEventListener('pointercancel',finish);root.addEventListener('lostpointercapture',finish);
 root.addEventListener('pointerleave',event=>{if(drag&&!drag.active)finish(event);});
 root.addEventListener('click',event=>{if(Date.now()<suppressClickUntil&&event.target.closest('.cw-board')){event.preventDefault();event.stopImmediatePropagation();suppressClickUntil=0;}},true);
 root.addEventListener('scroll',cwUpdatePanControls,true);
 root.addEventListener('keydown',event=>{
  if(!event.target.matches('.cw-board')||event.altKey||event.ctrlKey||event.metaKey)return;
  if(event.key==='ArrowLeft'||event.key==='ArrowRight'){event.preventDefault();cwPan(event.key==='ArrowLeft'?-1:1);}
  else if(event.key==='Home'||event.key==='End'){event.preventDefault();event.target.scrollLeft=event.key==='Home'?0:event.target.scrollWidth;}
 });
 root.addEventListener('wheel',event=>{
  const board=event.target.closest('.cw-board');if(!board||event.ctrlKey||Math.abs(event.deltaX)>Math.abs(event.deltaY))return;
  const stack=event.target.closest('.cw-stack');
  if(!event.shiftKey&&stack&&((event.deltaY>0&&stack.scrollTop<stack.scrollHeight-stack.clientHeight-1)||(event.deltaY<0&&stack.scrollTop>0)))return;
  const delta=event.deltaY*(event.deltaMode===1?16:event.deltaMode===2?board.clientWidth:1),left=board.scrollLeft;
  if((delta<0&&left<=0)||(delta>0&&left>=board.scrollWidth-board.clientWidth-1))return;
  event.preventDefault();board.scrollLeft+=delta;
 },{passive:false});
 if(typeof ResizeObserver!=='undefined')new ResizeObserver(cwUpdatePanControls).observe(root);else window.addEventListener('resize',cwUpdatePanControls);
})();
async function cwLoad(append){
 if(cwState.busy||cwState.writeBusy||cwState.dialog)return;const serial=++cwState.serial;let completed=false;cwState.busy=true;if(!cwState.rows.length)cwRender();
 try{
  let incoming=[],d,offset=append?cwState.rows.length:0,target=append?500:Math.max(500,cwState.rows.length);
  do{const r=await apiFetch('/api/cases?filter=workspace&limit='+Math.min(500,target-incoming.length)+'&offset='+(offset+incoming.length),'case-workspace');d=await r.json();if(serial!==cwState.serial)return;incoming.push(...(d.cases||[]));}while(d.has_more&&incoming.length<target&&d.cases?.length);
  const unique=new Map((append?cwState.rows:[]).concat(incoming).map(c=>[cwId(c),c]));cwState.rows=Array.from(unique.values());cwState.total=d.total||0;cwState.hasMore=!!d.has_more;completed=true;
  if(!cwState.initialSelection&&cwState.rows.length){cwState.initialSelection=true;if(!window.matchMedia('(max-width:760px)').matches)cwSelect(cwId(cwState.rows[0]));}
  else if(cwState.selected&&!cwState.dialog)cwFetchDetail(cwState.selected,true);
 }catch(e){if(e.name!=='AbortError'){if(!cwState.rows.length)document.getElementById('cw-content').innerHTML='<div class="cw-empty"><h3>Unable to load maintenance reports</h3><p>Refresh to try again.</p><button type="button" class="cw-secondary" onclick="cwRefresh()">Retry</button></div>';else cwNotice('Reports could not refresh. Your current board is still visible.');}}
 finally{if(serial===cwState.serial){cwState.busy=false;if(completed||cwState.rows.length)cwRender();}}
}
function cwRefresh(){return cwLoad(false);}
function cwLoadMore(){return cwLoad(true);}
function cwSelect(id){
 if(!cwState.selected)cwState.returnScroll=window.scrollY;
 cwState.selected=id;cwState.tab='details';cwState.detail=null;
 const drawer=document.getElementById('cw-detail');drawer.hidden=false;drawer.innerHTML='<div class="loading">Loading case details…</div>';document.querySelector('.cw-work-area').classList.add('has-detail');cwRender();
 if(cwState.full)document.getElementById('cw-full').innerHTML='<nav class="cw-full-nav"><button type="button" id="cw-back" class="cw-secondary" onclick="cwBack()">← Workspace</button></nav><div class="loading">Loading case details…</div>';
 return cwFetchDetail(id,false);
}
async function cwFetchDetail(id,quiet){
 const serial=++cwState.detailSerial;
 try{const r=await apiFetch('/api/case?id='+encodeURIComponent(id),'workspace-detail');const c=await r.json();if(serial!==cwState.detailSerial||cwState.selected!==id||cwState.dialog)return;cwState.detail=c;
  if(!cwIsActive(c)){cwState.rows=cwState.rows.filter(x=>cwId(x)!==id);cwRender();}
  // Quiet polling must not replace an input, scroll position, or focused action.
  const drawer=document.getElementById('cw-detail'),scroll=drawer.querySelector('.cw-detail-body')?.scrollTop||0,focusId=document.activeElement?.id;
  cwRenderDetail();if(cwState.full)cwRenderFull();const body=drawer.querySelector('.cw-detail-body');if(body)body.scrollTop=scroll;
  if(!quiet){document.getElementById(cwState.full?'cw-back':'cw-detail-close')?.focus({preventScroll:true});if(window.matchMedia('(max-width:760px)').matches&&!cwState.full)drawer.scrollIntoView({block:'start'});}
  else if(focusId)document.getElementById(focusId)?.focus({preventScroll:true});
 }catch(e){if(serial!==cwState.detailSerial||e.name==='AbortError')return;if(!quiet){document.getElementById('cw-detail').innerHTML='<div class="cw-empty">Unable to load case. <button type="button" onclick="cwSelect(cwState.selected)">Retry</button><button type="button" onclick="cwCloseDetail()">Close details</button></div>';if(cwState.full)document.getElementById('cw-full').innerHTML='<div class="cw-empty"><h3>Unable to load this case</h3><button type="button" class="cw-secondary" onclick="cwSelect(cwState.selected)">Retry</button><button type="button" class="cw-secondary" onclick="cwBack()">Back to Workspace</button></div>';}}
}
function cwCloseDetail(){const id=cwState.selected;cwState.selected='';cwState.detail=null;cwState.detailSerial++;document.getElementById('cw-detail').hidden=true;document.querySelector('.cw-work-area').classList.remove('has-detail');cwRender();Array.from(document.querySelectorAll('#cw-content button[data-id]')).find(b=>b.dataset.id===id)?.focus({preventScroll:true});if(window.matchMedia('(max-width:760px)').matches)window.scrollTo(0,cwState.returnScroll||0);}
function cwDetailTab(tab){cwState.tab=tab;cwRenderDetail();document.getElementById('cw-tab-'+tab)?.focus({preventScroll:true});}
function cwInfo(c){
 const field=(label,value)=>`<div class="cw-info-row"><dt>${label}</dt><dd>${value||'Not provided'}</dd></div>`;
 return `<dl>${field('Unit',cwEscape(c.unit_number))}${field('Driver',cwEscape(c.report_driver||c.driver))}${field('Issue',cwEscape(c.issue_text||c.description))}${field('Status',cwStatus(c))}${field('Assigned to',`<span class="cw-assignee"><span class="cw-initial">${cwEscape((c.agent||'?').charAt(0))}</span>${cwEscape(c.agent)}</span>`)}${field('Location',cwEscape(c.location))}${field('Priority',`<span class="cw-priority cw-${cwPriority(c)}">${cwLabel(cwPriority(c))}</span>`)}${field('Reported',cwEscape(c.reported||c.opened))}${field('Updated',cwEscape(c.updated))}${c.status==='done'?field('Closed',cwEscape(c.closed)):''}</dl>`;
}
function cwDescription(c){return cwEscape(c.full_description||c.issue_text||c.description||'No description provided.');}
function cwNotes(c){
 const original=[c.comments,c.full_notes&&c.full_notes!=='case reported'?c.full_notes:''].filter((v,i,a)=>v&&a.indexOf(v)===i),notes=c.workspace_notes||[];
 return original.map(v=>`<article class="cw-note"><small>Report notes</small><p>${cwEscape(v)}</p></article>`).join('')+notes.map(n=>`<article class="cw-note"><header><strong>${cwEscape(n.author)}</strong><time>${cwEscape(cwDate(n.created_at))}</time></header><p>${cwEscape(n.text)}</p></article>`).join('')||'<p class="cw-muted">No notes recorded.</p>';
}
function cwHistory(c){const events=[{label:'Case opened',time:c.opened},{label:'Assigned to '+c.agent,time:c.assigned_at!=='—'?c.assigned_at:''},{label:'Maintenance report submitted',time:c.reported}].filter(e=>e.time);(c.workspace_history||[]).forEach(e=>events.push({label:e.author+' '+({close:'closed the case',edit:'updated the report',note:'added a note'}[e.action]||'updated the case'),time:cwDate(e.created_at)}));if(c.status==='done'&&!(c.workspace_history||[]).some(e=>e.action==='close'))events.push({label:'Case closed',time:c.closed});return `<ol class="cw-timeline">${events.map(e=>`<li><span>${cwEscape(e.label)}</span><time>${cwEscape(e.time)}</time></li>`).join('')}</ol>`;}
function cwRelated(c){const related=cwState.rows.filter(x=>cwId(x)!==cwId(c)&&x.unit_number&&x.unit_number===c.unit_number).slice(0,4);return related.length?related.map(x=>`<button type="button" class="cw-related" data-id="${cwEscape(cwId(x))}" onclick="cwSelect(this.dataset.id)"><strong>${cwEscape(x.unit_number)}</strong><span>${cwEscape(x.issue_text||x.description)}</span><i class="ph ph-caret-right"></i></button>`).join(''):'<p class="cw-muted">No other active reports for this unit in the loaded board.</p>';}
function cwAttachments(c){const files=c.attachments||[];return files.length?files.map(a=>`<div class="cw-attachment"><i class="ph ph-paperclip" aria-hidden="true"></i><span>${cwEscape(a.name)}</span></div>`).join('')+'<p class="cw-muted">Original files are available in the Telegram report.</p>':'<p class="cw-muted">No attachments recorded.</p>';}
function cwEditButton(c,kind,label){return cwCanEdit(c)?`<button type="button" class="cw-text-button" onclick="cwOpenDialog('${kind}')"><i class="ph ph-${kind==='note'?'plus':'pencil-simple'}"></i> ${label}</button>`:'';}
function cwRenderDetail(){
 const c=cwState.detail;if(!c)return;const p=cwPriority(c),tab=cwState.tab,attachments=c.attachments||[];
 let content='';
 if(tab==='details')content=`${!cwIsActive(c)?'<div class="cw-closed-hint">Closed · retained in Cases history.</div>':''}<section><h3>Information ${cwEditButton(c,'edit','Edit')}</h3>${cwInfo(c)}</section><section><h3>Description</h3><p>${cwDescription(c)}</p></section><section><h3>Related cases</h3>${cwRelated(c)}</section>`;
 if(tab==='notes')content=`<section><h3>Notes ${cwEditButton(c,'note','Add note')}</h3>${cwNotes(c)}</section>`;
 if(tab==='attachments')content=`<section><h3>Attachments</h3>${cwAttachments(c)}</section>`;
 if(tab==='history')content=`<section><h3>Activity timeline</h3>${cwHistory(c)}</section>`;
 document.getElementById('cw-detail').innerHTML=`<header class="cw-detail-head"><h2>${cwEscape(c.unit_number||'Case')} <span class="cw-priority cw-${p}">${cwLabel(p)}</span></h2><div><button type="button" id="cw-expand" onclick="cwOpenFull()" aria-label="Open full case"><i class="ph ph-arrow-square-out"></i></button><button id="cw-detail-close" type="button" onclick="cwCloseDetail()" aria-label="Close case details"><i class="ph ph-x"></i></button></div></header><div class="cw-detail-tabs" role="group" aria-label="Case information">${[['details','Details'],['notes','Notes'],['attachments','Attachments'],['history','History']].map(([key,label])=>`<button type="button" id="cw-tab-${key}" class="${tab===key?'active':''}" aria-pressed="${tab===key}" onclick="cwDetailTab('${key}')">${label}${key==='attachments'?` <span>${attachments.length}</span>`:key==='notes'?` <span>${Number(c.notes_count||0)}</span>`:''}</button>`).join('')}</div><div class="cw-detail-body">${content}</div><footer>${cwCanEdit(c)?'<button type="button" id="cw-close-case" class="cw-secondary" onclick="cwOpenDialog(\'close\')"><i class="ph ph-check-circle"></i> Close case</button>':''}<button type="button" id="cw-open-full" class="cw-primary" onclick="cwOpenFull()">Open full case <i class="ph ph-arrow-right"></i></button></footer>`;
}
function cwOpenFull(){if(!cwState.detail)return;cwState.full=true;document.getElementById('cw-shell').classList.add('full-case');document.getElementById('cw-full').hidden=false;cwRenderFull();document.getElementById('cw-back').focus({preventScroll:true});window.scrollTo(0,0);}
function cwBack(){cwState.full=false;document.getElementById('cw-shell').classList.remove('full-case');document.getElementById('cw-full').hidden=true;cwRender();document.getElementById('cw-open-full')?.focus({preventScroll:true});}
function cwRenderFull(){
 const c=cwState.detail;if(!c)return;const editable=cwCanEdit(c),p=cwPriority(c);
 const actions=editable?`<div class="cw-case-actions" role="group" aria-label="Case actions">
   <button type="button" id="cw-full-edit" class="cw-secondary" onclick="cwOpenDialog('edit')"><i class="ph ph-pencil-simple" aria-hidden="true"></i> Edit report</button>
   <button type="button" id="cw-full-note" class="cw-secondary" onclick="cwOpenDialog('note')"><i class="ph ph-note-pencil" aria-hidden="true"></i> Add note</button>
   <button type="button" id="cw-full-close" class="cw-primary" onclick="cwOpenDialog('close')"><i class="ph ph-check-circle" aria-hidden="true"></i> Close case</button>
   <p class="cw-action-help">Closing marks this case Done. Its report and history stay in Cases.</p>
  </div>`:'';
 document.getElementById('cw-full').innerHTML=`
  <nav class="cw-full-nav" aria-label="Case navigation">
   <button type="button" class="cw-secondary" id="cw-back" onclick="cwBack()"><i class="ph ph-arrow-left" aria-hidden="true"></i> Workspace</button>
   <div class="cw-full-identity"><strong>Case ${cwEscape(c.unit_number||c.id)}</strong>${cwStatus(c)}<span class="cw-priority cw-${p}">${cwLabel(p)}</span></div>
  </nav>
  <div class="cw-full-grid">
   <div class="cw-full-main">
    <section class="cw-panel cw-overview" aria-labelledby="cw-case-heading"><h2 id="cw-case-heading">${cwEscape(c.issue_text||c.description||'Maintenance report')}</h2><p>${cwDescription(c)}</p></section>
    <section class="cw-panel" aria-labelledby="cw-timeline-heading"><h3 id="cw-timeline-heading"><i class="ph ph-pulse" aria-hidden="true"></i> Activity timeline</h3>${cwHistory(c)}</section>
    <section class="cw-panel" aria-labelledby="cw-notes-heading"><h3 id="cw-notes-heading"><i class="ph ph-chat-circle" aria-hidden="true"></i> Notes & comments</h3>${cwNotes(c)}</section>
    <section class="cw-panel cw-files-panel" aria-labelledby="cw-files-heading"><h3 id="cw-files-heading"><i class="ph ph-paperclip" aria-hidden="true"></i> Attachments <span>${(c.attachments||[]).length}</span></h3>${cwAttachments(c)}</section>
   </div>
   <aside class="cw-full-sidebar" aria-label="Case information and actions">
    <section class="cw-panel cw-case-details" aria-labelledby="cw-info-heading">${actions}<h3 id="cw-info-heading">Case details</h3>${!cwIsActive(c)?'<p class="cw-closed-hint">Closed · retained in Cases history.</p>':''}${cwInfo(c)}</section>
    <section class="cw-panel" aria-labelledby="cw-related-heading"><h3 id="cw-related-heading">Related cases</h3>${cwRelated(c)}</section>
   </aside>
  </div>`;
}
function cwOpenDialog(kind){
 const c=cwState.detail;if(cwState.writeBusy||(!['new'].includes(kind)&&(!c||!cwCanEdit(c))))return;
 const titles={new:'New maintenance case',edit:'Edit report',note:'Add note',close:'Close case'},overlay=document.getElementById('cw-dialog-overlay');
 cwState.dialog={kind,id:cwState.selected,revision:c?.revision,requestId:window.crypto?.randomUUID?crypto.randomUUID():Date.now().toString(36)+'-'+Math.random().toString(36).slice(2),returnFocus:document.activeElement};
 const input=(label,name,value,required,max=120)=>`<label>${label}<input name="${name}" value="${cwEscape(value)}" maxlength="${max}" ${required?'required':''}></label>`;
 const area=(label,name,value,required)=>`<label class="cw-form-wide">${label}<textarea name="${name}" rows="4" maxlength="4000" ${required?'required':''}>${cwEscape(value)}</textarea></label>`;
 const priority=value=>`<label>Priority<select name="priority" aria-label="Priority">${['normal','low','medium','high'].map(p=>`<option value="${p}" ${value===p?'selected':''}>${cwLabel(p)}</option>`).join('')}</select></label>`;
 let fields='';
 if(kind==='new')fields=`<p class="cw-form-wide cw-muted">Create a maintenance report assigned to you. It appears on the board immediately.</p>${input('Unit number','unit_number','',true)}<label>Equipment<select name="vehicle_type" aria-label="Equipment"><option value="truck">Truck</option><option value="trailer">Trailer</option><option value="reefer">Reefer</option></select></label>${input('Driver','driver','',true)}${priority('medium')}${input('Group','group','',false)}${input('Current location','location','',false,2000)}${area('Issue / description','issue','',true)}${area('Comments (optional)','comments','',false)}`;
 if(kind==='edit')fields=`${input('Issue','issue_text',c.issue_text||c.description,true,2000)}${priority(cwPriority(c))}${input('Location','location',c.location,false,2000)}${area('Description','description',c.full_description||c.issue_text||c.description,true)}`;
 if(kind==='note')fields=area('Note','text','',true);
 if(kind==='close')fields=`<div class="cw-form-wide"><p>Close <strong>${cwEscape(c.unit_number||'this case')}</strong>?</p><p class="cw-muted">It will be marked Done and removed from the active board. Its report, notes and history stay in Cases.</p></div>`;
 document.getElementById('cw-dialog-title').textContent=titles[kind];
 document.getElementById('cw-dialog-body').innerHTML=`<form id="cw-form" onsubmit="event.preventDefault();cwSubmitDialog(this)"><div class="cw-form-grid">${fields}</div><p id="cw-form-error" class="cw-form-error" role="alert" tabindex="-1" hidden></p><div class="cw-form-actions"><button type="button" id="cw-dialog-cancel" class="cw-secondary" onclick="cwDismissDialog()">Cancel</button><button type="submit" class="cw-primary">${kind==='close'?'Close case':kind==='new'?'Create case':kind==='note'?'Add note':'Save changes'}</button></div></form>`;
 overlay.classList.add('open');lockBodyScroll();overlay.querySelector(kind==='close'?'#cw-dialog-cancel':'input,textarea,button[type="submit"]')?.focus();
}
function cwDismissDialog(){if(cwState.writeBusy)return;const focus=cwState.dialog?.returnFocus;cwState.dialog=null;document.getElementById('cw-dialog-overlay').classList.remove('open');unlockBodyScroll();if(focus?.isConnected)focus.focus({preventScroll:true});}
async function cwSubmitDialog(form){
 if(cwState.writeBusy||!cwState.dialog)return;
 const dialog=cwState.dialog,values=Object.fromEntries(new FormData(form)),payload=dialog.kind==='new'?{...values,request_id:dialog.requestId}:dialog.kind==='edit'?{action:'edit',fields:values,revision:dialog.revision}:{...values,action:dialog.kind,revision:dialog.revision};
 const error=document.getElementById('cw-form-error'),submit=form.querySelector('[type="submit"]'),label=submit.textContent;
 error.hidden=true;cwState.writeBusy=true;activeRequests.get('case-workspace')?.abort();cwState.serial++;cwState.busy=false;cwState.detailSerial++;activeRequests.get('workspace-detail')?.abort();form.querySelectorAll('button,input,select,textarea').forEach(b=>b.disabled=true);document.querySelector('#cw-dialog-overlay .modal-close').disabled=true;submit.textContent='Saving…';
 try{
  const response=await apiFetch('/api/workspace/cases'+(dialog.kind==='new'?'':'/'+encodeURIComponent(dialog.id)),{method:dialog.kind==='new'?'POST':'PATCH',headers:{'Content-Type':'application/json','X-Workspace-CSRF':document.querySelector('meta[name="workspace-csrf"]').content},body:JSON.stringify(payload)}),result=await response.json();
  cwState.writeBusy=false;cwDismissDialog();
  if(dialog.kind==='close'){const next=cwData().find(c=>cwId(c)!==dialog.id);if(cwState.full)cwBack();cwCloseDetail();cwState.rows=cwState.rows.filter(c=>cwId(c)!==dialog.id);cwState.total=Math.max(0,cwState.total-1);cwRender();if(next&&!window.matchMedia('(max-width:760px)').matches)cwSelect(cwId(next));cwNotice('Case '+(result.case.unit_number||'')+' closed. Its history is available in Cases.');}
  else{cwNotice(dialog.kind==='new'?'Maintenance case created.':dialog.kind==='note'?'Note added.':'Report updated.');if(dialog.kind==='new'){await cwRefresh();cwSelect(cwId(result.case));}else await cwFetchDetail(dialog.id,false);}
  if(dialog.kind!=='new')cwRefresh();
 }catch(e){error.hidden=false;error.textContent=e.message||'Unable to save. Your entered values are preserved.';error.focus();}
 finally{cwState.writeBusy=false;if(form.isConnected){form.querySelectorAll('button,input,select,textarea').forEach(b=>b.disabled=false);submit.textContent=label;}document.querySelector('#cw-dialog-overlay .modal-close').disabled=false;}
}
document.addEventListener('keydown',e=>{if(e.key!=='Escape'||currentPage!=='case_workspace'||cwState.dialog||document.querySelector('.modal-overlay.open'))return;if(cwState.full)cwBack();else if(cwState.selected)cwCloseDetail();});
function cwExport(){const fields=['unit_number','issue_text','report_driver','agent','status','priority','location'];const quote=value=>'"'+String(value||'').replace(/^(\s*[=+@-]|[\t\r\n])/,"'$&").replace(/"/g,'""')+'"';const csv=[fields.join(',')].concat(cwData().map(c=>fields.map(k=>quote(c[k])).join(','))).join('\r\n');const url=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'})),a=document.createElement('a');a.href=url;a.download='kurtex-active-maintenance-reports.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
function cwPrint(){window.print();}
