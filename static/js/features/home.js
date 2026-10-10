/* Home is a daily briefing over the shared case store, not a second case workflow. */
var homeState = {data:null, insights:[], insightAt:0, insightGeneratedAt:'', insightBusy:false, insightFailed:false, serial:0};
function homeAllowed(page) {
  var pages=(document.body.dataset.allowedPages||'').split(',');
  return pages.includes(page)||(page==='case_workspace'&&pages.includes('cases'));
}
function homeTime(value, full) {
  var date=new Date(value);
  if(Number.isNaN(date.getTime()))return '';
  return date.toLocaleString('en-US',Object.assign({timeZone:'America/Chicago',hour:'numeric',minute:'2-digit',timeZoneName:'short'},full?{month:'short',day:'numeric'}:{}));
}
function homeWelcome() {
  var now=new Date(), hour=Number(new Intl.DateTimeFormat('en-US',{timeZone:'America/Chicago',hour:'numeric',hourCycle:'h23'}).format(now));
  document.getElementById('home-greeting').textContent=hour<12?'Good morning':hour<17?'Good afternoon':'Good evening';
  document.getElementById('home-day-label').textContent=now.toLocaleDateString('en-US',{timeZone:'America/Chicago',weekday:'long',month:'long',day:'numeric'});
}
function homeFindUnit() {
  showPage('fleet_intel');
  document.getElementById('issue-search-input')?.focus({preventScroll:true});
}
function homeReportMaintenance() { showPage('case_workspace');cwOpenDialog('new'); }
function homeOpenCase(button) { if(homeAllowed('cases'))openCase(button); }
function homeCases(filter) {
  if(!homeAllowed('cases'))return;
  clearTimeout(searchTimers.cases);
  currentFilter=filter;currentDateFilter='';
  ['cases-search','status-filter','cases-date-picker'].forEach(id=>document.getElementById(id).value='');
  document.getElementById('cases-date-clear').style.display='none';
  uiSyncSelect('status-filter');
  document.querySelectorAll('#page-cases .filter-tabs .tab-btn').forEach(b=>{
    var selected=filter==='all'&&b.textContent.trim()==='All';
    b.classList.toggle('active',selected);b.setAttribute('aria-pressed',String(selected));
  });
  uiRenderCaseFilters(null);
  showPage('cases');
}
function homeMetricHTML(m) {
  var delta=Number(m.reported)-Number(m.reported_previous);
  var comparison=delta===0?'Same as yesterday':Math.abs(delta)+' '+(delta>0?'more':'fewer')+' than yesterday';
  var items=[
    {id:'reported',icon:'ph-clipboard-text',label:'Reported today',value:m.reported,note:comparison,action:"homeCases('home_reported')"},
    {id:'active',icon:'ph-kanban',label:'Active maintenance',value:m.active,note:'Open reports in Workspace',action:"showPage('case_workspace')"},
    {id:'attention',icon:'ph-flag',label:'Needs attention',value:m.attention,note:Number(m.attention)?'Priority, missed, or waiting':'All clear in your queue',action:"homeCases('home_attention')"},
    {id:'resolved',icon:'ph-check-circle',label:'Resolved today',value:m.resolved,note:'Closed across the team',action:"homeCases('home_resolved')"}
  ];
  return items.map(x=>'<button type="button" class="stat-card home-metric" id="home-metric-'+x.id+'" data-tone="'+x.id+'" onclick="'+x.action+'"><span class="home-metric-top"><span class="stat-label">'+x.label+'</span><i class="ph '+x.icon+'" aria-hidden="true"></i></span><strong class="stat-value">'+Number(x.value||0).toLocaleString()+'</strong><span class="home-metric-note">'+h(x.note)+'</span><i class="ph ph-arrow-up-right home-metric-arrow" aria-hidden="true"></i></button>').join('');
}
function homeAge(hours) {
  if(hours==null)return 'Time unavailable';
  if(hours<1)return 'Less than 1h open';
  if(hours<24)return Math.floor(hours)+'h open';
  return Math.floor(hours/24)+'d open';
}
function renderHomeAttention(data) {
  var root=document.getElementById('home-attention-list');
  if(root.contains(document.activeElement))return; // Leave a keyboard user's current row in place.
  document.getElementById('home-attention-count').textContent=Number(data.metrics.attention).toLocaleString();
  document.getElementById('home-queue-note').textContent=data.metrics.attention>data.attention.length?'Showing the first '+data.attention.length+' of '+data.metrics.attention+' cases. Highest priority first.':data.attention.length?'Highest priority first · oldest within each group.':'';
  updateHTML(root,data.attention.length?data.attention.map(c=>{
    var name=c.agent&&c.agent!=='—'?c.agent:'Unassigned',id=c.full_id||c.id;
    return '<article class="home-attention-row" data-id="'+attr(id)+'" data-tone="'+attr(c.attention_tone)+'"><span class="home-queue-marker" aria-hidden="true"></span><div class="home-attention-copy"><div class="home-case-title"><strong>'+h(c.unit_number||c.driver||'Maintenance case')+'</strong><span class="home-reason">'+h(c.attention_reason)+'</span></div><p>'+h(c.issue_text||c.description||'Open case for details')+'</p><small>'+h(name)+'<span aria-hidden="true"> · </span>'+homeAge(c.age_hours)+'</small></div><button type="button" class="home-review" data-id="'+attr(id)+'" onclick="homeOpenCase(this)" aria-label="Review case '+attr(c.unit_number||c.driver||id)+'">Review <i class="ph ph-arrow-right" aria-hidden="true"></i></button></article>';
  }).join(''):homeEmpty('ph-check-circle','You’re all caught up','No priority, missed, unassigned, or aging maintenance cases.'));
  if(data.metrics.attention>data.attention.length)root.insertAdjacentHTML('beforeend','<button type="button" class="home-queue-more" onclick="homeCases(\'home_attention\')">View all '+Number(data.metrics.attention)+' cases <i class="ph ph-arrow-right"></i></button>');
}
function renderHomeActivity(events) {
  var root=document.getElementById('home-recent-activity');
  if(root.contains(document.activeElement))return;
  var kinds={reported:['ph-clipboard-text','Maintenance reported'],opened:['ph-plus-circle','Case opened'],assigned:['ph-user-check','Case assigned'],resolved:['ph-check-circle','Case resolved'],note:['ph-chat-circle-text','Note added'],updated:['ph-pencil-simple','Report updated']};
  updateHTML(root,events.length?events.map(event=>{
    var kind=kinds[event.kind]||kinds.updated;
    var person=(event.actor||(event.kind==='assigned'?event.detail:'')).trim();
    var initial=person?Array.from(person.trim())[0]:'';
    var detail=event.kind==='note'?event.detail:event.issue;
    return '<button type="button" class="home-event" data-id="'+attr(event.id)+'" data-case="'+attr(event.case_id)+'" data-kind="'+attr(event.kind)+'" onclick="homeOpenCase({dataset:{id:this.dataset.case}})"><span class="home-event-avatar" aria-hidden="true">'+(initial?h(initial.toUpperCase()):'<i class="ph '+kind[0]+'"></i>')+'</span><span class="home-event-copy"><strong>'+h(kind[1])+(event.unit?' <span>· '+h(event.unit)+'</span>':'')+'</strong><span class="home-event-detail">'+h(detail||'Open case for details')+'</span><small>'+(person?h(event.kind==='assigned'?'Assigned to '+person:person)+' · ':'')+'<time datetime="'+attr(event.at)+'">'+h(homeTime(event.at,true))+'</time></small></span><i class="ph ph-caret-right home-event-arrow" aria-hidden="true"></i></button>';
  }).join(''):homeEmpty('ph-coffee','A fresh start','New reports and recorded case updates will appear here.'));
}
function renderHomeBriefing() {
  var root=document.getElementById('home-ai-summary'),data=homeState.data;
  if(!data)return;
  var m=data.metrics,items=[];
  if(m.high_priority)items.push({icon:'ph-flag',title:'Priority check',text:m.high_priority+' high-priority case'+(m.high_priority===1?' is':'s are')+' still open.',filter:'home_attention'});
  if(m.unassigned)items.push({icon:'ph-user-circle-plus',title:'Waiting for a teammate',text:m.unassigned+' open case'+(m.unassigned===1?' needs':'s need')+' an assignment.',filter:'home_attention'});
  homeState.insights.forEach(x=>items.push({icon:x.type==='repair_followup'?'ph-arrow-counter-clockwise':'ph-chart-line-up',title:x.title,text:x.text,case_id:x.case_id,page:'kurtex_intelligence'}));
  if(!items.length)items.push({icon:'ph-check-circle',title:m.attention?'A few things to review':'Room to breathe',text:m.attention?m.attention+' case'+(m.attention===1?' needs':'s need')+' a closer look. Start with your attention queue.':m.resolved?'The team has closed '+m.resolved+' case'+(m.resolved===1?'':'s')+' today. No cases are flagged for attention.':'No cases are flagged for attention. New work will appear here as it arrives.',filter:'home_attention'});
  var summary=items.slice(0,3).map((x,i)=>{
    var action=x.case_id?'homeOpenCase({dataset:{id:this.dataset.caseId}})':x.filter?"homeCases('"+x.filter+"')":"showPage('"+x.page+"')";
    return '<button type="button" class="home-brief-item" data-id="brief-'+i+'" data-case-id="'+attr(x.case_id||'')+'" onclick="'+action+'"><span class="home-brief-icon"><i class="ph '+x.icon+'" aria-hidden="true"></i></span><span><strong>'+h(x.title)+'</strong><p>'+h(x.text)+'</p><small>'+h(x.case_id?'Review supporting case':x.filter?'Review matching cases':'Explore fleet evidence')+' <i class="ph ph-arrow-right" aria-hidden="true"></i></small></span></button>';
  }).join('');
  if(homeState.insightFailed&&homeAllowed('kurtex_intelligence'))summary+='<p class="home-summary-note">Fleet patterns could not refresh. '+(homeState.insights.length?'The previous findings remain visible.':'Your case briefing is still available.')+' <button type="button" onclick="loadHomeAISummary(true)">Retry patterns</button></p>';
  updateHTML(root,summary);
  document.getElementById('home-briefing-source').textContent=homeState.insights.length?'Case activity · fleet patterns checked '+homeTime(homeState.insightGeneratedAt,true):'Based on recorded case activity';
}
function homeTeamRows(period, fallback) {
  var agents=(stats['performance_'+period]||{}).agents;
  if(!agents)return fallback.length?fallback.slice(0,5).map(a=>'<div class="home-team-row"><span class="home-event-avatar" aria-hidden="true">'+h(Array.from(a.name||'?')[0].toUpperCase())+'</span><span><strong>'+h(a.name)+'</strong><small>'+Number(a.count||0)+' cases handled</small></span></div>').join(''):homeEmpty('ph-users-three','A quiet start','Team activity will appear as cases are handled.');
  return agents.length?agents.slice(0,5).map(a=>'<div class="home-team-row"><span class="home-event-avatar" aria-hidden="true">'+h(Array.from(a.name||'?')[0].toUpperCase())+'</span><span><strong>'+h(a.name)+'</strong><small>'+Number(a.active||0)+' active · '+Number(a.handled||0)+' handled</small></span><span class="home-team-total">'+Number(a.resolved||0)+' resolved</span></div>').join(''):homeEmpty('ph-users-three','A quiet start','Team activity will appear as cases are handled.');
}
async function loadHomeBriefing() {
  homeWelcome();
  if(!homeAllowed('cases')){
    ['stat-grid','home-attention-list','home-recent-activity','home-ai-summary','recent-table'].forEach(id=>updateHTML(document.getElementById(id),homeEmpty('ph-lock-key','Case access is unavailable','Your available tools are in the shortcuts above.')));
    document.getElementById('home-refresh-status').textContent='Your workspace';return;
  }
  var serial=++homeState.serial,status=document.getElementById('home-refresh-status');
  try{
    var response=await apiFetch('/api/home/briefing','home-briefing'),data=await response.json();
    if(serial!==homeState.serial)return;
    homeState.data=data;
    updateHTML(document.getElementById('stat-grid'),homeMetricHTML(data.metrics));
    var m=data.metrics;
    document.getElementById('home-welcome-detail').textContent=m.resolved?'The team has closed '+m.resolved+' case'+(m.resolved===1?'':'s')+' today. '+(m.attention?m.attention+' still need'+(m.attention===1?'s':'')+' attention.':'Your attention queue is clear.'):(m.attention?'Let’s keep things moving. '+m.attention+' case'+(m.attention===1?' needs':'s need')+' attention.':'A clear queue and a fresh start. You’re ready for what’s next.');
    renderHomeAttention(data);renderHomeActivity(data.activity);renderHomeBriefing();
    updateHTML(document.getElementById('recent-table'),caseTable(data.recent_cases));
    var updated=requestProblems.has('home-briefing')?'Showing saved snapshot · refresh needed':'Updated '+homeTime(data.generated_at,false);
    if(status.textContent!==updated)status.textContent=updated;
  }catch(e){
    if(e.name==='AbortError'||serial!==homeState.serial)return;
    status.textContent=homeState.data?'Update unavailable · showing previous data':'Briefing unavailable';
    if(!homeState.data){
      var error=homeEmpty('ph-cloud-slash','Your briefing couldn’t load','Check your connection, then try again.')+'<button type="button" class="home-retry" onclick="loadHomeBriefing()">Retry briefing</button>';
      ['stat-grid','home-attention-list','home-recent-activity','home-ai-summary','recent-table'].forEach(id=>updateHTML(document.getElementById(id),error));
    }
  }
}
async function loadHomeAISummary(force) {
  if(!homeAllowed('cases')||!homeAllowed('kurtex_intelligence')||homeState.insightBusy)return;
  if(!force&&Date.now()-homeState.insightAt<300000)return;
  homeState.insightBusy=true;homeState.insightAt=Date.now();
  var button=document.getElementById('home-briefing-refresh');button.disabled=true;button.setAttribute('aria-busy','true');
  try{
    var response=await apiFetch('/api/home/ai-summary','home-ai-summary'),data=await response.json();
    homeState.insights=data.insights||[];homeState.insightGeneratedAt=data.generated_at;homeState.insightFailed=false;
  }catch(e){if(e.name!=='AbortError')homeState.insightFailed=true;}
  finally{homeState.insightBusy=false;button.disabled=false;button.removeAttribute('aria-busy');renderHomeBriefing();}
}
async function homeRefreshBriefing() {
  await Promise.allSettled([loadHomeBriefing(),loadHomeAISummary(true)]);
}
['home-attention-list','home-recent-activity'].forEach(id=>document.getElementById(id).addEventListener('focusout',()=>setTimeout(()=>{
  if(homeState.data){renderHomeAttention(homeState.data);renderHomeActivity(homeState.data.activity);}
},0)));
homeWelcome();
if(!homeAllowed('cases')){
  document.querySelector('.home-attention-card .home-text-action').hidden=true;
  document.getElementById('home-briefing-refresh').hidden=true;
}
if(!document.querySelector('.home-quick-actions button'))document.querySelector('.home-quick-actions').hidden=true;
