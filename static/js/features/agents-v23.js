/* Agent directory: server-owned statistics and 20-case pagination. */
var k23AgentState={agents:[],selected:'',cases:[],query:'',caseQuery:'',status:'',sort:'newest',page:0,data:null,serial:0,loading:false};
function k23Role(role){return ({super_admin:'Manager',admin:'Manager',manager:'Manager',developer:'Developer',agent:'Agent'})[role]||'Agent';}
async function loadAgents(background){
 const el=document.getElementById('agents-content');if(!el)return;
 try{
  const r=await apiFetch('/api/agents','agent-directory');if(!r.ok)throw Error('Unable to load agents');
  k23AgentState.agents=await r.json();
  if(!el.querySelector('.k23-agents-shell'))el.innerHTML=`<div class="k23-agents-shell"><aside class="k23-agent-sidebar"><div class="k23-agent-list-head"><strong>Team directory</strong><span id="k23-agent-count"></span></div><div class="k23-agent-search"><i class="ph ph-magnifying-glass"></i><input id="k23-agent-search" aria-label="Search agents" placeholder="Search name or role" oninput="k23FilterAgentList(this.value)"><button type="button" onclick="k23ClearAgentSearch()" aria-label="Clear agent search"><i class="ph ph-x"></i></button></div><div id="k23-agent-list"></div></aside><section class="k23-agent-detail" id="k23-agent-detail"></section></div>`;
  if(!k23AgentState.agents.some(a=>String(a.id||a.name)===k23AgentState.selected))k23AgentState.selected=String(k23AgentState.agents[0]?.id||k23AgentState.agents[0]?.name||'');
  k23RenderAgentList();
  if(!k23AgentState.selected){document.getElementById('k23-agent-detail').innerHTML='<div class="empty-state">No agents are available yet.</div>';return;}
  await k23LoadAgentCases(!!background);
 }catch(e){if(e.name==='AbortError')return;if(!el.querySelector('.k23-agents-shell'))el.innerHTML='<div class="empty-state">Unable to load agents. <button type="button" onclick="loadAgents()">Retry</button></div>';}
}
function k23ClearAgentSearch(){document.getElementById('k23-agent-search').value='';k23FilterAgentList('');document.getElementById('k23-agent-search').focus();}
function k23FilterAgentList(q){k23AgentState.query=q.toLowerCase();k23RenderAgentList();}
function k23RenderAgentList(){
 const el=document.getElementById('k23-agent-list');if(!el)return;
 const list=k23AgentState.agents.filter(a=>(a.name+' '+(a.username||'')+' '+k23Role(a.role)).toLowerCase().includes(k23AgentState.query));
 document.getElementById('k23-agent-count').textContent=list.length;
 el.innerHTML=list.map(a=>{const id=String(a.id||a.name),selected=id===k23AgentState.selected;return `<button type="button" class="k23-agent-item ${selected?'selected':''}" aria-pressed="${selected}" data-id="${attr(id)}" onclick="k23SelectAgent(this.dataset.id)"><span class="k23-agent-initial">${h((a.name||'?')[0].toUpperCase())}</span><span class="k23-agent-identity"><strong>${h(a.name)}</strong><small>${h(k23Role(a.role))}</small></span><i class="ph ph-caret-right"></i></button>`;}).join('')||'<div class="empty-state">No matching agents.</div>';
}
function k23SelectAgent(id){
 k23AgentState.selected=String(id);k23AgentState.page=0;k23AgentState.status='';k23AgentState.caseQuery='';k23AgentState.sort='newest';k23AgentState.data=null;
 k23RenderAgentList();k23LoadAgentCases();
}
async function k23LoadAgentCases(quiet){
 const a=k23AgentState.agents.find(x=>String(x.id||x.name)===k23AgentState.selected);if(!a)return;
 const serial=++k23AgentState.serial;k23AgentState.loading=true;
 if(!quiet||!document.getElementById('k23-agent-table'))k23RenderAgentDetail();
 const params=new URLSearchParams({id:a.id||'',name:a.name,username:a.username||'',limit:20,offset:k23AgentState.page*20,status:k23AgentState.status,sort:k23AgentState.sort,search:k23AgentState.caseQuery});
 try{
  const r=await apiFetch('/api/agent?'+params,'agent-workspace');if(!r.ok)throw Error('Unable to load activity');const data=await r.json();if(serial!==k23AgentState.serial)return;
  if(data.filtered_total>0&&!data.cases?.length&&k23AgentState.page>0){k23AgentState.page=Math.max(0,Math.ceil(data.filtered_total/20)-1);k23LoadAgentCases();return;}
  k23AgentState.data=data;k23AgentState.cases=data.cases||[];k23AgentState.loading=false;k23RenderAgentDetail();
 }catch(e){if(serial!==k23AgentState.serial)return;k23AgentState.loading=false;if(e.name==='AbortError')return;const el=document.getElementById('k23-agent-table');if(el)el.innerHTML='<div class="empty-state">Unable to load case activity. <button type="button" onclick="k23LoadAgentCases()">Retry</button></div>';}
}
function k23AgentFilter(value){k23AgentState.status=value;k23AgentState.page=0;k23LoadAgentCases();}
function k23AgentSort(value){k23AgentState.sort=value;k23AgentState.page=0;k23LoadAgentCases();}
function k23AgentPage(delta){if(k23AgentState.loading)return;k23AgentState.page=Math.max(0,k23AgentState.page+delta);k23LoadAgentCases();}
var k23CaseSearchTimer;
function k23AgentSearch(value){k23AgentState.caseQuery=value;k23AgentState.page=0;clearTimeout(k23CaseSearchTimer);k23CaseSearchTimer=setTimeout(()=>k23LoadAgentCases(),300);}
function k23ClearCaseSearch(){clearTimeout(k23CaseSearchTimer);k23AgentState.caseQuery='';k23AgentState.page=0;k23LoadAgentCases();}
function k23RenderAgentDetail(){
 const el=document.getElementById('k23-agent-detail');if(!el)return;const a=k23AgentState.agents.find(x=>String(x.id||x.name)===k23AgentState.selected);if(!a)return;
 const d=k23AgentState.data,loading=k23AgentState.loading,total=d?.filtered_total||0,pages=Math.max(1,Math.ceil(total/20));
 const metrics=[['ph-clipboard-text','Total cases',d?.total??a.total],['ph-check-circle','Resolved',d?.done??a.done],['ph-hourglass','Active',d?.active],['ph-warning','Missed',d?.missed??a.missed],['ph-chart-line-up','Resolution rate',(d?.rate??a.rate??0)+'%'],['ph-timer','Avg. response',d?.avg_resp||a.avg_resp]];
 const markup=`<header class="k23-detail-header"><div class="k23-agent-large-avatar">${h((a.name||'?')[0].toUpperCase())}</div><div><span class="k23-role-label">${h(k23Role(a.role))}</span><h2>${h(a.name)}</h2><p>${h(a.username?'@'+a.username:'Team member')} · All-time performance</p></div><button type="button" class="btn secondary k23-view-profile" data-name="${attr(a.name)}" data-username="${attr(a.username||'')}" data-id="${attr(a.id||'')}" onclick="openAgentModal(this.dataset.name,this.dataset.username,this.dataset.id)"><i class="ph ph-user-circle"></i> View profile</button></header>
 <div class="k23-agent-metrics">${metrics.map(x=>`<div class="k23-agent-metric"><span><i class="ph ${x[0]}"></i>${x[1]}</span><strong>${h(x[2]??'—')}</strong></div>`).join('')}</div>
 <section class="k23-case-section"><div class="k23-case-toolbar"><div><strong>Case activity</strong><small>20 cases per page</small></div><div class="k23-case-filters"><label class="k23-case-search"><i class="ph ph-magnifying-glass"></i><input id="k23-case-search" aria-label="Search agent cases" placeholder="Search unit or issue" value="${attr(k23AgentState.caseQuery)}" oninput="if(!event.isComposing)k23AgentSearch(this.value)" oncompositionend="k23AgentSearch(this.value)"><button type="button" aria-label="Clear case search" onclick="k23ClearCaseSearch()"><i class="ph ph-x"></i></button></label><select aria-label="Filter agent cases" onchange="k23AgentFilter(this.value)">${[['','All statuses'],['active','Active'],['open','Open'],['assigned','Assigned'],['reported','Reported'],['done','Resolved'],['missed','Missed']].map(x=>`<option value="${x[0]}" ${k23AgentState.status===x[0]?'selected':''}>${x[1]}</option>`).join('')}</select><select aria-label="Sort agent cases" onchange="k23AgentSort(this.value)"><option value="newest" ${k23AgentState.sort==='newest'?'selected':''}>Newest first</option><option value="oldest" ${k23AgentState.sort==='oldest'?'selected':''}>Oldest first</option></select></div></div>
 <div id="k23-agent-table" aria-busy="${loading}">${loading?'<div class="loading">Loading case activity…</div>':`<div class="k23-table-scroll"><table class="k23-case-table"><thead><tr><th>Unit / Driver</th><th>Issue</th><th>Reported</th><th>Status</th><th>Action</th></tr></thead><tbody>${k23AgentState.cases.map(c=>`<tr><td><strong>${h(c.unit_number||'—')}</strong><small>${h(c.report_driver||c.driver||'')}</small></td><td>${h(c.issue_text||c.description||'Maintenance case')}</td><td>${h(c.opened||'—')}</td><td>${statusBadge(c.status||'open')}</td><td><button type="button" class="k23-open-case" data-id="${attr(c.full_id||c.id)}" onclick="openCase(this)">View <i class="ph ph-arrow-up-right"></i></button></td></tr>`).join('')||'<tr><td colspan="5" class="k23-no-cases">No cases match these filters.</td></tr>'}</tbody></table></div>`}</div>
 <div class="k23-case-footer"><span>${loading?'Loading…':`Showing ${total?k23AgentState.page*20+1:0}–${Math.min(total,(k23AgentState.page+1)*20)} of ${total}`}</span><div><button type="button" onclick="k23AgentPage(-1)" ${loading||!k23AgentState.page?'disabled':''}>Previous</button><span>${k23AgentState.page+1} / ${pages}</span><button type="button" onclick="k23AgentPage(1)" ${loading||!d?.has_more?'disabled':''}>Next</button></div></div></section>`;
 preserveInput('k23-case-search',()=>{el.innerHTML=markup;});
}

