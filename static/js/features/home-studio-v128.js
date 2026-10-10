/* Kurtex Home Studio v128 — one role-aware controller, live backend data and account settings. */
(function () {
'use strict';
const page = document.getElementById('page-overview');
if (!page) return;
const manager = page.dataset.dashboardRole === 'management';
const userKey = document.body.dataset.userId || 'user';
const layoutKey = 'kurtex-home-studio-v128-' + userKey + '-' + (manager ? 'manager' : 'agent');
const noteKey = 'kurtex-home-notes-v128-' + userKey;
const options = manager ? [
  ['metric_0','Total Cases',1,'ph-files','blue','Cases recorded in selected period'],
  ['metric_1','Open Cases',1,'ph-folder-open','red','Open cases from selected period'],
  ['metric_2','Resolved Cases',1,'ph-check-circle','green','Closed cases from selected period'],
  ['metric_3','Missed / Delayed',1,'ph-clock-countdown','orange','Cases marked missed in selected period'],
  ['metric_4','Active Agents',1,'ph-users-three','purple','Agents assigned open cases in selected period'],
  ['metric_5','Units in Reports',1,'ph-truck','teal','Distinct units with cases in selected period'],
  ['trend','Cases Trend',2,'ph-chart-line-up','blue'],
  ['status','Cases by Status',2,'ph-chart-donut','green'],
  ['top_agents','Top Agents',2,'ph-medal','purple'],
  ['recent','Recent Cases',3,'ph-list-checks','blue'],
  ['activity','Latest Activity',3,'ph-clock-counter-clockwise','orange'],
  ['attention','Needs Attention',2,'ph-warning-circle','red'],
  ['briefing','Fleet Briefing',2,'ph-sparkle','purple'],
  ['units','Recurring Units',2,'ph-truck-trailer','teal']
] : [
  ['banner','Daily Briefing',4,'ph-sun-horizon','blue'],
  ['assistant','AI Assistant',2,'ph-brain','purple'],
  ['metric_0','My Cases',1,'ph-files','blue','Cases assigned in the selected period'],
  ['metric_1','Active Cases',1,'ph-kanban','orange','Assigned cases still open'],
  ['metric_2','Needs Attention',1,'ph-warning-circle','red','Assigned cases needing action'],
  ['metric_3','Resolved Cases',1,'ph-check-circle','green','Assigned cases resolved in the period'],
  ['quick_access','Quick Access',2,'ph-squares-four','blue'],
  ['my_cases','My Active Cases',4,'ph-clipboard-text','blue'],
  ['activity','Recent Activity',2,'ph-activity','teal'],
  ['recommendations','AI Recommendations',3,'ph-lightbulb','green'],
  ['reminders','Notes & Reminders',3,'ph-note-pencil','orange']

];
const allowed = new Map(options.map(o => [o[0],o]));
const mobile = () => window.matchMedia('(max-width: 760px)').matches;
const mode = () => mobile() ? 'mobile' : 'desktop';
function initial() { return { order:options.map(o=>o[0]),hidden:[],sizes:Object.fromEntries(options.map(o=>[o[0],o[2]])),density:'comfortable'}; }
function normal(x) {
  const b=initial(); if (!x || typeof x!=='object') return b;
  if (Array.isArray(x.order)) b.order=[...new Set([...x.order.filter(id=>allowed.has(id)),...b.order])];
  if (Array.isArray(x.hidden)) b.hidden=[...new Set(x.hidden.filter(id=>allowed.has(id)))];
  if (Array.isArray(x.hiddenMetrics)) x.hiddenMetrics.forEach(i=>{const id='metric_'+i;if(allowed.has(id)&&!b.hidden.includes(id))b.hidden.push(id)});
  if (x.sizes && typeof x.sizes==='object') for(const id in b.sizes){const n=Number(x.sizes[id]);if(Number.isInteger(n)&&n>=1&&n<=6)b.sizes[id]=n;}
  b.density=x.density==='compact'?'compact':'comfortable'; return b;
}
let prefs={desktop:initial(),mobile:initial()};
try { const s=JSON.parse(localStorage.getItem(layoutKey)||'{}');for(const d of ['desktop','mobile'])if(s[d])prefs[d]=normal(s[d]); }
catch(e){}
const view={home:null,agent:null,stats:null,range:'day',charts:{},grid:null,settings:null,editing:false,dragged:null,saveTimer:null,refreshing:false,ready:false,notes:[],notesLoaded:false,notesSaveTimer:null,requestId:0};
const esc=s=>String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const number=v=>v==null?'—':Number(v).toLocaleString('en-US');
const icon=(name)=>'<i class="ph '+name+'" aria-hidden="true"></i>';
const dateTime=t=>{const d=new Date(t);return isNaN(d)?'':d.toLocaleString('en-US',{timeZone:'America/Chicago',month:'short',day:'numeric',hour:'numeric',minute:'2-digit'})};
const labels={day:'Today',week:'7 days',month:'30 days'};
const record=()=>prefs[mode()];
function notify(message,error){
 let x=document.getElementById('kh-toast');if(!x){x=document.createElement('div');x.id='kh-toast';x.setAttribute('role','status');document.body.append(x)}
 x.className='kh-toast'+(error?' kh-error':'');x.textContent=message;x.hidden=false;clearTimeout(notify.timer);notify.timer=setTimeout(()=>x.hidden=true,3200);
}
async function saveLayout(){
 const payload={desktop:prefs.desktop,mobile:prefs.mobile};
 try { localStorage.setItem(layoutKey,JSON.stringify(payload)); }
 catch(e){}
 const serial=++view.requestId;
 try{
   const res=await fetch('/api/preferences/home-grid',{method:'PUT',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({layout:payload})});
   if(!res.ok)throw new Error('Save failed');
   if(serial===view.requestId){const state=document.getElementById('kh-save-state');if(state)state.textContent='Saved to your account';}
 }catch(e){const state=document.getElementById('kh-save-state');if(state)state.textContent='Saved on this device';notify('Layout saved locally. Account sync unavailable.',true)}
}
function persist(){const state=document.getElementById('kh-save-state');if(state)state.textContent='Saving…';try{localStorage.setItem(layoutKey,JSON.stringify(prefs))}catch(e){}clearTimeout(view.saveTimer);view.saveTimer=setTimeout(saveLayout,550)}
async function loadLayout(){try{
 const res=await fetch('/api/preferences/home-grid',{credentials:'same-origin'});
 if(!res.ok)return;
 const data=await res.json();const layout=data.layout||{};
 const legacy=layout.desktop?.order?.includes('metrics')||layout.mobile?.order?.includes('metrics');
 if(legacy){ // migrate pre-v127 layout without discarding settings
   for(const d of ['desktop','mobile'])if(layout[d]){
     const x={...layout[d],order:layout[d].order.flatMap(id=>id==='metrics'?options.filter(o=>o[0].startsWith('metric_')).map(o=>o[0]):[id]),hidden:[...(layout[d].hidden||[])]};
     prefs[d]=normal(x);
   }
 }else{for(const d of ['desktop','mobile'])if(layout[d]&&Array.isArray(layout[d].order)&&layout[d].order.length)prefs[d]=normal(layout[d]);}
 try{localStorage.setItem(layoutKey,JSON.stringify(prefs))}catch(e){}
 applyLayout();
 }catch(e){}
}
const renderMetric=(id)=>{
 const i=Number(id.slice(7));const val=metricValue(i),m=allowed.get(id),trace=manager ? (view.stats?.['performance_trend_'+view.range]||[]) : [];
 const base=trace.map(x=>Number(x.total||0));
 const bars=base.length?base.slice(-8):[];
 const max=Math.max(1,...bars);
 const spark=bars.length?'<span class="kh-spark" aria-hidden="true">'+bars.map(v=>'<b style="height:'+Math.max(18,Math.round(v/max*100))+'%"></b>').join('')+'</span>':icon('ph-arrow-up-right');
 return '<button type="button" class="kh-metric-inner" title="'+esc(m[5])+'" data-metric-click="'+id+'">'
  +'<span class="kh-metric-head"><span class="kh-metric-icon">'+icon(m[3])+'</span><span class="kh-metric-trend">'+spark+'</span></span>'
  +'<strong class="kh-metric-number">'+number(val)+'</strong><span class="kh-metric-name">'+esc(m[1])+'</span>'
  +'<span class="kh-metric-sub">'+(manager&&i===4?'Agents handling open cases':manager&&i===5?'Units with recorded reports':labels[view.range]+' · View cases')+'</span></button>';
};
function metricValue(index){
 if(manager){const perf=view.stats?.['performance_'+view.range];const hp=view.stats?.home_periods?.[view.range]||{};
   if(!perf)return null;
   return [perf.total,perf.active,perf.resolved,perf.missed,hp.active_agent_count,hp.unit_count][index];
 }
 const a=view.agent?.metrics?.[view.range];return a?[a.total,a.active,a.attention,a.resolved][index]:null;
}
function empty(message,iconName='ph-magnifying-glass'){return '<div class="kh-empty">'+icon(iconName)+'<span>'+esc(message)+'</span></div>'}
function card(id){
 const item=allowed.get(id),isMetric=id.startsWith('metric_');
 const div=document.createElement('section');div.className='kh-widget kh-tone-'+item[4]+(isMetric?' kh-kpi':'');div.dataset.widget=id;div.dataset.size=String(item[2]);div.setAttribute('aria-label',item[1]);
 const drag='<div class="kh-draghandle" draggable="true" tabindex="0" role="button" aria-label="Move '+esc(item[1])+' widget" title="Drag to arrange">'+icon('ph-dots-six-vertical')+'</div>';
 const edit='<div class="kh-editor">'+drag+'<button type="button" class="kh-size" data-edit="size" title="Resize widget">'+icon('ph-arrows-out')+'</button><button type="button" data-edit="hide" title="Hide widget">'+icon('ph-eye-slash')+'</button></div>';
 div.innerHTML=edit+(isMetric?'<div class="kh-metric" data-content="'+id+'">'+renderMetric(id)+'</div>':'<div class="kh-card"><header class="kh-card-header"><span class="kh-heading"><span class="kh-heading-icon">'+icon(item[3])+'</span><span>'+esc(item[1])+'</span></span><div class="kh-header-actions" data-header="'+id+'"></div></header><div class="kh-card-body" data-content="'+id+'">'+empty('Loading data…','ph-circle-notch')+'</div></div>');
 div.draggable=false;return div;
}
function button(label,fn,ico){return '<button type="button" class="kh-quiet" data-action="'+fn+'" title="'+esc(label)+'">'+(ico?icon(ico):esc(label))+'</button>'}
function heads(id){const node=view.grid?.querySelector('[data-header="'+id+'"]');if(!node)return;
 let html='';if(id==='trend')html='<span class="kh-filter-hint">'+labels[view.range]+'</span>';
 else if(id==='status')html='<span class="kh-filter-hint">'+labels[view.range]+'</span>';
 else if(id==='top_agents'||id==='recent'||id==='activity'||id==='my_cases')html=button('View cases','cases','ph-arrow-up-right');
 else if(id==='briefing'||id==='recommendations')html=button('Refresh recommendations','refresh-briefing','ph-arrows-clockwise');
 else if(id==='reminders')html=button('Add note','add-note','ph-plus');
 else if(id==='units')html=button('Fleet Search','fleet','ph-arrow-up-right');
 else if(id==='attention')html=button('Review cases','cases','ph-arrow-up-right');
 node.innerHTML=html;
}
function managerTrend(){const trend=view.stats?.['performance_trend_'+view.range];if(!trend)return empty('Waiting for trend data','ph-chart-line-up');
 if(!trend.some(d=>Number(d.total)>0||Number(d.resolved)>0))return empty('No new or resolved cases in this period','ph-chart-line-up');
 return '<div class="kh-trend-intro"><span><b>'+number(trend.reduce((n,x)=>n+Number(x.total||0),0))+'</b> new cases</span><span class="kh-resolved-key">'+number(trend.reduce((n,x)=>n+Number(x.resolved||0),0))+' resolved</span></div><div class="kh-chart"><canvas id="kh-trend-chart" aria-label="New cases and resolved cases over time"></canvas></div>';
}
function statusHTML(){const x=view.stats?.home_periods?.[view.range]?.status_counts;if(!x)return empty('Waiting for case status data','ph-chart-donut');
 const labels=[['active','In progress','blue'],['resolved','Resolved','green'],['missed','Missed','red'],['other','Other','orange']];const total=Object.values(x).reduce((n,v)=>n+Number(v||0),0);
 if(!total)return empty('No cases during this period','ph-chart-donut');
 return '<div class="kh-donut-layout"><div class="kh-donut-chart"><canvas id="kh-status-chart" aria-label="Case status distribution"></canvas><div class="kh-donut-center"><b>'+number(total)+'</b><small>cases</small></div></div><div class="kh-donut-key">'+labels.map(([key,name,color])=>'<button type="button" data-action="cases" class="kh-status-row"><span class="kh-status-dot kh-dot-'+color+'"></span><span>'+name+'</span><strong>'+number(x[key]||0)+'</strong></button>').join('')+'</div></div>';
}
function agentsHTML(){const perf=view.stats?.['performance_'+view.range];if(!perf)return empty('Waiting for team data','ph-users-three');
 const rows=(perf.agents||[]).slice().sort((a,b)=>Number(b.handled||0)-Number(a.handled||0)).slice(0,5);if(!rows.length)return empty('No agents handled cases in this period','ph-users-three');
 const max=Math.max(1,...rows.map(x=>Number(x.handled||0)));
 return '<div class="kh-ranking">'+rows.map((a,i)=>'<button type="button" class="kh-agent-row" data-action="agents"><span class="kh-agent-place">'+(i+1)+'</span><span class="kh-avatar">'+esc(String(a.name||'?').split(' ').slice(0,2).map(w=>w[0]).join('').toUpperCase())+'</span><span class="kh-agent-info"><strong>'+esc(a.name||'Agent')+'</strong><span class="kh-agent-bar"><i style="width:'+Math.max(3,Math.round(Number(a.handled||0)/max*100))+'%"></i></span></span><span class="kh-agent-stat">'+number(a.handled||0)+'<small>cases</small></span></button>').join('')+'</div>';
}
function caseItems(cases){if(!cases?.length)return empty('No cases to show yet','ph-files');
 return '<div class="kh-case-list">'+cases.slice(0,8).map(c=>{const id=String(c.id||'');const status=String(c.status||'open').toLowerCase();const tone=['done','resolved','closed'].includes(status)?'green':['missed'].includes(status)?'red':['reported'].includes(status)?'orange':'blue';
 return '<button type="button" class="kh-case-row" data-case="'+esc(id)+'"><span class="kh-case-id">#'+esc(id)+'</span><span class="kh-case-title">'+esc(c.issue_text||c.description||'Maintenance case')+'<small>'+esc(c.unit_number||c.group_name||'Case record')+'</small></span><span class="kh-case-status kh-tag-'+tone+'">'+esc(status.replace(/_/g,' '))+'</span>'+icon('ph-caret-right')+'</button>';
 }).join('')+'</div>';
}
function activityHTML(){const rows=(manager?view.home?.activity:view.agent?.activity)||[];if(!rows.length)return empty('New case activity will appear here','ph-clock');
 const types={reported:'Reported',opened:'Opened',assigned:'Assigned',resolved:'Resolved',updated:'Updated',note:'Note added'};
 return '<div class="kh-activity">'+rows.slice(0,7).map(x=>'<button class="kh-event" type="button" data-case="'+esc(x.case_id)+'"><span class="kh-event-dot"></span><span><strong>'+esc(types[x.kind]||'Case updated')+(x.unit?' · '+esc(x.unit):'')+'</strong><small>'+esc(x.issue||x.detail||'View case details')+'</small></span><time>'+esc(dateTime(x.at))+'</time></button>').join('')+'</div>';
}
function attentionHTML(){const rows=view.home?.attention||[];if(!rows.length)return empty('No urgent cases in the attention queue','ph-check-circle');
 return '<div class="kh-attention-list">'+rows.slice(0,6).map(c=>'<button type="button" class="kh-attention-row" data-case="'+esc(c.id||'')+'"><span class="kh-attention-flag">'+icon('ph-warning')+'</span><span><strong>#'+esc(c.id||'')+' · '+esc(c.unit_number||'Unit not set')+'</strong><small>'+esc(c.issue_text||c.description||'Needs attention')+'</small></span>'+icon('ph-arrow-up-right')+'</button>').join('')+'</div>';
}
function briefingHTML(){const m=view.home?.metrics;
 if(!m)return empty('Your fleet briefing will appear here','ph-sparkle');
 const insights=(window.homeState?.insights||[]).slice(0,3);
 const notes=[];
 if(Number(m.high_priority))notes.push({title:'High-priority cases',detail:m.high_priority+' open cases are marked high priority',ic:'ph-warning-octagon'});
 if(Number(m.unassigned))notes.push({title:'Cases awaiting assignment',detail:m.unassigned+' open cases need an agent',ic:'ph-user-circle-plus'});
 insights.forEach(x=>notes.push({title:x.title||'Fleet finding',detail:x.text||'View related fleet intelligence',ic:'ph-sparkle'}));
 if(!notes.length)notes.push({title:'Your queue is under control',detail:Number(m.attention)?m.attention+' cases need attention':'No priority cases are currently flagged',ic:'ph-check-circle'});
 return '<div class="kh-briefing">'+notes.slice(0,4).map(n=>'<div class="kh-brief-item"><span>'+icon(n.ic)+'</span><div><strong>'+esc(n.title)+'</strong><p>'+esc(n.detail)+'</p></div></div>').join('')+'</div>';
}
function unitsHTML(){const arr=view.stats?.['top_problem_units_'+view.range]||[];
 if(!view.stats)return empty('Loading recurring unit data','ph-truck');
 if(!arr.length)return empty('No recurring unit reports for this period','ph-truck');
 const top=Math.max(1,...arr.map(x=>Number(x.count||0)));
 return '<div class="kh-unit-list">'+arr.slice(0,6).map(u=>'<button class="kh-unit-row" type="button" data-unit="'+esc(u.unit)+'" data-vtype="'+esc(u.vtype||'')+'"><span class="kh-unit-icon">'+icon('ph-truck')+'</span><span><strong>'+esc(u.unit)+'</strong><small>'+esc(u.vtype||'Unit')+'</small><i style="width:'+Math.max(5,Math.round(u.count/top*100))+'%"></i></span><b>'+number(u.count)+'</b></button>').join('')+'</div>';
}
function assistantHTML(){return '<div class="kh-assistant"><p>Search Kurtex knowledge, review cases, or ask for a repair summary.</p><div class="kh-suggestion"><button data-ask="Summarize my active cases">'+icon('ph-sparkle')+' Summarize my cases '+icon('ph-arrow-up-right')+'</button><button data-ask="Find similar maintenance cases">'+icon('ph-magnifying-glass')+' Find similar cases '+icon('ph-arrow-up-right')+'</button><button data-ask="Explain the latest repair notes">'+icon('ph-note')+' Explain repair notes '+icon('ph-arrow-up-right')+'</button></div><form id="kh-ask-form"><input type="text" placeholder="Ask Kurtex AI…" aria-label="Ask Kurtex AI" maxlength="1000" required><button aria-label="Send AI question" type="submit">'+icon('ph-arrow-up')+'</button></form></div>'}
function quickHTML(){const access=new Set((document.body.dataset.allowedPages||'').split(','));const actions=[['New case','new','ph-plus-circle','cases'],['Case Workspace','workspace','ph-kanban','cases'],['Fleet Search','fleet','ph-truck','fleet_intel'],['Parts Manual','parts','ph-book-open','parts_manual'],['AI Assistant','ai','ph-sparkle','ai_assistant'],['All Cases','cases','ph-files','cases']].filter(a=>!access.size||access.has(a[3]));return '<div class="kh-quick-list">'+actions.map(([label,action,ico])=>'<button data-action="'+action+'" type="button">'+icon(ico)+'<span>'+label+'</span>'+icon('ph-arrow-up-right')+'</button>').join('')+'</div>'}
function recommendationsHTML(){return briefingHTML()+'<button class="kh-more" data-action="ai" type="button">Ask a follow-up in AI Assistant '+icon('ph-arrow-right')+'</button>'}
function remindersHTML(){if(!view.notesLoaded)return empty('Loading notes…','ph-note-pencil');return '<div class="kh-notes">'+(view.notes.length?view.notes.map((n,i)=>'<div class="kh-note-row" data-note="'+i+'"><input type="checkbox" aria-label="Complete reminder" data-note-action="check" '+(n.done?'checked':'')+'><span class="'+(n.done?'kh-done':'')+'">'+esc(n.text)+'</span><button data-note-action="edit" title="Edit note">'+icon('ph-pencil')+'</button><button data-note-action="delete" title="Delete note">'+icon('ph-trash')+'</button></div>').join(''):empty('No reminders yet. Add a note to get started.','ph-note'))+'</div><form id="kh-note-form" class="kh-note-form"><input name="text" type="text" placeholder="Add a reminder…" aria-label="Add a reminder" maxlength="1500" required><button type="submit" aria-label="Save reminder">'+icon('ph-plus')+'</button></form>'}
function content(id){switch(id){case 'trend':return managerTrend();case 'status':return statusHTML();case 'top_agents':return agentsHTML();case 'recent':return caseItems(view.home?.recent_cases);case 'activity':return activityHTML();case 'attention':return attentionHTML();case 'briefing':return briefingHTML();case 'units':return unitsHTML();case 'my_cases':return view.agent?caseItems(view.agent.active_cases):empty('Loading your assigned cases…');case 'assistant':return assistantHTML();case 'recommendations':return recommendationsHTML();case 'reminders':return remindersHTML();case 'quick_access':return quickHTML();case 'banner':return '';default:return empty('No data available.')}}
function render(rebuild=false){if(!view.ready||!view.grid)return;
 const active=document.activeElement;
 for(const item of options){const id=item[0],el=view.grid.querySelector('[data-widget="'+id+'"]');if(!el)continue;
  if(id.startsWith('metric_')){const target=el.querySelector('[data-content]');const html=renderMetric(id);if(target.innerHTML!==html)target.innerHTML=html;continue;}
  if(id==='banner')continue;
  if(!rebuild && el.contains(active) && (active.matches('input,textarea')||active.closest('.kh-note-row')))continue;
  const target=el.querySelector('[data-content]');const html=content(id);
  // Update chart annotations without replacing the active canvas (no flashes on refresh).
  if((id==='trend'||id==='status') && target.querySelector('canvas') && html.includes('<canvas')){
    const temp=document.createElement('div');temp.innerHTML=html;
    const selectors=id==='trend'?['.kh-trend-intro']:['.kh-donut-key','.kh-donut-center'];
    selectors.forEach(selector=>{const current=target.querySelector(selector),next=temp.querySelector(selector);if(current&&next&&current.innerHTML!==next.innerHTML)current.innerHTML=next.innerHTML});
  }else if(target.innerHTML!==html)target.innerHTML=html;
  heads(id);
 }
 requestAnimationFrame(drawCharts);
}
function drawCharts(){if(!window.Chart||!manager||!view.grid)return;
 const trends=view.stats?.['performance_trend_'+view.range]||[];
 const trendCanvas=document.getElementById('kh-trend-chart');
 if(trendCanvas){const chart=view.charts.trend;const data={labels:trends.map(x=>x.date),datasets:[{label:'New cases',data:trends.map(x=>Number(x.total||0)),borderColor:'#4285f4',backgroundColor:'rgba(66,133,244,.18)',borderWidth:2,tension:.3,fill:true,pointRadius:0,pointHoverRadius:4},{label:'Resolved',data:trends.map(x=>Number(x.resolved||0)),borderColor:'#18b981',backgroundColor:'rgba(24,185,129,.08)',borderWidth:2,tension:.3,fill:true,pointRadius:0,pointHoverRadius:4}]};
  if(chart&&chart.canvas===trendCanvas){chart.data.labels=data.labels;chart.data.datasets.forEach((d,i)=>d.data=data.datasets[i].data);chart.update('none');}else{if(chart)chart.destroy();view.charts.trend=new Chart(trendCanvas,{type:'line',data,options:{animation:false,responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},plugins:{legend:{position:'bottom',labels:{usePointStyle:true,boxWidth:8,font:{size:11}}},tooltip:{padding:10}},scales:{x:{grid:{display:false},ticks:{maxTicksLimit:7,maxRotation:0,color:'#8290a4'}},y:{beginAtZero:true,ticks:{precision:0,color:'#8290a4'},grid:{color:'rgba(150,163,181,.12)'}}}}});}
 }else if(view.charts.trend){view.charts.trend.destroy();view.charts.trend=null;}
 const statuses=view.stats?.home_periods?.[view.range]?.status_counts||{};
 const canvas=document.getElementById('kh-status-chart');if(canvas){const dataset=[statuses.active||0,statuses.resolved||0,statuses.missed||0,statuses.other||0];const chart=view.charts.status;
  if(chart&&chart.canvas===canvas){chart.data.datasets[0].data=dataset;chart.update('none');}else{if(chart)chart.destroy();view.charts.status=new Chart(canvas,{type:'doughnut',data:{labels:['In progress','Resolved','Missed','Other'],datasets:[{data:dataset,backgroundColor:['#4387ff','#1dbf89','#fa5967','#ffb348'],borderWidth:0,hoverOffset:4}]},options:{animation:false,responsive:true,maintainAspectRatio:false,cutout:'77%',plugins:{legend:{display:false},tooltip:{padding:10}}}});}
 }else if(view.charts.status){view.charts.status.destroy();view.charts.status=null;}
}
function applyLayout(){if(!view.grid)return;const p=record();for(const id of p.order){const el=view.grid.querySelector('[data-widget="'+id+'"]');if(!el)continue;view.grid.append(el);el.hidden=p.hidden.includes(id);el.dataset.size=String(Math.min(mobile()?2:6,p.sizes[id]||allowed.get(id)[2]));}
 page.classList.toggle('kh-editing',view.editing);view.grid.classList.toggle('kh-compact',p.density==='compact');requestAnimationFrame(()=>{Object.values(view.charts).forEach(c=>{try{c?.resize()}catch(e){}})});
}
function setupDrag(){const grid=view.grid;let source=null,handleDown=null,pointerStart=null;
 grid.addEventListener('keydown',e=>{
  const grip=e.target.closest('.kh-draghandle');if(!view.editing||!grip||!['ArrowLeft','ArrowUp','ArrowRight','ArrowDown'].includes(e.key))return;
  const el=grip.closest('.kh-widget'),order=record().order,id=el?.dataset.widget,i=order.indexOf(id);
  const j=i+(['ArrowLeft','ArrowUp'].includes(e.key)?-1:1);
  if(i<0||j<0||j>=order.length)return;
  e.preventDefault();[order[i],order[j]]=[order[j],order[i]];persist();applyLayout();grip.focus();notify('Widget moved');
 });

 grid.addEventListener('dragstart',e=>{
  const el=e.target.closest('.kh-widget');if(!view.editing||!el||!e.target.closest('.kh-draghandle')){e.preventDefault();return}
  source=el;el.classList.add('kh-dragging');e.dataTransfer.effectAllowed='move';e.dataTransfer.setData('text/plain',el.dataset.widget);
 });
 grid.addEventListener('pointerdown',e=>{const handle=e.target.closest('.kh-draghandle');handleDown=handle?.closest('.kh-widget')||null;if(!handleDown||!view.editing)return;
  if(e.pointerType==='touch'){pointerStart={el:handleDown,x:e.clientX,y:e.clientY,origin:e.target};}
 });
 grid.addEventListener('dragover',e=>{if(!view.editing||!source)return;const candidate=e.target.closest('.kh-widget');if(!candidate||candidate===source)return;e.preventDefault();candidate.classList.add('kh-drop-hover')});
 grid.addEventListener('dragleave',e=>{const candidate=e.target.closest('.kh-widget');if(candidate&&!candidate.contains(e.relatedTarget))candidate.classList.remove('kh-drop-hover')});
 grid.addEventListener('drop',e=>{if(!source)return;e.preventDefault();const target=e.target.closest('.kh-widget');if(target&&source!==target)moveTo(source,target,e.clientX,e.clientY);clearDrag()});
 grid.addEventListener('dragend',clearDrag);
 function clearDrag(){grid.querySelectorAll('.kh-drop-hover,.kh-dragging').forEach(n=>n.classList.remove('kh-drop-hover','kh-dragging'));source=null;handleDown=null;pointerStart=null;}
 // Touch devices: move by holding the grip, then lifting over another card.
 grid.addEventListener('pointermove',e=>{if(!pointerStart||e.pointerType!=='touch'||!view.editing)return;if(Math.hypot(e.clientX-pointerStart.x,e.clientY-pointerStart.y)<8)return;e.preventDefault();pointerStart.el.classList.add('kh-dragging');}, {passive:false});
 grid.addEventListener('pointerup',e=>{if(!pointerStart)return;let hit=document.elementFromPoint(e.clientX,e.clientY)?.closest('.kh-widget');if(hit&&hit!==pointerStart.el)moveTo(pointerStart.el,hit,e.clientX,e.clientY);clearDrag()});
 grid.addEventListener('pointercancel',e=>{if(e.pointerType==='touch')clearDrag()});
 function moveTo(from,target,x,y){const rect=target.getBoundingClientRect();const before=(x<rect.left+rect.width/2&&Math.abs(y-(rect.top+rect.height/2))<rect.height/3)||y<rect.top+rect.height/2;
  grid.insertBefore(from,before?target:target.nextSibling);record().order=Array.from(grid.children).map(c=>c.dataset.widget);persist();applyLayout();buildSettingsBody();}
 // Native HTML5 drag starts from the card when a handle is held.
 grid.addEventListener('pointerup',()=>{handleDown=null});
}
function editorClick(e){const b=e.target.closest('[data-edit]');if(!b||!view.editing)return;const el=b.closest('.kh-widget'),id=el?.dataset.widget;if(!id)return;
 if(b.dataset.edit==='hide'){record().hidden=Array.from(new Set([...record().hidden,id]));persist();applyLayout();buildSettingsBody();notify('Widget hidden. Restore it in Customize Home.');}
 else if(b.dataset.edit==='size'){const max=mobile()?2:6;record().sizes[id]=(record().sizes[id]||1)%max+1;persist();applyLayout();buildSettingsBody();}
}
function setArrangeMode(enabled){
 view.editing=!!enabled;
 if(view.settings)view.settings.hidden=true;
 if(view.arrangeBar)view.arrangeBar.hidden=!view.editing;
 document.querySelectorAll('.kh-arrange-toggle').forEach(btn=>{
   btn.classList.toggle('is-active',view.editing);
   btn.setAttribute('aria-pressed',String(view.editing));
   btn.setAttribute('title',view.editing?'Finish arranging Home':'Arrange Home widgets');
   const label=btn.querySelector('.kh-toggle-text');if(label)label.textContent=view.editing?'Finish arranging':'Arrange';
 });
 applyLayout();
}
function settings(){const overlay=document.createElement('div');overlay.className='kh-overlay';overlay.hidden=true;overlay.innerHTML='<div class="kh-settings" role="dialog" aria-modal="true" aria-label="Customize Home"><header><div><span class="kh-eyebrow">YOUR WORKSPACE</span><h2>Customize Home</h2><p>Reorder, resize and show the widgets you use most.</p></div><button type="button" class="kh-settings-close" title="Close">'+icon('ph-x')+'</button></header><div class="kh-settings-scroll"><section class="kh-settings-intro"><span>'+icon('ph-hand-grabbing')+'</span><div><strong>Arrange your dashboard</strong><p>Turn on Arrange mode to drag or resize widgets directly on Home.</p></div></section><label class="kh-arrange-setting" for="kh-arrange-switch"><span class="kh-switch-copy"><strong>Enable layout editing</strong><small>Move, resize or hide cards on the page</small></span><input type="checkbox" id="kh-arrange-switch" role="switch" aria-label="Enable layout editing"><span class="kh-switch-track" aria-hidden="true"></span></label><h3>Dashboard widgets <small id="kh-visible-count"></small></h3><div id="kh-settings-items"></div><h3>Display density</h3><div class="kh-density"><button data-density="comfortable" type="button">Comfortable</button><button data-density="compact" type="button">Compact</button></div><p id="kh-save-state" aria-live="polite">Ready</p></div><footer><button type="button" class="kh-reset">Reset layout</button><button type="button" class="kh-arrange">Arrange on page</button><button type="button" class="kh-finish">Done</button></footer></div>';document.body.append(overlay);view.settings=overlay;
 overlay.querySelector('#kh-arrange-switch').addEventListener('change',e=>setArrangeMode(e.target.checked));overlay.querySelector('.kh-settings-close').addEventListener('click',closeSettings);overlay.querySelector('.kh-finish').addEventListener('click',()=>setArrangeMode(false));overlay.querySelector('.kh-arrange').addEventListener('click',beginArrange);
 overlay.querySelector('.kh-reset').addEventListener('click',()=>{if(!confirm('Reset this device layout to defaults?'))return;prefs[mode()]=initial();persist();applyLayout();buildSettingsBody();notify('Default Home layout restored')});
 overlay.addEventListener('click',e=>{if(e.target===overlay)closeSettings();const label=e.target.closest('[data-density]');if(label){record().density=label.dataset.density;persist();applyLayout();buildSettingsBody()}});
 overlay.querySelector('#kh-settings-items').addEventListener('click',e=>{
   const btn=e.target.closest('[data-move]');if(!btn)return;
   const order=record().order,id=btn.dataset.moveId,i=order.indexOf(id),j=i+(btn.dataset.move==='up'?-1:1);
   if(i<0||j<0||j>=order.length)return;
   [order[i],order[j]]=[order[j],order[i]];persist();applyLayout();buildSettingsBody();
   view.settings.querySelector('[data-move-id="'+id+'"][data-move="'+btn.dataset.move+'"]')?.focus();
 });
 overlay.querySelector('#kh-settings-items').addEventListener('change',e=>{const cb=e.target.closest('[data-visible]'),select=e.target.closest('[data-width]');if(cb){const id=cb.dataset.visible;record().hidden=record().hidden.filter(x=>x!==id);if(!cb.checked)record().hidden.push(id)}else if(select){record().sizes[select.dataset.width]=Number(select.value)}else return;persist();applyLayout();buildSettingsBody()});
}
function buildSettingsBody(){if(!view.settings)return;const p=record();view.settings.querySelector('#kh-visible-count').textContent=options.length-p.hidden.length+' of '+options.length+' visible';
 const box=view.settings.querySelector('#kh-settings-items');box.innerHTML=p.order.map(id=>{const x=allowed.get(id);if(!x)return '';const width=p.sizes[id]||x[2],max=mobile()?2:6;
 return '<div class="kh-setting-item"><label><input data-visible="'+id+'" type="checkbox" '+(!p.hidden.includes(id)?'checked':'')+'><span class="kh-setting-icon kh-tone-'+x[4]+'">'+icon(x[3])+'</span><span>'+esc(x[1])+'</span></label><div class="kh-row-actions"><button type="button" data-move="up" data-move-id="'+id+'" aria-label="Move '+esc(x[1])+' earlier" title="Move earlier">'+icon('ph-arrow-up')+'</button><button type="button" data-move="down" data-move-id="'+id+'" aria-label="Move '+esc(x[1])+' later" title="Move later">'+icon('ph-arrow-down')+'</button></div><select data-width="'+id+'" aria-label="Width of '+esc(x[1])+'">'+Array.from({length:max},(_,i)=>'<option value="'+(i+1)+'" '+(width===i+1?'selected':'')+'>'+(mobile()?(i===0?'Half':'Full'):['1 / 6','2 / 6','3 / 6','4 / 6','5 / 6','Full width'][i])+'</option>').join('')+'</select></div>'}).join('');
 view.settings.querySelectorAll('[data-density]').forEach(b=>b.classList.toggle('is-active',p.density===b.dataset.density));
}
function beginArrange(){setArrangeMode(true);notify('Arrange mode on: drag a grip, resize or hide any card.');}
function openSettings(){buildSettingsBody();view.settings.querySelector('#kh-arrange-switch').checked=view.editing;view.settings.hidden=false;if(view.arrangeBar)view.arrangeBar.hidden=true;view.settings.querySelector('.kh-settings-close').focus();}
function closeSettings(){view.settings.hidden=true;if(view.arrangeBar)view.arrangeBar.hidden=!view.editing;applyLayout();}
function toggleArrange(){setArrangeMode(!view.editing);}
function switchRange(range){if(!['day','week','month'].includes(range))return;view.range=range;page.querySelectorAll('.kx-period [data-range],.kh-agent-period [data-range]').forEach(b=>{b.classList.toggle('active',b.dataset.range===range);b.setAttribute('aria-pressed',String(b.dataset.range===range))});
 if(typeof window.setHomeRange==='function'){window.setHomeRange('agents',range);window.setHomeRange('units',range)}
 render();}
function updateFromHome(){view.home=window.homeState?.data||null;render()}
function updateFromStats(){view.stats=typeof window.stats==='object'?window.stats:null;render()}
async function getAgent(){if(manager)return;try{const r=await fetch('/api/home/agent-dashboard',{credentials:'same-origin'});if(!r.ok)throw new Error('Agent data unavailable');view.agent=await r.json();render();}catch(e){view.agent=null;render();}}
async function getNotes(){if(manager)return;try{const local=JSON.parse(localStorage.getItem(noteKey)||'[]');view.notes=Array.isArray(local)?local:[]}catch(e){view.notes=[]}
 if(!view.notes.length){try{const previous=JSON.parse(localStorage.getItem('kurtex-home-reminders')||'[]');if(Array.isArray(previous))view.notes=previous.filter(n=>n&&n.text).map((n,i)=>({id:'legacy-'+i,text:String(n.text).slice(0,1500),done:!!n.done}));}catch(e){}}
 view.notesLoaded=true;render();try{const res=await fetch('/api/preferences/home-notes',{credentials:'same-origin'});if(res.ok){const data=await res.json();if(Array.isArray(data.notes)&&data.notes.length)view.notes=data.notes;else if(view.notes.length)saveNotes();localStorage.setItem(noteKey,JSON.stringify(view.notes));render()}}catch(e){}
}
async function saveNotes(){localStorage.setItem(noteKey,JSON.stringify(view.notes));clearTimeout(view.notesSaveTimer);view.notesSaveTimer=setTimeout(async()=>{try{const res=await fetch('/api/preferences/home-notes',{method:'PUT',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({notes:view.notes})});if(!res.ok)throw Error('Note sync failed')}catch(e){notify('Notes saved on this device. Account sync unavailable.',true)}},350)}
function changeNote(action,index){if(!Number.isInteger(index)||!view.notes[index])return;
 if(action==='check')view.notes[index].done=!view.notes[index].done;
 else if(action==='delete'){if(!confirm('Delete this reminder?'))return;view.notes.splice(index,1)}
 else if(action==='edit'){const text=prompt('Edit reminder',view.notes[index].text);if(text===null)return;if(!text.trim())return notify('Enter a note before saving.',true);view.notes[index].text=text.trim().slice(0,1500)}
 else return;saveNotes();render(true);notify(action==='delete'?'Reminder deleted':'Reminder updated');}
function navigate(action){try{switch(action){case 'new':window.homeReportMaintenance();break;case 'workspace':window.showPage('case_workspace');break;case 'fleet':window.homeFindUnit();break;case 'parts':window.showPage('parts_manual');break;case 'ai':window.showPage('ai_assistant');break;case 'agents':if(manager)window.showPage('agents');break;case 'cases':window.homeCases('all');break;case 'refresh-briefing':window.loadHomeAISummary(true).then(render);break;case 'add-note':view.grid.querySelector('#kh-note-form input')?.focus();break;}}catch(e){console.warn('Home action unavailable',e)}}
function clickHandler(e){const edit=e.target.closest('[data-edit]');if(edit){editorClick(e);return}
 const note=e.target.closest('[data-note-action]');if(note){changeNote(note.dataset.noteAction,Number(note.closest('[data-note]')?.dataset.note));return}
 const unit=e.target.closest('[data-unit]');if(unit){window.openUnitModal?.(unit.dataset.unit,unit.dataset.vtype);return}
 const c=e.target.closest('[data-case]');if(c){window.homeOpenCase?.({dataset:{id:c.dataset.case}});return}
 const ask=e.target.closest('[data-ask]');if(ask){window.kxAsk?.(ask.dataset.ask);return}
 const a=e.target.closest('[data-action]');if(a){navigate(a.dataset.action);return}
 const m=e.target.closest('[data-metric-click]');if(m){if(manager&&m.dataset.metricClick==='metric_4')navigate('agents');else if(manager&&m.dataset.metricClick==='metric_5')navigate('fleet');else navigate('cases')}
}
function submitHandler(e){if(e.target.id==='kh-note-form'){e.preventDefault();const input=e.target.querySelector('input'),text=input?.value.trim();if(!text)return;view.notes.push({id:String(Date.now())+'-'+Math.random().toString(36).slice(2,7),text:text.slice(0,1500),done:false});saveNotes();render(true);notify('Reminder added')}
 else if(e.target.id==='kh-ask-form'){e.preventDefault();const text=e.target.querySelector('input')?.value.trim();if(text)window.kxAsk?.(text)}}
function mount(){if(view.ready)return;
 const grid=document.createElement('div');grid.id='kh-home-grid';grid.className='kh-grid';view.grid=grid;
 const sources=[...page.children].filter(x=>x.matches('.kx-manager-stats,.kx-manager-charts,.kx-manager-bottom,.kx-extra-widgets,.kx-agent-grid'));
 const anchor=manager?page.querySelector('.kx-manager-stats'):page.querySelector('.kx-agent-grid');if(anchor)page.insertBefore(grid,anchor);else page.append(grid);
 for(const item of options)grid.append(card(item[0]));
 if(!manager){const banner=page.querySelector('.kx-agent-banner');const dest=grid.querySelector('[data-content="banner"]');if(banner&&dest){dest.innerHTML='';dest.append(banner);banner.classList.add('kh-banner')}
 const toolbar=document.createElement('div');toolbar.className='kh-agent-toolbar';toolbar.innerHTML='<div class="kh-agent-period"><button data-range="day" class="active">Today</button><button data-range="week">7 days</button><button data-range="month">30 days</button></div><button class="kh-customize" type="button">'+icon('ph-sliders-horizontal')+' Customize</button>';page.insertBefore(toolbar,grid);
 toolbar.querySelector('.kh-customize').onclick=openSettings;const arrange=document.createElement('button');arrange.type='button';arrange.className='kh-arrange-toggle';arrange.setAttribute('aria-pressed','false');arrange.innerHTML=icon('ph-hand-grabbing')+' <span class="kh-toggle-text">Arrange</span>';toolbar.append(arrange);arrange.onclick=toggleArrange;toolbar.querySelectorAll('[data-range]').forEach(b=>b.onclick=()=>switchRange(b.dataset.range));
 }
 sources.forEach(x=>{
   // Legacy containers remain mounted for API updates, but are never displayed.
   // An inline !important declaration wins against old #stat-grid stylesheets.
   x.classList.add('kh-legacy-archived');
   x.setAttribute('aria-hidden','true');
   x.style.setProperty('display','none','important');
   x.style.setProperty('visibility','hidden','important');
   x.style.setProperty('height','0px','important');
   x.style.setProperty('overflow','hidden','important');
 });
 const heroButton=page.querySelector('.kx-manager-hero .kx-icon-btn');if(heroButton){heroButton.onclick=openSettings;heroButton.setAttribute('aria-label','Home settings');heroButton.setAttribute('title','Home settings');const toggle=document.createElement('button');toggle.type='button';toggle.className='kh-arrange-toggle';toggle.setAttribute('aria-pressed','false');toggle.innerHTML=icon('ph-hand-grabbing')+' <span class="kh-toggle-text">Arrange</span>';heroButton.before(toggle);toggle.onclick=toggleArrange;}
 // Existing theme, navigation and global date/time bar remain untouched.
 page.classList.add('kh-ready');view.ready=true;
 grid.addEventListener('click',clickHandler);grid.addEventListener('submit',submitHandler);
 const legacyClick=window.kxPeriod;
 window.kxPeriod=function(b){switchRange(b.dataset.range)};
 window.openHomeSettings=openSettings;
 setupDrag();settings();const bar=document.createElement('div');bar.className='kh-arrange-bar';bar.hidden=true;bar.innerHTML='<span>'+icon('ph-hand-grabbing')+' Arrange Home</span><button type="button" data-action="settings">Widget settings</button><button type="button" data-action="done">Done</button>';document.body.append(bar);view.arrangeBar=bar;bar.addEventListener('click',e=>{const btn=e.target.closest('button');if(!btn)return;if(btn.dataset.action==='settings')openSettings();else setArrangeMode(false)});applyLayout();render(true);
 const resizeObserver=window.ResizeObserver?new ResizeObserver(()=>{Object.values(view.charts).forEach(c=>{try{c?.resize()}catch(e){}})}):null;
 if(resizeObserver)resizeObserver.observe(grid);
 if(window.homeState?.data)updateFromHome();if(typeof window.stats==='object'&&window.stats?.today)updateFromStats();
 const oldBrief=window.loadHomeBriefing;
 if(typeof oldBrief==='function')window.loadHomeBriefing=async function(...args){const out=await oldBrief.apply(this,args);updateFromHome();if(!manager)await getAgent();return out;};
 const oldStats=window.loadStats;
 if(typeof oldStats==='function')window.loadStats=async function(...args){const out=await oldStats.apply(this,args);updateFromStats();return out;};
 const oldAI=window.loadHomeAISummary;
 if(typeof oldAI==='function')window.loadHomeAISummary=async function(...args){const out=await oldAI.apply(this,args);render();return out;};
 // Legacy Chart instances refer to hidden canvases. The Home Studio charts own visible canvases.
 window.kxRenderCharts=function(){};
 loadLayout();getNotes();getAgent();
 // First read on pages opened without a full refresh and where global polling started before mount.
 if(!view.home && typeof window.loadHomeBriefing==='function')window.loadHomeBriefing();
 if(!view.stats?.today && typeof window.loadStats==='function')window.loadStats();
 document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!view.settings.hidden)closeSettings();});
 window.addEventListener('resize',()=>{const d=mode();if(view.lastMode!==d){view.lastMode=d;applyLayout();buildSettingsBody()}},{passive:true});
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',mount);else mount();
})();
