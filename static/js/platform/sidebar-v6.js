/* Sidebar behavior: desktop compact mode is separate from mobile drawer. */
(function(){
  const KEY='kurtex-sidebar-compact';
  function apply(compact){
    document.body.classList.toggle('k6-sidebar-compact',compact);
    const b=document.getElementById('k6-sidebar-toggle');
    if(b){b.setAttribute('aria-expanded',String(!compact));b.setAttribute('aria-label',compact?'Expand sidebar':'Collapse sidebar');b.title=compact?'Expand sidebar':'Collapse sidebar';}
  }
  window.kurtexToggleCompactSidebar=function(){
    if(window.matchMedia('(max-width:760px)').matches){window.closeSidebar?.();return;}
    const next=!document.body.classList.contains('k6-sidebar-compact');apply(next);
    try{localStorage.setItem(KEY,next?'1':'0')}catch(e){}
  };
  document.addEventListener('DOMContentLoaded',function(){
    let compact=false;try{compact=localStorage.getItem(KEY)==='1'}catch(e){}
    apply(compact);
    const nav=document.querySelector('.sidebar .v3-navigation');
    if(nav){nav.querySelectorAll('[role="button"]').forEach(el=>{
      if(!el.hasAttribute('aria-label')) el.setAttribute('aria-label',el.textContent.trim().replace(/\s+/g,' '));
      el.setAttribute('title',el.getAttribute('aria-label'));
      el.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();el.click()}});
    });}

    nav?.querySelectorAll('.nav-group-header').forEach(el=>{
      el.addEventListener('click',function(){
        if(document.body.classList.contains('k6-sidebar-compact') && !window.matchMedia('(max-width:760px)').matches){
          apply(false);try{localStorage.setItem(KEY,'0')}catch(e){}
        }
      });
    });
    document.addEventListener('keydown',e=>{if(e.key==='Escape'&&window.matchMedia('(max-width:760px)').matches)window.closeSidebar?.()});
  });
})();
