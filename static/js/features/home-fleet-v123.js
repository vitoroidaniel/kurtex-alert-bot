/* v123: drag panels within their existing grid only; preserve existing server-backed settings. */
(function(){
 const root=document.getElementById('page-overview');if(!root)return;
 function init(){
  const grids=root.querySelectorAll('.kx-manager-charts,.kx-manager-bottom,.kx-agent-bottom,.kx-agent-side,.kx-extra-widgets');
  grids.forEach((grid,gi)=>{
   const children=Array.from(grid.children).filter(n=>n.matches('.kx-panel,.card,.home-attention-card,.home-ai-summary-card,.home-units-card'));
   if(children.length<2)return;
   const key='kurtex-widget-order-v123-'+(document.body.dataset.userId||'user')+'-'+(root.dataset.dashboardRole||'agent')+'-'+gi;
   try{const saved=JSON.parse(localStorage.getItem(key)||'[]');saved.forEach(id=>{const node=children.find(n=>n.dataset.widgetDragId===id);if(node)grid.append(node)})}catch(e){}
   children.forEach((node,i)=>{
    node.dataset.widgetDragId=node.dataset.widgetDragId||('w'+i);
    node.draggable=true;
    node.addEventListener('dragstart',e=>{if(e.target.closest('button,input,select,textarea,a')){e.preventDefault();return}node.classList.add('kx-dragging');e.dataTransfer.setData('text/plain',node.dataset.widgetDragId);e.dataTransfer.effectAllowed='move'});
    node.addEventListener('dragend',()=>{node.classList.remove('kx-dragging');grid.querySelectorAll('.kx-drag-target').forEach(n=>n.classList.remove('kx-drag-target'))});
    node.addEventListener('dragover',e=>{e.preventDefault();node.classList.add('kx-drag-target')});
    node.addEventListener('dragleave',()=>node.classList.remove('kx-drag-target'));
    node.addEventListener('drop',e=>{e.preventDefault();node.classList.remove('kx-drag-target');const dragged=Array.from(grid.children).find(n=>n.dataset.widgetDragId===e.dataTransfer.getData('text/plain'));if(!dragged||dragged===node)return;const box=node.getBoundingClientRect();grid.insertBefore(dragged,e.clientY>box.top+box.height/2?node.nextSibling:node);localStorage.setItem(key,JSON.stringify(Array.from(grid.children).map(n=>n.dataset.widgetDragId).filter(Boolean)))});
   });
   // Apply saved order after identifiers are initialized.
   try{JSON.parse(localStorage.getItem(key)||'[]').forEach(id=>{const node=children.find(n=>n.dataset.widgetDragId===id);if(node)grid.append(node)})}catch(e){}
  });
 }
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);else init();
})();
