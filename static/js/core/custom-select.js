(function(){
  var openState=null;
  function closeMenu(){
    if(!openState)return;
    openState.menu.remove();
    openState.button.setAttribute('aria-expanded','false');
    openState=null;
  }
  function sync(select){
    if(!select._kurtexSelect)return;
    var opt=select.options[select.selectedIndex];
    select._kurtexSelect.label.textContent=opt?opt.textContent:'';
    select._kurtexSelect.button.disabled=!!select.disabled;
  }
  function openMenu(select){
    if(select.disabled)return;
    if(openState&&openState.select===select){closeMenu();return;}
    closeMenu();
    var ui=select._kurtexSelect, rect=ui.button.getBoundingClientRect();
    var menu=document.createElement('div'); menu.className='kurtex-select-menu'; menu.setAttribute('role','listbox');
    Array.from(select.options).forEach(function(opt){
      var b=document.createElement('button'); b.type='button'; b.className='kurtex-select-option'+(opt.selected?' selected':''); b.disabled=opt.disabled;
      b.setAttribute('role','option'); b.setAttribute('aria-selected',opt.selected?'true':'false');
      b.innerHTML='<span>'+escapeHtml(opt.textContent)+'</span>'+(opt.selected?'<i class="ph ph-check"></i>':'');
      b.onclick=function(e){e.stopPropagation();select.value=opt.value;select.dispatchEvent(new Event('change',{bubbles:true}));sync(select);closeMenu();};
      menu.appendChild(b);
    });
    document.body.appendChild(menu);
    var width=Math.max(rect.width, menu.scrollWidth, 150), left=Math.min(rect.left,window.innerWidth-width-10), top=rect.bottom+6;
    if(top+menu.offsetHeight>window.innerHeight-10 && rect.top>menu.offsetHeight+10) top=rect.top-menu.offsetHeight-6;
    menu.style.left=Math.max(8,left)+'px'; menu.style.top=Math.max(8,top)+'px'; menu.style.width=width+'px';
    ui.button.setAttribute('aria-expanded','true'); openState={select:select,button:ui.button,menu:menu};
  }
  function escapeHtml(v){var d=document.createElement('div');d.textContent=v==null?'':String(v);return d.innerHTML;}
  function enhance(select){
    if(!select||select.dataset.kurtexCustomSelect==='1'||select.multiple||select.size>1)return;
    select.dataset.kurtexCustomSelect='1'; select.classList.add('kurtex-select-native');
    var shell=document.createElement('span'); shell.className='kurtex-select-shell';
    if(select.className) shell.dataset.sourceClass=select.className;
    var button=document.createElement('button');button.type='button';button.className='kurtex-select-button';button.setAttribute('aria-haspopup','listbox');button.setAttribute('aria-expanded','false');
    button.innerHTML='<span class="kurtex-select-label"></span><i class="ph ph-caret-down kurtex-select-caret"></i>';
    select.parentNode.insertBefore(shell,select);shell.appendChild(select);shell.appendChild(button);
    select._kurtexSelect={shell:shell,button:button,label:button.querySelector('.kurtex-select-label')};
    sync(select); button.onclick=function(e){e.preventDefault();e.stopPropagation();openMenu(select);};
    select.addEventListener('change',function(){sync(select);});
    new MutationObserver(function(){sync(select);if(openState&&openState.select===select){closeMenu();}}).observe(select,{childList:true,subtree:true,attributes:true});
  }
  function scan(root){(root||document).querySelectorAll('select').forEach(enhance);}
  document.addEventListener('DOMContentLoaded',function(){scan(document);new MutationObserver(function(ms){ms.forEach(function(m){m.addedNodes.forEach(function(n){if(n.nodeType!==1)return;if(n.matches&&n.matches('select'))enhance(n);scan(n);});});}).observe(document.body,{childList:true,subtree:true});});
  document.addEventListener('click',function(e){if(openState&&!e.target.closest('.kurtex-select-menu'))closeMenu();});
  document.addEventListener('keydown',function(e){if(e.key==='Escape')closeMenu();});
  window.addEventListener('resize',closeMenu);window.addEventListener('scroll',closeMenu,true);
})();
