// Browser-independent regression checks; not a substitute for visual/device QA.
const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict'),path=require('node:path');
const root=path.join(__dirname,'..');
(async()=>{
 let timers=[],pending=[],storage=new Map();
 const ctx={console,Map,Date,DOMException,AbortController,localStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)},document:{getElementById:()=>null},window:{location:{assign(){}}},setTimeout:(fn,ms)=>{timers.push(ms);return timers.length;},clearTimeout(){},fetch:(url,options)=>new Promise(resolve=>pending.push({resolve,options}))};
 vm.createContext(ctx);vm.runInContext(fs.readFileSync(path.join(root,'static/js/core/api.js'),'utf8'),ctx);
 const first=ctx.apiFetch('/api/ai/chat',{method:'POST'}),second=ctx.apiFetch('/api/ai/chat',{method:'POST'});
 assert.equal(pending[0].options.signal.aborted,false);assert.deepEqual(timers,[95000,95000]);
 pending.forEach(p=>p.resolve({ok:true,status:200,json:async()=>({answer:'ok'}),headers:{get:()=>null}}));await Promise.all([first,second]);

 let resolveRequest,rejectRequest;
 function element(){return {innerHTML:'',textContent:'',value:'',disabled:false,children:[],classList:{remove(){},add(){}},setAttribute(){},focus(){},querySelector(){return null;},appendChild(x){this.children.push(x)},replaceChildren(){this.children=[];this.innerHTML='';}};}
 const input=element(),tray=element(),messages=element(),upload=element();input.value='Inspect this wheel';tray.innerHTML='pending attachment';
 const nodes={'ai-page-input':input,'ai-chat-context':tray,'ai-page-messages':messages,'ai-upload-status':upload};
 const ai={console,document:{addEventListener(){},getElementById:id=>nodes[id]||null,querySelectorAll:()=>[],createElement:element},setTimeout,clearTimeout,alert(){}};
 vm.createContext(ai);vm.runInContext(fs.readFileSync(path.join(root,'static/js/features/ai.js'),'utf8'),ai);
 ai.kurtexAIChatId='chat';ai.aiChatPendingAttachments=[{id:'photo',name:'wheel.jpg',kind:'image'}];
 ai.apiFetch=()=>new Promise((resolve,reject)=>{resolveRequest=resolve;rejectRequest=reject;});
 ai.refreshAIChatContext=async()=>{tray.innerHTML=ai.aiChatPendingAttachments.length?'pending attachment':''};ai.loadKurtexAIChats=()=>{};ai.loadAIPageSidebar=()=>{};
 const sending=ai.sendKurtexAIPage({preventDefault(){}});
 assert.equal(ai.aiChatPendingAttachments.length,0);assert.equal(tray.innerHTML,'');assert.equal(ai.aiBusy,true);
 resolveRequest({ok:true,json:async()=>({answer:'Visible rim fracture',chat_id:'chat',message_id:'answer'})});await sending;
 assert.equal(tray.innerHTML,'');assert.equal(ai.aiBusy,false);
 input.value='Inspect again';ai.aiChatPendingAttachments=[{id:'photo',name:'wheel.jpg',kind:'image'}];tray.innerHTML='pending attachment';
 const failed=ai.sendKurtexAIPage({preventDefault(){}});rejectRequest(new Error('offline'));await failed;
 assert.equal(ai.aiChatPendingAttachments.length,1);assert.equal(tray.innerHTML,'pending attachment');assert.equal(input.value,'Inspect again');assert.equal(ai.aiBusy,false);
 const actions=ai.aiWrapMessage('assistant','answer','','abc');assert.match(actions,/aria-label=\"Report\"/);assert.doesNotMatch(actions,/>\s*Report\s*</);assert.doesNotMatch(actions,/Teach \/ correct/);assert.match(actions,/copyAIMessage/);
 console.log('PASS: mutation isolation, timeout, optimistic attachment clearing, failure restore plus agent copy/report actions');
})();
