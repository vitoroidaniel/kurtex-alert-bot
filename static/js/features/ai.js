var aiLastSendAttempt=null;
var aiBusy=false, aiUploading=false, aiOpenSequence=0;
var kurtexAIChatId=null,kurtexAIChats=[];

function aiAttr(s){return String(s==null?'':s).replace(/\\/g,'\\\\').replace(/'/g,"\\'").replace(/\r?\n/g,' ')}
function escapeAI(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]})}
function formatAIChatDate(v){if(!v)return'No date';try{var d=new Date(v);if(isNaN(d.getTime()))return'';return d.toLocaleDateString([], {month:'short',day:'numeric'})}catch(e){return''}}
function shortAIChatTitle(title){var raw=String(title||'New Chat').replace(/\s+/g,' ').trim()||'New Chat';var words=raw.split(' ').filter(Boolean);return words.slice(0,5).join(' ');}
function updateAIPageChatTitle(title){var el=document.getElementById('ai-page-chat-title');if(!el)return;var strong=el.querySelector('strong')||el;var shortTitle=shortAIChatTitle(title);strong.textContent=shortTitle;el.title=String(title||shortTitle);}
async function openKurtexAIChat(id){if(aiBusy||aiUploading)return;try{var r=await apiFetch('/api/ai/chats/'+encodeURIComponent(id)),x=await r.json();if(!r.ok)throw new Error(x.error||'Unable to open conversation');var chat=x.item||x.chat||x;kurtexAIChatId=chat.id||id;var t=document.getElementById('kurtex-ai-title');if(t)t.textContent=(chat.title||'Maintenance conversation');var box=document.getElementById('kurtex-ai-messages');if(box){box.innerHTML='';(chat.messages||[]).forEach(function(m,index){kurtexAIAdd(m.role==='assistant'?'assistant':'user',m.content||m.text||'')});if(!(chat.messages||[]).length)aiWelcome()}document.getElementById('kurtex-ai-panel').classList.remove('history-open');renderAIChatList()}catch(e){var el=document.getElementById('kurtex-ai-chat-list');if(el)el.innerHTML='<div class="ai-history-error"><i class="ph ph-warning-circle"></i><strong>Could not open conversation</strong><span>'+escapeAI(e.message||'Try again.')+'</span><button onclick="loadKurtexAIChats()">Retry</button></div>'}}
async function deleteKurtexAIChat(e,id){if(e){e.preventDefault();e.stopPropagation()}if(!confirm('Delete this conversation?'))return;try{var r=await apiFetch('/api/ai/chats/'+encodeURIComponent(id),{method:'DELETE'}),x=await r.json();if(!r.ok)throw new Error(x.error||'Unable to delete conversation');if(String(kurtexAIChatId)===String(id))newKurtexAIChat();await loadKurtexAIChats()}catch(err){var el=document.getElementById('kurtex-ai-chat-list');if(el)el.innerHTML='<div class="ai-history-error"><strong>Could not delete conversation</strong><span>'+escapeAI(err.message||'Try again.')+'</span></div>'}}

function kurtexCurrentPage(){var p=document.querySelector('.page.active');return p?(p.id||'dashboard').replace(/^page-/,''):'dashboard'}
function toggleKurtexAI(force){var p=document.getElementById('kurtex-ai-panel');if(!p)return;var open=typeof force==='boolean'?force:!p.classList.contains('open');p.classList.toggle('open',open);p.setAttribute('aria-hidden',open?'false':'true');if(open){loadKurtexAIChats();refreshAIChatContext();setTimeout(function(){document.getElementById('kurtex-ai-input').focus()},80)}}
function toggleAIHistory(){var p=document.getElementById('kurtex-ai-panel');if(!p)return;var open=!p.classList.contains('history-open');p.classList.toggle('history-open',open);if(open)loadKurtexAIChats()}
function kurtexAIAdd(role,text){var box=document.getElementById('kurtex-ai-messages'),d=document.createElement('div');d.className='kurtex-ai-msg '+role;d.innerHTML=role==='assistant'?kurtexAIFormat(text):escapeAI(text);box.appendChild(d);box.scrollTop=box.scrollHeight;return d}
function aiWelcome(){document.getElementById('kurtex-ai-messages').innerHTML='<div class="kurtex-ai-welcome"><div class="ai-welcome-icon"><i class="ph ph-wrench"></i></div><strong>How can I help?</strong><span>Describe the issue naturally. I can review Kurtex history, approved maintenance knowledge, and help narrow down the problem.</span></div>'}
function newKurtexAIChat(){if(aiBusy||aiUploading)return;aiChatPendingAttachments=[];aiChatKnowledgeSelected=[];kurtexAIChatId=null;var t=document.getElementById('kurtex-ai-title');if(t)t.textContent='Kurtex Maintenance AI';aiWelcome();document.getElementById('kurtex-ai-panel').classList.remove('history-open');document.getElementById('kurtex-ai-input').focus()}
async function loadKurtexAIChats(){var el=document.getElementById('kurtex-ai-chat-list');if(el)el.innerHTML='<div class="ai-history-loading">Loading conversations...</div>';try{var r=await apiFetch('/api/ai/chats'),x=await r.json();if(!r.ok)throw new Error(x.error||'Unable to load chats');kurtexAIChats=Array.isArray(x)?x:(x.items||x.chats||[]);renderAIChatList()}catch(e){if(el)el.innerHTML='<div class="ai-history-error"><i class="ph ph-warning-circle"></i><strong>Could not load history</strong><span>'+escapeAI(e.message||'Try again.')+'</span><button onclick="loadKurtexAIChats()">Retry</button></div>'}}
function renderAIChatList(){
 var el=document.getElementById('kurtex-ai-chat-list');if(!el)return;
 if(!kurtexAIChats.length){el.innerHTML='<div class="ai-no-chats"><i class="ph ph-chats-circle"></i><strong>No conversations yet</strong><span>Start a new chat and it will be saved here automatically.</span></div>';return}
 el.innerHTML=kurtexAIChats.map(function(c){
  var raw=(c.title||'').trim(), title=raw&&raw.toLowerCase()!=='new chat'?raw:'Maintenance conversation';
  var count=Number(c.message_count||((c.messages||[]).length)||0);
  return '<div class="ai-chat-row '+(c.id===kurtexAIChatId?'active':'')+'"><button class="ai-chat-open" type="button" onclick="openKurtexAIChat(\''+c.id+'\')"><span class="ai-chat-icon"><i class="ph ph-chat-circle-text"></i></span><span class="ai-chat-copy"><strong title="'+escapeAI(title)+'">'+escapeAI(title)+'</strong><small>'+formatAIChatDate(c.updated_at)+' · '+count+' messages</small></span></button><button class="ai-chat-delete" type="button" onclick="deleteKurtexAIChat(event,\''+c.id+'\')" title="Delete conversation"><i class="ph ph-trash"></i></button></div>'
 }).join('')
}
function toggleAITrainer(open){var t=document.getElementById('kurtex-ai-trainer');if(!t)return;t.hidden=!open;if(open)loadAIKnowledge()}

function aiKnowledgeTagClass(tag){
 var v=String(tag||'').toLowerCase(), n=0;
 for(var i=0;i<v.length;i++)n=(n+v.charCodeAt(i)*(i+1))%6;
 return 'ai-tag-tone-'+n;
}
var aiKnowledgeItems=[];
function aiKnowledgeStatus(message,type){var el=document.getElementById('ai-kb-status');if(!el)return;el.className='ai-kb-status '+(type||'');el.textContent=message||'';if(message)setTimeout(function(){if(el.textContent===message)el.textContent=''},3500)}
function aiKnowledgePreview(s){s=String(s||'').replace(/\s+/g,' ').trim();return s.length>180?s.slice(0,180)+'…':s}
async function loadAIKnowledge(){var el=document.getElementById('ai-kb-list');try{if(el)el.innerHTML='<div class="ai-kb-loading"><i class="ph ph-spinner-gap"></i> Loading knowledge…</div>';var r=await apiFetch('/api/ai/knowledge'),x=await r.json();if(!r.ok)throw new Error(x.error||'Unable to load knowledge');aiKnowledgeItems=x.items||[];var count=document.getElementById('ai-kb-count'),cases=document.getElementById('ai-case-count');if(count)count.textContent=aiKnowledgeItems.length;if(cases)cases.textContent=x.case_count||0;if(!el)return;el.innerHTML=aiKnowledgeItems.length?aiKnowledgeItems.map(function(k){var tags=(k.tags||[]).map(function(t){var col=(k.tag_colors&&k.tag_colors[t])||'gray';return'<span class="ai-kb-tag ai-tag-color-'+escapeAI(col)+'">'+escapeAI(t)+'</span>'}).join('');return '<article class="ai-kb-item" data-id="'+escapeAI(k.id)+'"><div class="ai-kb-item-top"><span class="ai-kb-source"><i class="ph '+(k.source==='file'?'ph-file-text':'ph-note-pencil')+'"></i></span><div class="ai-kb-item-copy ai-kb-main"><div class="ai-kb-title-line"><strong>'+escapeAI(k.title||'Maintenance knowledge')+'</strong>'+(tags?'<span class="ai-kb-tags-inline">'+tags+'</span>':'')+'</div><small>'+(k.source==='file'?'Imported file':'Manual entry')+(k.updated_at?' · '+escapeAI(formatAIChatDate(k.updated_at)):'')+'</small></div><div class="ai-kb-actions"><button type="button" onclick="previewAIKnowledge(\''+k.id+'\')" title="Preview"><i class="ph ph-eye"></i></button><button type="button" onclick="editAIKnowledge(\''+k.id+'\')" title="Edit"><i class="ph ph-pencil-simple"></i></button><button class="danger" type="button" onclick="deleteAIKnowledge(\''+k.id+'\')" title="Delete"><i class="ph ph-trash"></i></button></div></div></article>'}).join(''):'<div class="ai-kb-empty"><span><i class="ph ph-books"></i></span><strong>No approved knowledge yet</strong><small>Add a verified note or import a file to build the library.</small></div>'}catch(e){if(el)el.innerHTML='<div class="ai-kb-error"><i class="ph ph-warning-circle"></i><strong>Could not load knowledge library</strong><small>'+escapeAI(e.message||'Unknown error')+'</small><button type="button" onclick="loadAIKnowledge()">Retry</button></div>'}}
async function saveAIKnowledge(e){e.preventDefault();var btn=e.submitter||e.target.querySelector('button[type=submit]'),title=document.getElementById('ai-kb-title').value.trim(),content=document.getElementById('ai-kb-content').value.trim(),tags=document.getElementById('ai-kb-tags').value.split(',').map(function(x){return x.trim()}).filter(Boolean),tagColor=(document.getElementById('ai-kb-tag-color')||{}).value||'blue',tagColors={};tags.forEach(function(t){tagColors[t]=tagColor});if(!content)return;if(btn){btn.disabled=true;btn.innerHTML='<i class="ph ph-spinner-gap"></i> Adding…'}try{var r=await apiFetch('/api/ai/knowledge',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:title,content:content,tags:tags,tag_colors:tagColors})}),x=await r.json();if(!r.ok)throw new Error(x.error||'Could not add knowledge');document.getElementById('ai-kb-form').reset();aiKnowledgeStatus('Knowledge added successfully.','ok');await loadAIKnowledge()}catch(err){aiKnowledgeStatus(err.message||'Could not add knowledge','bad')}finally{if(btn){btn.disabled=false;btn.innerHTML='<i class="ph ph-check"></i> Add to knowledge'}}}
async function uploadAIKnowledge(){var input=document.getElementById('ai-kb-file'),f=input.files[0],btn=document.querySelector('.ai-upload-card .ai-secondary-btn');if(!f)return aiKnowledgeStatus('Choose a file first.','bad');var fd=new FormData();fd.append('file',f);if(btn){btn.disabled=true;btn.innerHTML='<i class="ph ph-spinner-gap"></i> Uploading…'}try{var r=await apiFetch('/api/ai/knowledge/upload',{method:'POST',body:fd}),x=await r.json();if(!r.ok)throw new Error(x.error||'Upload failed');input.value='';var name=document.getElementById('ai-file-name');if(name)name.textContent='No file selected';aiKnowledgeStatus('File added to the knowledge library.','ok');await loadAIKnowledge()}catch(err){aiKnowledgeStatus(err.message||'Upload failed','bad')}finally{if(btn){btn.disabled=false;btn.innerHTML='<i class="ph ph-upload-simple"></i> Upload & add'}}}
function previewAIKnowledge(id){var k=aiKnowledgeItems.find(function(x){return String(x.id)===String(id)});if(!k)return;var m=document.getElementById('ai-preview-modal');if(!m)return;document.getElementById('ai-preview-title').textContent=k.title||'Maintenance knowledge';document.getElementById('ai-preview-meta').textContent=(k.source==='file'?'Imported file':'Manual entry')+(k.updated_at?' · '+formatAIChatDate(k.updated_at):'');document.getElementById('ai-preview-content').textContent=k.content||'';m.hidden=false;document.body.classList.add('modal-open')}
function closeAIKnowledgePreview(){var m=document.getElementById('ai-preview-modal');if(m)m.hidden=true;document.body.classList.remove('modal-open')}
function editAIKnowledge(id){var k=aiKnowledgeItems.find(function(x){return String(x.id)===String(id)});if(!k)return;document.getElementById('ai-edit-id').value=k.id;document.getElementById('ai-edit-title').value=k.title||'';document.getElementById('ai-edit-content').value=k.content||'';document.getElementById('ai-edit-tags').value=(k.tags||[]).join(', ');
var ec=document.getElementById('ai-edit-tag-color'), first=(k.tags||[])[0], chosen=(first&&k.tag_colors&&k.tag_colors[first])||'blue';
if(ec)ec.value=chosen;syncAITagColorPicker('ai-edit-tag-color');
document.getElementById('ai-edit-modal').hidden=false;document.body.classList.add('modal-open')}
function closeAIKnowledgeEdit(){document.getElementById('ai-edit-modal').hidden=true;document.body.classList.remove('modal-open')}
async function updateAIKnowledge(e){e.preventDefault();var id=document.getElementById('ai-edit-id').value,btn=e.submitter||e.target.querySelector('button[type=submit]'),editTags=document.getElementById('ai-edit-tags').value.split(',').map(function(x){return x.trim()}).filter(Boolean),editColor=(document.getElementById('ai-edit-tag-color')||{}).value||'blue',editColors={};editTags.forEach(function(t){editColors[t]=editColor});var payload={title:document.getElementById('ai-edit-title').value.trim(),content:document.getElementById('ai-edit-content').value.trim(),tags:editTags,tag_colors:editColors};if(btn)btn.disabled=true;try{var r=await apiFetch('/api/ai/knowledge/'+id,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}),x=await r.json();if(!r.ok)throw new Error(x.error||'Update failed');closeAIKnowledgeEdit();aiKnowledgeStatus('Knowledge updated.','ok');await loadAIKnowledge()}catch(err){aiKnowledgeStatus(err.message||'Update failed','bad')}finally{if(btn)btn.disabled=false}}
async function deleteAIKnowledge(id){var k=aiKnowledgeItems.find(function(x){return String(x.id)===String(id)});if(!confirm('Delete "'+((k&&k.title)||'this knowledge item')+'"? This removes it from Kurtex AI reference material.'))return;try{var r=await apiFetch('/api/ai/knowledge/'+id,{method:'DELETE'}),x=await r.json();if(!r.ok)throw new Error(x.error||'Delete failed');aiKnowledgeStatus('Knowledge deleted.','ok');await loadAIKnowledge()}catch(err){aiKnowledgeStatus(err.message||'Delete failed','bad')}}
document.addEventListener('DOMContentLoaded',function(){if(document.getElementById('page-ai_knowledge'))loadAIKnowledge();/* Connection test is manual to avoid paid model calls at page load. */});


function kurtexAIFormat(text){
 var raw=String(text==null?'':text)
   .replace(/\\r\\n|\\n|\\r/g,'\n')
   .replace(/\\\s*$/gm,'')
   .replace(/&#x20;|&nbsp;/gi,' ')
   .replace(/[ \t]+\n/g,'\n')
   .replace(/\n[ \t]*\n[ \t]*\n+/g,'\n\n');
 var safe=escapeAI(raw).replace(/\r/g,'');
 safe=safe.replace(/^#{1,6}\s+(.+)$/gm,'<div class="ai-md-heading">$1</div>');
 safe=safe.replace(/\*\*([^*\n]+)\*\*/g,'<strong>$1</strong>');
 safe=safe.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g,'$1<em>$2</em>');
 safe=safe.replace(/^\s*[-*]\s+(.+)$/gm,'<div class="ai-md-bullet"><span>•</span><span>$1</span></div>');
 safe=safe.replace(/^\s*(\d+)\.\s+(.+)$/gm,'<div class="ai-md-number"><span>$1.</span><span>$2</span></div>');
 safe=safe.replace(/^\s*\*+\s*$/gm,'');
 safe=safe.replace(/(^|\s)#{1,6}(?=\s|$)/g,'$1');
 safe=safe.replace(/\n{2,}/g,'<br>').replace(/\n/g,'<br>');
 return safe
}
function toggleKurtexAIFullscreen(force){
 var p=document.getElementById('kurtex-ai-panel');if(!p)return;
 var on=typeof force==='boolean'?force:!p.classList.contains('ai-fullscreen');
 p.classList.toggle('ai-fullscreen',on);
 var b=p.querySelector('.ai-expand-btn i');if(b)b.className=on?'ph ph-arrows-in':'ph ph-arrows-out';
}
function toggleAIPageSidebar(force){
 var s=document.getElementById('ai-page-sidebar'),layout=document.querySelector('#page-ai_assistant .ai-page-layout');if(!s||!layout)return;
 var show=typeof force==='boolean'?force:s.classList.contains('collapsed');
 s.classList.toggle('collapsed',!show);layout.classList.toggle('sidebar-collapsed',!show);
 if(window.matchMedia&&window.matchMedia('(max-width:1024px)').matches){
   document.body.classList.toggle('ai-mobile-history-open',show);
 }
}
async function loadAIPageSidebar(){
 var list=document.getElementById('ai-page-sidebar-list');if(!list)return;
 list.innerHTML='<div class="ai-history-empty">Loading conversations...</div>';
 var timeout=setTimeout(function(){if(list.textContent.trim()==='Loading conversations...')list.innerHTML='<div class="ai-history-empty">Chat history is taking longer than expected. <button type="button" onclick="loadAIPageSidebar()">Retry</button></div>'},8000);
 try{
  var r=await apiFetch('/api/ai/chats'),x=await r.json(),items=Array.isArray(x)?x:(x.items||x.chats||[]);
  list.innerHTML=items.length?items.map(function(c){return '<div class="ai-side-chat '+(String(c.id)===String(kurtexAIChatId)?'active':'')+'" data-chat-id="'+escapeAI(c.id)+'"><button type="button" class="ai-side-chat-open" onclick="openAIPageSavedChat(\''+aiAttr(c.id)+'\',this.closest(\'.ai-side-chat\'))"><span>'+escapeAI(c.title||'Maintenance conversation')+'</span><small>'+escapeAI(formatAIChatDate(c.updated_at||c.created_at))+'</small></button><button type="button" class="ai-side-chat-delete" onclick="deleteAIPageSavedChat(event,\''+aiAttr(c.id)+'\')" title="Delete chat" aria-label="Delete chat"><i class="ph ph-trash"></i></button></div>'}).join(''):'<div class="ai-history-empty ai-history-empty-rich"><span class="ai-empty-icon"><i class="ph ph-chats-circle"></i></span><strong>No conversations yet</strong><small>Start a maintenance chat and it will appear here automatically for quick access later.</small><button type="button" onclick="newKurtexAIPageChat()"><i class="ph ph-plus"></i> Start a chat</button></div>'
 }catch(e){list.innerHTML='<div class="ai-history-empty">Unable to load chats. <button type="button" onclick="loadAIPageSidebar()">Retry</button></div>'}finally{clearTimeout(timeout)}
}

async function deleteAIPageSavedChat(event,id){
 if(aiBusy||aiUploading)return;
 if(!confirm("Delete this conversation?"))return;
 if(event){event.preventDefault();event.stopPropagation()}
 if(!id)return;
 try{
  var r=await fetch('/api/ai/chats/'+encodeURIComponent(id),{method:'DELETE'});
  if(!r.ok)throw new Error('Delete failed');
  if(String(kurtexAIChatId)===String(id)){
   kurtexAIChatId=null;
   var box=document.getElementById('ai-page-messages');
   if(box)box.innerHTML='<div class="ai-page-welcome"><div class="ai-hero-spark"><i class="ph ph-sparkle"></i></div><h3>Ask Kurtex AI</h3><p>Diagnose maintenance issues, check fault codes, find similar cases, or use approved fleet knowledge.</p><div class="ai-start-grid"><button type="button" onclick="aiPageQuick(\'Help me diagnose a maintenance issue. Ask only for the details you actually need.\')"><i class="ph ph-stethoscope"></i><span>Diagnose an issue</span></button><button type="button" onclick="aiPageQuick(\'Find similar Kurtex cases based on the maintenance issue I describe.\')"><i class="ph ph-files"></i><span>Find similar cases</span></button><button type="button" onclick="aiPageQuick(\'Help me identify a truck, trailer, or reefer fault code and explain the next checks.\')"><i class="ph ph-warning-circle"></i><span>Check a fault code</span></button><button type="button" onclick="aiPageQuick(\'Use the approved maintenance knowledge library to help me with a repair or troubleshooting procedure.\')"><i class="ph ph-book-open-text"></i><span>Browse knowledge</span></button></div></div>';
  }
  await loadAIPageSidebar();
 }catch(e){console.error('AI chat delete failed',e)}
}

async function newKurtexAIPageChat(){if(aiBusy||aiUploading)return;aiOpenSequence++;aiChatPendingAttachments=[];aiChatKnowledgeSelected=[];aiClearAttachmentTray();
 aiPageFailed=null;
 if(document.getElementById('ai-history-overlay').classList.contains('open'))closeAIPageHistory();
 kurtexAIChatId=null;updateAIPageChatTitle('New Chat');
 var side=document.getElementById('ai-page-sidebar-list');
 if(side)side.querySelectorAll('.ai-side-chat.active').forEach(function(el){el.classList.remove('active')});
 var t=document.getElementById('ai-page-messages');
 if(t)t.innerHTML='<div class="ai-page-welcome"><div class="ai-hero-spark"><i class="ph ph-sparkle"></i></div><h3>Ask Kurtex AI</h3><p>Diagnose maintenance issues, check fault codes, find similar cases, or use approved fleet knowledge.</p><div class="ai-start-grid"><button type="button" onclick="aiPageQuick(\'Help me diagnose a maintenance issue. Ask only for the details you actually need.\')"><i class="ph ph-stethoscope"></i><span>Diagnose an issue</span></button><button type="button" onclick="aiPageQuick(\'Find similar Kurtex cases based on the maintenance issue I describe.\')"><i class="ph ph-files"></i><span>Find similar cases</span></button><button type="button" onclick="aiPageQuick(\'Help me identify a truck, trailer, or reefer fault code and explain the next checks.\')"><i class="ph ph-warning-circle"></i><span>Check a fault code</span></button><button type="button" onclick="aiPageQuick(\'Use the approved maintenance knowledge library to help me with a repair or troubleshooting procedure.\')"><i class="ph ph-book-open-text"></i><span>Browse knowledge</span></button></div></div>';
 var i=document.getElementById('ai-page-input');if(i){i.value='';i.dispatchEvent(new Event('input'));if(!window.isKurtexMobile?.())i.focus()}
 try{await aiEnsureChat()}catch(e){console.error('AI new chat failed',e);if(t)t.insertAdjacentHTML('beforeend','<div class="ai-chat-failure">'+escapeAI(e.message||'Unable to create a new chat.')+' <button type="button" onclick="newKurtexAIPageChat()">Retry</button></div>')}
}
var aiHistoryItems=[],aiHistoryFocus=null,aiOptionsFocus=null,aiPageFailed=null;
function openAIOptions(){
 aiOptionsFocus=document.activeElement;
 document.getElementById('ai-options-overlay').classList.add('open');lockBodyScroll();
 document.getElementById('ai-options-overlay').querySelector('.modal-close').focus();
}
function closeAIOptions(){
 document.getElementById('ai-options-overlay').classList.remove('open');unlockBodyScroll();
 if(aiOptionsFocus?.isConnected)aiOptionsFocus.focus({preventScroll:true});
}
async function openAIPageHistory(){
 const overlay=document.getElementById('ai-history-overlay');
 if(!overlay.classList.contains('open'))aiHistoryFocus=document.activeElement;
 overlay.classList.add('open');lockBodyScroll();
 document.getElementById('ai-history-search').value='';
 document.getElementById('ai-history-clear').hidden=true;
 document.getElementById('ai-history-results').innerHTML='<p class="loading" role="status">Loading conversations…</p>';
 overlay.querySelector('.modal-close').focus();
 try{
  const response=await apiFetch('/api/ai/chats','ai-mobile-history'),data=await response.json();
  aiHistoryItems=Array.isArray(data)?data:(data.items||data.chats||[]);
  renderAIPageHistory();
 }catch(error){if(error.name!=='AbortError')document.getElementById('ai-history-results').innerHTML='<p role="alert">Conversations could not load.</p><button type="button" class="ai-options-done" onclick="openAIPageHistory()">Retry</button>';}
}
function closeAIPageHistory(){
 document.getElementById('ai-history-overlay').classList.remove('open');unlockBodyScroll();
 if(aiHistoryFocus?.isConnected)aiHistoryFocus.focus({preventScroll:true});
}
function renderAIPageHistory(){
 const query=document.getElementById('ai-history-search').value.trim().toLocaleLowerCase();
 document.getElementById('ai-history-clear').hidden=!query;
 const items=aiHistoryItems.filter(chat=>String(chat.title||'Maintenance conversation').toLocaleLowerCase().includes(query));
 document.getElementById('ai-history-results').innerHTML=items.length?items.map(chat=>'<button type="button" class="ai-page-history-row" data-chat-id="'+escapeAI(chat.id)+'" onclick="openAIPageSavedChat(this.dataset.chatId)"><strong>'+escapeAI(chat.title||'Maintenance conversation')+'</strong><small>'+escapeAI(formatAIChatDate(chat.updated_at||chat.created_at))+'</small></button>').join(''):'<p class="ai-history-empty">'+(query?'No conversations match your search.':'No conversations yet. Your first conversation will appear here.')+'</p>';
}
function clearAIPageHistorySearch(){document.getElementById('ai-history-search').value='';renderAIPageHistory();document.getElementById('ai-history-search').focus();}
async function openAIPageSavedChat(id,clicked){if(aiBusy||aiUploading)return;var hp=document.getElementById('ai-page-history');if(hp)hp.remove();var sequence=++aiOpenSequence;
 aiPageFailed=null;
 if(document.getElementById('ai-history-overlay').classList.contains('open'))closeAIPageHistory();
 var list=document.getElementById('ai-page-sidebar-list');
 if(list){
  list.querySelectorAll('.ai-side-chat.active').forEach(function(el){el.classList.remove('active')});
  if(clicked)clicked.classList.add('active');
 }
 // Switch identity before loading context so the previous chat's knowledge/files
 // can never visually bleed into the newly selected conversation.
 kurtexAIChatId=String(id);
 aiChatKnowledgeSelected=[];
 aiChatPendingAttachments=[];
 aiClearAttachmentTray();
 var contextBox=document.getElementById('ai-chat-context');
 var inlineKnowledge=document.getElementById('ai-chat-knowledge-inline');
 if(contextBox)contextBox.innerHTML='';
 if(inlineKnowledge)inlineKnowledge.innerHTML='';
 try{
  var r=await apiFetch('/api/ai/chats/'+encodeURIComponent(id)),x=await r.json();
  if(!r.ok)throw new Error(x.error||'Unable to open conversation');
  if(sequence!==aiOpenSequence)return;
  var chat=x.item||x.chat||x;
  kurtexAIChatId=String(chat.id||id);
  updateAIPageChatTitle(chat.title||'Maintenance conversation');
  var box=document.getElementById('ai-page-messages');box.innerHTML='';
  (chat.messages||[]).forEach(function(m,index){
   var d=document.createElement('div'),role=m.role==='assistant'?'assistant':'user';
   d.className='ai-page-msg '+role;
   var body=role==='assistant'?kurtexAIFormat(m.content||m.text||'')+aiSourceLinks(m.sources,m.research_status):escapeAI(m.content||m.text||'');
   d.innerHTML=aiWrapMessage(role,body+(role==='assistant'?aiEvidenceCards([],m.parts):''),aiAttachmentCards(kurtexAIChatId,chat.attachments||[],m.attachment_ids||[]),m.id||String(index));
   box.appendChild(d)
  });
  // Important: an empty saved chat remains the SAME saved chat.
  // Do not call newKurtexAIPageChat() here; that used to create a second chat
  // and made chat-scoped knowledge appear to jump between conversations.
  if(!(chat.messages||[]).length){
   box.innerHTML='<div class="ai-page-welcome"><div class="ai-hero-spark"><i class="ph ph-sparkle"></i></div><h3>Ask Kurtex AI</h3><p>This conversation is ready. Attached knowledge and files stay with this chat only.</p><div class="ai-start-grid"><button type="button" onclick="aiPageQuick(\'Help me diagnose a maintenance issue. Ask only for the details you actually need.\')"><i class="ph ph-stethoscope"></i><span>Diagnose an issue</span></button><button type="button" onclick="aiPageQuick(\'Find similar Kurtex cases based on the maintenance issue I describe.\')"><i class="ph ph-files"></i><span>Find similar cases</span></button><button type="button" onclick="aiPageQuick(\'Help me identify a truck, trailer, or reefer fault code and explain the next checks.\')"><i class="ph ph-warning-circle"></i><span>Check a fault code</span></button><button type="button" onclick="openAIChatKnowledge()"><i class="ph ph-book-open-text"></i><span>Use knowledge</span></button></div></div>';
  }
  var p=document.getElementById('ai-page-history');if(p)p.remove();
  box.scrollTop=box.scrollHeight;
  await refreshAIChatContext();
  if(window.matchMedia&&window.matchMedia('(max-width:1024px)').matches) toggleAIPageSidebar(false);
  loadAIPageSidebar();
 }catch(e){
  if(sequence!==aiOpenSequence)return;
  console.error('AI saved chat open failed',e);
  kurtexAIChatId=null;
  if(contextBox)contextBox.innerHTML='';
  if(inlineKnowledge)inlineKnowledge.innerHTML='';
 }
}

function openKurtexAIPageChat(){
 if(typeof showPage==='function')showPage('ai_assistant');
 var p=document.getElementById('kurtex-ai-panel');if(p)p.classList.remove('open','ai-fullscreen','history-open');
 syncKurtexAIPageFromPanel();loadAIPageSidebar();
 setTimeout(function(){var i=document.getElementById('ai-page-input');if(i)i.focus()},50)
}
function syncKurtexAIPageFromPanel(){
 var src=document.getElementById('kurtex-ai-messages'),dst=document.getElementById('ai-page-messages');if(!src||!dst)return;
 var msgs=src.querySelectorAll('.kurtex-ai-msg');
 if(!msgs.length)return;
 dst.innerHTML='';
 msgs.forEach(function(m){var d=document.createElement('div');d.className='ai-page-msg '+(m.classList.contains('user')?'user':'assistant');d.innerHTML=m.innerHTML;dst.appendChild(d)});
 dst.scrollTop=dst.scrollHeight
}
async function sendKurtexAIPage(e){
 if(aiBusy||aiUploading)return false;
 if(e&&e.preventDefault)e.preventDefault();
 var input=document.getElementById('ai-page-input'),msg=(input&&input.value||'').trim();if(!msg)return false;
 if(aiPageFailed&&aiPageFailed.message===msg){aiPageFailed.user.remove();aiPageFailed.error.remove();aiPageFailed=null;}
 aiSetBusy(true);
 input.value='';
 input.dispatchEvent(new Event('input'));
 var dst=document.getElementById('ai-page-messages');var welcome=dst&&dst.querySelector('.ai-page-welcome');if(welcome)welcome.remove();
 var sentAttachments=aiChatPendingAttachments.slice();
 var requestKey=aiSendRequestKey(msg,sentAttachments);
 aiChatPendingAttachments=[];aiClearAttachmentTray();
 var u=document.createElement('div');u.className='ai-page-msg user';u.innerHTML=aiWrapMessage('user',escapeAI(msg),aiAttachmentCards(kurtexAIChatId,sentAttachments,sentAttachments.map(function(a){return a.id})));dst.appendChild(u);
 var wait=document.createElement('div');wait.className='ai-page-msg assistant thinking';wait.innerHTML='<div class="ai-msg-content"><div class="ai-msg-body">Searching fleet knowledge...</div></div>';dst.appendChild(wait);dst.scrollTop=dst.scrollHeight;
 try{
  // A knowledge selection may exist before a chat exists. Persist it only when
  // the first real message is sent, so opening Knowledge never creates a chat.
  if(!kurtexAIChatId && aiChatKnowledgeSelected.length){
   var pendingKnowledge=aiChatKnowledgeSelected.slice();
   await aiEnsureChat();
   aiChatKnowledgeSelected=pendingKnowledge;
   await apiFetch('/api/ai/chats/'+encodeURIComponent(kurtexAIChatId)+'/context',{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({knowledge_ids:pendingKnowledge})});
  }
  var r=await apiFetch('/api/ai/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:msg,request_id:requestKey,chat_id:kurtexAIChatId,page:'ai_assistant',web_search:!!document.getElementById('ai-web-search')?.checked,similar_cases:!!document.getElementById('ai-similar-cases')?.checked,attachment_ids:sentAttachments.map(function(a){return a.id})})}),x=await r.json();
  if(!r.ok)throw new Error(x.error||'Kurtex AI request failed');
  aiLastSendAttempt=null;
  var followReply=dst.scrollHeight-dst.scrollTop-dst.clientHeight<100;
  kurtexAIChatId=x.chat_id||x.id||kurtexAIChatId;if(x.title)updateAIPageChatTitle(x.title);wait.classList.remove('thinking');wait.innerHTML=aiWrapMessage('assistant',kurtexAIFormat(x.answer||x.response||x.reply||x.message||'No response returned.')+aiEvidenceCards([],x.parts)+aiSourceLinks(x.sources,x.research_status),'',x.message_id);aiLoadEvidencePhotos(wait);aiClearAttachmentTray();await refreshAIChatContext();
  if(followReply)dst.scrollTop=dst.scrollHeight;
  loadKurtexAIChats();loadAIPageSidebar()
 }catch(err){aiChatPendingAttachments=sentAttachments;await refreshAIChatContext();wait.classList.remove('thinking');wait.classList.add('ai-chat-failure');input.value=msg;input.dispatchEvent(new Event('input'));wait.textContent='Message failed. Your draft and attachments are restored. '+(err.message||'Try again.');wait.setAttribute('role','alert');var retry=document.createElement('button');retry.type='button';retry.textContent='Retry message';retry.addEventListener('click',()=>sendKurtexAIPage({preventDefault:function(){}}));wait.append(retry);aiPageFailed={message:msg,user:u,error:wait};}
 aiSetBusy(false);if(!window.isKurtexMobile?.())input.focus();return false
}
function aiPageQuick(text){var i=document.getElementById('ai-page-input');if(!i)return;i.value=text;sendKurtexAIPage({preventDefault:function(){}})}
async function sendKurtexAI(e){
 if(aiBusy||aiUploading)return false;
 if(e&&e.preventDefault)e.preventDefault();
 var input=document.getElementById('kurtex-ai-input'),msg=(input&&input.value||'').trim();if(!msg)return false;
 aiSetBusy(true);
 var panel=document.getElementById('kurtex-ai-panel');if(panel){panel.classList.add('open');panel.classList.remove('history-open')}
 input.value='';var w=document.querySelector('#kurtex-ai-messages .kurtex-ai-welcome');if(w)w.remove();
 var sentAttachments=aiChatPendingAttachments.slice(),requestKey=aiSendRequestKey(msg,sentAttachments);
 aiChatPendingAttachments=[];aiClearAttachmentTray();
 kurtexAIAdd('user',msg);var wait=kurtexAIAdd('assistant','Reviewing maintenance evidence…');wait.classList.add('thinking');
 try{
  if(!kurtexAIChatId && aiChatKnowledgeSelected.length){var pendingKnowledge=aiChatKnowledgeSelected.slice();await aiEnsureChat();aiChatKnowledgeSelected=pendingKnowledge;await apiFetch('/api/ai/chats/'+encodeURIComponent(kurtexAIChatId)+'/context',{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({knowledge_ids:pendingKnowledge})});}
  var r=await apiFetch('/api/ai/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:msg,request_id:requestKey,chat_id:kurtexAIChatId,page:kurtexCurrentPage(),web_search:!!(document.getElementById('kurtex-ai-web-search')||{}).checked,similar_cases:!!(document.getElementById('kurtex-ai-similar-cases')||{}).checked,attachment_ids:sentAttachments.map(function(a){return a.id}),knowledge_ids:aiChatKnowledgeSelected.slice()})}),x=await r.json();if(!r.ok)throw new Error(x.error||'Kurtex AI request failed');kurtexAIChatId=x.chat_id||x.id||kurtexAIChatId;wait.classList.remove('thinking');wait.innerHTML=kurtexAIFormat(x.answer||x.response||x.reply||x.message||'No response returned.');var t=document.getElementById('kurtex-ai-title');if(t&&x.title)t.textContent=x.title;loadKurtexAIChats()}catch(err){input.value=msg;wait.remove();var box=document.getElementById('kurtex-ai-messages');if(box&&!box.querySelector('.ai-chat-failure')){var f=document.createElement('div');f.className='ai-chat-failure';f.textContent='Message was not sent. '+(err.message||'Check AI Training diagnostics.');box.appendChild(f)}if(typeof loadNotifications==='function')loadNotifications()}aiSetBusy(false);input.focus();return false
}
function kurtexAIQuick(text){var input=document.getElementById('kurtex-ai-input');if(!input)return;input.value=text;sendKurtexAI({preventDefault:function(){}})}
document.addEventListener('DOMContentLoaded',function(){
 var box=document.getElementById('kurtex-ai-messages');
 if(box && box.children.length===1) aiWelcome();
 var input=document.getElementById('kurtex-ai-input');
 if(input)input.addEventListener('keydown',function(e){if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();sendKurtexAI({preventDefault:function(){}})}});
 var pageInput=document.getElementById('ai-page-input');
 if(pageInput)pageInput.addEventListener('keydown',function(e){if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing&&!window.isKurtexMobile?.()){e.preventDefault();if(!aiBusy&&!aiUploading&&pageInput.value.trim())sendKurtexAIPage({preventDefault:function(){}})}});
});

async function testKurtexAIConnection(){var el=document.getElementById('ai-test-result');if(!el)return;el.className='ai-test-result testing';el.textContent='Testing Workers AI...';try{var r=await apiFetch('/api/ai/test',{method:'POST'}),x=await r.json();if(!r.ok||!x.ok)throw new Error(x.error||'Connection failed');el.className='ai-test-result ok';el.textContent='Connected · '+(x.model||'Workers AI')+' · '+(x.response||'OK')}catch(e){el.className='ai-test-result bad';el.textContent='Failed · '+(e.message||'Unknown error')+'. Check Railway logs for the Cloudflare HTTP response.'}}

document.addEventListener('click',function(e){
 var n=e.target&&e.target.closest?e.target.closest('.nav-item[data-page="ai_assistant"]'):null;
 if(n)setTimeout(loadAIPageSidebar,80);
});
document.addEventListener('DOMContentLoaded',function(){
 if(document.getElementById('page-ai_assistant')&&document.getElementById('page-ai_assistant').classList.contains('active'))loadAIPageSidebar();
});

function syncAITagColorPicker(inputId){
 var input=document.getElementById(inputId);if(!input)return;
 var picker=input.closest('.ai-tag-color-picker');if(!picker)return;
 picker.querySelectorAll('.ai-color-swatch').forEach(function(b){b.classList.toggle('selected',b.dataset.color===input.value)})
}
document.addEventListener('click',function(e){
 var b=e.target.closest&&e.target.closest('.ai-color-swatch');if(!b)return;
 var picker=b.closest('.ai-tag-color-picker'),input=picker&&document.getElementById(picker.dataset.target);if(!input)return;
 input.value=b.dataset.color;syncAITagColorPicker(input.id);
});


// v124 — chat-scoped files + verified knowledge context
var aiChatKnowledgeOptions=[], aiChatKnowledgeSelected=[];
var aiChatPendingAttachments=[];
async function aiEnsureChat(){
 if(kurtexAIChatId)return kurtexAIChatId;
 var r=await apiFetch('/api/ai/chats',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:'New maintenance chat'})});
 var x={};try{x=await r.json()}catch(_e){}
 if(!r.ok||!x.id)throw new Error(x.error||'Unable to create a new chat.');
 kurtexAIChatId=x.id;
 await loadAIPageSidebar();
 await refreshAIChatContext();
 return x.id;
}
async function prepareAIChatUpload(file){
 if(!file||!/^image\/(jpeg|png|webp)$/i.test(file.type||""))return file;
 if(file.size<=1800000)return file;
 return new Promise(function(resolve){var img=new Image(),url=URL.createObjectURL(file);img.onload=function(){try{var max=1600,scale=Math.min(1,max/Math.max(img.naturalWidth||1,img.naturalHeight||1)),c=document.createElement("canvas");c.width=Math.max(1,Math.round(img.naturalWidth*scale));c.height=Math.max(1,Math.round(img.naturalHeight*scale));c.getContext("2d").drawImage(img,0,0,c.width,c.height);c.toBlob(function(blob){URL.revokeObjectURL(url);resolve(blob?new File([blob],file.name.replace(/\.(png|webp)$/i,".jpg"),{type:"image/jpeg"}):file)},"image/jpeg",.82)}catch(e){URL.revokeObjectURL(url);resolve(file)}};img.onerror=function(){URL.revokeObjectURL(url);resolve(file)};img.src=url})
}
async function uploadAIChatFiles(input){
 var files=Array.from((input&&input.files)||[]);if(!files.length||aiBusy||aiUploading)return;
 aiUploading=true;aiUploadStatus('Preparing attachments…');
 try{
  var id=await aiEnsureChat();
  for(var file of files){
   var isVideo=/^video\//.test(file.type)||/\.(mp4|mov|webm)$/i.test(file.name);
   if(isVideo&&file.size>80*1024*1024)throw new Error('Video must be under 80 MB and 2 minutes.');
   var budget=aiChatPendingAttachments.reduce(function(n,a){return n+(a.kind==='video'?8:a.kind==='image'?1:0)},0);
   if(budget+(isVideo?8:/^image\//.test(file.type)?1:0)>12)throw new Error('Send one video and up to four photos per message.');
   var prepared=[isVideo?file:await prepareAIChatUpload(file)];
   if(aiChatPendingAttachments.length+prepared.length>12)throw new Error('Maximum 12 images/files per message. Send the current attachments first.');
   for(var upload of prepared){
    aiUploadStatus(isVideo?'Uploading video, extracting views and transcribing the driver…':'Uploading '+upload.name+'…');
    var fd=new FormData();fd.append('file',upload,upload.name);
    var r=await apiFetch('/api/ai/chats/'+encodeURIComponent(id)+'/files',{method:'POST',body:fd}),x=await r.json();
    aiChatPendingAttachments.push({id:x.id,name:x.name,kind:x.kind,mime:x.mime,audio_status:x.audio_status,warning:x.warning});
   }
  }
  aiSyncAttachmentStatus();
 }catch(e){aiUploadStatus(e.message||'Could not attach file');}
 finally{aiUploading=false;if(input)input.value='';await refreshAIChatContext();}
}
async function refreshAIChatContext(){
 var requestedChat=kurtexAIChatId;
 var box=document.getElementById('ai-chat-context'),inline=document.getElementById('ai-chat-knowledge-inline');
 if(!kurtexAIChatId){if(box)box.innerHTML='';if(inline)inline.innerHTML='';return}
 try{
  var results=await Promise.all([apiFetch('/api/ai/chats/'+encodeURIComponent(kurtexAIChatId)),apiFetch('/api/ai/knowledge/options')]);
  var x=await results[0].json(), ox=await results[1].json(), chat=x.item||x.chat||x,files=chat.attachments||[],kids=chat.knowledge_ids||[];
  if(requestedChat!==kurtexAIChatId)return;
  aiChatKnowledgeSelected=kids.slice();aiChatKnowledgeOptions=ox.items||aiChatKnowledgeOptions||[];
  if(box){
   var pendingIds=new Set(aiChatPendingAttachments.map(function(a){return String(a.id)}));
   var pending=files.filter(function(a){return pendingIds.has(String(a.id))});
   box.innerHTML=pending.map(function(a){
    var isImg=a.kind==='image'||/^image\//.test(a.mime||'');
    var url='/api/ai/chats/'+encodeURIComponent(kurtexAIChatId)+'/files/'+encodeURIComponent(a.id);
    return '<div class="ai-attach-card '+(isImg?'is-image':'is-file')+'">'+
      (isImg?'<button type="button" class="ai-attach-image-open" onclick="openAIImagePreview(\''+url+'\',\''+aiAttr(a.name||'Image')+'\')" aria-label="Preview image"><img src="'+url+'" alt=""></button>':'<span class="ai-file-icon"><i class="ph '+aiFileIcon(a.name)+'"></i></span>')+
      '<span class="ai-attach-meta"><strong>'+escapeAI(a.name)+'</strong><small>'+escapeAI(aiFileType(a.name,a.mime))+'</small>'+(a.kind==='video'?'<button type="button" onclick="openAITranscript(\''+aiAttr(a.id)+'\')">Transcript</button>':'')+'</span>'+
      '<button type="button" class="ai-attach-remove" onclick="removeAIChatFile(&quot;'+aiAttr(a.id)+'&quot;)" aria-label="Remove"><i class="ph ph-x"></i></button></div>'
   }).join('')
  }
  if(inline){var selected=aiChatKnowledgeOptions.filter(function(k){return kids.indexOf(k.id)>=0});inline.innerHTML=selected.map(function(k){var colors=k.tag_colors||{},tags=k.tags||[],color=(tags.length&&colors[tags[0]])||k.tag_color||'blue';return '<span class="ai-inline-kb ai-tag-color-'+escapeAI(color)+'" title="'+escapeAI(k.title||'Maintenance knowledge')+'"><button type="button" class="ai-inline-kb-open" onclick="openAIChatKnowledge()"><span class="ai-inline-kb-dot"></span><span>'+escapeAI(k.title||'Maintenance knowledge')+'</span></button><button type="button" class="ai-inline-kb-remove" onclick="event.stopPropagation();removeAIChatKnowledge(\''+aiAttr(k.id)+'\')" aria-label="Remove '+escapeAI(k.title||'knowledge source')+'" title="Remove from this chat"><i class="ph ph-x"></i></button></span>'}).join('')}
 syncKurtexAIPanelContext();
 }catch(e){}
}
function syncKurtexAIPanelContext(){
 var s=document.getElementById('ai-chat-context'),d=document.getElementById('kurtex-ai-chat-context');if(d)d.innerHTML=s?s.innerHTML:'';
 var sk=document.getElementById('ai-chat-knowledge-inline'),dk=document.getElementById('kurtex-ai-chat-knowledge-inline');if(dk)dk.innerHTML=sk?sk.innerHTML:'';
 var ss=document.getElementById('ai-upload-status'),ds=document.getElementById('kurtex-ai-upload-status');if(ds&&ss)ds.textContent=ss.textContent||'';
}
function openAIImagePreview(url,name){var m=document.getElementById('ai-image-preview-modal'),img=document.getElementById('ai-image-preview-img'),title=document.getElementById('ai-image-preview-name');if(!m||!img)return;img.src=url;img.alt=name||'Attachment preview';if(title)title.textContent=name||'Image preview';m.hidden=false;document.body.classList.add('modal-open')}
function closeAIImagePreview(){var m=document.getElementById('ai-image-preview-modal'),img=document.getElementById('ai-image-preview-img');if(m)m.hidden=true;if(img)img.removeAttribute('src');document.body.classList.remove('modal-open')}
function aiFileType(name,mime){
 var ext=((name||'').split('.').pop()||'file').toUpperCase();
 if(/^image\//.test(mime||''))return 'Image';
 if(/^video\//.test(mime||''))return 'Video + speech';
 var map={PDF:'PDF',DOCX:'Document',XLSX:'Spreadsheet',ZIP:'Archive',CSV:'CSV',JSON:'JSON',TXT:'Text',MD:'Markdown'};
 return map[ext]||'File'
}
function aiFileIcon(name){
 var ext=((name||'').split('.').pop()||'').toLowerCase();
 if(['mp4','mov','webm'].includes(ext))return 'ph-video-camera';
 if(ext==='xlsx'||ext==='csv')return 'ph-file-xls';
 if(ext==='zip')return 'ph-file-zip';
 if(ext==='pdf')return 'ph-file-pdf';
 if(ext==='docx'||ext==='txt'||ext==='md')return 'ph-file-text';
 return 'ph-file'
}
function aiAttachmentCards(chatId,attachments,ids){
 var wanted=new Set((ids||[]).map(String));
 return (attachments||[]).filter(function(a){return wanted.has(String(a.id))}).map(function(a){
  var isImg=a.kind==='image'||/^image\//.test(a.mime||''),url='/api/ai/chats/'+encodeURIComponent(chatId)+'/files/'+encodeURIComponent(a.id);
  if(a.kind==='video'||a.kind==='audio')return '<div class="ai-msg-video"><a href="'+url+'" target="_blank" rel="noopener"><i class="ph '+(a.kind==='audio'?'ph-waveform':'ph-video')+'"></i> '+escapeAI(a.name||(a.kind==='audio'?'Audio':'Video'))+'</a><button type="button" onclick="openAITranscript(\''+aiAttr(a.id)+'\')">Transcript</button></div>';
  if(isImg)return '<button type="button" class="ai-msg-attachment ai-msg-image" onclick="openAIImagePreview(\''+url+'\',\''+aiAttr(a.name||'Image')+'\')"><img src="'+url+'" alt="'+escapeAI(a.name||'Attached image')+'"><span>'+escapeAI(a.name||'Image')+'</span></button>';
  return '<a class="ai-msg-attachment ai-msg-file" href="'+url+'" target="_blank"><span class="ai-file-icon"><i class="ph '+aiFileIcon(a.name)+'"></i></span><span><strong>'+escapeAI(a.name||'Attachment')+'</strong><small>'+escapeAI(aiFileType(a.name,a.mime))+'</small></span></a>'
 }).join('')
}
function copyAIMessage(btn){
 var msg=btn.closest('.ai-page-msg');if(!msg)return;
 var body=msg.querySelector('.ai-msg-body')||msg;
 navigator.clipboard.writeText(body.innerText||body.textContent||'').then(function(){
  btn.classList.add('copied');btn.innerHTML='<i class="ph ph-check"></i>';
  setTimeout(function(){btn.classList.remove('copied');btn.innerHTML='<i class="ph ph-copy"></i>'},1200)
 }).catch(function(){})
}
function aiWrapMessage(role,html,attachmentHtml,messageId){
 return '<div class="ai-msg-content">'+(attachmentHtml||'')+'<div class="ai-msg-body">'+html+'</div></div><div class="ai-msg-actions"><button type="button" class="ai-msg-action" onclick="copyAIMessage(this)" aria-label="Copy" data-tooltip="Copy"><i class="ph ph-copy"></i></button>'+(role==='assistant'&&messageId?'<button type="button" class="ai-msg-action" data-message-id="'+escapeAI(messageId)+'" onclick="openAIReport(this)" aria-label="Report" data-tooltip="Report"><i class="ph ph-flag"></i></button>':'')+'</div>'
}
async function removeAIChatFile(fid){
 if(!kurtexAIChatId||aiBusy||aiUploading)return;
 var removed=aiChatPendingAttachments.find(function(a){return String(a.id)===String(fid)});
 aiChatPendingAttachments=aiChatPendingAttachments.filter(function(a){return String(a.id)!==String(fid)});
 aiSyncAttachmentStatus();
 try{
  var r=await apiFetch('/api/ai/chats/'+encodeURIComponent(kurtexAIChatId)+'/files/'+encodeURIComponent(fid),{method:'DELETE'});
  if(!r.ok)throw new Error('Unable to remove attachment');
 }catch(e){
  if(removed)aiChatPendingAttachments.push(removed);
  aiSyncAttachmentStatus();
  throw e;
 }
 await refreshAIChatContext();
 aiSyncAttachmentStatus();
}
async function openAIChatKnowledge(){
 try{
  // Opening the picker is a UI action only. Do not create a conversation until
  // the agent actually sends a message or uploads a file.
  var r=await apiFetch('/api/ai/knowledge/options'),x=await r.json();if(!r.ok)throw new Error(x.error||'Unable to load knowledge');
  aiChatKnowledgeOptions=x.items||[];
  if(kurtexAIChatId){
   var cr=await apiFetch('/api/ai/chats/'+encodeURIComponent(kurtexAIChatId)),chat=await cr.json();
   aiChatKnowledgeSelected=(chat.knowledge_ids||[]).slice();
  }
  document.getElementById('ai-chat-knowledge-modal').hidden=false;renderAIChatKnowledgeOptions();
 }catch(e){alert(e.message||'Unable to load knowledge')}
}
function closeAIChatKnowledge(){var m=document.getElementById('ai-chat-knowledge-modal');if(m)m.hidden=true}
function aiChatKnowledgeMeta(k){
 var tags=(k.tags||[]).filter(Boolean), source=(k.source||'').toLowerCase(), title=(k.title||'').toLowerCase();
 var type=source==='file'?'FILE':source==='database'||title.indexOf('database')>=0?'DATABASE':'VERIFIED';
 var detail=tags.length?tags.join(' · '):(type==='DATABASE'?'Fleet case history':type==='FILE'?'Imported reference':'Verified knowledge');
 return {type:type,detail:detail};
}
function filteredAIChatKnowledge(){
 var q=((document.getElementById('ai-chat-kb-search')||{}).value||'').trim().toLowerCase();
 return aiChatKnowledgeOptions.filter(function(k){return !q||((k.title||'')+' '+(k.tags||[]).join(' ')+' '+(k.source||'')).toLowerCase().includes(q)});
}
function renderAIChatKnowledgeOptions(){
 var list=document.getElementById('ai-chat-kb-list');if(!list)return;
 var items=filteredAIChatKnowledge();
 list.innerHTML=items.length?items.map(function(k){
  var on=aiChatKnowledgeSelected.indexOf(k.id)>=0,m=aiChatKnowledgeMeta(k);
  return '<button type="button" class="ai-chat-kb-option '+(on?'is-selected':'')+'" onclick="toggleAIChatKnowledge(\''+aiAttr(k.id)+'\','+(!on)+')" aria-pressed="'+(on?'true':'false')+'">'+
   '<span class="ai-chat-kb-check"><i class="ph '+(on?'ph-check':'ph-plus')+'"></i></span>'+
   '<span class="ai-chat-kb-copy"><strong>'+escapeAI(k.title||'Maintenance knowledge')+'</strong><small>'+escapeAI(m.detail)+'</small></span>'+
   '<span class="ai-chat-kb-type">'+escapeAI(m.type)+'</span></button>'
 }).join(''):'<div class="ai-chat-kb-empty"><i class="ph ph-magnifying-glass"></i><strong>No matching sources</strong><small>Try a different title, tag, or source type.</small></div>';
 var c=document.getElementById('ai-chat-kb-count'),label=document.getElementById('ai-chat-kb-selected-label'),n=aiChatKnowledgeSelected.length;
 if(c)c.textContent=n+' source'+(n===1?'':'s')+' selected';if(label)label.textContent=n?'Selected '+n:'No sources selected';
}
function toggleAIChatKnowledge(id,on){var i=aiChatKnowledgeSelected.indexOf(id);if(on&&i<0)aiChatKnowledgeSelected.push(id);if(!on&&i>=0)aiChatKnowledgeSelected.splice(i,1);renderAIChatKnowledgeOptions()}
function selectAllAIChatKnowledge(){filteredAIChatKnowledge().forEach(function(k){if(aiChatKnowledgeSelected.indexOf(k.id)<0)aiChatKnowledgeSelected.push(k.id)});renderAIChatKnowledgeOptions()}
function clearAIChatKnowledge(){aiChatKnowledgeSelected=[];renderAIChatKnowledgeOptions()}
async function removeAIChatKnowledge(id){
 if(aiBusy)return;
 aiChatKnowledgeSelected=aiChatKnowledgeSelected.filter(function(x){return String(x)!==String(id)});
 if(!kurtexAIChatId){renderPendingAIKnowledge();return}
 var r=await apiFetch('/api/ai/chats/'+encodeURIComponent(kurtexAIChatId)+'/context',{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({knowledge_ids:aiChatKnowledgeSelected})});
 if(r.ok)await refreshAIChatContext();
}
function renderPendingAIKnowledge(){
 var inline=document.getElementById('ai-chat-knowledge-inline');if(!inline)return;
 var selected=aiChatKnowledgeOptions.filter(function(k){return aiChatKnowledgeSelected.indexOf(k.id)>=0});
 inline.innerHTML=selected.map(function(k){return '<span class="ai-inline-kb" title="'+escapeAI(k.title||'Maintenance knowledge')+'"><button type="button" class="ai-inline-kb-open" onclick="openAIChatKnowledge()"><span class="ai-inline-kb-dot"></span><span>'+escapeAI(k.title||'Maintenance knowledge')+'</span></button><button type="button" class="ai-inline-kb-remove" onclick="event.stopPropagation();removeAIChatKnowledge(\''+aiAttr(k.id)+'\')" aria-label="Remove '+escapeAI(k.title||'knowledge source')+'"><i class="ph ph-x"></i></button></span>'}).join('');
}
async function applyAIChatKnowledge(){
 if(!kurtexAIChatId){closeAIChatKnowledge();renderPendingAIKnowledge();return}
 var r=await apiFetch('/api/ai/chats/'+encodeURIComponent(kurtexAIChatId)+'/context',{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({knowledge_ids:aiChatKnowledgeSelected})});if(r.ok){closeAIChatKnowledge();refreshAIChatContext()}
}

function aiSetBusy(on){
 aiBusy=on;
 document.querySelectorAll('.ai-compose-send,#kurtex-ai-panel button[type="submit"]').forEach(function(b){b.disabled=on;b.setAttribute('aria-busy',String(on));});
}
function aiUploadStatus(text){var el=document.getElementById('ai-upload-status');if(el)el.textContent=text||'';}
function aiSyncAttachmentStatus(){
 var items=aiChatPendingAttachments||[];
 if(!items.length){aiUploadStatus('');return}
 if(items.some(function(a){return a.warning})){aiUploadStatus('Audio needs attention. Open Transcript to retry or add the driver explanation.');return}
 var videos=items.filter(function(a){return a.kind==='video'||/^video\//.test(a.mime||'')});
 if(videos.length){aiUploadStatus(videos.length>1?'Videos ready':'Video ready');return}
 aiUploadStatus('');
}
function aiKnowledgeTemplate(){
 var el=document.getElementById('ai-kb-content');if(!el)return;
 if(el.value.trim()&&!confirm('Replace the current unsaved knowledge draft?'))return;
 el.value='Equipment / make / model / year:\nComponent and fault code:\nReported symptom:\nVisible defect and photo reference:\nTests performed and measured results:\nConfirmed root cause:\nRepair performed:\nPost-repair verification:\nPrevious AI mistake and correct observation:\nSource / technician / date:\nApplicability and exceptions:';el.focus();
}

function aiClearAttachmentTray(){
 var tray=document.getElementById('ai-chat-context');if(tray)tray.replaceChildren();
 aiUploadStatus('');
}
function aiSourceLinks(sources,status){
 var links=(sources||[]).filter(function(x){return /^https:\/\//.test(x.url||'')});
 if(!links.length)return status&&status!=='disabled'?'<p class="ai-research-note">Web research: '+escapeAI(status.replace(/_/g,' '))+'.</p>':'';
 return '<details class="ai-sources"><summary>Research sources ('+links.length+')</summary>'+links.map(function(x){return '<a href="'+escapeAI(x.url)+'" target="_blank" rel="noopener noreferrer">['+escapeAI(x.citation)+'] '+escapeAI(x.title)+'</a><small>'+escapeAI(x.source_type)+'</small>';}).join('')+'</details>';
}
var aiReportMessageId=null;
function openAIReport(button){
 aiReportMessageId=button.dataset.messageId;
 var modal=document.getElementById('ai-report-modal'),reason=document.getElementById('ai-report-reason'),status=document.getElementById('ai-report-status');
 if(reason)reason.value='';if(status)status.textContent='';if(modal)modal.hidden=false;setTimeout(function(){if(reason)reason.focus()},0);
}
function closeAIReport(){var modal=document.getElementById('ai-report-modal');if(modal)modal.hidden=true;aiReportMessageId=null}
async function submitAIReport(){
 var reason=(document.getElementById('ai-report-reason').value||'').trim(),category=document.getElementById('ai-report-category').value,status=document.getElementById('ai-report-status');
 if(reason.length<5){status.textContent='Please add a short reason for the developer.';return}
 status.textContent='Sending report…';
 try{var r=await apiFetch('/api/ai/chats/'+encodeURIComponent(kurtexAIChatId)+'/feedback',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message_id:aiReportMessageId,category:category,reason:reason})}),x=await r.json();if(!r.ok)throw new Error(x.error||'Unable to report answer');status.textContent='Report sent.';setTimeout(closeAIReport,700)}
 catch(e){status.textContent=e.message||'Unable to report answer'}
}
var aiTranscriptFile=null,aiTranscriptChat=null;
async function openAITranscript(id){
 aiTranscriptFile=id;aiTranscriptChat=kurtexAIChatId;
 try{var r=await apiFetch('/api/ai/chats/'+encodeURIComponent(aiTranscriptChat)+'/files/'+encodeURIComponent(id)+'/transcript'),x=await r.json();
 document.getElementById('ai-transcript-text').value=x.transcript||'';
 document.getElementById('ai-transcript-status').textContent=(x.audio_status||'').replace(/_/g,' ')+(x.warning?' - '+x.warning:'');
 document.getElementById('ai-transcript-modal').hidden=false;
 }catch(e){alert(e.message);}
}
async function saveAITranscript(retry){
 var status=document.getElementById('ai-transcript-status');status.textContent=retry?'Transcribing…':'Saving…';
 try{var r=await apiFetch('/api/ai/chats/'+encodeURIComponent(aiTranscriptChat)+'/files/'+encodeURIComponent(aiTranscriptFile)+'/transcript',{method:retry?'POST':'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({transcript:document.getElementById('ai-transcript-text').value})}),x=await r.json();
 document.getElementById('ai-transcript-text').value=x.transcript||'';status.textContent=retry?'Transcript updated. Please check model names and fault codes.':'Saved. This is used for the next question about this video.';
 }catch(e){status.textContent=e.message;}
}

function aiSendRequestKey(message,attachments){
 var signature=JSON.stringify([kurtexAIChatId,message,attachments.map(function(a){return a.id})]);
 if(aiLastSendAttempt&&aiLastSendAttempt.signature===signature)return aiLastSendAttempt.id;
 var id=typeof crypto!=='undefined'&&crypto.randomUUID?crypto.randomUUID():Date.now().toString(36)+'-'+Math.random().toString(36).slice(2);
 aiLastSendAttempt={signature:signature,id:id};return id;
}

/* v9 — maintenance evidence cards */
function aiEvidenceCards(cases,parts){
 var out='';
 if(Array.isArray(cases)&&cases.length){
  out+='<section class="ai-evidence-block"><div class="ai-evidence-head"><span><i class="ph ph-files"></i> Similar fleet cases</span><small>Historical evidence - verify before repair</small></div><div class="ai-case-cards">'+cases.map(function(c){
   var vehicle=[c.vehicle?c.vehicle.toUpperCase():'',c.unit?('Unit '+c.unit):''].filter(Boolean).join(' · ');
   var who=c.reported_by||[c.driver,c.group].filter(Boolean).join(' / ');
   return '<article class="ai-case-card">'+
    '<div class="ai-case-top"><span class="ai-case-status '+escapeAI((c.status||'').toLowerCase())+'">'+escapeAI(c.status||'Fleet case')+'</span>'+(vehicle?'<strong>'+escapeAI(vehicle)+'</strong>':'')+'</div>'+
    (who?'<div class="ai-case-field"><span>Reported driver / group</span><b>'+escapeAI(who)+'</b></div>':'')+
    (c.issue?'<div class="ai-case-field"><span>Issue</span><p>'+escapeAI(c.issue)+'</p></div>':'')+
    (c.notes?'<div class="ai-case-field"><span>Case notes</span><p>'+escapeAI(c.notes)+'</p></div>':'')+
    (c.resolution?'<div class="ai-case-resolution"><span><i class="ph ph-check-circle"></i> How it was solved</span><p>'+escapeAI(c.resolution)+'</p>'+(c.solved_by?'<small>Solved by '+escapeAI(c.solved_by)+'</small>':'')+'</div>':'<div class="ai-case-unresolved"><i class="ph ph-info"></i> No recorded resolution</div>')+
   '</article>';
  }).join('')+'</div></section>';
 }
 if(Array.isArray(parts)&&parts.length){
  out+='<section class="ai-evidence-block"><div class="ai-evidence-head"><span><i class="ph ph-wrench"></i> Relevant Parts Manual</span><small>Reference photos may vary by installed model</small></div><div class="ai-part-cards">'+parts.slice(0,4).map(function(p){return '<article class="ai-part-card" data-ai-part-photo="'+aiAttr(p.id||p.name||'')+'"><div class="ai-part-thumb"><i class="ph ph-image"></i></div><div class="ai-part-copy"><strong>'+escapeAI(p.name||'Part')+'</strong><span>'+escapeAI([p.category,p.location].filter(Boolean).join(' · '))+'</span><button type="button" onclick="aiOpenPart(\''+aiAttr(p.id||'')+'\')">Open in Parts Manual <i class="ph ph-arrow-up-right"></i></button></div></article>'}).join('')+'</div></section>';
 }
 return out;
}
function aiLoadEvidencePhotos(root){
 (root||document).querySelectorAll('.ai-part-card[data-ai-part-photo]').forEach(function(card){if(card.dataset.photoLoaded)return;card.dataset.photoLoaded='1';var id=card.dataset.aiPartPhoto,p=(window.partsDB||[]).find(function(x){return String(x.id)===String(id)});if(!p)return;var qs=new URLSearchParams({q:p.name,cat:p.cat||'',keywords:p.keywords||''});fetch('/api/part_image?'+qs).then(function(r){return r.ok?r.json():Promise.reject()}).then(function(x){var im=(x.results||[])[0];if(!im)return;var box=card.querySelector('.ai-part-thumb');box.innerHTML='<img src="'+escapeAI(im.thumbnail_url||im.image_url)+'" alt="'+escapeAI(p.name)+' reference" loading="lazy" referrerpolicy="no-referrer">'}).catch(function(){})
 });
}
function aiOpenPart(id){if(typeof showPage==='function')showPage('parts_manual');setTimeout(function(){if(typeof selectPart==='function'&&id)selectPart(id)},80)}
