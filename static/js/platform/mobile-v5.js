/* V5 mobile behavior: no API changes. */
(function(){
 const mq=matchMedia('(max-width:760px)');
 const mobile=()=>mq.matches;
 const allowed=p=>(document.body.dataset.allowedPages||'').split(',').includes(p);
 let notificationHome=null,notificationNext=null;
 function nav(){
  if(!mobile())return;
  const page=document.body.dataset.currentPage||'overview';
  document.querySelectorAll('.mobile-tabbar [data-mobile-page]').forEach(b=>b.classList.toggle('active',b.dataset.mobilePage===page));
  document.querySelectorAll('.mobile-more-sheet button[onclick*=mobileGo]').forEach(b=>{
   const target=(b.getAttribute('onclick')||'').match(/mobileGo\('([^']+)'\)/);
   if(target)b.hidden=!allowed(target[1])&&target[1]!=='my_profile';
  });
 }
 function notificationDock(){
  const center=document.querySelector('.notification-center');
  if(!center)return;
  if(!notificationHome){notificationHome=center.parentNode;notificationNext=center.nextSibling}
  if(mobile()){if(center.parentNode!==document.body)document.body.appendChild(center)}
  else if(center.parentNode===document.body&&notificationHome)notificationHome.insertBefore(center,notificationNext);
 }
 window.mobileV5Notifications=function(){
  if(!mobile())return;
  window.toggleMobileMore?.(false);
  notificationDock();
  const panel=document.getElementById('notification-panel');
  if(!panel)return;
  if(panel.hidden&&typeof window.toggleNotifications==='function')window.toggleNotifications();
  else panel.hidden=true;
  document.body.classList.toggle('v5-notifications-open',!panel.hidden);
  document.querySelector('.mobile-tabbar [data-mobile-page=notifications]')?.classList.toggle('active',!panel.hidden);
 };
 window.mobileV5Filters=function(open){
  if(!mobile())return;
  document.body.classList.toggle('v5-case-filters-open',Boolean(open));
  const trigger=document.querySelector('.v5-case-filter-trigger');if(trigger)trigger.setAttribute('aria-expanded',String(Boolean(open)));
 };
 function addFilterControls(){
  const controls=document.querySelector('#page-cases .section-header>div:last-child');
  if(!controls||controls.querySelector('.v5-filter-heading'))return;
  const head=document.createElement('div');head.className='v5-filter-heading';
  head.innerHTML='<strong>Filter cases</strong><button type="button" aria-label="Close filters"><i class="ph ph-x"></i></button>';
  head.querySelector('button').addEventListener('click',()=>window.mobileV5Filters(false));
  controls.prepend(head);
  const done=document.createElement('button');done.type='button';done.className='v5-filter-apply';done.textContent='Show cases';done.addEventListener('click',()=>window.mobileV5Filters(false));controls.append(done);
 }
 function init(){notificationDock();addFilterControls();nav()}
 document.addEventListener('DOMContentLoaded',()=>{
  init();new MutationObserver(nav).observe(document.body,{attributes:true,attributeFilter:['data-current-page']});
  document.addEventListener('keydown',e=>{if(e.key==='Escape'){window.mobileV5Filters(false);window.toggleMobileMore?.(false);const p=document.getElementById('notification-panel');if(p&&!p.hidden){p.hidden=true;document.body.classList.remove('v5-notifications-open')}}});
  document.addEventListener('click',e=>{if(!mobile())return;
   if(document.body.classList.contains('v5-case-filters-open')&&!e.target.closest('#page-cases .section-header>div:last-child,.v5-case-filter-trigger'))window.mobileV5Filters(false);
  });
 });
 mq.addEventListener?.('change',()=>{notificationDock();nav();if(!mobile())document.body.classList.remove('v5-case-filters-open','v5-notifications-open')});
})();
