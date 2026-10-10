/* v125: widget empty states, stable controls, and a single date filter. */
(function(){
 const root=document.getElementById('page-overview');if(!root)return;
 const ids=['home-recent-activity','lb-overview','home-attention-list','units-overview'];
 const names={'home-recent-activity':'No recent activity','lb-overview':'No agent activity available','home-attention-list':'No cases need attention','units-overview':'No recurring units found'};
 function fillEmpty(){if(!root.classList.contains('active'))return;ids.forEach(id=>{const el=document.getElementById(id);if(!el||el.hidden)return;if(!el.children.length&&!el.textContent.trim()){el.innerHTML='<div class="kx-widget-empty"><i class="ph ph-info"></i>'+names[id]+'</div>';}})}
 let timer;const observer=new MutationObserver(()=>{clearTimeout(timer);timer=setTimeout(fillEmpty,1400)});
 ['home-recent-activity','lb-overview','home-attention-list','units-overview'].forEach(id=>{const el=document.getElementById(id);if(el)observer.observe(el,{childList:true})});
 // New dashboard widget settings extend the legacy catalog without moving elements between grids.
 const newItems=[['trend','Cases Trend','.kx-manager-charts>.kx-panel:nth-child(1)'],['status','Cases by Status','.kx-manager-charts>.kx-panel:nth-child(2)'],['top_agents','Top Agents by Cases','.kx-manager-charts>.kx-panel:nth-child(3)']];
 const key='kurtex-home-extra-widgets-v125-'+(document.body.dataset.userId||'user');
 function prefs(){try{return JSON.parse(localStorage.getItem(key)||'{}')}catch(_){return {}}}
 function apply(){const p=prefs();newItems.forEach(([id,label,selector])=>{const el=root.querySelector(selector);if(el)el.hidden=p[id]===false});}
 function addControls(){const settings=document.getElementById('layout-settings-body');if(!settings||settings.querySelector('.kx-new-widget-settings'))return;const group=document.createElement('section');group.className='kx-new-widget-settings';group.innerHTML='<h3>Dashboard charts</h3><p style="font-size:12px;color:#778399;margin:0 0 10px">Choose which management widgets appear on Home.</p>'+newItems.map(([id,label])=>'<label class="setting-row"><span>'+label+'</span><input type="checkbox" data-kx-extra="'+id+'" '+(prefs()[id]!==false?'checked':'')+'></label>').join('');group.addEventListener('change',e=>{if(!e.target.matches('[data-kx-extra]'))return;const p=prefs();p[e.target.dataset.kxExtra]=e.target.checked;localStorage.setItem(key,JSON.stringify(p));apply()});settings.append(group)}
 const mo=new MutationObserver(()=>{if(document.getElementById('layout-settings-overlay')?.classList.contains('open'))addControls()});
 const overlay=document.getElementById('layout-settings-overlay');if(overlay)mo.observe(overlay,{attributes:true,attributeFilter:['class']});
 const settings=document.getElementById('layout-settings-body');if(settings)mo.observe(settings,{childList:true});
 apply();setTimeout(fillEmpty,2500);
})();
