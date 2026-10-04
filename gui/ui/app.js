'use strict';
const $=id=>document.getElementById(id);
const invoke=(command,args)=>window.__TAURI__.core.invoke(command,args);
const modeNames={starting:'正在准备',auto:'自动执行',waiting_decision:'等待战略判断',manual:'已暂停 · 手动控制',halted:'输入已锁定',stopping:'正在停止',stopped:'已停止',failed:'执行失败',completed:'本次流程完成',unconnected:'未连接执行器',unconfirmed:'控制状态未确认'};
const unknown=value=>value===undefined||value===null||value===''?'未知':String(value);
const obj=value=>value&&typeof value==='object'&&!Array.isArray(value)?value:{};
const source=value=>{const time=Date.parse(value);if(!Number.isFinite(time))return '尚无核验时间 · 数值待读取';const age=Math.max(0,(Date.now()-time)/1000);return age<60?`最近核验 · ${Math.round(age)} 秒前`:`历史记录 · ${Math.round(age/60)} 分钟前 · 当前值待核验`;};
let current={},page='overview',messages=[],pollBusy=false,previewRun='';
function put(id,value){const node=$(id);const text=String(value??'');if(node.textContent!==text)node.textContent=text;}
function render(data){
 const update=obj(data.update);put('update-status','更新：'+(update.message||'状态待读取')+(update.setup_required?' '+update.setup_required:''));
 current=data;const state=obj(data.runner),decision=obj(state.decision_request),observation=obj(state.observation||decision.observation),game={...obj(data.game),...observation};
 const freshGame=Number.isFinite(Date.parse(game.verified_at))&&Date.now()-Date.parse(game.verified_at)<90000;
 $('test-banner').hidden=!data.test_mode;
 put('mode',modeNames[data.mode]||'控制状态未知');$('mode').className='badge '+data.mode;
 $('connection-dot').className='dot '+(data.fresh?'online':'');put('connection-text',data.fresh?'执行器已连接':'等待核验连接');
 for(const key of ['stage','coins','health'])put('metric-'+key,freshGame?unknown(game[key]):'未知');
 put('metric-promotion',unknown(game.promotion_level));$('promotion-bar').style.width=typeof game.promotion_level==='number'?Math.max(0,Math.min(100,game.promotion_level/170*100))+'%':'0%';
 put('mode-label',freshGame?'本局模式：'+unknown(game.current_mode):'本局模式待读取');put('rank-label','职级：'+unknown(game.current_rank));
 put('source',source(game.verified_at)+' · '+(data.fresh?'执行器状态已核验':'执行器状态待核验'));
 put('phase',state.phase||'等待开始');put('reason',data.error||state.reason||'本地执行器尚未启动，点击开始运行。');
 put('execution-tag',data.mode==='waiting_decision'?'等待判断':'本地流程');
 put('next-label',decision.kind?'正在等待战略计划':state.next_step||'等待执行器报告下一步');
 put('decision-reason',decision.reason||'固定步骤由本地执行器处理；购买、升级、阵容与装备根据当前证据判断。');
 put('decision-age',decision.created_at?'当前判断证据：'+source(decision.created_at):'暂停后必须明确点击继续自动才恢复。');
 const environment=obj(game.investment_environment),guide=obj(game.applied_guide),tracking=obj(game.equipment_tracking);
 let strategies=game.investment_strategies;if(!Array.isArray(strategies))strategies=strategies?[strategies]:[];
 put('strategy-content',`投资环境：${unknown(environment.name||game.investment_environment)}\n投资策略：${strategies.map(v=>typeof v==='object'?unknown(v.name):unknown(v)).join('；')||'未知'}\n攻略：${unknown(guide.title||game.applied_guide)}\n攻略模式：${unknown(guide.mode_tag)}\n\n装备追踪\n${[1,2,3].map(i=>unknown(tracking['priority'+i])).join('\n')}`);
 const steps=Array.isArray(state.steps)?state.steps:Array.isArray(state.workflow)?state.workflow:[];
 const stepText=steps.map((step,i)=>`${i+1}. ${typeof step==='string'?step:step.name||step.phase||'未知'} · ${typeof step==='object'?unknown(step.status):'待核验'}${step.reason?'\n'+step.reason:''}`).join('\n\n');put('steps',stepText||'执行器尚未提供已执行步骤。');
 const receipt=obj(data.receipt),ready=obj(data.readiness);put('receipt',ready.ready!==true&&receipt.phase==='idle'?'执行器正在核验，开始暂不可用。':receipt.message||'等待实际控制回执。');$('receipt').className='receipt '+(receipt.phase||'');
 const hardware=obj(data.hardware),pad=obj(data.gamepad);put('input-state',(data.test_mode?'界面测试 · 监听未注册':hardware.f8?'F8 接管已启用':'F8 注册未确认 · 可用接管按钮')+' · '+(pad.available?(pad.active?'手柄正在输入':pad.connected?'手柄已归零':'手柄监测已启用'):'手柄状态待读取'));
 const busy=(data.pending||[]).length>0,resume=data.mode==='manual'&&data.input_release_confirmed;
 put('start',resume?'继续自动':'开始一条龙');$('start').disabled=busy||pad.available!==true||pad.active===true||(pad.errors||[]).length>0||(!resume&&!['unconnected','stopped','failed','completed'].includes(data.mode));
 if(!data.test_mode&&(hardware.f8!==true||hardware.raw_input!==true))$('start').disabled=true;
 if(ready.ready!==true)$('start').disabled=true;
 $('start').title=ready.ready===true?'启动本地执行器':ready.reason||'执行器正在核验，开始暂不可用。';
 if(resume&&!data.fresh)$('start').disabled=true;
 // Safety controls are never disabled or queued behind a pending poll/invoke.
 for(const action of ['pause','takeover','stop'])$(action).disabled=false;
 const logs=(data.logs||[]).slice().reverse();const logKey=JSON.stringify(logs);if($('log-content').dataset.key!==logKey){$('log-content').dataset.key=logKey;$('log-content').replaceChildren(...logs.map(log=>{const item=document.createElement('div');item.className='log-entry';const stamp=document.createElement('time');stamp.textContent=new Date(Number(log.at)).toLocaleTimeString();const text=document.createElement('span');text.textContent=log.message;item.append(stamp,text);return item;}));if(!logs.length)put('log-content','暂无日志。');}
 put('diagnostics',JSON.stringify({framework:data.framework,mode:data.mode,run_id:state.run_id,runner_pid:state.runner_pid,state_sequence:state.state_sequence,heartbeat_at:state.heartbeat_at,epoch:data.epoch,generation:data.generation,manual_latch:data.manual_latch,release_confirmed:data.input_release_confirmed,pending:data.pending},null,2));
 messages=Array.isArray(data.messages)?data.messages:[];put('message-records',messages.map(m=>(m.state==='replied'?'助手已回复':m.state==='read'?'助手已实际读取':'等待助手读取')+'\n您：'+m.text+(m.state==='replied'?'\n助手：'+m.reply:'')).join('\n\n')||'暂无留言。');
 if(previewRun&&previewRun!==state.run_id){$('preview-image').hidden=true;$('preview-image').removeAttribute('src');$('preview-empty').hidden=false;previewRun='';}
}
async function poll(){if(pollBusy)return;pollBusy=true;try{render(await invoke('dashboard'));}catch(error){put('receipt','界面连接未确认：'+String(error));}finally{pollBusy=false;}}
function action(name){if(name==='start'&&current.mode==='manual'&&current.input_release_confirmed)name='resume';put('receipt',name==='stop'?'正在停止并等待真实退出证据…':name==='start'?'正在启动本地一条龙…':'控制请求已发出，等待真实确认…');$('receipt').className='receipt pending';invoke('runner_control',{action:name}).then(()=>poll()).catch(error=>{put('receipt','操作未确认：'+String(error));$('receipt').className='receipt error';poll();});}
for(const name of ['start','pause','takeover','stop'])$(name).addEventListener('click',()=>action(name));
for(const button of document.querySelectorAll('[data-page]'))button.addEventListener('click',()=>{page=button.dataset.page;for(const node of document.querySelectorAll('.page'))node.classList.toggle('active',node.id===page);for(const node of document.querySelectorAll('.nav'))node.classList.toggle('active',node===button);if(page==='preview')loadPreview();});
async function loadPreview(){const clear=()=>{$('preview-image').hidden=true;$('preview-image').removeAttribute('src');$('preview-empty').hidden=false;previewRun='';};try{const result=await invoke('preview');if(!result.available){clear();put('preview-empty','尚无当前执行器的已核验画面');return;}$('preview-image').src=result.url;$('preview-image').hidden=false;$('preview-empty').hidden=true;previewRun=result.run_id;put('preview-source',source(result.verified_at)+' · 执行器观察画面（非直播）');}catch(error){clear();put('preview-empty','画面未确认：'+String(error));}}
$('refresh-preview').addEventListener('click',loadPreview);
$('message-send').addEventListener('click',()=>{const message=$('message-input').value.trim();if(!message){put('message-result','请输入留言。');return;}$('message-send').disabled=true;invoke('leave_message',{message}).then(record=>{messages.push(record);$('message-input').value='';put('message-result','已保存，等待助手实际读取。');put('message-records',messages.map(m=>'等待助手读取\n您：'+m.text).join('\n\n'));}).catch(error=>put('message-result',String(error))).finally(()=>$('message-send').disabled=false);});
// Cached dashboard reads stay fast while Rust independently waits for any CLI.
poll();setInterval(poll,400);
window.currencyWarsUI={poll,action,render,getState:()=>current};
