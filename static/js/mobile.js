/* Kurtex Mobile UI v2 — mobile-only behavior, isolated from desktop */
(function(){
  const mq=window.matchMedia('(max-width:760px)');
  const isMobile=()=>mq.matches;
  function wrapTables(){document.querySelectorAll('.table-wrap table').forEach(t=>{if(t.parentElement&&!t.parentElement.classList.contains('table-scroll')){const w=document.createElement('div');w.className='table-scroll';t.parentNode.insertBefore(w,t);w.appendChild(t)}})}
  function normalize(){
    document.documentElement.classList.toggle('kurtex-mobile',isMobile());
    if(!isMobile()) return;
    wrapTables();
    document.querySelectorAll('input,select,textarea').forEach(el=>{el.style.fontSize='16px'});
    document.querySelectorAll('.dropdown,.popover').forEach(el=>{el.style.maxWidth='calc(100vw - 24px)'});
  }
  function closeTransient(){
    document.querySelectorAll('.dropdown.open,.popover.open').forEach(x=>x.classList.remove('open'));
  }
  document.addEventListener('DOMContentLoaded',normalize);
  window.addEventListener('resize',normalize,{passive:true});
  document.addEventListener('click',e=>{
    if(!isMobile()) return;
    const nav=e.target.closest('.nav-item[data-page]');
    if(nav&&typeof window.closeSidebar==='function') setTimeout(window.closeSidebar,40);
    if(e.target.closest('.sidebar-overlay')) closeTransient();
  });
  if(mq.addEventListener) mq.addEventListener('change',normalize);
})();

/* v127 mobile viewport + keyboard + overflow QA */
(function(){
  const mobile=()=>window.matchMedia('(max-width:760px)').matches;
  function setViewport(){
    if(!mobile()) return;
    const vv=window.visualViewport;
    const h=vv?vv.height:window.innerHeight;
    document.documentElement.style.setProperty('--mobile-vh',h+'px');
    const keyboard=Math.max(0,window.innerHeight-h);
    document.documentElement.style.setProperty('--mobile-keyboard',keyboard+'px');
    document.body.classList.toggle('mobile-keyboard-open',keyboard>120);
  }
  function harden(){
    if(!mobile()) return;
    document.querySelectorAll('img').forEach(i=>{i.loading=i.loading||'lazy';});
    document.querySelectorAll('pre').forEach(p=>{p.style.maxWidth='100%';p.style.overflowX='auto';});
    document.querySelectorAll('.sidebar').forEach(s=>s.setAttribute('aria-modal','true'));
  }
  document.addEventListener('focusin',e=>{
    if(!mobile()||!e.target.matches('input,textarea,select')) return;
    setTimeout(()=>e.target.scrollIntoView({block:'nearest',behavior:'smooth'}),180);
  });
  document.addEventListener('DOMContentLoaded',()=>{setViewport();harden();});
  window.addEventListener('resize',setViewport,{passive:true});
  window.addEventListener('orientationchange',()=>setTimeout(setViewport,150),{passive:true});
  if(window.visualViewport){visualViewport.addEventListener('resize',setViewport,{passive:true});visualViewport.addEventListener('scroll',setViewport,{passive:true});}
  const mo=new MutationObserver(()=>harden());
  document.addEventListener('DOMContentLoaded',()=>mo.observe(document.body,{childList:true,subtree:true}));
})();

/* v137 mobile state normalization */
(function(){
 const mobile=()=>window.matchMedia('(max-width:760px)').matches;
 function init(){
   if(!mobile())return;
   const side=document.getElementById('ai-page-sidebar'),layout=document.querySelector('#page-ai_assistant .ai-page-layout');
   if(side&&layout&&!document.body.classList.contains('ai-mobile-history-open')){
     side.classList.add('collapsed');layout.classList.add('sidebar-collapsed');
   }
 }
 document.addEventListener('DOMContentLoaded',init);
 document.addEventListener('click',function(e){
   if(!mobile())return;
   if(e.target.closest('.ai-side-chat-open')) setTimeout(()=>toggleAIPageSidebar(false),60);
 },true);
})();

/* v139 — mobile companion app navigation */
function toggleMobileMore(force){
 var sheet=document.getElementById('mobile-more-sheet');if(!sheet)return;
 var show=typeof force==='boolean'?force:sheet.hasAttribute('hidden');
 if(show)sheet.removeAttribute('hidden');else sheet.setAttribute('hidden','');
}
function mobileGo(page){
 toggleMobileMore(false);
 if(typeof showPage==='function')showPage(page);
 document.querySelectorAll('[data-mobile-page]').forEach(function(b){b.classList.toggle('active',b.dataset.mobilePage===page)});
 if(page==='ai_assistant'){
   requestAnimationFrame(function(){
     var box=document.getElementById('ai-page-messages');
     if(box && !box.children.length && typeof newKurtexAIPageChat==='function') newKurtexAIPageChat();
     if(typeof loadAIPageSidebar==='function') loadAIPageSidebar();
     if(typeof refreshAIChatContext==='function') refreshAIChatContext();
   });
 }
}
(function(){
 function sync(){
  if(!window.matchMedia('(max-width:760px)').matches)return;
  var page=document.body.getAttribute('data-current-page')||'overview';
  var allowed=['overview','parts_manual','ai_assistant','fleet_intel','my_profile'];
  if(!allowed.includes(page)){page='overview';if(typeof showPage==='function')showPage(page)}
  document.querySelectorAll('[data-mobile-page]').forEach(function(b){b.classList.toggle('active',b.dataset.mobilePage===page)});
 }
 document.addEventListener('DOMContentLoaded',function(){setTimeout(sync,80)});
 document.addEventListener('click',function(e){if(!e.target.closest('.mobile-more-sheet,.mobile-more'))toggleMobileMore(false)},true);
 window.addEventListener('resize',sync,{passive:true});
})();
