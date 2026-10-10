/* Shared shell and filter affordances. Filter queries remain in memory. */
var uiCaseTotal=null;
function uiSyncSelect(id){const el=document.getElementById(id);if(el&&typeof syncKurtexSelect==='function')syncKurtexSelect(el);}
function uiUpdateShell(page){
 document.title=(titles[page]||'Kurtex')+' · Kurtex';
 const copy={cases:'Browse, search and filter your case history.',case_workspace:'Organize fleet cases and inspect details without leaving the board.',agents:'Choose a team member to review performance and case activity.',leaderboard:'Compare agent activity and see where cases stand.'};
 if(copy[page])document.getElementById('page-description').textContent=copy[page];
 document.querySelectorAll('.nav-item[data-page],[data-mobile-page]').forEach(el=>{if(el.dataset.page===page||el.dataset.mobilePage===page)el.setAttribute('aria-current','page');else el.removeAttribute('aria-current');});
}
function uiCaseSearch(){const el=document.getElementById('cases-search');document.getElementById('cases-search-clear').hidden=!el.value;uiRenderCaseFilters(null);onSearch('cases');}
function uiClearCaseSearch(){document.getElementById('cases-search').value='';document.getElementById('cases-search-clear').hidden=true;clearTimeout(searchTimers.cases);loadCases();document.getElementById('cases-search').focus();}
function uiRenderCaseFilters(total){
 if(total!==undefined)uiCaseTotal=total;
 const root=document.getElementById('cases-filter-summary');if(!root)return;
 const query=document.getElementById('cases-search').value.trim(),status=document.getElementById('status-filter').value,period={today:'Today',week:'This week',active:'Active cases',all:'All time'}[currentFilter];
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
function cwClearFilters(){document.getElementById('cw-search').value='';document.getElementById('cw-status').value='';uiSyncSelect('cw-status');cwRender();}
function uiWorkspaceFilters(rows){
 const root=document.getElementById('cw-filter-summary');if(!root)return;
 const q=document.getElementById('cw-search').value.trim(),status=document.getElementById('cw-status').value;
 const chips=[];if(status)chips.push(status==='done'?'Resolved':status);if(q)chips.push('Search: '+q);
 root.innerHTML='<div class="ui-filter-chips">'+(chips.length?chips.map(x=>'<span class="ui-filter-chip">'+h(x)+'</span>').join(''):'<span class="ui-filter-neutral">All statuses</span>')+'</div><span class="ui-result-count">'+rows.length+' matches in '+cwState.rows.length+' loaded cases</span>'+(chips.length?'<button type="button" class="ui-clear-filters" onclick="cwClearFilters()">Clear filters</button>':'');
 document.getElementById('cw-search-clear').hidden=!q;
 preferences.set('kurtex-workspace-group',document.getElementById('cw-group').value);
}
const uiWorkspaceGroup=preferences.get('kurtex-workspace-group');
if(['agent','status','priority','vehicle_type'].includes(uiWorkspaceGroup))document.getElementById('cw-group').value=uiWorkspaceGroup;
