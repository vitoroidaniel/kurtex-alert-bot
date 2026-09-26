var kurtexAIChatId=null,kurtexAIChats=[];
function kurtexCurrentPage(){var p=document.querySelector('.page.active');return p?(p.id||'dashboard').replace(/^page-/,''):'dashboard'}
function toggleKurtexAI(force){var p=document.getElementById('kurtex-ai-panel');if(!p)return;var open=typeof force==='boolean'?force:!p.classList.contains('open');p.classList.toggle('open',open);p.setAttribute('aria-hidden',open?'false':'true');if(open){loadKurtexAIChats();setTimeout(function(){document.getElementById('kurtex-ai-input').focus()},80)}}
function toggleAIHistory(){var p=document.getElementById('kurtex-ai-panel');if(!p)return;var open=!p.classList.contains('history-open');p.classList.toggle('history-open',open);if(open)loadKurtexAIChats()}
function kurtexAIAdd(role,text){var box=document.getElementById('kurtex-ai-messages'),d=document.createElement('div');d.className='kurtex-ai-msg '+role;d.textContent=text;box.appendChild(d);box.scrollTop=box.scrollHeight;return d}
function aiWelcome(){document.getElementById('kurtex-ai-messages').innerHTML='<div class="kurtex-ai-welcome"><div class="ai-welcome-icon"><i class="ph ph-wrench"></i></div><strong>How can I help?</strong><span>Describe the issue naturally. I can review Kurtex history, approved maintenance knowledge, and help narrow down the problem.</span></div>'}
function newKurtexAIChat(){kurtexAIChatId=null;var t=document.getElementById('kurtex-ai-title');if(t)t.textContent='Kurtex Maintenance AI';aiWelcome();document.getElementById('kurtex-ai-panel').classList.remove('history-open');document.getElementById('kurtex-ai-input').focus()}
async function loadKurtexAIChats(){var el=document.getElementById('kurtex-ai-chat-list');if(el)el.innerHTML='<div class="ai-history-loading">Loading conversations...</div>';try{var r=await apiFetch('/api/ai/chats'),x=await r.json();if(!r.ok)throw new Error(x.error||'Unable to load chats');kurtexAIChats=x.items||[];renderAIChatList()}catch(e){if(el)el.innerHTML='<div class="ai-history-error"><i class="ph ph-warning-circle"></i><strong>Could not load history</strong><span>'+escapeAI(e.message||'Try again.')+'</span><button onclick="loadKurtexAIChats()">Retry</button></div>'}}
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
async function loadAIKnowledge(){try{var r=await apiFetch('/api/ai/knowledge'),x=await r.json();if(!r.ok)return;document.getElementById('ai-kb-count').textContent=(x.items||[]).length;document.getElementById('ai-case-count').textContent=x.case_count||0;var el=document.getElementById('ai-kb-list');el.innerHTML=(x.items||[]).length?(x.items||[]).map(function(k){return '<div class="ai-kb-row"><div><strong>'+escapeAI(k.title)+'</strong><small>'+escapeAI((k.source||'manual')+' · '+(k.tags||[]).join(', '))+'</small></div><button onclick="deleteAIKnowledge(\''+k.id+'\')"><i class="ph ph-trash"></i></button></div>'}).join(''):'<div class="ai-no-chats">No approved knowledge added yet.</div>'}catch(e){}}
async function saveAIKnowledge(e){e.preventDefault();var title=document.getElementById('ai-kb-title').value.trim(),content=document.getElementById('ai-kb-content').value.trim(),tags=document.getElementById('ai-kb-tags').value.split(',').map(function(x){return x.trim()}).filter(Boolean);if(!content)return;var r=await apiFetch('/api/ai/knowledge',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:title,content:content,tags:tags})});if(r.ok){document.getElementById('ai-kb-form').reset();loadAIKnowledge()}}
async function uploadAIKnowledge(){var f=document.getElementById('ai-kb-file').files[0];if(!f)return alert('Choose a file first.');var fd=new FormData();fd.append('file',f);var r=await apiFetch('/api/ai/knowledge/upload',{method:'POST',body:fd});var x=await r.json();if(!r.ok)return alert(x.error||'Upload failed');document.getElementById('ai-kb-file').value='';loadAIKnowledge()}
async function deleteAIKnowledge(id){if(!confirm('Remove this approved knowledge from Kurtex AI?'))return;var r=await apiFetch('/api/ai/knowledge/'+id,{method:'DELETE'});if(r.ok)loadAIKnowledge()}
document.addEventListener('DOMContentLoaded',initAITrainer);

document.addEventListener('DOMContentLoaded',function(){
 var box=document.getElementById('kurtex-ai-messages');
 if(box && box.children.length===1) aiWelcome();
 var input=document.getElementById('kurtex-ai-input');
 if(input)input.addEventListener('keydown',function(e){if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();sendKurtexAI({preventDefault:function(){}})}});
});

async function testKurtexAIConnection(){var el=document.getElementById('ai-test-result');if(!el)return;el.className='ai-test-result testing';el.textContent='Testing Workers AI...';try{var r=await apiFetch('/api/ai/test',{method:'POST'}),x=await r.json();if(!r.ok||!x.ok)throw new Error(x.error||'Connection failed');el.className='ai-test-result ok';el.textContent='Connected · '+(x.model||'Workers AI')+' · '+(x.response||'OK')}catch(e){el.className='ai-test-result bad';el.textContent='Failed · '+(e.message||'Unknown error')+'. Check Railway logs for the Cloudflare HTTP response.'}}
