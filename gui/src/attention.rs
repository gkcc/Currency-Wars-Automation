//! A bounded idle return to the bound game, using the existing
//! pause identity handoff. Explicit manual controls always revoke this return.
use crate::{control_guarded, now_ms, protocol::{identity, read_json, text, ResumeGuard}, Config, SharedState};
use serde_json::{json, Value};
use std::{fs, mem::{size_of, zeroed}, sync::atomic::Ordering, thread, time::Duration};
use windows_sys::Win32::{Foundation::{CloseHandle, FILETIME}, System::{Threading::{OpenProcess, GetProcessTimes, QueryFullProcessImageNameW, PROCESS_QUERY_LIMITED_INFORMATION}, SystemInformation::GetTickCount}, UI::{Input::KeyboardAndMouse::{GetAsyncKeyState, GetLastInputInfo, LASTINPUTINFO}, WindowsAndMessaging::{GetForegroundWindow, GetWindowThreadProcessId, IsWindow}}};

pub const TEMPORARY_REASON: &str = "查看助手；暂时让出游戏输入";
pub const INPUT_REASON: &str = "普通键鼠临时让位；空闲 3 秒后受控恢复";
const LOST_FOREGROUND: &str = "批次外前台丢失，保持手动，不自动抢回";

#[derive(Clone)]
struct WindowIdentity { hwnd:usize, pid:u32, creation:String, path:String }
impl WindowIdentity {
    fn load(value:&Value)->Option<Self>{
        let result=Self{hwnd:value["hwnd"].as_u64()? as usize,pid:u32::try_from(value["pid"].as_u64()?).ok()?,creation:identity(value,"creation_id"),path:text(value,"path").into()};
        if result.hwnd==0 || result.pid==0 || result.creation.is_empty(){None}else{Some(result)}
    }
    fn valid(&self)->bool{unsafe{
        let mut pid=0;
        if IsWindow(self.hwnd as _)==0 || GetWindowThreadProcessId(self.hwnd as _,&mut pid)==0 || pid!=self.pid{return false;}
        let process=OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION,0,self.pid);if process.is_null(){return false;}
        let mut created:FILETIME=zeroed();let mut exited=zeroed();let mut kernel=zeroed();let mut user=zeroed();
        let mut valid=GetProcessTimes(process,&mut created,&mut exited,&mut kernel,&mut user)!=0 && (((created.dwHighDateTime as u64)<<32)|created.dwLowDateTime as u64).to_string()==self.creation;
        if valid&&!self.path.is_empty(){let mut buffer=[0u16;32768];let mut length=buffer.len() as u32;valid=QueryFullProcessImageNameW(process,0,buffer.as_mut_ptr(),&mut length)!=0 && String::from_utf16_lossy(&buffer[..length as usize]).eq_ignore_ascii_case(&self.path);}
        CloseHandle(process);valid
    }}
}

pub struct Policy {
    enabled:bool, assistant:Option<WindowIdentity>, armed:bool, blocked:bool,
    seen_assistant:bool, input_yield:bool, navigation_pending:Option<u64>, pub recovering:bool,
    recovery_epoch:Option<(u64,u64)>, pub reason:String,
}
impl Default for Policy {
    fn default()->Self{Self{enabled:false,assistant:None,armed:false,blocked:true,seen_assistant:false,input_yield:false,navigation_pending:None,recovering:false,recovery_epoch:None,reason:"尚未设置当前助手窗口身份。".into()}}
}
impl Policy {
    pub fn load(config:&Config)->Self{
        if config.test_mode{return Self::default();}
        let mut result=Self::default();
        if let Ok(record)=read_json(&config.project.join("docs/ATTENTION_POLICY.json")){
            if text(&record,"chat_id")==config.chat && record["protocol_version"]==1 {
                result.assistant=WindowIdentity::load(&record["assistant_window"]).filter(|value|value.valid());
                result.enabled=record["enabled"]==true&&result.assistant.is_some();
                result.reason=if result.enabled{"已启用；开始或继续后，查看助手空闲 3 秒会受控返回。"}else{"已关闭查看助手后的自动继续。"}.into();
            }
        }
        result
    }
    pub fn info(&self)->Value{json!({"enabled":self.enabled,"available":self.assistant.is_some(),"recovering":self.recovering,"input_yield":self.input_yield,"blocked":self.blocked,"reason":self.reason})}
    fn approved(&self,hwnd:usize,own:usize)->bool{
        hwnd!=0&&(hwnd==own || self.assistant.as_ref().map(|window|window.hwnd==hwnd&&window.valid()).unwrap_or(false))
    }
    fn return_pending(&self)->bool{self.input_yield||self.enabled&&self.seen_assistant}
    fn revoke(&mut self,reason:&str){self.blocked=true;self.armed=false;self.seen_assistant=false;self.input_yield=false;self.navigation_pending=None;self.recovering=false;self.recovery_epoch=None;self.reason=reason.into();}
}

pub fn set_enabled(shared:&SharedState,enabled:bool)->Result<Value,String>{
    if shared.config.test_mode{return Err("界面测试模式不能自动返回游戏".into());}
    let mut policy=shared.attention.lock().map_err(|_|"返回策略正在更新".to_string())?;
    if enabled&&!policy.assistant.as_ref().map(|value|value.valid()).unwrap_or(false){return Err("当前助手窗口身份已失效，请重新启动助手连接".into());}
    let path=shared.config.project.join("docs/ATTENTION_POLICY.json");let mut record=read_json(&path)?;
    if text(&record,"chat_id")!=shared.config.chat{return Err("返回策略不属于当前聊天".into());}
    record["enabled"]=json!(enabled);
    fs::write(&path,serde_json::to_vec_pretty(&record).map_err(|e|e.to_string())?).map_err(|e|e.to_string())?;
    policy.enabled=enabled;
    if !enabled{policy.revoke("自动返回已关闭；已有暂停保持，需明确继续。");}
    else{policy.reason="查看助手后自动继续已启用；F8、暂停和停止仍保持暂停。".into();}
    Ok(policy.info())
}

fn handoff_pending(session:&crate::Session,epoch:u64)->bool{
    if ["start","resume"].iter().any(|action|session.pending.get(*action)==Some(&epoch)) || session.resume.as_ref().map(|pending|pending.epoch)==Some(epoch){return true;}
    !session.manual&&match &session.startup{
        Some(crate::StartupHandshake::Pending{epoch:owner})=>*owner==epoch,
        Some(crate::StartupHandshake::Bound{epoch:owner,run_id,launch_id,..})=>*owner==epoch&&session.binding.as_ref().map(|binding|&binding.run_id==run_id&&&binding.launch_id==launch_id).unwrap_or(false),
        None=>false,
    }
}

pub fn block(shared:&SharedState,reason:&str){block_guarded(shared,reason,None);}
fn block_guarded(shared:&SharedState,reason:&str,observed_epoch:Option<u64>){
    // Same lock order as the final Resume spawn. A revocation that wins this
    // boundary synchronously invalidates the queued/in-flight GUI epoch.
    let mut session=shared.session.lock().unwrap();
    if let Some(epoch)=observed_epoch{if epoch!=shared.epoch.load(Ordering::SeqCst)||handoff_pending(&session,epoch){return;}}
    let mut policy=shared.attention.lock().unwrap();
    if !session.manual||session.resume.is_some()||session.pending.contains_key("resume")||session.pending.contains_key("start")||policy.recovering{
        shared.epoch.fetch_add(1,Ordering::SeqCst);session.manual=true;session.release_confirmed=false;
        session.resume=None;session.resumed=None;session.startup=None;session.manual_guard=None;
    }
    policy.revoke(reason);
}
pub fn on_control(shared:&SharedState,action:&str,reason:&str,automatic:bool){
    let mut policy=shared.attention.lock().unwrap();
    if ["pause","stop"].contains(&action) || action=="takeover"&&![TEMPORARY_REASON,INPUT_REASON].contains(&reason)&&!reason.starts_with("界面保护：") {
        policy.revoke("已手动暂停；按继续自动后才恢复运行。");
    }else if ["start","resume"].contains(&action)&&!automatic {
        policy.armed=true;policy.blocked=false;policy.seen_assistant=false;policy.input_yield=false;policy.navigation_pending=None;policy.recovering=false;policy.recovery_epoch=None;
        policy.reason="普通键鼠临时让位，释放后空闲 3 秒会受控恢复；F8、暂停和停止需明确继续。".into();
    }else if action=="resume"&&automatic{
        if let Some((_,owner))=policy.recovery_epoch.as_mut(){*owner=shared.epoch.load(Ordering::SeqCst);}
    }
}

pub fn temporary_input(shared:&SharedState)->Option<(u64,bool)>{temporary_input_guarded(shared,None)}
fn temporary_input_guarded(shared:&SharedState,observed_epoch:Option<u64>)->Option<(u64,bool)>{
    // Use the Resume dispatch lock order. Only an explicit Start/Resume arms
    // this policy; ordinary input can never reopen a permanent manual block.
    let mut session=shared.session.lock().unwrap();let mut policy=shared.attention.lock().unwrap();
    if let Some(epoch)=observed_epoch{if epoch!=shared.epoch.load(Ordering::SeqCst)||handoff_pending(&session,epoch){return None;}}
    if shared.closing.load(Ordering::SeqCst)||shared.closed.load(Ordering::SeqCst)||session.binding.is_none()||session.terminal||!session.fresh()||session.startup.is_some()||session.pending.contains_key("start")||!policy.armed||policy.blocked{return None;}
    let Some(_game)=WindowIdentity::load(&session.state["broker"]["game"]).filter(|value|value.valid()) else{return None;};
    let pause=!session.manual||session.resume.is_some()||session.pending.contains_key("resume")||policy.recovering;
    if pause{
        shared.epoch.fetch_add(1,Ordering::SeqCst);session.manual=true;session.release_confirmed=false;
        session.resume=None;session.resumed=None;session.manual_guard=None;
        // The old resume CLI still owns its child and will cancel on epoch.
        // Remove its UI slot so the new guarded pause can be dispatched now.
        session.pending.remove("resume");
        // Reserve this pause while holding the manual-latch lock, so poll
        // cannot race in a generic ReassertManual intent before dispatch.
        session.pending.insert("takeover".into(),shared.epoch.load(Ordering::SeqCst));
    }
    policy.input_yield=true;policy.navigation_pending=None;policy.recovering=false;policy.recovery_epoch=None;
    policy.reason="键鼠正在使用；临时让出输入，释放后连续空闲 3 秒再受控恢复。".into();
    Some((shared.epoch.load(Ordering::SeqCst),pause))
}

pub fn begin_navigation(shared:&SharedState,epoch:u64)->bool{
    let mut policy=shared.attention.lock().unwrap();
    if !policy.enabled||!policy.armed||policy.blocked{return false;}
    policy.navigation_pending=Some(epoch);true
}
pub fn finish_navigation(shared:&SharedState,epoch:u64){
    let mut policy=shared.attention.lock().unwrap();if policy.navigation_pending==Some(epoch){policy.navigation_pending=None;}
}

fn idle()->Option<u32>{unsafe{let mut info=LASTINPUTINFO{cbSize:size_of::<LASTINPUTINFO>() as u32,dwTime:0};if GetLastInputInfo(&mut info)==0{return None;}let age=GetTickCount().wrapping_sub(info.dwTime);if age>i32::MAX as u32{None}else{Some(age)}}}
fn input_idle()->bool{
    if idle().map(|age|age<3000).unwrap_or(true){return false;}
    // High bits only: held keyboard keys and mouse buttons prevent recovery.
    // No text is collected, hook installed, or input emitted.
    !(1..=254).any(|key|unsafe{GetAsyncKeyState(key)<0})
}
fn foreground_ready()->bool{unsafe{let foreground=GetForegroundWindow();!foreground.is_null()&&IsWindow(foreground)!=0}}
pub fn dispatch_allowed(policy:&Policy,_own:usize,game:&Value)->bool{
    if !policy.armed||policy.blocked||!policy.return_pending()||policy.navigation_pending.is_some()||!input_idle(){return false;}
    let Some(_game)=WindowIdentity::load(game).filter(|value|value.valid()) else{return false;};
    foreground_ready()
}
fn temporary_guard(shared:&SharedState,guard:&ResumeGuard)->bool{
    let binding={shared.session.lock().unwrap().binding.clone()};let Some(binding)=binding else{return false;};
    if guard.run_id!=binding.run_id || guard.pending_ids.is_empty() || guard.pause_id.is_none(){return false;}
    if binding.resume_pending_changed(guard)!=Ok(false){return false;}
    for id in &guard.pending_ids{
        let Ok(record)=read_json(&binding.root.join("manual-intents").join(format!("{id}.json"))) else{return false;};
        if text(&record,"manual_id")!=id || ![TEMPORARY_REASON,INPUT_REASON,LOST_FOREGROUND].contains(&text(&record,"reason")){return false;}
    }
    true
}

pub fn eligible(shared:&SharedState,guard:&ResumeGuard)->bool{
    if shared.closed.load(Ordering::SeqCst)||shared.closing.load(Ordering::SeqCst)||!input_idle(){return false;}
    let (game,pads)={let session=shared.session.lock().unwrap();(WindowIdentity::load(&session.state["broker"]["game"]),session.gamepad.clone())};
    if pads["active"]==true||pads["available"]!=true||pads["errors"].as_array().map(|v|!v.is_empty()).unwrap_or(true){return false;}
    let Some(_game)=game.filter(|value|value.valid()) else{return false;};
    let policy=shared.attention.lock().unwrap();
    let allowed=policy.armed&&!policy.blocked&&policy.navigation_pending.is_none()&&policy.return_pending()&&foreground_ready();
    drop(policy);allowed&&temporary_guard(shared,guard)
}

fn tick(shared:&SharedState){
    // Only this epoch's existing Start/Resume owns its focus transition.
    // Physical input and F8 still revoke it through the synchronous input path.
    let (epoch,handoff,bound,manual,fresh,released,pending,guard,game,terminal)={let session=shared.session.lock().unwrap();let epoch=shared.epoch.load(Ordering::SeqCst);(epoch,handoff_pending(&session,epoch),session.binding.is_some(),session.manual,session.fresh(),session.release_confirmed,!session.pending.is_empty(),session.manual_guard.clone(),WindowIdentity::load(&session.state["broker"]["game"]),session.terminal)};
    if handoff||!bound||terminal||shared.closing.load(Ordering::SeqCst){return;}
    if !fresh{block_guarded(shared,"执行器状态过期；暂停保持，需明确继续。",Some(epoch));return;}
    // An incomplete broker packet grants no return; wait for a full identity.
    let Some(game)=game else{return;};
    if !game.valid(){block_guarded(shared,"本局游戏身份失效；暂停保持，需重新核验。",Some(epoch));return;}
    let foreground=unsafe{GetForegroundWindow()} as usize;
    let mut policy=shared.attention.lock().unwrap();
    if !policy.armed||policy.blocked||policy.navigation_pending.is_some(){return;}
    if foreground!=game.hwnd&&!policy.input_yield{
        // Ordinary window changes share the physical-input pause reservation.
        // A stable non-game foreground must not cancel its own queued return.
        drop(policy);
        if let Some((epoch,pause))=temporary_input_guarded(shared,Some(epoch)){
            if pause{let cloned=shared.clone();thread::spawn(move||{
                let _=control_guarded(&cloned,"takeover",INPUT_REASON,Some(epoch));
                let mut session=cloned.session.lock().unwrap();
                if session.pending.get("takeover")==Some(&epoch){session.pending.remove("takeover");}
            });}
        }
        return;
    }
    if policy.approved(foreground,shared.hwnd.load(Ordering::SeqCst)){
        if policy.enabled{policy.seen_assistant=true;}
        if policy.input_yield{policy.reason=if fresh&&manual&&released{"键鼠临时让位；释放后空闲 3 秒会受控恢复。"}else{"键鼠临时让位；等待暂停和释放输入确认。"}.into();}
        else if policy.enabled{policy.reason=if fresh&&manual&&released{"正在查看助手；游戏输入已暂停，空闲 3 秒后受控返回。"}else{"正在查看助手；等待游戏暂停和释放输入确认。"}.into();}
    }
    if policy.recovering{return;}
    if !manual {if foreground==game.hwnd&&!policy.input_yield{policy.seen_assistant=false;policy.reason="游戏运行中；助手进度和执行状态见浮窗。".into();}return;}
    if !policy.return_pending()||!fresh||!released||pending{return;}
    drop(policy);
    let Some(guard)=guard else{return;};
    if !temporary_guard(shared,&guard){block_guarded(shared,"本次暂停包含手动接管或其他保护；需明确继续。",Some(epoch));return;}
    if !eligible(shared,&guard){return;}
    let epoch=shared.epoch.load(Ordering::SeqCst);
    {let session=shared.session.lock().unwrap();let mut policy=shared.attention.lock().unwrap();
        if epoch!=shared.epoch.load(Ordering::SeqCst)||session.manual_guard.as_ref()!=Some(&guard)||policy.recovering||policy.blocked||!policy.return_pending(){return;}
        policy.recovering=true;policy.recovery_epoch=Some((epoch,epoch));policy.reason="正在核验本局暂停身份并恢复游戏…".into();}
    let cloned=shared.clone();thread::spawn(move||{
        let result=control_guarded(&cloned,"resume","键鼠空闲或查看助手后受控继续",Some(epoch));
        let mut policy=cloned.attention.lock().unwrap();
        if !matches!(policy.recovery_epoch,Some((request,owner)) if request==epoch&&owner==cloned.epoch.load(Ordering::SeqCst)){return;}
        policy.recovering=false;policy.recovery_epoch=None;
        if let Err(error)=result{
            if policy.input_yield&&(!input_idle()||!foreground_ready()){policy.reason="键鼠活动或窗口转场延后恢复；保持临时让位，等待有效前台且释放后空闲 3 秒。".into();}
            else{policy.revoke(&format!("自动继续未确认：{error}；已有暂停保持。"));}
        }
        else if !policy.blocked {policy.seen_assistant=false;policy.input_yield=false;policy.reason="已返回本局游戏并确认继续。".into();}
    });
}

pub fn watch(shared:SharedState){thread::spawn(move||{while !shared.closed.load(Ordering::SeqCst){tick(&shared);thread::sleep(Duration::from_millis(200));}});}

pub fn assistant_status(config:&Config)->Value{
    let record=read_json(&config.project.join("docs/ASSISTANT_HEARTBEAT.json")).ok();
    let Some(record)=record.filter(|value|text(value,"chat_id")==config.chat&&value["updated_at_ms"].as_u64().map(|stamp|stamp as u128<=now_ms()).unwrap_or(false)) else{return json!({"available":false,"message":"尚未收到有效的本次助手进度。"});};
    let age=(now_ms().saturating_sub(record["updated_at_ms"].as_u64().unwrap() as u128)) as f64/1000.;
    json!({"available":true,"state":text(&record,"state"),"message":text(&record,"message"),"age_seconds":age,"stale":age>60.})
}
