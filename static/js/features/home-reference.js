/* Role-specific dashboard enhancements; all metrics originate from the existing briefing API. */
var kxCharts={};
function kxUpdateHome(data){
 var root=document.getElementById('page-overview');if(!root||!data)return;
 if(root.dataset.dashboardRole==='management'){
   var m=data.metrics||{},cards=document.getElementById('stat-grid');
   var items=[['Total Cases',Number(m.active||0)+Number(m.resolved||0),'ph-file-text','reported'],['Open Cases',m.active,'ph-warning','attention'],['Resolved',m.resolved,'ph-check-circle','resolved'],['Missed / Delayed',m.attention,'ph-clock','active'],['Active Agents',null,'ph-users','agents'],['Units in Fleet',null,'ph-truck','fleet']];
   cards.innerHTML=items.map(function(x){return '<button type="button" class="stat-card home-metric" data-tone="'+x[3]+'" '+(x[3]==='fleet'?'onclick="homeFindUnit()"':x[3]==='agents'?'onclick="showPage(\'agents\')"':'onclick="homeCases(\'all\')"')+'><span class="home-metric-icon"><i class="ph '+x[2]+'"></i></span><span class="home-metric-content"><strong class="stat-value">'+(x[1]===null?'—':Number(x[1]).toLocaleString())+'</strong><span class="stat-label">'+x[0]+'</span></span><span class="home-metric-footer">'+(x[1]===null?'See details':'Live case data')+'</span></button>'}).join('');
   kxRenderCharts();
 }else{
   var items=document.querySelectorAll('#stat-grid .home-metric');
   items.forEach(function(item){var label=item.querySelector('.stat-label');if(label&&label.textContent==='Reported today')label.textContent='My Cases';if(label&&label.textContent==='Active maintenance')label.textContent='Active Cases';});
   kxRenderReminders();
 }
}
function kxRenderCharts(){
 if(!window.Chart||!homeState.data||!document.getElementById('kx-cases-trend'))return;
 var data=homeState.data,events=data.activity||[],cases=data.recent_cases||[],now=new Date(),n=Number(document.getElementById('kx-trend-range')?.value||7),labels=[],newCounts=[],doneCounts=[];
 for(var i=n-1;i>=0;i--){var d=new Date(now);d.setDate(d.getDate()-i);var day=d.toLocaleDateString('en-CA',{timeZone:'America/Chicago'});labels.push(d.toLocaleDateString('en-US',{month:'short',day:'numeric'}));newCounts.push(events.filter(function(e){return e.kind==='reported'&&String(e.at||'').slice(0,10)===day}).length);doneCounts.push(events.filter(function(e){return e.kind==='resolved'&&String(e.at||'').slice(0,10)===day}).length)}
 if(kxCharts.trend)kxCharts.trend.destroy();
 kxCharts.trend=new Chart(document.getElementById('kx-cases-trend'),{type:'bar',data:{labels:labels,datasets:[{label:'New',data:newCounts,backgroundColor:'#3984fb',borderRadius:3},{label:'Resolved',data:doneCounts,backgroundColor:'#2fc889',borderRadius:3}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{position:'top',labels:{boxWidth:10,usePointStyle:true}}},scales:{x:{stacked:true,grid:{display:false}},y:{stacked:true,beginAtZero:true,ticks:{precision:0}}}}});
 var m=data.metrics||{},values=[Number(m.resolved||0),Number(m.active||0),Number(m.attention||0)],colors=['#2fc889','#ff4b56','#ffad30'];
 if(kxCharts.donut)kxCharts.donut.destroy();kxCharts.donut=new Chart(document.getElementById('kx-status-donut'),{type:'doughnut',data:{labels:['Resolved','Active','Needs attention'],datasets:[{data:values,backgroundColor:colors,borderWidth:0}]},options:{cutout:'74%',responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}}}});
 document.getElementById('kx-donut-total').textContent=String(values[0]+values[1]);
 document.getElementById('kx-status-legend').innerHTML=values.map(function(v,i){return '<div class="kx-legend-row"><span><i style="background:'+colors[i]+'"></i>'+['Resolved','Active','Attention'][i]+'</span><strong>'+v+'</strong></div>'}).join('');
}
function kxPeriod(button){document.querySelectorAll('.kx-period button').forEach(function(b){b.classList.toggle('active',b===button)});var range=button.dataset.range;if(typeof setHomeRange==='function')setHomeRange('agents',range);}
function kxAsk(question){if(!question||!question.trim())return;showPage('ai_assistant');var input=document.querySelector('#page-ai_assistant textarea');if(input){input.value=question;input.dispatchEvent(new Event('input',{bubbles:true}));input.focus();}}
function kxReminders(){try{return JSON.parse(localStorage.getItem('kurtex-home-reminders')||'[]')}catch(e){return []}}
function kxRenderReminders(){var root=document.getElementById('kx-reminders');if(!root)return;var notes=kxReminders();root.innerHTML=notes.length?notes.map(function(n,i){return '<div class="kx-reminder-row"><input type="checkbox" '+(n.done?'checked':'')+' onchange="kxToggleReminder('+i+')"><span '+(n.done?'style="text-decoration:line-through;opacity:.55"':'')+'></span><button onclick="kxEditReminder('+i+')" aria-label="Edit note"><i class="ph ph-pencil"></i></button><button onclick="kxDeleteReminder('+i+')" aria-label="Delete note"><i class="ph ph-trash"></i></button></div>'}).join(''):'<p style="color:#72809a;font-size:12px">No reminders yet. Add one to keep track of your work.</p>';notes.forEach(function(n,i){root.querySelectorAll('.kx-reminder-row span')[i].textContent=n.text})}
function kxSaveReminders(items){localStorage.setItem('kurtex-home-reminders',JSON.stringify(items));kxRenderReminders()}
function kxAddReminder(){var text=prompt('New reminder');if(text&&text.trim()){var items=kxReminders();items.push({text:text.trim(),done:false});kxSaveReminders(items)}}
function kxEditReminder(i){var items=kxReminders(),text=prompt('Edit reminder',items[i].text);if(text&&text.trim()){items[i].text=text.trim();kxSaveReminders(items)}}
function kxDeleteReminder(i){if(!confirm('Delete this reminder?'))return;var items=kxReminders();items.splice(i,1);kxSaveReminders(items)}
function kxToggleReminder(i){var items=kxReminders();items[i].done=!items[i].done;kxSaveReminders(items)}
if(document.getElementById('kx-reminders'))kxRenderReminders();
