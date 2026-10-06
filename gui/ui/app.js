'use strict';
const $=id=>document.getElementById(id);
const invoke=(command,args)=>window.__TAURI__.core.invoke(command,args);
const modeNames={starting:'正在准备',auto:'自动执行',waiting_decision:'等待战略判断',manual:'已暂停 · 手动控制',halted:'输入已锁定',stopping:'正在停止',stopped:'已停止',failed:'执行失败',completed:'本次流程完成',unconnected:'未连接执行器',unconfirmed:'控制状态未确认'};
const unknown=value=>value===undefined||value===null||value===''?'未知':String(value);
const obj=value=>value&&typeof value==='object'&&!Array.isArray(value)?value:{};
const source=value=>{const time=Date.parse(value);if(!Number.isFinite(time))return '尚无核验时间 · 数值待读取';const age=Math.max(0,(Date.now()-time)/1000);return age<60?`最近核验 · ${Math.round(age)} 秒前`:`历史记录 · ${Math.round(age/60)} 分钟前 · 当前值待核验`;};
let current={},page='overview',messages=[],pollBusy=false,previewRun='',updateBusy=false,floatingBusy=false,effectsBusy=false,nativeWindowError='',attentionBusy=false,attentionError='',coachingBusy=false,hudAction='pause';
function put(id,value){const node=$(id);const text=String(value??'');if(node.textContent!==text)node.textContent=text;}
function showReceipt(message,phase=''){for(const id of ['receipt']){put(id,message);$(id).className='receipt '+phase;}}
function applyFloating(enabled){const active=enabled===true,wasActive=document.body.classList.contains('floating');document.body.classList.toggle('floating',active);document.documentElement.classList.toggle('floating',active);if(!active||!wasActive)$('floating-menu').hidden=true;$('floating-panel').hidden=!active;$('floating-control').setAttribute('aria-pressed',String(active));}
function windowMessage(message){for(const id of ['window-status']){put(id,message);$(id).hidden=!message;}}
function renderAssistant(status){
 const value=obj(status),available=value.available===true,age=typeof value.age_seconds==='number'&&Number.isFinite(value.age_seconds)?Math.max(0,value.age_seconds):null;
 const stale=available&&(value.stale===true||(age!==null&&age>60));
 const states={working:'工作中',running:'工作中',busy:'工作中',idle:'等待下一步',completed:'本轮已完成',done:'本轮已完成',error:'进度报告异常',failed:'进度报告异常'};
 const message=typeof value.message==='string'&&value.message.trim()?value.message.trim():available?'尚无进度说明':'尚未收到助手进度。';
 const elapsed=age===null?'更新时间待核验':age<60?`最近更新 ${Math.floor(age)} 秒前`:`最近更新 ${Math.floor(age/60)} 分 ${Math.floor(age%60)} 秒前`;
 const detail=(available?(stale?'暂未更新':states[value.state]||'进度已收到'):'进度暂不可用')+' · '+elapsed;
 for(const prefix of ['']){put(prefix+'assistant-message',(stale?'上次进度：':'助手：')+message);put(prefix+'assistant-age',detail);const panel=$(prefix+'assistant-progress');panel.className='assistant-progress'+(stale||!available?' stale':'');panel.title=message+'\n'+detail;}
}
function renderAttention(attention,testMode){
 const value=obj(attention),known=typeof value.enabled==='boolean';
 const message=attentionBusy?'正在等待本机确认设置…':attentionError?attentionError:testMode===true?'界面测试模式不能自动返回游戏。':!known?'查看聊天后的返回策略尚未确认。':value.reason||(value.recovering?'正在核验返回本局游戏。':value.enabled?'普通切窗或键鼠操作后，全部释放并空闲 3 秒会受控返回本局游戏。':'查看聊天后的返回已关闭；已有暂停保持。明确开始或继续后，普通键鼠仍可空闲恢复。');
 for(const id of ['chat-focus-auto']){const checkbox=$(id);checkbox.checked=known&&value.enabled;checkbox.disabled=attentionBusy||!known||testMode===true;checkbox.setAttribute('aria-busy',String(attentionBusy));}
 for(const id of ['attention-status']){put(id,message);$(id).title=message;$(id).className='source attention-status'+(attentionError?' attention-error':'');}
}
async function setChatFocusAuto(enabled){
 if(attentionBusy||current.test_mode===true||typeof obj(current.attention).enabled!=='boolean'){renderAttention(current.attention,current.test_mode);return;}
 attentionBusy=true;attentionError='';renderAttention(current.attention,current.test_mode);
 try{const result=await invoke('set_chat_focus_auto',{enabled});const value=obj(result?.attention??result);if(typeof value.enabled!=='boolean')throw new Error('设置回执未确认');current.attention=value;}
 catch(error){attentionError='设置未确认：'+String(error);poll();}
 finally{attentionBusy=false;renderAttention(current.attention,current.test_mode);}
}
function renderFloatingOptions(options){
 const value=obj(options),through=value.click_through===true;
 const shortcuts=[];if(value.f9_registered===true)shortcuts.push('F9');if(value.backup_registered===true)shortcuts.push('Ctrl+Alt+O');
 const shortcut=shortcuts.length===1?shortcuts[0]:shortcuts.length===2?(typeof value.recovery_shortcut==='string'&&value.recovery_shortcut.trim()?value.recovery_shortcut.trim():shortcuts.join(' / ')):'';
 const ready=!!shortcut&&value.effects_ready===true&&value.hotkey_ready!==false;
 const opacity=Number.isInteger(value.opacity)?value.opacity:80;
 if(!effectsBusy&&document.activeElement!==$('floating-opacity'))$('floating-opacity').value=String(opacity);
 put('floating-opacity-value',$('floating-opacity').value+'%');$('floating-opacity').disabled=effectsBusy;
 const button=$('floating-click-through');button.disabled=effectsBusy||(!through&&!ready);button.setAttribute('aria-pressed',String(through));put('floating-click-through',through?'关闭鼠标穿透':'开启鼠标穿透');button.title=shortcut?'按 '+shortcut+' 恢复点击':'恢复快捷键未注册，不能启用穿透';
 const passMessage=through?(shortcut?'鼠标穿透已开启 · 按 '+shortcut+' 恢复点击':'恢复快捷键未确认；请返回完整界面恢复点击。'):ready?'浮窗可点击 · '+shortcut+' 切换穿透 / 解锁':'恢复快捷键未注册或窗口效果未就绪，穿透不可用。';
 put('floating-pass-status',passMessage);$('floating-pass-status').title=passMessage;$('floating-pass-status').className='source '+(through?'through-active':'');
 if(value.error){nativeWindowError=value.error;windowMessage(value.error);}else if(nativeWindowError){nativeWindowError='';windowMessage('');}
}
async function floatingEffect(command,args){
 if(effectsBusy)return;effectsBusy=true;renderFloatingOptions(current.floating_options);
 try{current.floating_options=await invoke(command,args);windowMessage('');}
 catch(error){windowMessage('浮窗设置未完成：'+String(error));poll();}
 finally{effectsBusy=false;renderFloatingOptions(current.floating_options);}
}
async function setFloating(enabled){
 if(floatingBusy)return;floatingBusy=true;for(const id of ['floating-control','floating-open-full','floating-expand'])$(id).disabled=true;
 try{const result=await invoke('set_floating',{enabled});current.floating=result.floating;current.floating_options=result;applyFloating(result.floating);windowMessage('');renderFloatingOptions(result);}
 catch(error){windowMessage('窗口切换未完成：'+String(error));poll();}
 finally{floatingBusy=false;for(const id of ['floating-control','floating-open-full','floating-expand'])$(id).disabled=false;}
}
function renderCoaching(value){
 const policy=obj(value),enabled=policy.enabled!==false;
 $('coaching-mode').setAttribute('aria-pressed',String(enabled));$('coaching-mode').disabled=coachingBusy;
 put('coaching-mode',enabled?'带教模式 · 点击切换全托管':'全托管 · 点击切换带教模式');
 put('coaching-status',coachingBusy?'正在保存模式…':policy.error|| (enabled?'带教模式：每次出战前由助手验收，可向你请教战略。':'全托管模式：自主推进；已有暂停仍需明确继续。'));
}
async function setCoachingMode(enabled){
 if(coachingBusy)return;coachingBusy=true;renderCoaching(current.coaching);
 try{current.coaching=await invoke('set_coaching_mode',{enabled});windowMessage('');}
 catch(error){windowMessage('模式未保存：'+String(error));}
 finally{coachingBusy=false;renderCoaching(current.coaching);poll();}
}
async function windowAction(action){
 if(action==='close')windowMessage('正在释放自动输入并退出助手…');
 try{await invoke('window_action',{action});if(action!=='close')windowMessage('');}
 catch(error){windowMessage('窗口操作未完成：'+String(error));showReceipt('窗口操作未完成：'+String(error),'error');}
}
function renderHud(data){
 const mode=data.mode,terminal=['stopped','failed','completed'].includes(mode),pending=data.pending||[];
 let label='状态待核验',tone='unknown';
 if(terminal){label=mode==='failed'?'执行失败':mode==='completed'?'已完成':'已停止';tone=mode==='failed'?'failed':'stopped';}
 else if(pending.includes('stop')||mode==='stopping'){label='正在停止';tone='paused';}
 else if(pending.some(name=>['pause','takeover'].includes(name))){label='正在暂停';tone='paused';}
 else if(pending.some(name=>['start','resume'].includes(name))||mode==='starting'){label='正在准备';tone='waiting';}
 else if(mode==='unconnected'){label='未运行';tone='stopped';}
 else if(data.fresh===true&&data.manual_latch===true&&obj(data.attention).input_yield===true){label=data.input_release_confirmed?'临时让位':'正在让位';tone='paused';}
 else if(data.fresh===true&&mode==='manual'){label='已暂停';tone='paused';}
 else if(data.fresh===true&&mode==='halted'){label='输入已锁定';tone='paused';}
 else if(data.fresh===true&&data.manual_latch!==true&&mode==='waiting_decision'){label=obj(data.coaching).enabled!==false&&obj(data.runner).needs_user_confirmation===true?'待出战验收':'待助手决策';tone='waiting';}
 else if(data.fresh===true&&data.manual_latch!==true&&mode==='auto'){label='运行中';tone='running';}
 hudAction=(terminal||mode==='unconnected'||mode==='manual')?'start':'pause';
 const blocked=hudAction==='start'&&$('start').disabled,verb=hudAction==='pause'?'暂停':mode==='manual'?'继续自动':terminal?'重新开始':'开始';
 const options=obj(data.floating_options),shortcut=options.recovery_shortcut||'恢复键未确认';
 put('floating-status',label);$('floating-dot').className='hud-dot '+tone;
 const button=$('floating-status-action');button.setAttribute('aria-disabled',String(blocked));button.setAttribute('aria-label',label+'，'+(blocked?'开始或继续暂不可用':'点击'+verb));
 button.title=(data.error||obj(data.runner).reason||label)+'\n'+(blocked?$('start').title:'点击'+verb)+'；右键打开完整界面。\nF8 只暂停，不恢复；'+shortcut+' 切换鼠标穿透。'+(options.error?'\n'+options.error:'');
}
function renderUpdate(update,checking=false){
 const busy=updateBusy||checking;const button=$('check-updates');button.disabled=busy;button.setAttribute('aria-busy',String(busy));put('check-updates',busy?'检查中…':'检查更新');
 if(busy){put('update-status','正在只读检查官方 main，最长 28 秒…');return;}
 update=obj(update);const versions=update.current_commit&&update.available_commit?` · 本地 ${String(update.current_commit).slice(0,8)} / 官方 ${String(update.available_commit).slice(0,8)}`:'';
 const checked=update.checked_at||update.checked_at_ms;const stamp=checked?new Date(checked):null;const time=stamp&&Number.isFinite(stamp.getTime())?' · '+stamp.toLocaleTimeString()+' 检查':'';
 put('update-status','更新：'+(update.message||'点击检查更新，读取官方 main 的版本。')+versions+time+(update.setup_required?' '+update.setup_required:''));
 $('update-status').className='source update-'+(update.status||'idle');
}
function render(data){
 applyFloating(data.floating);
 renderFloatingOptions(data.floating_options);
 renderAssistant(data.assistant_status);
 renderAttention(data.attention,data.test_mode);
 renderCoaching(data.coaching);
 renderUpdate(data.update,data.update_checking===true);
 current=data;const terminal=['stopped','failed','completed'].includes(data.mode),state=obj(data.runner),decision=terminal?{}:obj(state.decision_request),observation=obj(state.observation||decision.observation),game={...obj(data.game),...observation};
 const phase=terminal?`${modeNames[data.mode]} · 执行器已退出`:state.phase;
 const reason=terminal?[data.error,state.reason].filter((value,index,list)=>value&&list.indexOf(value)===index).join(' · ')||'执行器已退出。':data.error||state.reason;
 const freshGame=Number.isFinite(Date.parse(game.verified_at))&&Date.now()-Date.parse(game.verified_at)<90000;
 $('test-banner').hidden=!data.test_mode;
 put('mode',modeNames[data.mode]||'控制状态未知');$('mode').className='badge '+data.mode;
 $('connection-dot').className='dot '+(data.fresh&&!terminal?'online':'');put('connection-text',terminal?'执行器已退出':data.fresh?'执行器已连接':'等待核验连接');
 for(const key of ['stage','coins','health'])put('metric-'+key,freshGame?unknown(game[key]):'未知');
 put('metric-promotion',unknown(game.promotion_level));$('promotion-bar').style.width=typeof game.promotion_level==='number'?Math.max(0,Math.min(100,game.promotion_level/170*100))+'%':'0%';
 put('mode-label',freshGame?'本局模式：'+unknown(game.current_mode):'本局模式待读取');put('rank-label','职级：'+unknown(game.current_rank));
 put('source',source(game.verified_at)+' · '+(terminal?'执行器已退出':data.fresh?'执行器状态已核验':'执行器状态待核验'));
 put('phase',phase||'等待开始');put('reason',reason||'本地执行器尚未启动，点击开始运行。');
 put('execution-tag',terminal?'流程已结束':data.mode==='waiting_decision'?'等待判断':'本地流程');
 put('next-label',terminal?'当前没有执行中的任务':decision.kind?'正在等待战略计划':state.next_step||'等待执行器报告下一步');
 put('decision-reason',terminal?'执行器已退出，当前没有待判断请求。':decision.reason||'固定步骤由本地执行器处理；购买、升级、阵容与装备根据当前证据判断。');
 put('decision-age',terminal?'重新开始前会重新核验执行器与输入状态。':decision.created_at?'当前判断证据：'+source(decision.created_at):'F8、暂停或停止后，仍需明确继续自动。');
 const environment=obj(game.investment_environment),guide=obj(game.applied_guide),tracking=obj(game.equipment_tracking);
 let strategies=game.investment_strategies;if(!Array.isArray(strategies))strategies=strategies?[strategies]:[];
 put('strategy-content',`投资环境：${unknown(environment.name||game.investment_environment)}\n投资策略：${strategies.map(v=>typeof v==='object'?unknown(v.name):unknown(v)).join('；')||'未知'}\n攻略：${unknown(guide.title||game.applied_guide)}\n攻略模式：${unknown(guide.mode_tag)}\n\n装备追踪\n${[1,2,3].map(i=>unknown(tracking['priority'+i])).join('\n')}`);
 const steps=Array.isArray(state.steps)?state.steps:Array.isArray(state.workflow)?state.workflow:[];
 const stepText=steps.map((step,i)=>`${i+1}. ${typeof step==='string'?step:step.name||step.phase||'未知'} · ${typeof step==='object'?unknown(step.status):'待核验'}${step.reason?'\n'+step.reason:''}`).join('\n\n');put('steps',stepText||'执行器尚未提供已执行步骤。');
 const receipt=obj(data.receipt),ready=obj(data.readiness);showReceipt(ready.ready!==true&&receipt.phase==='idle'?'执行器正在核验，开始暂不可用。':receipt.message||'等待实际控制回执。',receipt.phase||'');
 const hardware=obj(data.hardware),pad=obj(data.gamepad);put('shortcut-help',(hardware.f8?'F8 已注册，只暂停，不恢复。':'F8 注册未确认，开始不可用。')+(hardware.pause_backup_registered?' Ctrl+Alt+P 暂停备用键已注册。':' Ctrl+Alt+P 暂停备用键未注册。'));put('input-state',(data.test_mode?'界面测试 · 监听未注册':hardware.f8?'F8 接管已启用':'F8 注册未确认 · 可用接管按钮')+' · '+(pad.available?(pad.active?'手柄正在输入':pad.connected?'手柄已归零':'手柄监测已启用'):'手柄状态待读取'));
 const busy=(data.pending||[]).length>0,resume=data.mode==='manual'&&data.input_release_confirmed;
 put('start',resume?'继续自动':'开始一条龙');$('start').disabled=busy||pad.available!==true||pad.active===true||(pad.errors||[]).length>0||(!resume&&!['unconnected','stopped','failed','completed'].includes(data.mode));
 if(!data.test_mode&&(hardware.f8!==true||hardware.raw_input!==true))$('start').disabled=true;
 if(ready.ready!==true)$('start').disabled=true;
 $('start').title=ready.ready===true?'启动本地执行器':ready.reason||'执行器正在核验，开始暂不可用。';
 if(resume&&!data.fresh)$('start').disabled=true;
 // Safety controls are never disabled or queued behind a pending poll/invoke.
 for(const action of ['pause','takeover','stop'])$(action).disabled=false;
 renderHud(data);
 const logs=(data.logs||[]).slice().reverse();const logKey=JSON.stringify(logs);if($('log-content').dataset.key!==logKey){$('log-content').dataset.key=logKey;$('log-content').replaceChildren(...logs.map(log=>{const item=document.createElement('div');item.className='log-entry';const stamp=document.createElement('time');stamp.textContent=new Date(Number(log.at)).toLocaleTimeString();const text=document.createElement('span');text.textContent=log.message;item.append(stamp,text);return item;}));if(!logs.length)put('log-content','暂无日志。');}
 put('diagnostics',JSON.stringify({framework:data.framework,mode:data.mode,run_id:state.run_id,runner_pid:state.runner_pid,state_sequence:state.state_sequence,heartbeat_at:state.heartbeat_at,epoch:data.epoch,generation:data.generation,manual_latch:data.manual_latch,release_confirmed:data.input_release_confirmed,pending:data.pending},null,2));
 messages=Array.isArray(data.messages)?data.messages:[];put('message-records',messages.map(m=>(m.state==='replied'?'助手已回复':m.state==='read'?'助手已实际读取':'等待助手读取')+'\n您：'+m.text+(m.state==='replied'?'\n助手：'+m.reply:'')).join('\n\n')||'暂无留言。');
 if(previewRun&&previewRun!==state.run_id){$('preview-image').hidden=true;$('preview-image').removeAttribute('src');$('preview-empty').hidden=false;previewRun='';}
}
async function poll(){if(pollBusy)return;pollBusy=true;try{render(await invoke('dashboard'));}catch(error){showReceipt('界面连接未确认：'+String(error),'error');renderHud({...current,mode:'unconfirmed',fresh:false,error:String(error)});}finally{pollBusy=false;}}
function action(name){if(name==='start'&&current.mode==='manual'&&current.input_release_confirmed)name='resume';showReceipt(name==='stop'?'正在停止并等待真实退出证据…':name==='start'?'正在启动本地一条龙…':name==='resume'?'正在核验继续自动…':'控制请求已发出，等待真实确认…','pending');invoke('runner_control',{action:name}).then(()=>poll()).catch(error=>{showReceipt('操作未确认：'+String(error),'error');poll();});}
for(const name of ['start','pause','takeover','stop'])$(name).addEventListener('click',()=>action(name));
$('floating-status-action').addEventListener('click',()=>{if(hudAction==='start'&&$('start').disabled)return;action(hudAction);});
$('floating-control').addEventListener('click',()=>setFloating(true));
$('floating-open-full').addEventListener('click',()=>setFloating(false));
$('floating-expand').addEventListener('click',()=>setFloating(false));
$('floating-exit').addEventListener('click',()=>windowAction('close'));
$('window-exit').addEventListener('click',()=>windowAction('close'));
$('window-maximize').addEventListener('click',()=>windowAction('maximize'));
$('floating-panel').addEventListener('contextmenu',event=>{event.preventDefault();$('floating-menu').hidden=false;$('floating-open-full').focus();});
$('floating-status-action').addEventListener('keydown',event=>{if(event.key==='ContextMenu'||event.key==='F10'&&event.shiftKey){event.preventDefault();$('floating-menu').hidden=false;$('floating-open-full').focus();}});
$('floating-panel').addEventListener('keydown',event=>{if(event.key==='Escape'){$('floating-menu').hidden=true;$('floating-status-action').focus();}});
$('floating-opacity').addEventListener('input',()=>put('floating-opacity-value',$('floating-opacity').value+'%'));
$('floating-opacity').addEventListener('change',()=>floatingEffect('set_floating_opacity',{opacity:Number($('floating-opacity').value)}));
$('floating-click-through').addEventListener('click',()=>floatingEffect('set_click_through',{enabled:obj(current.floating_options).click_through!==true}));
for(const id of ['chat-focus-auto'])$(id).addEventListener('change',()=>setChatFocusAuto($(id).checked));
$('coaching-mode').addEventListener('click',()=>setCoachingMode(obj(current.coaching).enabled===false));
$('floating-drag').addEventListener('pointerdown',event=>{if(event.button!==0||event.target.closest('button'))return;event.preventDefault();invoke('drag_floating').catch(error=>windowMessage('窗口移动未完成：'+String(error)));});
for(const button of document.querySelectorAll('[data-page]'))button.addEventListener('click',()=>{page=button.dataset.page;for(const node of document.querySelectorAll('.page'))node.classList.toggle('active',node.id===page);for(const node of document.querySelectorAll('.nav'))node.classList.toggle('active',node===button);if(page==='preview')loadPreview();});
async function loadPreview(){const clear=()=>{$('preview-image').hidden=true;$('preview-image').removeAttribute('src');$('preview-empty').hidden=false;previewRun='';};try{const result=await invoke('preview');if(!result.available){clear();put('preview-empty','尚无当前执行器的已核验画面');return;}$('preview-image').src=result.url;$('preview-image').hidden=false;$('preview-empty').hidden=true;previewRun=result.run_id;put('preview-source',source(result.verified_at)+' · 执行器观察画面（非直播）');}catch(error){clear();put('preview-empty','画面未确认：'+String(error));}}
$('refresh-preview').addEventListener('click',loadPreview);
async function checkUpdates(){
 if(updateBusy)return;updateBusy=true;renderUpdate(current.update);
 try{const result=await invoke('check_updates');current.update=result;}
 catch(error){current.update={status:'unavailable',message:'检查更新未完成：'+String(error),checked_at_ms:Date.now()};}
 finally{updateBusy=false;renderUpdate(current.update);}
}
$('check-updates').addEventListener('click',checkUpdates);
$('message-send').addEventListener('click',()=>{const message=$('message-input').value.trim();if(!message){put('message-result','请输入留言。');return;}$('message-send').disabled=true;invoke('leave_message',{message}).then(record=>{messages.push(record);$('message-input').value='';put('message-result','已保存，等待助手实际读取。');put('message-records',messages.map(m=>'等待助手读取\n您：'+m.text).join('\n\n'));}).catch(error=>put('message-result',String(error))).finally(()=>$('message-send').disabled=false);});
// Cached dashboard reads stay fast while Rust independently waits for any CLI.
poll();setInterval(poll,400);
window.currencyWarsUI={poll,action,render,renderHud,checkUpdates,setFloating,floatingEffect,setChatFocusAuto,getState:()=>current};
