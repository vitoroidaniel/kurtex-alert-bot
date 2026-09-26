/* Kurtex mobile-only behavior. Desktop logic remains in the existing files. */
(function(){
  const mq=window.matchMedia('(max-width:700px)');
  function mobile(){return mq.matches}
  function normalize(){
    if(!mobile()) return;
    document.documentElement.classList.add('kurtex-mobile');
    document.querySelectorAll('.table-wrap table').forEach(t=>{if(t.parentElement&&!t.parentElement.classList.contains('table-scroll')){const w=document.createElement('div');w.className='table-scroll';t.parentNode.insertBefore(w,t);w.appendChild(t)}});
    document.querySelectorAll('input,select,textarea').forEach(el=>{if(!el.style.fontSize) el.style.fontSize='16px'});
  }
  document.addEventListener('DOMContentLoaded',normalize);
  window.addEventListener('resize',normalize,{passive:true});
  document.addEventListener('click',e=>{if(!mobile())return;const nav=e.target.closest('.nav-item[data-page]');if(nav&&typeof window.closeSidebar==='function') setTimeout(window.closeSidebar,60)});
})();
