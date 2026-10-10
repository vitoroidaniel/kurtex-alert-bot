/* Kurtex mobile behavior — stable navigation + viewport handling */
(function(){
  const mq=window.matchMedia('(max-width:760px)');
  const isMobile=()=>mq.matches;
  function wrapTables(){document.querySelectorAll('.table-wrap table').forEach(t=>{if(t.parentElement&&!t.parentElement.classList.contains('table-scroll')){const w=document.createElement('div');w.className='table-scroll';t.parentNode.insertBefore(w,t);w.appendChild(t)}})}
  function normalize(){
    document.documentElement.classList.toggle('kurtex-mobile',isMobile());
    if(!isMobile())return;
    wrapTables();
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
    document.querySelectorAll('[aria-controls="mobile-more-sheet"]').forEach(b=>b.setAttribute('aria-expanded',String(show)));
    if(show)sheet.querySelector('button')?.focus();
  };
  document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!document.getElementById('mobile-more-sheet').hidden){window.toggleMobileMore(false);document.querySelector('.mobile-more').focus();}});
  document.addEventListener('click',e=>{if(!e.target.closest('#mobile-more-sheet,[aria-controls="mobile-more-sheet"]'))window.toggleMobileMore(false);});
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

/* v17 mobile case-card adapter — presentation only, API/data logic unchanged */
(function(){
  const mq=window.matchMedia('(max-width:760px)');
  let queued=false;
  function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
  function buildMobileCases(){
    if(!mq.matches)return;
    const host=document.getElementById('cases-table'); if(!host)return;
    const table=host.querySelector('table'); if(!table)return;
    const old=host.parentElement&&host.parentElement.querySelector('.mobile-case-cards'); if(old)old.remove();
    const wrap=document.createElement('div'); wrap.className='mobile-case-cards';
    table.querySelectorAll('tbody tr').forEach(tr=>{
      const td=[...tr.children], id=tr.dataset.id||''; if(td.length<7)return;
      const b=document.createElement('button'); b.type='button'; b.className='mobile-case-card'; b.dataset.id=id;
      const status=td[3].innerHTML;
      b.innerHTML='<div class="mobile-case-card-top"><strong>'+esc(td[0].textContent.trim()||'Unknown reporter')+'</strong><span>'+status+'</span></div>'+ 
        '<div class="mobile-case-card-group"><i class="ph ph-users-three"></i><span>'+esc(td[1].textContent.trim()||'No group')+'</span></div>'+ 
        '<p class="mobile-case-card-issue">'+esc(td[6].textContent.trim()||'No issue description provided')+'</p>'+ 
        '<div class="mobile-case-card-meta"><span>Assigned to<b>'+esc(td[2].textContent.trim()||'—')+'</b></span><span>Opened<b>'+esc(td[4].textContent.trim()||'—')+'</b></span><span>Response<b>'+esc(td[5].textContent.trim()||'—')+'</b></span><span>Case<b>View details →</b></span></div>';
      b.addEventListener('click',()=>{if(typeof window.openCase==='function')window.openCase(id)}); wrap.appendChild(b);
    });
    if(wrap.children.length)host.parentElement.appendChild(wrap);
  }
  function schedule(){if(queued)return;queued=true;requestAnimationFrame(()=>{queued=false;buildMobileCases()})}
  document.addEventListener('DOMContentLoaded',()=>{schedule();const host=document.getElementById('cases-table');if(host)new MutationObserver(schedule).observe(host,{childList:true,subtree:true})});
  if(mq.addEventListener)mq.addEventListener('change',schedule);
})();

/* v28 mobile interaction polish */
(function(){
  const mobile=()=>matchMedia('(max-width:760px)').matches;
  function autosize(el){if(!el)return;el.style.height='auto';el.style.height=Math.min(120,Math.max(42,el.scrollHeight))+'px'}
  function init(){
    const ai=document.getElementById('ai-page-input');
    if(ai){ai.addEventListener('input',()=>autosize(ai));autosize(ai)}
    document.querySelectorAll('input[type="search"]').forEach(input=>input.setAttribute('enterkeyhint','search'));
    document.querySelectorAll('textarea').forEach(t=>t.setAttribute('enterkeyhint',t.id==='ai-page-input'?'send':'enter'));
  }
  document.addEventListener('keydown',e=>{
    if(e.key!=='Escape'||!mobile())return;
    const overlays=['layout-settings-overlay','report-view-overlay','unit-modal-overlay','modal-overlay'];
    for(const id of overlays){const el=document.getElementById(id);if(el&&getComputedStyle(el).display!=='none'){const close=el.querySelector('.modal-close,[aria-label^="Close"],button[onclick*="close"]');if(close){close.click();break}}}
  });
  document.addEventListener('DOMContentLoaded',init);
})();
