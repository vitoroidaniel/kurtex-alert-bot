var aiLearningOffset=0;
async function loadFleetKnowledgeStats(){
 var out=document.getElementById('fleet-knowledge-status');if(!out)return;
 try{var r=await apiFetch('/api/ai/fleet-knowledge'),x=await r.json();if(!r.ok)throw new Error(x.error||'Unable to load fleet knowledge');
  document.getElementById('fleet-knowledge-total').textContent=x.total||0;document.getElementById('fleet-knowledge-resolved').textContent=x.resolved||0;document.getElementById('fleet-knowledge-review').textContent=x.incomplete||0;
  out.innerHTML=x.last_sync?'<i class="ph ph-check-circle"></i><span><b>Fleet index updated</b> · '+new Date(x.last_sync).toLocaleString([], {dateStyle:'medium',timeStyle:'short'})+'</span>':'<i class="ph ph-clock"></i><span>Waiting for first sync</span>';
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
