var aiLearningOffset=0;
async function loadAILearning(offset){
 var list=document.getElementById('ai-learning-list');if(!list)return;
 var status=document.getElementById('ai-learning-status');
 try{
  var state=document.getElementById('ai-learning-filter').value;
  var r=await apiFetch('/api/ai/learning?status='+encodeURIComponent(state)+'&offset='+(offset||0)),x=await r.json();
  aiLearningOffset=x.offset||0;
  status.textContent=x.total+' '+state+' entries. Approved: '+(x.counts.approved||0)+'.';
  list.innerHTML=(x.items||[]).map(function(item){return '<article class="ai-lesson" data-lesson-id="'+escapeAI(item.id)+'"><span>'+escapeAI(item.source_type)+' · '+escapeAI(item.status)+'</span><label>Lesson title<input class="lesson-title" value="'+escapeAI(item.title)+'"></label><details><summary>Original evidence (may include incorrect AI advice)</summary><pre>'+escapeAI(item.evidence)+'</pre></details><label>Verified lesson<textarea class="lesson-content" rows="5" placeholder="Write the corrected, verified maintenance guidance. Include evidence and applicability; do not approve an AI suggestion as a confirmed repair.">'+escapeAI(item.content||'')+'</textarea></label><label>Tags<input class="lesson-tags" value="'+escapeAI(JSON.parse(item.tags||'[]').join(', '))+'"></label><div class="ai-learning-toolbar"><button type="button" onclick="reviewAILesson(this,\'approved\')">Approve verified lesson</button><button type="button" onclick="reviewAILesson(this,\'pending\')">Keep pending</button><button type="button" onclick="reviewAILesson(this,\'rejected\')">Reject / revoke</button></div></article>';}).join('')||'<p>No entries on this page.</p>';
 }catch(e){status.textContent=e.message;}
}
async function syncAILearning(){
 var status=document.getElementById('ai-learning-status');status.textContent='Collecting available chats and cases…';
 try{var r=await apiFetch('/api/ai/learning/import',{method:'POST'}),x=await r.json();await loadAILearning(0);status.textContent+=' Scanned '+x.chats+' chats and '+x.cases+' cases.';}catch(e){status.textContent=e.message;}
}
async function reviewAILesson(button,state){
 var card=button.closest('.ai-lesson');
 if(state==='approved'&&!confirm('Have you verified this lesson against mechanic findings or a reliable procedure? Approval shares it with all agents.'))return;
 button.disabled=true;
 try{await apiFetch('/api/ai/learning/'+encodeURIComponent(card.dataset.lessonId),{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({status:state,title:card.querySelector('.lesson-title').value,content:card.querySelector('.lesson-content').value,tags:card.querySelector('.lesson-tags').value.split(',').map(function(x){return x.trim()}).filter(Boolean)})});await loadAILearning(aiLearningOffset);}
 catch(e){document.getElementById('ai-learning-status').textContent=e.message;}finally{button.disabled=false;}
}
document.addEventListener('click',function(e){if(e.target.closest('[data-page="ai_training"]'))loadAILearning(0);});
async function loadFleetKnowledgeStats(){
 var out=document.getElementById('fleet-knowledge-status');if(!out)return;
 try{var r=await apiFetch('/api/ai/fleet-knowledge'),x=await r.json();if(!r.ok)throw new Error(x.error||'Unable to load fleet knowledge');
  document.getElementById('fleet-knowledge-total').textContent=x.total||0;document.getElementById('fleet-knowledge-resolved').textContent=x.resolved||0;document.getElementById('fleet-knowledge-review').textContent=x.incomplete||0;
  out.textContent=x.last_sync?'Last indexed '+new Date(x.last_sync).toLocaleString():'Waiting for first sync.';
 }catch(e){out.textContent=e.message;}
}
async function syncFleetKnowledge(){
 var out=document.getElementById('fleet-knowledge-status');if(!out)return;out.textContent='Indexing fleet cases…';
 try{var r=await apiFetch('/api/ai/fleet-knowledge',{method:'POST'}),x=await r.json();if(!r.ok)throw new Error(x.error||'Sync failed');out.textContent='Fleet knowledge updated. '+x.changed+' case record(s) changed.';await loadFleetKnowledgeStats();}catch(e){out.textContent=e.message;}
}
document.addEventListener('click',function(e){if(e.target.closest('[data-page="ai_knowledge"]'))setTimeout(loadFleetKnowledgeStats,0);});
async function importFleetKnowledgeCSV(){
 var input=document.getElementById('fleet-knowledge-csv'),out=document.getElementById('fleet-knowledge-status');if(!input||!input.files[0]){out.textContent='Choose a CSV first.';return;}
 var fd=new FormData();fd.append('file',input.files[0]);out.textContent='Importing and indexing CSV…';
 try{var r=await apiFetch('/api/ai/fleet-knowledge/import-csv',{method:'POST',body:fd}),x=await r.json();if(!r.ok)throw new Error(x.error||'Import failed');out.textContent='Imported '+x.rows+' rows; '+x.changed+' fleet records added or updated.';input.value='';await loadFleetKnowledgeStats();}catch(e){out.textContent=e.message;}
}
