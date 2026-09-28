var kurtexNotifications=[],notificationFilter='all';
function notificationSeenValue(v){return v===true||v===1||v==='1'||String(v).toLowerCase()==='true'}
function normalizeNotification(n){if(n)n.seen=notificationSeenValue(n.seen);return n}
function notificationIcon(n){if(n.kind==='ai')return'ph-sparkle';if(n.severity==='warning'||n.severity==='error')return'ph-warning-circle';return'ph-bell'}
async function loadNotifications(){try{
 var r=await apiFetch('/api/notifications'),x=await r.json();if(!r.ok)return;
 var remote=Array.isArray(x)?x:(x.items||x.notifications||[]),byId={};
 kurtexNotifications.forEach(function(item){if(item&&item.id){normalizeNotification(item);byId[String(item.id)]=item}});
 remote.forEach(function(item){if(!item||!item.id)return;normalizeNotification(item);var key=String(item.id);byId[key]=item});
 kurtexNotifications=Object.keys(byId).map(function(k){return byId[k]}).sort(function(a,b){return new Date(b.created_at||0)-new Date(a.created_at||0)});
 updateNotificationBadge(kurtexNotifications.filter(function(item){return!item.seen}).length);renderNotifications()
}catch(e){renderNotifications()}}
function updateNotificationBadge(n){var b=document.getElementById('notification-count'),s=document.getElementById('notification-summary');if(!b)return;b.hidden=!n;b.textContent=n>99?'99+':n;if(s)s.textContent=n?n+' unseen':'All caught up'}
function toggleNotifications(){var p=document.getElementById('notification-panel');if(!p)return;var open=p.hidden;p.hidden=!open;if(open){notificationFilter='all';document.querySelectorAll('.notification-tabs button').forEach(function(x){x.classList.toggle('active',x.dataset.filter==='all')});renderNotifications()}}
function filterNotifications(f,b){notificationFilter=f;document.querySelectorAll('.notification-tabs button').forEach(function(x){x.classList.toggle('active',x===b)});renderNotifications()}
function renderNotifications(){var el=document.getElementById('notification-list');if(!el)return;var items=kurtexNotifications.filter(function(n){if(notificationFilter==='unseen')return!n.seen;if(notificationFilter==='alerts')return n.severity==='warning'||n.severity==='error'||n.kind==='ai';return true});el.innerHTML=items.length?items.map(function(n){return '<article class="notification-item '+(!n.seen?'unseen ':'')+(n.severity||'info')+'"><span class="notification-icon"><i class="ph '+notificationIcon(n)+'"></i></span><span class="notification-copy"><strong>'+escapeNotif(n.title)+'</strong><span>'+escapeNotif(n.message)+'</span><small>'+notifDate(n.created_at)+'</small></span><span class="notification-actions">'+(!n.seen?'<button type="button" onclick="markNotificationSeen(\''+n.id+'\')"><i class="ph ph-check"></i> Seen</button>':'<span class="notification-seen"><i class="ph ph-check-circle"></i> Seen</span>')+'</span></article>'}).join(''):'<div class="notification-empty"><span class="notification-empty-icon"><i class="ph ph-bell-slash"></i></span><strong>No notifications</strong><span>New system alerts and AI issues will appear here.</span></div>'}
function escapeNotif(s){return String(s||'').replace(/[&<>"']/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]})}
function notifDate(v){try{return new Date(v).toLocaleString([], {month:'short',day:'numeric',hour:'numeric',minute:'2-digit'})}catch(e){return''}}
async function markNotificationSeen(id){
 var item=kurtexNotifications.find(function(n){return String(n.id)===String(id)});if(!item)return;
 if(String(id).indexOf('local-')===0){item.seen=true;updateNotificationBadge(kurtexNotifications.filter(function(n){return!n.seen}).length);renderNotifications();return}
 try{
  var r=await apiFetch('/api/notifications/'+id+'/seen',{method:'POST'}),x=await r.json();
  if(!r.ok||!x.ok)throw new Error((x&&x.error)||'Unable to mark notification seen');
  item.seen=true;updateNotificationBadge(kurtexNotifications.filter(function(n){return!n.seen}).length);renderNotifications();
 }catch(e){if(typeof pushLocalNotification==='function')pushLocalNotification('system','Notification update failed',e.message||'Could not update notification','error')}
}
async function markAllNotificationsSeen(){
 try{
  var r=await apiFetch('/api/notifications/seen-all',{method:'POST'}),x=await r.json();
  if(!r.ok||!x.ok)throw new Error((x&&x.error)||'Unable to mark notifications seen');
  kurtexNotifications.forEach(function(n){n.seen=true});updateNotificationBadge(0);renderNotifications();
 }catch(e){if(typeof pushLocalNotification==='function')pushLocalNotification('system','Notification update failed',e.message||'Could not update notifications','error')}
}
function pushLocalNotification(kind,title,message,severity){var id='local-'+Date.now();kurtexNotifications.unshift({id:id,kind:kind,title:title,message:message,severity:severity||'info',seen:false,created_at:new Date().toISOString()});updateNotificationBadge(kurtexNotifications.filter(function(n){return!n.seen}).length);renderNotifications()}
document.addEventListener('DOMContentLoaded',function(){loadNotifications();setInterval(loadNotifications,60000);document.addEventListener('click',function(e){var c=document.querySelector('.notification-center'),p=document.getElementById('notification-panel');if(c&&p&&!p.hidden&&!c.contains(e.target))p.hidden=true})});
