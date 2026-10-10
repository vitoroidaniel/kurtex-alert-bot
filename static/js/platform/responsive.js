/* One mobile viewport contract for phones, tablets and split-screen windows. */
(function(){
  const mq=window.matchMedia('(max-width:1024px)');
  window.isKurtexMobile=()=>mq.matches;
  let frame=0,baseline=window.innerHeight,orientation=window.innerWidth>window.innerHeight;
  const notificationHome=document.createComment('Desktop notification location');
  let notificationCenter;
  function resizeViewport(){
    if(!mq.matches){
      document.documentElement.style.removeProperty('--mobile-vh');
      document.body.classList.remove('mobile-keyboard-open');return;
    }
    const vv=window.visualViewport;
    const height=vv&&vv.scale===1?vv.height:window.innerHeight;
    const focused=document.activeElement?.matches('input:not([type="checkbox"]):not([type="radio"]),textarea,[contenteditable="true"]');
    const nextOrientation=window.innerWidth>window.innerHeight;
    if(!focused&&nextOrientation!==orientation){baseline=window.innerHeight;orientation=nextOrientation;}
    if(!focused)baseline=Math.max(height,window.innerHeight);
    const keyboard=!!focused&&baseline-height>120;
    document.documentElement.style.setProperty('--mobile-vh',Math.round(height)+'px');
    document.body.classList.toggle('mobile-keyboard-open',keyboard);
  }
  function scheduleViewport(){if(frame)return;frame=requestAnimationFrame(()=>{frame=0;resizeViewport();});}
  function syncNavigation(){
    const page=document.body.dataset.currentPage||'overview';
    document.querySelectorAll('[data-mobile-page]').forEach(button=>{
      const selected=button.dataset.mobilePage===page;
      button.classList.toggle('active',selected);
      if(selected)button.setAttribute('aria-current','page');else button.removeAttribute('aria-current');
    });
    const title=document.querySelector('.mobile-brand strong'),pageTitle=document.getElementById('page-title');
    if(title&&pageTitle)title.textContent=pageTitle.textContent||'Kurtex';
    syncAIConversation();scheduleViewport();
  }
  function syncAIConversation(){
    const messages=document.getElementById('ai-page-messages');
    if(!messages)return;
    const hasMessages=!!messages.querySelector('.ai-page-msg');
    messages.classList.toggle('has-messages',hasMessages);
    messages.setAttribute('role','region');
    messages.tabIndex=hasMessages?0:-1;
    messages.setAttribute('aria-label',hasMessages?'Conversation':'Start a conversation');
    if(!hasMessages)messages.scrollTop=0;
  }
  function syncShell(){
    document.documentElement.classList.toggle('kurtex-mobile',mq.matches);
    if(notificationCenter){
      if(mq.matches)document.querySelector('.mobile-header').insertBefore(notificationCenter,document.querySelector('.mobile-header .mobile-more'));
      else notificationHome.after(notificationCenter);
    }
    if(!mq.matches)window.toggleMobileMore(false);
    syncNavigation();
  }
  function buildMoreNavigation(){
    const sheet=document.getElementById('mobile-more-sheet');if(!sheet)return;
    sheet.replaceChildren();
    const primary=new Set(Array.from(document.querySelectorAll('[data-mobile-page]')).map(b=>b.dataset.mobilePage));
    const seen=new Set();
    function add(label,icon,action,page){
      const button=document.createElement('button');button.type='button';
      const glyph=document.createElement('i');glyph.className='ph '+icon;glyph.setAttribute('aria-hidden','true');
      const text=document.createElement('span');text.textContent=label;button.append(glyph,text);
      if(page)button.dataset.mobilePage=page;
      button.addEventListener('click',()=>{window.toggleMobileMore(false);action();});sheet.append(button);
    }
    document.querySelectorAll('.sidebar .nav-item[data-page]').forEach(item=>{
      const page=item.dataset.page;if(item.hidden||item.closest('.nav-group[hidden]')||primary.has(page)||seen.has(page))return;seen.add(page);
      const icon=Array.from(item.querySelector('i')?.classList||[]).find(c=>c.startsWith('ph-'))||'ph-squares-four';
      add(item.textContent.trim(),icon,()=>mobileGo(page),page);
    });
    add('My profile','ph-user',()=>mobileGo('my_profile'),'my_profile');
    add('Settings','ph-gear-six',()=>openLayoutSettings('appearance'));
    add('Print page','ph-printer',()=>uiPrintCurrentPage());
    if((document.body.dataset.allowedPages||'').split(',').includes('cases'))add('Export cases','ph-download-simple',()=>uiExportCurrentPage());
    add('Sign out','ph-sign-out',()=>openSignOutConfirm());
  }
  window.toggleMobileMore=function(force){
    const sheet=document.getElementById('mobile-more-sheet');if(!sheet)return;
    const show=typeof force==='boolean'?force:sheet.hidden;
    sheet.hidden=!show;
    document.querySelectorAll('[aria-controls="mobile-more-sheet"]').forEach(button=>button.setAttribute('aria-expanded',String(show)));
    if(show)sheet.querySelector('button')?.focus();
  };
  window.mobileGo=function(page){
    window.toggleMobileMore(false);showPage(page);syncNavigation();
    if(page==='ai_assistant'){
      if(typeof loadAIPageSidebar==='function')loadAIPageSidebar();
      if(typeof refreshAIChatContext==='function')refreshAIChatContext();
    }
  };
  function autosize(input){
    input.style.height='auto';input.style.height=Math.min(96,Math.max(40,input.scrollHeight))+'px';
  }
  document.addEventListener('DOMContentLoaded',()=>{
    notificationCenter=document.querySelector('.notification-center');
    if(notificationCenter)notificationCenter.before(notificationHome);
    buildMoreNavigation();syncShell();
    const messages=document.getElementById('ai-page-messages');
    if(messages)new MutationObserver(syncAIConversation).observe(messages,{childList:true,subtree:true});
    new MutationObserver(()=>{syncNavigation();window.toggleMobileMore(false);}).observe(document.body,{attributes:true,attributeFilter:['data-current-page']});
    const input=document.getElementById('ai-page-input');
    if(input){input.addEventListener('input',()=>autosize(input));input.setAttribute('enterkeyhint','enter');autosize(input);}
    document.querySelectorAll('input[type="search"]').forEach(el=>el.setAttribute('enterkeyhint','search'));
  });
  document.addEventListener('focusin',scheduleViewport);
  document.addEventListener('focusout',()=>setTimeout(scheduleViewport,120));
  document.addEventListener('keydown',event=>{
    const sheet=document.getElementById('mobile-more-sheet');
    if(event.key==='Escape'&&sheet&&!sheet.hidden){window.toggleMobileMore(false);document.querySelector('.mobile-more-trigger')?.focus();}
  });
  document.addEventListener('click',event=>{
    if(!event.target.closest('#mobile-more-sheet,[aria-controls="mobile-more-sheet"]'))window.toggleMobileMore(false);
  });
  window.addEventListener('resize',scheduleViewport,{passive:true});
  window.addEventListener('orientationchange',()=>{baseline=0;setTimeout(scheduleViewport,150);},{passive:true});
  if(window.visualViewport){visualViewport.addEventListener('resize',scheduleViewport,{passive:true});visualViewport.addEventListener('scroll',scheduleViewport,{passive:true});}
  mq.addEventListener('change',syncShell);
})();

/* Case cards reuse the existing table's records and canonical case dialog. */
(function(){
  const mq=window.matchMedia('(max-width:1024px)');let queued=false,signature='';
  function esc(value){return String(value||'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
  function build(){
    const host=document.getElementById('cases-table');if(!host)return;
    const parent=host.parentElement,old=parent.querySelector('.mobile-case-cards'),table=host.querySelector('table');
    if(!mq.matches||!table){old?.remove();parent.classList.remove('cases-cards-ready');signature='';return;}
    if(old?.contains(document.activeElement))return;
    const nextSignature=table.tBodies[0]?.innerHTML||'';
    if(old&&signature===nextSignature)return;
    old?.remove();parent.classList.remove('cases-cards-ready');signature=nextSignature;
    const cards=document.createElement('div');cards.className='mobile-case-cards';
    table.querySelectorAll('tbody tr[data-id]').forEach(row=>{
      const cells=Array.from(row.children),id=row.dataset.id;if(cells.length<7||!id)return;
      const card=document.createElement('button');card.type='button';card.className='mobile-case-card';card.dataset.id=id;
      const status=cells[3].cloneNode(true),findings=Array.from(status.querySelectorAll('button'));findings.forEach(button=>button.remove());
      card.innerHTML='<div class="mobile-case-card-top"><strong>'+esc(cells[0].textContent.trim()||'Unknown reporter')+'</strong><span>'+status.innerHTML+'</span></div><div class="mobile-case-card-group"><i class="ph ph-users-three" aria-hidden="true"></i><span>'+esc(cells[1].textContent.trim()||'No group')+'</span></div><p class="mobile-case-card-issue">'+esc(cells[6].textContent.trim()||'No issue description provided')+'</p><div class="mobile-case-card-meta"><span>Assigned to<b>'+esc(cells[2].textContent.trim()||'Unassigned')+'</b></span><span>Opened<b>'+esc(cells[4].textContent.trim()||'—')+'</b></span><span>Response<b>'+esc(cells[5].textContent.trim()||'—')+'</b></span><span>Case<b>View details →</b></span></div>';
      card.addEventListener('click',()=>openCase(id));
      const record=document.createElement('article');record.className='mobile-case-record';record.append(card);
      if(findings.length){const links=document.createElement('div');links.className='mobile-case-findings';links.append(...findings);record.append(links);}
      cards.append(record);
    });
    if(cards.children.length){parent.append(cards);parent.classList.add('cases-cards-ready');}
  }
  function schedule(){if(queued)return;queued=true;requestAnimationFrame(()=>{queued=false;build();});}
  document.addEventListener('DOMContentLoaded',()=>{
    const host=document.getElementById('cases-table');if(!host)return;
    new MutationObserver(schedule).observe(host,{childList:true,subtree:true});
    host.parentElement.addEventListener('focusout',()=>setTimeout(schedule,0));schedule();
  });
  mq.addEventListener('change',schedule);
})();
