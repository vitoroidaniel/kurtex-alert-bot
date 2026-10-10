/* Progressive keyboard support for existing div-based navigation; no routing changes. */
(function(){
  function activate(event){
    if(event.key !== 'Enter' && event.key !== ' ') return;
    var target=event.target.closest('.sidebar .nav-item[role="button"], .sidebar .nav-group-header[role="button"]');
    if(!target || event.target!==target && event.target.closest('button,a,input,select,textarea')) return;
    event.preventDefault(); target.click();
  }
  document.addEventListener('keydown',activate);
})();
