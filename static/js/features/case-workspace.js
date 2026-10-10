/* Case Workspace: all views use the existing authenticated cases API and case modal. */
var cwView = 'board';
function cwSetView(view) {
  if (!['board','grid','table','card'].includes(view)) return;
  cwView=view;
  cwRender();
}
function cwData() {
  var rows=(typeof caseLists!=='undefined' && caseLists.cases ? caseLists.cases.rows : []) || [];
  var q=(document.getElementById('cw-search')?.value||'').trim().toLowerCase();
  var status=document.getElementById('cw-status')?.value||'';
  return rows.filter(function(c){
    if(status && String(c.status||'').toLowerCase()!==status)return false;
    return !q || [c.unit_number,c.unit,c.issue_text,c.description,c.driver,c.report_driver,c.agent,c.group,c.status,c.vehicle_type].some(function(x){return String(x||'').toLowerCase().includes(q)});
  });
}
function cwEscape(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]});}
function cwId(c){return c.full_id||c.id||''}
function cwField(c,name,other){return c[name]||c[other]||''}
function cwCard(c){
  var id=cwId(c),unit=cwField(c,'unit_number','unit')||'No unit',issue=cwField(c,'issue_text','description')||'Maintenance case';
  var priority=String(c.priority||'normal').toLowerCase(), status=String(c.status||'open').toLowerCase();
  return '<button type="button" class="cw-case" data-id="'+cwEscape(id)+'" onclick="openCase(this)" aria-label="Open case '+cwEscape(unit)+'"><div class="cw-case-top"><strong>'+cwEscape(unit)+'</strong><span class="cw-priority cw-'+cwEscape(priority)+'">'+cwEscape(priority)+'</span></div><div class="cw-issue">'+cwEscape(issue)+'</div><div class="cw-location">'+cwEscape(c.location||c.current_location||c.group||'')+'</div><div class="cw-case-bottom"><span>'+cwEscape(c.driver||c.report_driver||'')+'</span><span class="cw-status">'+cwEscape(status)+'</span></div></button>';
}
function cwRender(){
  var root=document.getElementById('cw-content');if(!root)return;
  document.querySelectorAll('[data-cw-view]').forEach(function(b){b.classList.toggle('active',b.dataset.cwView===cwView);b.setAttribute('aria-pressed',String(b.dataset.cwView===cwView))});
  var rows=cwData();var count=document.getElementById('cw-count');if(count)count.textContent=rows.length+' loaded cases';
  if(!rows.length){root.innerHTML='<div class="cw-empty">No cases match these filters.</div>';return}
  if(cwView==='table'){
    root.innerHTML='<div class="cw-table-scroll"><table class="cw-table"><thead><tr><th>Unit</th><th>Issue</th><th>Driver</th><th>Assigned to</th><th>Status</th><th>Priority</th></tr></thead><tbody>'+rows.map(function(c){return '<tr data-id="'+cwEscape(cwId(c))+'" tabindex="0" role="button" onclick="openCase(this)" onkeydown="if(event.key===\'Enter\')openCase(this)"><td>'+cwEscape(cwField(c,'unit_number','unit'))+'</td><td>'+cwEscape(cwField(c,'issue_text','description'))+'</td><td>'+cwEscape(c.driver||c.report_driver)+'</td><td>'+cwEscape(c.agent)+'</td><td>'+cwEscape(c.status)+'</td><td>'+cwEscape(c.priority)+'</td></tr>'}).join('')+'</tbody></table></div>';return;
  }
  if(cwView==='grid'||cwView==='card'){root.innerHTML='<div class="cw-'+cwView+'">'+rows.map(cwCard).join('')+'</div>';return}
  var field=document.getElementById('cw-group')?.value||'agent';var groups=new Map();
  rows.forEach(function(c){var name=String(c[field]|| (field==='agent'?'Unassigned':'Not specified'));if(!groups.has(name))groups.set(name,[]);groups.get(name).push(c)});
  root.innerHTML='<div class="cw-board">'+Array.from(groups,function(entry){return '<section class="cw-column"><header><strong>'+cwEscape(entry[0])+'</strong><span>'+entry[1].length+'</span></header><div class="cw-stack">'+entry[1].map(cwCard).join('')+'</div></section>'}).join('')+'</div>';
}
function cwRefresh(){if(typeof loadCases==='function')return loadCases(false,true)}
function cwLoadMore(){if(typeof loadCases==='function')return loadCases(true,true)}
function cwExport(){
 var rows=cwData();var fields=['unit_number','issue_text','driver','agent','status','priority','vehicle_type','group'];
 var csv=[fields.join(',')].concat(rows.map(function(c){return fields.map(function(k){return '"'+String(c[k]||'').replace(/"/g,'""')+'"'}).join(',')})).join('\r\n');
 var blob=new Blob([csv],{type:'text/csv;charset=utf-8'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='kurtex-case-workspace.csv';a.click();setTimeout(function(){URL.revokeObjectURL(url)},1000);
}
function cwPrint(){window.print()}
