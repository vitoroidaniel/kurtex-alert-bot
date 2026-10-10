/* Shared shell and filter affordances. Filter queries remain in memory. */
var uiCaseTotal=null;
function uiSyncSelect(id){const el=document.getElementById(id);if(el&&typeof syncKurtexSelect==='function')syncKurtexSelect(el);}
function uiUpdateShell(page){
 document.title=(titles[page]||'Kurtex')+' · Kurtex';
 document.querySelectorAll('.topbar-report-action,.topbar-print-action,.topbar-export-action,.topbar-refresh-action').forEach(b=>b.hidden=page!=='overview');
 document.querySelector('.topbar-right').hidden=page==='case_workspace';
 if(page==='case_workspace'&&typeof closeNotifications==='function')closeNotifications(false);
 if(page==='cases')document.getElementById('page-title').textContent='Case history';
 if(page==='agents')document.getElementById('page-title').textContent='Agents';
 document.querySelectorAll('.nav-item[data-page],[data-mobile-page]').forEach(el=>{if(el.dataset.page===page||el.dataset.mobilePage===page)el.setAttribute('aria-current','page');else el.removeAttribute('aria-current');});
}
function uiPrintCurrentPage(){if(currentPage==='case_workspace')cwPrint();else window.print();}
function uiExportCurrentPage(){if(currentPage==='case_workspace')cwExport();else window.location.assign('/api/export');}
function uiCaseSearch(){const el=document.getElementById('cases-search');document.getElementById('cases-search-clear').hidden=!el.value;uiRenderCaseFilters(null);onSearch('cases');}
function uiClearCaseSearch(){document.getElementById('cases-search').value='';document.getElementById('cases-search-clear').hidden=true;clearTimeout(searchTimers.cases);loadCases();document.getElementById('cases-search').focus();}
function uiMissedSearch(){document.getElementById('missed-search-clear').hidden=!document.getElementById('missed-search').value;onSearch('missed');}
function uiClearMissedSearch(){const input=document.getElementById('missed-search');input.value='';document.getElementById('missed-search-clear').hidden=true;clearTimeout(searchTimers.missed);loadMissed();input.focus();}
function uiRenderCaseFilters(total){
 if(total!==undefined)uiCaseTotal=total;
 const root=document.getElementById('cases-filter-summary');if(!root)return;
 const query=document.getElementById('cases-search').value.trim(),status=document.getElementById('status-filter').value,period={today:'Today',week:'This week',active:'Active cases',all:'All time',home_reported:'Maintenance reported today',home_resolved:'Resolved today',home_attention:'Needs attention'}[currentFilter];
 const chips=[currentDateFilter||period||'All time'];if(status)chips.push(status==='done'?'Resolved':status);if(query)chips.push('Search: '+query);
 root.innerHTML='<div class="ui-filter-chips">'+chips.map(label=>'<span class="ui-filter-chip">'+h(label)+'</span>').join('')+'</div><span class="ui-result-count">'+(uiCaseTotal===null?'Updating results…':Number(uiCaseTotal).toLocaleString()+' matching cases')+'</span><button type="button" class="ui-clear-filters" onclick="uiResetCaseFilters()">Clear filters</button>';
 document.getElementById('cases-search-clear').hidden=!query;
}
function uiResetCaseFilters(){
 clearTimeout(searchTimers.cases);currentFilter='all';currentDateFilter='';
 document.getElementById('cases-search').value='';document.getElementById('status-filter').value='';document.getElementById('cases-date-picker').value='';document.getElementById('cases-date-clear').style.display='none';uiSyncSelect('status-filter');
 document.querySelectorAll('#page-cases .filter-tabs .tab-btn').forEach(b=>{const active=b.textContent.trim()==='All';b.classList.toggle('active',active);b.setAttribute('aria-pressed',String(active));});uiRenderCaseFilters(null);loadCases();
}
function cwClearSearch(){document.getElementById('cw-search').value='';cwRender();document.getElementById('cw-search').focus();}
function cwClearFilters(){document.getElementById('cw-search').value='';['cw-status','cw-priority-filter','cw-equipment-filter','cw-sort'].forEach(id=>{document.getElementById(id).value=id==='cw-sort'?'newest':'';uiSyncSelect(id);});cwRender();}
function uiWorkspaceFilters(rows){
 const root=document.getElementById('cw-filter-summary');if(!root)return;
 const q=document.getElementById('cw-search').value.trim(),status=document.getElementById('cw-status').value,priority=document.getElementById('cw-priority-filter').value,equipment=document.getElementById('cw-equipment-filter').value,sort=document.getElementById('cw-sort').value;
 const chips=[];if(status)chips.push(cwLabel(status));if(priority)chips.push(cwLabel(priority)+' priority');if(equipment)chips.push(cwLabel(equipment));if(q)chips.push('Search: '+q);if(sort!=='newest')chips.push(sort==='oldest'?'Oldest first':'Highest priority');
 root.innerHTML='<div class="ui-filter-chips"><span class="ui-filter-neutral">Active maintenance reports</span>'+chips.map(x=>'<span class="ui-filter-chip">'+h(x)+'</span>').join('')+'</div><span class="ui-result-count">'+rows.length+' matching · closed reports stay in Cases</span>'+(chips.length?'<button type="button" class="ui-clear-filters" onclick="cwClearFilters()">Clear filters</button>':'');
 const count=document.getElementById('cw-filter-count'),n=[status,priority,equipment].filter(Boolean).length;count.hidden=!n;count.textContent=n;
 document.getElementById('cw-search-clear').hidden=!q;
 preferences.set('kurtex-workspace-group',document.getElementById('cw-group').value);
}
const uiWorkspaceGroup=preferences.get('kurtex-workspace-group');
if(['agent','status','priority','vehicle_type'].includes(uiWorkspaceGroup))document.getElementById('cw-group').value=uiWorkspaceGroup;
