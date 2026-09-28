/* Kurtex mobile behavior — stable navigation + viewport handling */
(function(){
  const mq=window.matchMedia('(max-width:760px)');
  const isMobile=()=>mq.matches;
  function wrapTables(){document.querySelectorAll('.table-wrap table').forEach(t=>{if(t.parentElement&&!t.parentElement.classList.contains('table-scroll')){const w=document.createElement('div');w.className='table-scroll';t.parentNode.insertBefore(w,t);w.appendChild(t)}})}
  function normalize(){
    document.documentElement.classList.toggle('kurtex-mobile',isMobile());
    if(!isMobile())return;
    wrapTables();
    document.querySelectorAll('input,select,textarea').forEach(el=>{el.style.fontSize='16px'});
    document.querySelectorAll('.dropdown,.popover').forEach(el=>{el.style.maxWidth='calc(100vw - 24px)'});
    syncMobileNav();
  }
  function setViewport(){
    if(!isMobile())return;
    const vv=window.visualViewport,h=vv?vv.height:window.innerHeight;
    document.documentElement.style.setProperty('--mobile-vh',h+'px');
    const keyboard=Math.max(0,window.innerHeight-h);
    document.documentElement.style.setProperty('--mobile-keyboard',keyboard+'px');
    document.body.classList.toggle('mobile-keyboard-open',keyboard>120);
  }
  function syncMobileNav(){
    if(!isMobile())return;
    const page=document.body.getAttribute('data-current-page')||'overview';
    document.querySelectorAll('[data-mobile-page]').forEach(b=>b.classList.toggle('active',b.dataset.mobilePage===page));
  }
  function harden(){
    if(!isMobile())return;
    document.querySelectorAll('img').forEach(i=>{i.loading=i.loading||'lazy'});
    document.querySelectorAll('pre').forEach(p=>{p.style.maxWidth='100%';p.style.overflowX='auto'});
  }
  window.toggleMobileMore=function(force){
    const sheet=document.getElementById('mobile-more-sheet');if(!sheet)return;
    const show=typeof force==='boolean'?force:sheet.hasAttribute('hidden');
    if(show)sheet.removeAttribute('hidden');else sheet.setAttribute('hidden','');
  };
  window.mobileGo=function(page){
    window.toggleMobileMore(false);
    if(typeof window.showPage==='function')window.showPage(page);
    syncMobileNav();
    if(page==='ai_assistant')requestAnimationFrame(()=>{
      const box=document.getElementById('ai-page-messages');
      if(box&&!box.children.length&&typeof window.newKurtexAIPageChat==='function')window.newKurtexAIPageChat();
      if(typeof window.loadAIPageSidebar==='function')window.loadAIPageSidebar();
      if(typeof window.refreshAIChatContext==='function')window.refreshAIChatContext();
    });
  };
  document.addEventListener('DOMContentLoaded',()=>{normalize();setViewport();harden();setTimeout(syncMobileNav,80)});
  document.addEventListener('focusin',e=>{if(!isMobile()||!e.target.matches('input,textarea,select'))return;setTimeout(()=>e.target.scrollIntoView({block:'nearest',behavior:'smooth'}),180)});
  document.addEventListener('click',e=>{
    if(!isMobile())return;
    if(!e.target.closest('.mobile-more-sheet,.mobile-more'))window.toggleMobileMore(false);
    if(e.target.closest('.ai-side-chat-open')&&typeof window.toggleAIPageSidebar==='function')setTimeout(()=>window.toggleAIPageSidebar(false),30);
  },true);
  window.addEventListener('resize',()=>{setViewport();normalize()},{passive:true});
  window.addEventListener('orientationchange',()=>setTimeout(()=>{setViewport();normalize()},150),{passive:true});
  if(window.visualViewport){visualViewport.addEventListener('resize',setViewport,{passive:true});visualViewport.addEventListener('scroll',setViewport,{passive:true})}
  if(mq.addEventListener)mq.addEventListener('change',normalize);
  const mo=new MutationObserver(()=>{harden();syncMobileNav()});
  document.addEventListener('DOMContentLoaded',()=>mo.observe(document.body,{childList:true,subtree:true,attributes:true,attributeFilter:['data-current-page']}));
})();
