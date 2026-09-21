// Personal layout, scoped to the signed-in user on this browser.
var layoutKey = 'kurtex-overview-v1-' + document.body.dataset.userId;
var widgetCatalog = [
  ['metrics','Key metrics'], ['agents','Top agents'], ['units','Problem units'], ['cases','Recent cases']
];
var metricLabels = ['Today total','Assigned','Resolved','Missed','Reassigned','Average response'];
var overviewLayout = {order:['metrics','agents','units','cases'], hidden:[], metrics:[], density:'comfortable'};
try {
  var savedLayout = JSON.parse(preferences.get(layoutKey));
  if (savedLayout && Array.isArray(savedLayout.order)) {
    overviewLayout.order = [...new Set(savedLayout.order.filter(function(k){return widgetCatalog.some(function(w){return w[0]===k;});}).concat(overviewLayout.order))];
    overviewLayout.hidden = Array.isArray(savedLayout.hidden) ? savedLayout.hidden : [];
    overviewLayout.metrics = Array.isArray(savedLayout.metrics) ? savedLayout.metrics : [];
    overviewLayout.density = savedLayout.density === 'compact' ? 'compact' : 'comfortable';
  }
} catch (_) {}
var overviewGrid = document.createElement('div');
overviewGrid.className = 'overview-grid';
var overviewPage = document.getElementById('page-overview');
var metricsWidget = document.createElement('section');
metricsWidget.append(document.querySelector('.overview-label'),document.getElementById('stat-grid'));
var widgetNodes = {metrics:metricsWidget, agents:document.getElementById('lb-overview').closest('.card'), units:document.getElementById('units-overview').closest('.card'), cases:document.getElementById('recent-table').closest('.section')};
var oldColumns = widgetNodes.agents.parentElement;
widgetCatalog.forEach(function(item) {
  var node=widgetNodes[item[0]];
  node.dataset.widget=item[0];
  node.classList.add('overview-widget');
  var controls=document.createElement('div');
  controls.className='widget-controls';
  controls.innerHTML='<button class="widget-drag" draggable="true" aria-label="Drag '+item[1]+'">⠿ '+item[1]+'</button><button onclick="toggleOverviewWidget(\''+item[0]+'\',false)" aria-label="Hide '+item[1]+'">Hide</button>';
  node.prepend(controls);
  overviewGrid.append(node);
});
oldColumns.remove();
overviewPage.append(overviewGrid);
var emptyLayout=document.createElement('div');
emptyLayout.className='empty-state';emptyLayout.textContent='Your overview is empty. Use Customize overview to add widgets.';overviewPage.append(emptyLayout);
function saveOverviewLayout(){preferences.set(layoutKey,JSON.stringify(overviewLayout));applyOverviewLayout();}
function applyOverviewLayout(){
  overviewLayout.order.forEach(function(id,index){var node=widgetNodes[id];if(node){if(overviewGrid.children[index]!==node)overviewGrid.insertBefore(node,overviewGrid.children[index]||null);node.hidden=overviewLayout.hidden.includes(id);}});
  document.querySelectorAll('#stat-grid .stat-card').forEach(function(card,i){card.hidden=overviewLayout.metrics.includes(i);});
  overviewPage.classList.toggle('compact-layout',overviewLayout.density==='compact');
  emptyLayout.hidden=overviewLayout.hidden.length<widgetCatalog.length;
}
function toggleOverviewWidget(id,visible){
  overviewLayout.hidden=overviewLayout.hidden.filter(function(x){return x!==id;});
  if(!visible)overviewLayout.hidden.push(id);
  saveOverviewLayout();renderLayoutSettings();
}
function moveOverviewWidget(id,direction){
  var index=overviewLayout.order.indexOf(id), next=index+direction;
  if(next<0||next>=overviewLayout.order.length)return;
  overviewLayout.order.splice(index,1);overviewLayout.order.splice(next,0,id);
  saveOverviewLayout();renderLayoutSettings();
}
var draggingWidget=null;
overviewGrid.addEventListener('dragstart',function(e){
  if(!e.target.matches('.widget-drag'))return;
  draggingWidget=e.target.closest('[data-widget]').dataset.widget;
  e.dataTransfer.setData('text/plain',draggingWidget);e.dataTransfer.effectAllowed='move';
});
overviewGrid.addEventListener('dragover',function(e){if(draggingWidget)e.preventDefault();});
overviewGrid.addEventListener('drop',function(e){
  if(!draggingWidget)return;e.preventDefault();
  var target=e.target.closest('[data-widget]');
  if(target&&target.dataset.widget!==draggingWidget){
    var next=overviewLayout.order.filter(function(id){return id!==draggingWidget;});
    next.splice(next.indexOf(target.dataset.widget),0,draggingWidget);overviewLayout.order=next;saveOverviewLayout();renderLayoutSettings();
  }draggingWidget=null;
});
overviewGrid.addEventListener('dragend',function(){draggingWidget=null;});
var settingsOverlay=document.createElement('div');
settingsOverlay.className='modal-overlay';settingsOverlay.id='layout-settings-overlay';settingsOverlay.style.zIndex='600';
settingsOverlay.innerHTML='<section class="modal layout-settings" role="dialog" aria-modal="true" aria-labelledby="layout-title"><button class="modal-close" aria-label="Close settings" onclick="closeLayoutSettings()">×</button><h2 id="layout-title">Make overview yours</h2><p class="settings-description">Choose your widgets and data. Drag the handles on Overview or use the arrows below to reorder. Saved for your account on this browser.</p><div id="layout-settings-body"></div><button class="btn" onclick="resetOverviewLayout()">Reset to default</button><button class="btn" onclick="closeLayoutSettings()">Done</button></section>';
document.body.append(settingsOverlay);
settingsOverlay.addEventListener('click',function(e){if(e.target===settingsOverlay)closeLayoutSettings();});
function renderLayoutSettings(){
  var html='<h3>Widgets</h3>'+overviewLayout.order.map(function(id,index){var title=widgetCatalog.find(function(w){return w[0]===id;})[1];return '<div class="setting-row"><label><input type="checkbox" '+(!overviewLayout.hidden.includes(id)?'checked':'')+' onchange="toggleOverviewWidget(\''+id+'\',this.checked)"> '+title+'</label><div><button aria-label="Move '+title+' up" '+(index===0?'disabled':'')+' onclick="moveOverviewWidget(\''+id+'\',-1)">↑</button><button aria-label="Move '+title+' down" '+(index===3?'disabled':'')+' onclick="moveOverviewWidget(\''+id+'\',1)">↓</button></div></div>';}).join('');
  html+='<h3>Visible metrics</h3><div class="metric-options">'+metricLabels.map(function(label,i){return '<label><input type="checkbox" '+(!overviewLayout.metrics.includes(i)?'checked':'')+' onchange="overviewLayout.metrics=overviewLayout.metrics.filter(function(x){return x!=='+i+'});if(!this.checked)overviewLayout.metrics.push('+i+');saveOverviewLayout()"> '+label+'</label>';}).join('')+'</div><h3>Appearance</h3><div class="setting-row"><label for="layout-density">Spacing</label><select id="layout-density" onchange="overviewLayout.density=this.value;saveOverviewLayout()"><option value="comfortable">Comfortable</option><option value="compact">Compact</option></select></div>';
  document.getElementById('layout-settings-body').innerHTML=html;
  document.getElementById('layout-density').value=overviewLayout.density;
}
function openLayoutSettings(){showPage('overview');renderLayoutSettings();settingsOverlay.classList.add('open');overviewPage.classList.add('editing-layout');lockBodyScroll();settingsOverlay.querySelector('.modal-close').focus();}
function closeLayoutSettings(){settingsOverlay.classList.remove('open');overviewPage.classList.remove('editing-layout');unlockBodyScroll();document.querySelector('.overview-toolbar button').focus();}
function resetOverviewLayout(){overviewLayout={order:['metrics','agents','units','cases'],hidden:[],metrics:[],density:'comfortable'};saveOverviewLayout();renderLayoutSettings();}
applyOverviewLayout();
