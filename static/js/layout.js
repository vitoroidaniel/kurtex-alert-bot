// Personal layout, scoped to the signed-in user on this browser.
var layoutKey = 'kurtex-overview-v1-' + document.body.dataset.userId;
var widgetCatalog = [
  ['metrics','Key metrics'], ['agents','Top agents'], ['units','Problem units'], ['cases','Recent cases']
];
var metricLabels = ['Today total','Assigned','Resolved','Missed','Reassigned','Average response'];
var overviewLayout = {order:['metrics','agents','units','cases'], hidden:[], metrics:[], density:'comfortable', reducedMotion:false, stickySidebar:true};
try {
  var savedLayout = JSON.parse(preferences.get(layoutKey));
  if (savedLayout && Array.isArray(savedLayout.order)) {
    overviewLayout.order = [...new Set(savedLayout.order.filter(function(k){return widgetCatalog.some(function(w){return w[0]===k;});}).concat(overviewLayout.order))];
    overviewLayout.hidden = Array.isArray(savedLayout.hidden) ? savedLayout.hidden : [];
    overviewLayout.metrics = Array.isArray(savedLayout.metrics) ? savedLayout.metrics : [];
    overviewLayout.density = savedLayout.density === 'compact' ? 'compact' : 'comfortable';
    overviewLayout.reducedMotion = !!savedLayout.reducedMotion;
    overviewLayout.stickySidebar = savedLayout.stickySidebar !== false;
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
  document.body.classList.toggle('reduced-motion',!!overviewLayout.reducedMotion);
  document.body.classList.toggle('nonsticky-sidebar',!overviewLayout.stickySidebar);
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
settingsOverlay.innerHTML='<section class="modal layout-settings" role="dialog" aria-modal="true" aria-labelledby="layout-title"><header class="settings-head"><div><span class="settings-kicker">Workspace</span><h2 id="layout-title">Settings</h2></div><button class="modal-close" aria-label="Close settings" onclick="closeLayoutSettings()"><i class="ph ph-x"></i></button></header><div class="settings-shell"><nav class="settings-nav" aria-label="Settings categories"><button class="active" data-settings-tab="overview" onclick="switchSettingsTab(\'overview\')"><i class="ph ph-squares-four"></i><span>Overview</span></button><button data-settings-tab="display" onclick="switchSettingsTab(\'display\')"><i class="ph ph-monitor"></i><span>Display</span></button><button data-settings-tab="interface" onclick="switchSettingsTab(\'interface\')"><i class="ph ph-sliders-horizontal"></i><span>Interface</span></button></nav><div class="settings-content"><div id="layout-settings-body"></div></div></div><footer class="settings-footer"><button class="settings-reset" onclick="resetOverviewLayout()"><i class="ph ph-arrow-counter-clockwise"></i> Reset</button><button class="settings-done" onclick="closeLayoutSettings()">Done <i class="ph ph-check"></i></button></footer></section>';
var activeSettingsTab='overview';
document.body.append(settingsOverlay);
settingsOverlay.addEventListener('click',function(e){if(e.target===settingsOverlay)closeLayoutSettings();});
function switchSettingsTab(tab){activeSettingsTab=tab;document.querySelectorAll('.settings-nav button').forEach(function(b){b.classList.toggle('active',b.dataset.settingsTab===tab);});renderLayoutSettings();}
function renderLayoutSettings(){
  var html='';
  if(activeSettingsTab==='overview'){
    html='<div class="settings-section-head"><h3>Overview widgets</h3><p>Choose which cards appear and set their order.</p></div><div class="settings-list">'+overviewLayout.order.map(function(id,index){var title=widgetCatalog.find(function(w){return w[0]===id;})[1];return '<div class="setting-row"><label><input type="checkbox" '+(!overviewLayout.hidden.includes(id)?'checked':'')+' onchange="toggleOverviewWidget(\''+id+'\',this.checked)"><span>'+title+'</span></label><div class="setting-actions"><button aria-label="Move '+title+' up" '+(index===0?'disabled':'')+' onclick="moveOverviewWidget(\''+id+'\',-1)"><i class="ph ph-arrow-up"></i></button><button aria-label="Move '+title+' down" '+(index===3?'disabled':'')+' onclick="moveOverviewWidget(\''+id+'\',1)"><i class="ph ph-arrow-down"></i></button></div></div>';}).join('')+'</div><div class="settings-section-head"><h3>Visible metrics</h3><p>Pick the KPI cards you want at the top.</p></div><div class="metric-options">'+metricLabels.map(function(label,i){return '<label><input type="checkbox" '+(!overviewLayout.metrics.includes(i)?'checked':'')+' onchange="overviewLayout.metrics=overviewLayout.metrics.filter(function(x){return x!=='+i+'});if(!this.checked)overviewLayout.metrics.push('+i+');saveOverviewLayout()"><span>'+label+'</span></label>';}).join('')+'</div>';
  } else if(activeSettingsTab==='display'){
    html='<div class="settings-section-head"><h3>Density</h3><p>Control how much information fits on the screen.</p></div><div class="density-picker" role="group" aria-label="Overview spacing"><button class="density-option '+(overviewLayout.density==='comfortable'?'active':'')+'" onclick="setOverviewDensity(\'comfortable\')"><i class="ph ph-rows"></i><span><strong>Comfortable</strong><small>More breathing room</small></span><i class="ph ph-check-circle density-check"></i></button><button class="density-option '+(overviewLayout.density==='compact'?'active':'')+'" onclick="setOverviewDensity(\'compact\')"><i class="ph ph-list-bullets"></i><span><strong>Compact</strong><small>Fit more on screen</small></span><i class="ph ph-check-circle density-check"></i></button></div><div class="settings-section-head"><h3>Theme</h3><p>Keep the workspace bright or switch modes.</p></div><button class="settings-action-card" onclick="toggleTheme()"><i class="ph ph-sun"></i><span><strong>Color mode</strong><small>Use the current Light / Dark mode control</small></span><i class="ph ph-caret-right"></i></button>';
  } else {
    html='<div class="settings-section-head"><h3>Interface</h3><p>Adjust motion and desktop navigation behavior.</p></div><label class="settings-toggle-row"><span><strong>Reduce motion</strong><small>Limit transitions and animated movement.</small></span><input type="checkbox" '+(overviewLayout.reducedMotion?'checked':'')+' onchange="overviewLayout.reducedMotion=this.checked;saveOverviewLayout()"><i></i></label><label class="settings-toggle-row"><span><strong>Keep sidebar fixed</strong><small>Keep navigation visible while scrolling on desktop.</small></span><input type="checkbox" '+(overviewLayout.stickySidebar?'checked':'')+' onchange="overviewLayout.stickySidebar=this.checked;saveOverviewLayout()"><i></i></label>';
  }
  document.getElementById('layout-settings-body').innerHTML=html;
}
function setOverviewDensity(value){overviewLayout.density=value;saveOverviewLayout();renderLayoutSettings();}
function openLayoutSettings(){showPage('overview');renderLayoutSettings();settingsOverlay.classList.add('open');overviewPage.classList.add('editing-layout');lockBodyScroll();settingsOverlay.querySelector('.modal-close').focus();}
function closeLayoutSettings(){settingsOverlay.classList.remove('open');overviewPage.classList.remove('editing-layout');unlockBodyScroll();document.querySelector('.overview-toolbar button').focus();}
function resetOverviewLayout(){overviewLayout={order:['metrics','agents','units','cases'],hidden:[],metrics:[],density:'comfortable',reducedMotion:false,stickySidebar:true};saveOverviewLayout();renderLayoutSettings();}
applyOverviewLayout();
