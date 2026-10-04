#![cfg_attr(target_os = "windows", windows_subsystem = "windows")]
mod protocol;
mod input;

use base64::{engine::general_purpose::STANDARD, Engine};
use protocol::{Binding, InitializationPause, ResumeGuard, ResumeProof, read_json, text, request_png};
use serde_json::{json, Value};
use sha2::{Digest,Sha256};
use std::{collections::HashMap, fs, io::Read, path::{Path, PathBuf}, process::{Child, Command, Stdio},
          sync::{Arc, Mutex, atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering}},
          thread, time::{Duration, Instant, SystemTime, UNIX_EPOCH}};

fn now_ms() -> u128 { SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_millis() }

#[derive(Clone)]
struct Config { project: PathBuf, python: PathBuf, runner: PathBuf, chat: String,
                runtime: PathBuf, test_mode: bool, debug_port: Option<u16> }

impl Config {
    fn load() -> Result<Self, String> {
        let args: Vec<String> = std::env::args().collect();
        let argument = |name: &str| args.iter().position(|v| v == name).and_then(|i| args.get(i+1)).cloned();
        let executable = std::env::current_exe().map_err(|e| e.to_string())?;
        let project = argument("--project-dir").map(PathBuf::from)
            .unwrap_or_else(|| executable.parent().unwrap().parent().unwrap().parent().unwrap().to_path_buf());
        let test_mode = args.iter().any(|v| v == "--test-mode");
        let runner = if test_mode { argument("--test-runner").map(PathBuf::from).unwrap_or_else(|| project.join("tools/currency_wars_runner.py")) }
                     else { project.join("tools/currency_wars_runner.py") };
        let runtime = PathBuf::from(argument("--runtime-dir").ok_or("请从gui/launch.py或货币战争助手.cmd启动，以保持运行目录归属")?);
        read_json(&runtime.join(".agent-workflow-owner.json"))?;
        let python = argument("--python").map(PathBuf::from).unwrap_or_else(|| project.join(".venv/Scripts/python.exe"));
        if !python.is_file() || !runner.is_file() { return Err("Python执行器路径尚未就绪".into()); }
        Ok(Self { project, python, runner, runtime, test_mode,
                  chat: argument("--chat-id").ok_or("启动器必须提供本次会话身份")?,
                  debug_port: if test_mode { argument("--debug-port").and_then(|v| v.parse().ok()) } else { None } })
    }
}

struct Session {
    binding: Option<Binding>, state: Value, game: Value, mode: String,
    sequence: u64, generation: u64, manual: bool, release_confirmed: bool,
    last_status: Option<Instant>, terminal: bool, pending: HashMap<String, u64>,
    receipt: Value, error: String, logs: Vec<Value>, hardware: Value, gamepad: Value,
    input_guard_tick: u32,
    messages: Vec<Value>,
    readiness: Value,
    startup: Option<StartupHandshake>,
    manual_guard:Option<ResumeGuard>,resume:Option<PendingResume>,resumed:Option<(u64,ResumeProof)>,
    last_request_id:String,
}

#[derive(Clone)]
struct PendingResume {epoch:u64,guard:ResumeGuard}

enum StartupHandshake {
    Pending {epoch:u64},
    Bound {epoch:u64,run_id:String,launch_id:String,initial:Option<InitializationPause>},
}

impl Session {
    fn new(test: bool) -> Self {
        Self { binding: None, state: json!({}), game: json!({}), mode: "unconnected".into(),
               sequence: 0, generation: 0, manual: false, release_confirmed: false,
               last_status: None, terminal: false, pending: HashMap::new(),
               receipt: json!({"phase":"idle", "message":"点击开始运行；收到真实确认后才会显示运行结果。"}),
               error: String::new(), logs: vec![], hardware: json!({"f8":false,"raw_input":false,"test_mode":test}),
               gamepad: json!({"available":test,"active":false,"connected":0,"errors":[]}), input_guard_tick:0, messages:vec![],
               readiness:json!({"ready":false,"reason":"执行器正在核验，开始暂不可用。"}),startup:None,manual_guard:None,resume:None,resumed:None,last_request_id:String::new() }
    }
    fn begin_control(&mut self,action:&str,epoch:u64,tick:u32){
        self.last_request_id.clear();
        self.resumed=None;self.resume=None;
        if ["start","resume"].contains(&action){self.release_confirmed=false;self.terminal=false;self.input_guard_tick=tick;}
        if action=="start"{
            // Only a user-requested new run clears the previous run's latch.
            // Its reply cannot later clear a new manual intent.
            self.manual=false;self.startup=Some(StartupHandshake::Pending{epoch});
        }else if ["resume","pause","takeover","stop"].contains(&action){self.startup=None;}
        if action=="resume"{self.resume=self.manual_guard.take().map(|guard|PendingResume{epoch,guard});}
        else{self.manual_guard=None;}
        if ["pause","takeover","stop"].contains(&action){self.manual=true;self.release_confirmed=false;}
        self.pending.insert(action.into(),epoch);
    }
    fn may_reassert(&self,current_epoch:u64,expected_epoch:u64)->bool{
        current_epoch==expected_epoch && self.manual && self.resume.is_none() && self.startup.is_none()
            && !["start","resume"].iter().any(|key|self.pending.contains_key(*key))
    }
    fn fresh(&self) -> bool { self.terminal || self.last_status.map(|v| v.elapsed() < Duration::from_secs(6)).unwrap_or(false) }
    fn log(&mut self, message: &str) {
        if self.logs.last().and_then(|v| v["message"].as_str()) == Some(message) { return; }
        self.logs.push(json!({"at":now_ms(),"message":message}));
        if self.logs.len() > 100 { self.logs.remove(0); }
    }
}

struct Shared {
    config: Config, session: Mutex<Session>, epoch: AtomicU64, closed: AtomicBool,
    closing: AtomicBool, hwnd: AtomicUsize, children: Mutex<HashMap<u32, Arc<Mutex<Child>>>>,
    app: Mutex<Option<tauri::AppHandle>>,
    message_lock: Mutex<()>, message_sequence: AtomicU64,
}

type SharedState = Arc<Shared>;
enum Acceptance { Applied, ReassertManual, Stale }

impl Shared {
    fn dashboard(&self) -> Value {
        let session = self.session.lock().unwrap();
        json!({"framework":"Rust / Tauri", "test_mode":self.config.test_mode,
               "mode":if session.fresh() || session.binding.is_none() { &session.mode } else { "unconfirmed" },
               "runner":session.state,"game":session.game,"manual_latch":session.manual,
               "input_release_confirmed":session.release_confirmed,"fresh":session.fresh(),
               "epoch":self.epoch.load(Ordering::SeqCst),"generation":session.generation,
               "pending":session.pending.keys().collect::<Vec<_>>(),"receipt":session.receipt,
               "error":session.error,"logs":session.logs,"hardware":session.hardware,"gamepad":session.gamepad,"messages":session.messages,"readiness":session.readiness,
               "update":read_json(&self.config.project.join("docs/UPDATE_STATUS.json")).unwrap_or_else(|_|json!({"message":"更新状态尚未读取；可运行Update.ps1检查"}))})
    }
    fn feedback(&self, action: &str, phase: &str, message: &str) {
        let mut session = self.session.lock().unwrap();
        session.receipt = json!({"action":action,"phase":phase,"message":message,"at":now_ms(),"epoch":self.epoch.load(Ordering::SeqCst),"request_id":if session.last_request_id.is_empty(){Value::Null}else{json!(session.last_request_id)}});
        if phase != "pending" { session.log(message); }
    }
    fn close_children(&self) {
        let children: Vec<_> = self.children.lock().unwrap().values().cloned().collect();
        for item in children { let mut child = item.lock().unwrap(); let _ = child.kill(); let _ = child.wait(); }
    }
    fn can_close(&self) -> bool {
        let session = self.session.lock().unwrap();
        if session.pending.contains_key("start") || session.pending.contains_key("resume") || session.resume.is_some() { return false; }
        session.release_confirmed && session.fresh() || session.terminal || session.binding.is_none()
    }
    fn finish_close(&self) {
        if self.closing.load(Ordering::SeqCst) && self.can_close() {
            self.closed.store(true, Ordering::SeqCst);
            self.close_children();
            if let Some(app) = self.app.lock().unwrap().as_ref() { app.exit(0); }
        }
    }
    fn binding(&self) -> Result<Binding, String> {
        let cached={let session=self.session.lock().unwrap();(session.binding.clone(),session.terminal)};
        if let Some(mut binding) = cached.0 {
            if cached.1{return Ok(binding);}
            let current=Binding::load(&binding.root,&binding.chat)?;binding.same_owner(&current)?;
            if current.broker.is_some(){binding.broker=current.broker;}
            self.bind(binding.clone())?;return Ok(binding);
        }
        let pointer = read_json(&self.config.project.join("docs/CURRENT_RUNNER.json"))?;
        let binding = Binding::load(Path::new(text(&pointer, "run_dir")), &self.config.chat)?;
        binding.verify(&pointer)?;
        self.bind(binding.clone())?;
        Ok(binding)
    }
    fn bind(&self, mut binding: Binding)->Result<(),String> {
        let mut session = self.session.lock().unwrap();
        if let Some(current)=session.binding.as_ref(){
            if current.run_id==binding.run_id{binding=binding.retain_known_broker(current)?;session.binding=Some(binding);return Ok(());}
        }
        session.binding = Some(binding);
        session.sequence = 0;
        session.generation += 1;
        session.terminal = false;
        session.last_status = None;
        session.release_confirmed = false;
        session.manual_guard=None;session.resumed=None;
        session.log("已核验本聊天执行器的标准归属和进程创建身份。");
        Ok(())
    }
    fn latest_binding(&self,binding:&Binding)->Result<Binding,String>{
        let session=self.session.lock().unwrap();
        binding.retain_known_broker(session.binding.as_ref().ok_or("当前运行绑定已失效")?)
    }
    fn terminal_snapshot(&self)->Result<Option<(Binding,Value)>,String>{
        let binding=self.session.lock().unwrap().binding.clone();
        let Some(binding)=binding else{return Ok(None);};
        let stored=read_json(&self.config.project.join("docs/CURRENT_RUNNER.json"))?;
        if !["stopped","completed","failed"].contains(&text(&stored,"control_mode")){return Ok(None);}
        let binding=self.latest_binding(&binding)?;
        let observed=binding.observe_terminal(&stored)?;
        Ok(Some((binding,observed)))
    }
    fn authorize_start(&self,binding:&Binding,state:&Value,epoch:u64)->Result<(),String>{
        binding.verify(state)?;
        let initial=binding.initialization_pause(state).or_else(|_|binding.initialization_pause(state)).ok();
        let mut session=self.session.lock().unwrap();
        if epoch!=self.epoch.load(Ordering::SeqCst)
            || !matches!(session.startup,Some(StartupHandshake::Pending{epoch:expected}) if expected==epoch){return Err("启动资格已被更新的手动请求撤销".into());}
        let binding=binding.retain_known_broker(session.binding.as_ref().ok_or("启动尚未绑定运行身份")?)?;
        session.startup=Some(StartupHandshake::Bound{epoch,run_id:binding.run_id,launch_id:binding.launch_id,initial});
        Ok(())
    }
    fn authorize_resume(&self,binding:&Binding,reply:&Value,epoch:u64)->Result<(),String>{
        let expected={let session=self.session.lock().unwrap();session.resume.as_ref().filter(|pending|pending.epoch==epoch).map(|pending|pending.guard.clone()).ok_or("本次恢复资格已失效")?};
        binding.verify(&reply["state"])?;
        let command_id=text(reply,"command_id");let result=&reply["resume_result"];
        if reply["resumed"]!=true || reply["guard_matched"]!=true || result["resumed"]!=true || result["guard_matched"]!=true
            || result["requested_guard"]!=expected.value() || text(result,"resume_epoch")!=command_id{return Err("缺少本次旧暂停身份的原子交接确认".into());}
        let proof=binding.resume_proof(&expected,&reply["state"],command_id)?;
        if result["consumed_manual_ids"]!=json!(proof.consumed_ids){return Err("交接回执与实际消费身份不符".into());}
        let mut session=self.session.lock().unwrap();
        if epoch!=self.epoch.load(Ordering::SeqCst) || session.resume.as_ref().map(|pending|(&pending.guard,pending.epoch))!=Some((&expected,epoch)){return Err("新手动请求已撤销本次恢复资格".into());}
        binding.retain_known_broker(session.binding.as_ref().ok_or("恢复运行绑定已失效")?)?;
        session.resumed=Some((epoch,proof));
        Ok(())
    }
    fn accept(&self, binding: &Binding, state: Value, captured_epoch: u64, explicit: bool) -> Result<Acceptance, String> {
        let mode = text(&state,"control_mode").to_string();
        let terminal_mode=["stopped","completed","failed"].contains(&mode.as_str());
        if !terminal_mode{binding.verify(&state)?;}
        if captured_epoch != self.epoch.load(Ordering::SeqCst) { return Ok(Acceptance::Stale); }
        let released = mode == "manual" && binding.pause_confirmed(&state).is_ok();
        let initialization=if mode=="manual"{binding.initialization_pause(&state).or_else(|_|binding.initialization_pause(&state)).ok()}else{None};
        let guard=if mode=="manual" && released{binding.resume_guard(&state).ok()}else{None};
        let (resuming,proof)={let session=self.session.lock().unwrap();(session.resume.clone(),session.resumed.clone())};
        let resume_changed=if !terminal_mode{resuming.as_ref().map(|pending|binding.resume_pending_changed(&pending.guard)).transpose()?}else{None};
        let obsolete=if !terminal_mode && mode=="manual"{proof.as_ref().filter(|(epoch,_)|*epoch==captured_epoch).map(|(_,proof)|binding.obsolete_manual_after_resume(proof,&state).unwrap_or(false)).unwrap_or(false)}else{false};
        let mut session = self.session.lock().unwrap();
        if captured_epoch != self.epoch.load(Ordering::SeqCst) {return Ok(Acceptance::Stale);}
        if !terminal_mode{
            if session.resume.is_some(){
                if resume_changed!=Some(false){
                    session.resume=None;session.resumed=None;session.manual_guard=None;session.manual=true;session.release_confirmed=false;
                    session.mode="unconfirmed".into();session.error="恢复期间出现新的手动/停止身份，旧恢复请求已过期。".into();
                    session.log(&format!("恢复请求过期：新手动/停止身份优先；原本地epoch={captured_epoch}。"));
                    self.epoch.fetch_add(1,Ordering::SeqCst);return Ok(Acceptance::Stale);
                }
                if !explicit{return Ok(Acceptance::Stale);}
            }
            if !explicit && obsolete && !session.manual{session.log("旧暂停观测属于已确认的恢复交接，未重新锁存。");return Ok(Acceptance::Stale);}
        }
        if explicit && (session.resume.as_ref().map(|pending|pending.epoch)!=Some(captured_epoch) || session.resumed.as_ref().map(|(epoch,_)|*epoch)!=Some(captured_epoch)){return Err("界面拒绝没有本次身份守卫的恢复回执".into());}
        // Discovery polling may beat the Start CLI reply. It cannot authorize
        // a run, or classify the old CURRENT record as this new handshake.
        if matches!(session.startup,Some(StartupHandshake::Pending{epoch}) if epoch==captured_epoch){return Ok(Acceptance::Stale);}
        let binding=binding.retain_known_broker(session.binding.as_ref().ok_or("当前运行绑定已失效")?)?;
        if terminal_mode{binding.verified_terminal(&state)?;}
        let terminated=terminal_mode&&binding.stop_confirmed(&state);
        let safety=terminated||released&&["pause","takeover"].iter().any(|a|session.pending.get(*a)==Some(&captured_epoch));
        if session.binding.as_ref().map(|v| &v.run_id) != Some(&binding.run_id) || !safety&&state["state_sequence"].as_u64().unwrap() < session.sequence { return Ok(Acceptance::Stale); }
        let initial_pause=!session.manual && match &session.startup{
            Some(StartupHandshake::Bound{epoch,run_id,launch_id,initial:Some(expected)})=>
                *epoch==captured_epoch && *run_id==binding.run_id && *launch_id==binding.launch_id && initialization.as_ref()==Some(expected),
            _=>false,
        };
        if mode=="manual" && !initial_pause || !["starting","manual"].contains(&mode.as_str()){session.startup=None;}
        session.sequence = session.sequence.max(state["state_sequence"].as_u64().unwrap());
        session.last_status = Some(Instant::now());
        session.error.clear();
        session.state = state;
        session.binding=Some(binding);
        if session.manual && ["starting","auto","waiting_decision"].contains(&mode.as_str()) && !explicit {
            session.mode = "unconfirmed".into();
            session.release_confirmed = false;
            session.error = "用户接管已锁存，拒绝自动状态覆盖；正在重新核验暂停。".into();
            return Ok(Acceptance::ReassertManual);
        }
        if explicit && ["auto","waiting_decision"].contains(&mode.as_str()) { session.manual = false;session.resume=None; }
        if mode == "manual" && !initial_pause { session.manual = true;session.resumed=None;session.manual_guard=guard; }
        else if !["manual"].contains(&mode.as_str()){session.manual_guard=None;}
        // An initial ACK is temporary: the worker is still authorized to
        // hand off to auto. Closing must first create a real manual intent.
        session.release_confirmed = released && !initial_pause;
        session.terminal = terminated;
        session.mode = if initial_pause { "starting".into() } else if mode == "manual" && !released { "unconfirmed".into() } else { mode };
        if initial_pause {session.log("初始化安全暂停已核验，等待本次执行器完成唯一控制器交接。");}
        else if text(&session.state,"control_mode") == "manual" && !released { session.error = "暂停状态尚未通过释放输入回执核验。".into(); }
        Ok(Acceptance::Applied)
    }
}

#[cfg(windows)]
fn drain_pipe<R: Read + std::os::windows::io::AsRawHandle>(pipe: &mut Option<R>, buffer: &mut Vec<u8>) -> Result<(), String> {
    use windows_sys::Win32::System::Pipes::PeekNamedPipe;
    let Some(pipe) = pipe.as_mut() else { return Ok(()); };
    for _ in 0..32 {
        let mut available = 0;
        let success = unsafe { PeekNamedPipe(pipe.as_raw_handle() as _, std::ptr::null_mut(), 0, std::ptr::null_mut(), &mut available, std::ptr::null_mut()) };
        if success == 0 || available == 0 { break; }
        let mut chunk = [0_u8;8192];
        let count = pipe.read(&mut chunk[..(available as usize).min(8192)]).map_err(|e|e.to_string())?;
        buffer.extend_from_slice(&chunk[..count]);
        if buffer.len() > 4_000_000 { return Err("执行器输出超过读取上限".into()); }
    }
    Ok(())
}

fn cli(shared: &Shared, action: &str, binding: Option<&Binding>, reason: &str, resume_guard:Option<&ResumeGuard>) -> Result<Value, String> {
    let command_epoch=shared.epoch.load(Ordering::SeqCst);
    if action=="resume"{
        let session=shared.session.lock().unwrap();
        if session.resume.as_ref().map(|pending|(&pending.guard,pending.epoch))!=resume_guard.map(|guard|(guard,command_epoch)) || command_epoch!=shared.epoch.load(Ordering::SeqCst){return Err("恢复派发前已收到新手动请求，旧请求已过期".into());}
    }
    let mut command = Command::new(&shared.config.python);
    command.args(["-B","-X","utf8"]).arg(&shared.config.runner).arg(action).arg("--chat-id").arg(&shared.config.chat);
    if let Some(binding) = binding {
        Binding::load(&binding.root,&binding.chat)?;
        command.arg("--run-dir").arg(&binding.root).arg("--run-token").arg(&binding.token);
    }
    if action == "start" { command.args(["--max-seconds","7200","--max-matches","1"]); }
    if action == "resume" { command.arg("--handoff").arg("--resume-guard-json").arg(serde_json::to_string(&resume_guard.ok_or("恢复缺少点击时的旧暂停守卫")?.value()).map_err(|e|e.to_string())?); }
    if !reason.is_empty() && ["pause","takeover","stop"].contains(&action) { command.arg("--reason").arg(reason.chars().take(200).collect::<String>()); }
    #[cfg(windows)] { use std::os::windows::process::CommandExt; command.creation_flags(0x08000000); }
    command.stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::piped());
    let child = Arc::new(Mutex::new(command.spawn().map_err(|e|e.to_string())?));
    let pid = child.lock().unwrap().id();
    shared.children.lock().unwrap().insert(pid,child.clone());
    let timeout = match action { "status"=>3, "pause"|"takeover"=>6, "resume"=>12, "stop"=>28, _=>20 };
    let started = Instant::now();
    let mut output = Vec::new();
    let mut error = Vec::new();
    let result = (|| {
        loop {
            let mut locked = child.lock().unwrap();
            drain_pipe(&mut locked.stdout,&mut output)?;
            drain_pipe(&mut locked.stderr,&mut error)?;
            if let Some(code) = locked.try_wait().map_err(|e|e.to_string())? {
                drain_pipe(&mut locked.stdout,&mut output)?;
                drain_pipe(&mut locked.stderr,&mut error)?;
                let source = String::from_utf8_lossy(&output);
                let value: Value = serde_json::from_str(source.lines().rev().find(|s|!s.trim().is_empty()).unwrap_or("{}")).map_err(|_|"执行器未返回可核验JSON".to_string())?;
                if ["start","resume","pause","takeover","stop"].contains(&action){let mut session=shared.session.lock().unwrap();if command_epoch==shared.epoch.load(Ordering::SeqCst){session.last_request_id=text(&value,"command_id").into();}}
                if !code.success() || value["ok"] != true {
                    let message = text(&value,"error");
                    return Err(if message.is_empty() { String::from_utf8_lossy(&error).chars().take(700).collect() } else { message.into() });
                }
                if !value["state"].is_object() || text(&value,"command_id").is_empty() { return Err("执行器回执缺少状态或命令ID".into()); }
                return Ok(value);
            }
            if action=="resume" && command_epoch!=shared.epoch.load(Ordering::SeqCst){let _=locked.kill();let _=locked.wait();return Err("新的手动请求撤销旧恢复请求；实际释放仍须核验ACK".into());}
            if shared.closed.load(Ordering::SeqCst) || started.elapsed() > Duration::from_secs(timeout) {
                let _ = locked.kill();
                let _ = locked.wait();
                return Err("命令超时或界面正在关闭；操作尚未确认，可重新接管或停止。".into());
            }
            drop(locked);
            thread::sleep(Duration::from_millis(20));
        }
    })();
    if result.is_err() {let mut owned=child.lock().unwrap();if owned.try_wait().ok().flatten().is_none(){let _=owned.kill();let _=owned.wait();}}
    shared.children.lock().unwrap().remove(&pid);
    result
}

fn control(shared: &SharedState, action: &str, reason: &str) -> Result<Value, String> {
    control_guarded(shared,action,reason,None)
}
fn control_guarded(shared:&SharedState,action:&str,reason:&str,reassert_epoch:Option<u64>)->Result<Value,String>{
    if !["start","resume","pause","takeover","stop"].contains(&action) { return Err("界面拒绝未知流程控制命令".into()); }
    let entered_epoch=shared.epoch.load(Ordering::SeqCst);
    if ["start","resume"].contains(&action){
        let checked=readiness(shared);let ready=checked["ready"]==true;
        shared.session.lock().unwrap().readiness=checked.clone();
        if !ready{return Err(format!("执行器正在核验，开始暂不可用：{}",text(&checked,"reason")));}
    }
    let epoch = {
        let mut session = shared.session.lock().unwrap();
        if let Some(expected)=reassert_epoch{if action!="takeover" || !session.may_reassert(shared.epoch.load(Ordering::SeqCst),expected){return Err("排队的旧自动接管已失效，保留本次明确交接".into());}}
        if ["start","resume"].contains(&action) {
            if entered_epoch!=shared.epoch.load(Ordering::SeqCst){return Err("源码核验期间收到更新的控制请求，保留用户接管".into());}
            if session.pending.values().any(|_|true) { return Err("请等待当前控制请求确认".into()); }
            if action=="start" && session.binding.is_some() && !session.terminal {return Err("已有执行流程；请先暂停交接或核验所属进程退出".into());}
            if session.gamepad["active"] == true || session.gamepad["available"] != true || session.gamepad["errors"].as_array().map(|v|!v.is_empty()).unwrap_or(true) { return Err("手柄仍在输入或状态未核验，请先松开输入".into()); }
            if !shared.config.test_mode && (session.hardware["f8"]!=true || session.hardware["raw_input"]!=true) {return Err("F8或真实输入监听未确认，请先恢复接管能力再开始".into());}
            if action == "resume" && (!session.fresh() || !session.release_confirmed || !session.manual) { return Err("请先获得同一执行器的暂停与释放输入确认".into()); }
            if action=="resume" && session.manual_guard.as_ref().map(|guard|guard.pending_ids.is_empty() || guard.pause_id.is_none()).unwrap_or(true){return Err("暂停尚未提供本次旧手动身份守卫，恢复不可派发".into());}
        }
        let epoch = shared.epoch.fetch_add(1,Ordering::SeqCst)+1;
        session.begin_control(action,epoch,input::current_tick());
        epoch
    };
    shared.feedback(action,"pending",match action {"start"=>"正在启动本地一条龙…","resume"=>"正在核验唯一控制器交接…","stop"=>"正在停止并等待所属进程退出…",_=>"正在暂停并等待释放鼠标/手柄确认…"});
    let result: Result<Value,String> = (|| {
        if ["pause","takeover","stop"].contains(&action){
            if let Ok(Some((binding,state)))=shared.terminal_snapshot(){
                if matches!(shared.accept(&binding,state.clone(),epoch,false)?,Acceptance::Applied){
                    return Ok(json!({"ok":true,"command_id":format!("gui-terminal-{epoch}"),"state":state}));
                }
            }
        }
        let binding = if action=="start" { None } else { Some(shared.binding()?) };
        let resume_guard=if action=="resume"{shared.session.lock().unwrap().resume.as_ref().filter(|pending|pending.epoch==epoch).map(|pending|pending.guard.clone())}else{None};
        let value = cli(shared,action,binding.as_ref(),reason,resume_guard.as_ref())?;
        let state = value["state"].clone();
        let binding = if let Some(binding)=binding { binding } else {
            let found=Binding::load(Path::new(text(&state,"run_dir")),&shared.config.chat)?;
            found.verify(&state)?;
            shared.bind(found.clone())?;
            found
        };
        if epoch != shared.epoch.load(Ordering::SeqCst) {
            if action == "start" {
                let cloned=shared.clone();
                thread::spawn(move||{let _=control(&cloned,"takeover","启动期间收到用户接管，保持手动");});
            }
            return Err("旧控制回执已失效，保留最新接管请求".into());
        }
        if action=="start"{shared.authorize_start(&binding,&state,epoch)?;}
        if action=="resume"{shared.authorize_resume(&binding,&value,epoch)?;}
        if !matches!(shared.accept(&binding,state.clone(),epoch,action=="resume")?, Acceptance::Applied) {
            return Err("回执已过期或违反手动锁，操作尚未确认".into());
        }
        let binding=shared.latest_binding(&binding)?;
        if ["pause","takeover"].contains(&action) {
            if !(text(&state,"control_mode")=="manual" && binding.pause_confirmed(&state).is_ok()) && !binding.stop_confirmed(&state) { return Err("尚无匹配的释放输入回执，暂停未确认".into()); }
        }
        if action == "stop" && (text(&state,"control_mode")!="stopped" || !binding.stop_confirmed(&state)) { return Err("缺少broker与worker的真实退出证据，停止未确认".into()); }
        if action == "resume" && !["auto","waiting_decision"].contains(&text(&state,"control_mode")) { return Err("执行器尚未确认继续自动".into()); }
        Ok(value)
    })();
    {
        let mut session=shared.session.lock().unwrap();
        if session.pending.get(action)==Some(&epoch) { session.pending.remove(action); }
        if action=="resume" && session.resume.as_ref().map(|pending|pending.epoch)==Some(epoch){session.resume=None;if result.is_err(){session.resumed=None;}}
    }
    if epoch==shared.epoch.load(Ordering::SeqCst) {
        match &result {
            Ok(value) => shared.feedback(action,"done",if ["pause","takeover","stop"].contains(&action)&&["completed","failed"].contains(&text(&value["state"],"control_mode")){"本次流程已经结束，所属进程已核验退出；结果见当前状态。"}else{match action {"pause"|"takeover"=>"已确认暂停并释放所属输入，可以手动操作。","stop"=>"本次流程已停止，所属输入进程已核验退出。","resume"=>"继续自动已确认；新暂停与接管持续优先。",_=>"本地执行器已启动，实际进度见上方。"}}),
            Err(error) => { shared.feedback(action,"error",&format!("操作未确认：{error}")); let mut session=shared.session.lock().unwrap(); session.error=error.clone(); session.mode="unconfirmed".into(); }
        }
    }
    shared.finish_close();
    result
}

fn poll(shared: &SharedState) {
    let checked=readiness(shared);shared.session.lock().unwrap().readiness=checked;
    let epoch=shared.epoch.load(Ordering::SeqCst);
    let terminal=shared.session.lock().unwrap().terminal;
    if !terminal {
        let refreshed=(||->Result<Option<Acceptance>,String>{
            let status=(||->Result<Acceptance,String>{
                let binding=shared.binding()?;
                let value=cli(shared,"status",Some(&binding),"",None)?;
                let binding=shared.latest_binding(&binding)?;
                let state=if ["completed","failed"].contains(&text(&value["state"],"control_mode")){binding.observe_terminal(&value["state"])?}else{value["state"].clone()};
                shared.accept(&binding,state,epoch,false)
            })();
            match status{
                Ok(acceptance)=>Ok(Some(acceptance)),
                Err(error)=>{
                    if shared.session.lock().unwrap().binding.is_none(){return Ok(None);}
                    match shared.terminal_snapshot(){
                        Ok(Some((binding,state)))=>shared.accept(&binding,state,epoch,false).map(Some),
                        Ok(None)=>Err(error),
                        Err(terminal_error)=>Err(terminal_error)
                    }
                }
            }
        })();
        match refreshed {
                Ok(Some(Acceptance::ReassertManual))=>{
                    let allowed={let session=shared.session.lock().unwrap();session.may_reassert(shared.epoch.load(Ordering::SeqCst),epoch) && !session.pending.keys().any(|v|["pause","takeover","stop"].contains(&v.as_str()))};
                    if allowed { let cloned=shared.clone(); thread::spawn(move||{let _=control_guarded(&cloned,"takeover","界面保护：保持已有手动锁，拒绝未授权自动覆盖",Some(epoch));}); }
                },
                Err(error)=>{let mut session=shared.session.lock().unwrap();if !session.terminal {session.error=error;session.mode="unconfirmed".into();session.release_confirmed=false;session.last_status=None;}},
                _=>{}
        }
    }
    if let Ok(game)=read_json(&shared.config.project.join("docs/CURRENT_VERIFICATION.json")) { shared.session.lock().unwrap().game=game; }
    if let Ok(mut messages)=message_records(shared) {shared.session.lock().unwrap().messages=std::mem::take(&mut messages);}
    let mut record=shared.dashboard();
    record["pid"]=json!(std::process::id());
    record["creation_id"]=json!(input::current_creation());
    record["hwnd"]=json!(shared.hwnd.load(Ordering::SeqCst));
    record["chat_id"]=json!(&shared.config.chat);
    record["updated_at_ms"]=json!(now_ms());
    let staged=shared.config.runtime.join("gui-state.staging");
    if fs::write(&staged,serde_json::to_vec_pretty(&record).unwrap()).is_ok(){let _=fs::rename(staged,shared.config.runtime.join("gui-state.json"));}
    shared.finish_close();
}

#[tauri::command]
fn dashboard(state: tauri::State<'_,SharedState>) -> Value { state.dashboard() }

#[tauri::command]
async fn runner_control(action: String, state: tauri::State<'_,SharedState>) -> Result<Value,String> {
    let shared=state.inner().clone();
    tauri::async_runtime::spawn_blocking(move||control(&shared,&action,match action.as_str(){"takeover"=>"用户手动接管","pause"=>"用户暂停","stop"=>"用户停止本次流程",_=>""})).await.map_err(|e|e.to_string())?
}

#[tauri::command]
async fn preview(state: tauri::State<'_,SharedState>) -> Result<Value,String> {
    let shared=state.inner().clone();
    tauri::async_runtime::spawn_blocking(move|| {
        let (binding,request)={let session=shared.session.lock().unwrap();(session.binding.clone(),session.state["decision_request"].clone())};
        let binding=binding.ok_or("尚未连接执行器")?;
        if request.is_null(){return Ok(json!({"available":false}));}
        let bytes=request_png(&binding,&request)?;
        Ok(json!({"available":true,"url":format!("data:image/png;base64,{}",STANDARD.encode(bytes)),"verified_at":request["created_at"],"run_id":binding.run_id,"snapshot_id":request["snapshot_id"]}))
    }).await.map_err(|e|e.to_string())?
}

#[tauri::command]
async fn leave_message(message: String, state: tauri::State<'_,SharedState>) -> Result<Value,String> {
    let shared=state.inner().clone();
    tauri::async_runtime::spawn_blocking(move|| {
        let _guard=shared.message_lock.lock().unwrap();
        let message=message.trim();
        if message.is_empty() || message.chars().count()>2000 { return Err("留言需要1–2000字".into()); }
        let path=shared.config.runtime.join("messages.json");
        let mut records=if path.is_file(){read_json(&path)?["messages"].as_array().cloned().unwrap_or_default()}else{vec![]};
        if records.len()>=20 {return Err("已有20条留言，请等待助手实际读取".into());}
        let record=json!({"id":format!("{}-{}-{}",std::process::id(),now_ms(),shared.message_sequence.fetch_add(1,Ordering::SeqCst)),"chat_id":shared.config.chat,"text":message,"state":"pending","created_at_ms":now_ms()});
        records.push(record.clone());
        let staging=shared.config.runtime.join("messages.staging");
        fs::write(&staging,serde_json::to_vec_pretty(&json!({"messages":records})).unwrap()).map_err(|e|e.to_string())?;
        fs::rename(staging,path).map_err(|e|e.to_string())?;
        let updated=message_records(&shared)?;shared.session.lock().unwrap().messages=updated;
        Ok(record)
    }).await.map_err(|e|e.to_string())?
}

fn message_records(shared:&Shared)->Result<Vec<Value>,String>{
    let path=shared.config.runtime.join("messages.json");
    if !path.is_file(){return Ok(vec![]);}
    let mut records=read_json(&path)?["messages"].as_array().cloned().ok_or("留言记录格式异常")?;
    let ack_path=shared.config.runtime.join("message-acks.json");
    let acknowledgements=if ack_path.is_file(){read_json(&ack_path)?["acks"].as_array().cloned().unwrap_or_default()}else{vec![]};
    for record in &mut records{
        if text(record,"chat_id")!=shared.config.chat{return Err("留言聊天归属不匹配".into());}
        if let Some(ack)=acknowledgements.iter().find(|ack|text(ack,"id")==text(record,"id")&&text(ack,"chat_id")==shared.config.chat&&text(ack,"gui_creation_id")==input::current_creation()){
            if ack["read_at_ms"].as_u64().is_some(){record["state"]=json!(if ack["reply"].is_string(){"replied"}else{"read"});record["reply"]=ack["reply"].clone();record["read_at_ms"]=ack["read_at_ms"].clone();}
        }
    }
    Ok(records)
}

const CORE_FILES:[&str;4]=["tools/currency_wars_runner.py","tools/currency_wars_broker_entry.py","tools/currency_wars_perception.py","tools/currency_wars_control.py"];
fn readiness(shared:&Shared)->Value{
    let checked=(||->Result<Value,String>{
        let manifest=read_json(&shared.config.project.join("docs/RUNNER_READY.json")).map_err(|_|"独立审查尚未完成".to_string())?;
        let review=&manifest["independent_review"];
        if manifest["ready"]!=true || text(&manifest,"owner")!="currency-wars-runner" || text(&manifest,"chat_id")!=shared.config.chat || text(review,"status")!="PASS" || text(review,"reviewer_chat_id").is_empty() || text(review,"reviewed_at").is_empty(){return Err("执行器尚未获得归属一致的独立审查通过记录".into());}
        for file in CORE_FILES{
            let expected=text(&manifest["hashes"],file);
            let path=shared.config.project.join(file);let metadata=fs::symlink_metadata(&path).map_err(|_|format!("源码缺失：{file}"))?;
            if !metadata.is_file() || metadata.file_type().is_symlink() || metadata.len()>4_000_000{return Err(format!("源码路径或大小异常：{file}"));}
            let bytes=fs::read(path).map_err(|_|format!("源码暂不可读取：{file}"))?;
            let actual=format!("{:X}",Sha256::digest(bytes));
            if expected.len()!=64 || !expected.eq_ignore_ascii_case(&actual){return Err(format!("源码已变更，需要重新核验：{file}"));}
        }
        Ok(json!({"ready":true,"reason":"本地执行器已通过独立审查与源码校验。","independent_review":review,"checked_at_ms":now_ms()}))
    })();
    checked.unwrap_or_else(|reason|json!({"ready":false,"reason":reason,"checked_at_ms":now_ms()}))
}

fn main() {
    // Double-clicking the real Rust EXE enters the recorded lifecycle helper;
    // that helper starts this same native UI with its owned scratch runtime.
    if !std::env::args().any(|v|v=="--runtime-dir") {
        let bootstrap=(|| -> Result<(),String> {
            let executable=std::env::current_exe().map_err(|e|e.to_string())?;
            let helper=executable.parent().and_then(Path::parent).ok_or("GUI入口路径异常")?.join("launch.py");
            if !helper.is_file(){return Err("GUI生命周期入口缺失".into());}
            let project=helper.parent().and_then(Path::parent).ok_or("项目入口路径异常")?;
            let python=project.join(".venv/Scripts/python.exe");
            if !python.is_file(){return Err("请先运行项目根目录的Setup.ps1".into());}
            let mut command=Command::new(python);
            command.args(["-B","-X","utf8"]).arg(helper).arg("--binary").arg(executable);
            #[cfg(windows)] {use std::os::windows::process::CommandExt;command.creation_flags(0x08000000);}
            command.stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null()).spawn().map_err(|e|e.to_string())?;
            Ok(())
        })();
        if let Err(error)=bootstrap{input::show_error(&error);}
        return;
    }
    let config=match Config::load(){Ok(config)=>config,Err(error)=>{input::show_error(&error);return;}};
    let shared=Arc::new(Shared{session:Mutex::new(Session::new(config.test_mode)),config,epoch:AtomicU64::new(0),closed:AtomicBool::new(false),closing:AtomicBool::new(false),hwnd:AtomicUsize::new(0),children:Mutex::new(HashMap::new()),app:Mutex::new(None),message_lock:Mutex::new(()),message_sequence:AtomicU64::new(0)});
    let setup_shared=shared.clone();
    let window_shared=shared.clone();
    let result=tauri::Builder::default().manage(shared.clone())
        .invoke_handler(tauri::generate_handler![dashboard,runner_control,preview,leave_message])
        .setup(move|app| {
            *setup_shared.app.lock().unwrap()=Some(app.handle().clone());
            let mut builder=tauri::WebviewWindowBuilder::from_config(app,&app.config().app.windows[0])?
                .focused(false).data_directory(setup_shared.config.runtime.join("webview-profile"));
            if let Some(port)=setup_shared.config.debug_port {builder=builder.additional_browser_args(&format!("--remote-debugging-port={port} --remote-allow-origins=http://127.0.0.1"));}
            let window=builder.build()?;
            #[cfg(windows)] setup_shared.hwnd.store(window.hwnd()?.0 as usize,Ordering::SeqCst);
            fs::write(setup_shared.config.runtime.join("gui-process.json"),serde_json::to_vec_pretty(&json!({"framework":"Rust/Tauri","pid":std::process::id(),"creation_id":input::current_creation(),"hwnd":setup_shared.hwnd.load(Ordering::SeqCst),"chat_id":setup_shared.config.chat,"started_at_ms":now_ms()}))?)?;
            let poll_shared=setup_shared.clone();
            thread::spawn(move||while !poll_shared.closed.load(Ordering::SeqCst){poll(&poll_shared);for _ in 0..10{if poll_shared.closed.load(Ordering::SeqCst){break;}thread::sleep(Duration::from_millis(100));}});
            if !setup_shared.config.test_mode {input::watch(setup_shared.clone());}
            Ok(())
        })
        .on_window_event(move|_,event|if let tauri::WindowEvent::CloseRequested{api,..}=event {
            api.prevent_close();
            window_shared.closing.store(true,Ordering::SeqCst);
            if window_shared.can_close(){window_shared.finish_close();}else{let cloned=window_shared.clone();thread::spawn(move||{let _=control(&cloned,"takeover","关闭界面前保持手动");});}
        })
        .run(tauri::generate_context!());
    shared.closed.store(true,Ordering::SeqCst);
    shared.close_children();
    if let Err(error)=result {let _=fs::write(shared.config.runtime.join("gui-error.txt"),error.to_string());}
}

#[cfg(test)]
mod tests{
    use super::*;
    static STARTUP_FIXTURE_LOCK:Mutex<()>=Mutex::new(());
    #[test]
    fn resume_handoff_keeps_old_snapshots_and_new_intents_separate(){
        let _fixture_guard=STARTUP_FIXTURE_LOCK.lock().unwrap();
        // All files remain inside the build owner's standard scratch run.
        // Exercise production Shared/Binding without a GUI, broker or input.
        let root=PathBuf::from(std::env::var("CW_GUI_STARTUP_FIXTURE").expect("run build.py --test-filter resume_handoff"));
        let marker=read_json(&root.join(".agent-workflow-owner.json")).unwrap();
        let run=text(&marker,"run_id");let creation=text(&marker,"process_identity").trim_start_matches("windows:");
        let launch="0123456789abcdef0123456789abcdef";let init="11111111111111111111111111111111";
        let user="22222222222222222222222222222222";let newer="33333333333333333333333333333333";let command="44444444444444444444444444444444";
        let write=|file:&str,value:&Value|fs::write(root.join(file),serde_json::to_vec(value).unwrap()).unwrap();
        let check=|name:&str,condition:bool|{assert!(condition,"{name}");println!("RESUME_CHECK {name}");};
        fs::create_dir_all(root.join("manual-intents")).unwrap();
        write("runner-owner.json",&json!({"owner":"currency-wars-runner","chat_id":"resume-fixture","run_id":run,"run_token":"fixture-only","runner_pid":marker["pid"],"runner_creation_id":creation,"artifact_chat_id":marker["session_hint"]["id"],"launch_id":launch}));
        write("owner.json",&json!({"owner":"currency-wars-control","chat_id":"resume-fixture","run_token":"fixture-only","artifact_run_id":run}));
        write("broker-process.json",&json!({"protocol_version":2,"pid":39,"creation_id":49,"chat_id":"resume-fixture","run_token":"fixture-only"}));
        let old_guard=json!({"run_id":run,"broker_pid":39,"broker_creation_id":"49","pending_manual_ids":[user],"broker_pause_id":"user-pause","resume_epoch":launch});
        let manual=json!({"protocol_version":1,"owner":"currency-wars-runner","chat_id":"resume-fixture","run_id":run,"run_dir":root,"runner_pid":marker["pid"],"runner_creation_id":creation,"launch_id":launch,"state_sequence":7,"control_mode":"manual","reason":"用户暂停","broker":{"protocol_version":2,"broker_pid":39,"broker_creation_time":49,"chat_id":"resume-fixture","run_dir":root,"ready":true,"paused":true,"acknowledged":true,"input_halted":false,"pause_id":"user-pause","broker_state":{"state":"running"}},"last_command":{"kind":"pause","id":user},"resume_guard":old_guard});
        let reset=||{
            for file in ["runner-resuming.json","input-halted.json","runner-stop","broker-stop"]{let _=fs::remove_file(root.join(file));}
            for entry in fs::read_dir(root.join("manual-intents")).unwrap(){fs::remove_file(entry.unwrap().path()).unwrap();}
            write(&format!("manual-intents/{init}.json"),&json!({"manual_id":init,"reason":"初始化；等待唯一broker与一次受控交接","priority_ns":1}));
            let intent=json!({"manual_id":user,"reason":"用户暂停","priority_ns":20});
            write(&format!("manual-intents/{user}.json"),&intent);write("runner-manual.json",&intent);
            write("manual-pause.json",&json!({"pause_id":"user-pause","reason":"用户暂停","chat_id":"resume-fixture","run_token":"fixture-only"}));
            write("pause-ack.json",&json!({"pause_id":"user-pause","broker_pid":39,"broker_creation_time":49,"chat_id":"resume-fixture","run_token":"fixture-only","owned_inputs_released":true}));
            write("runner-resume-epoch.json",&json!({"id":launch,"consumed_manual_id":init,"consumed_manual_ids":[init]}));
            write("runner-state.json",&manual);
        };
        let setup=||{
            reset();let s=Arc::new(Shared{config:Config{project:root.clone(),python:PathBuf::new(),runner:PathBuf::new(),chat:"resume-fixture".into(),runtime:root.clone(),test_mode:true,debug_port:None},session:Mutex::new(Session::new(true)),epoch:AtomicU64::new(1),closed:AtomicBool::new(false),closing:AtomicBool::new(false),hwnd:AtomicUsize::new(0),children:Mutex::new(HashMap::new()),app:Mutex::new(None),message_lock:Mutex::new(()),message_sequence:AtomicU64::new(0)});
            let b=Binding::load(&root,"resume-fixture").unwrap();s.bind(b.clone()).unwrap();s.accept(&b,manual.clone(),1,false).unwrap();(s,b)
        };
        let begin=|s:&Shared|{s.epoch.store(2,Ordering::SeqCst);s.session.lock().unwrap().begin_control("resume",2,0);};
        let commit=||{
            fs::remove_file(root.join("manual-pause.json")).unwrap();fs::remove_file(root.join("runner-manual.json")).unwrap();
            write("runner-resume-epoch.json",&json!({"id":command,"consumed_manual_id":user,"consumed_manual_ids":[init,user]}));
            let mut auto=manual.clone();auto["control_mode"]=json!("auto");auto["broker"]["pause_id"]=Value::Null;auto["broker"]["paused"]=json!(false);auto["broker"]["acknowledged"]=json!(false);
            auto["last_command"]=json!({"kind":"resume","id":command});
            auto["resume_guard"]=json!({"run_id":run,"broker_pid":39,"broker_creation_id":"49","pending_manual_ids":[],"broker_pause_id":null,"resume_epoch":command});
            write("runner-state.json",&auto);
            json!({"ok":true,"command_id":command,"resumed":true,"guard_matched":true,"state":auto,"resume_result":{"resumed":true,"guard_matched":true,"requested_guard":old_guard,"resume_epoch":command,"consumed_manual_ids":[init,user]}})
        };

        let (s,b)=setup();
        check("real v2 manual ACK caches exactly the six-field old guard",s.can_close() && s.session.lock().unwrap().manual_guard.as_ref().unwrap().value()==old_guard);
        begin(&s);s.session.lock().unwrap().pending.remove("resume");
        check("authorized resume keeps manual until proof and never grants close from old ACK",s.session.lock().unwrap().manual && !s.can_close());
        s.session.lock().unwrap().pending.insert("resume".into(),2);
        check("repeated old manual during pending does not revoke the authorized handoff",matches!(s.accept(&b,manual.clone(),2,false),Ok(Acceptance::Stale)) && matches!(s.accept(&b,manual.clone(),2,false),Ok(Acceptance::Stale)) && s.epoch.load(Ordering::SeqCst)==2);
        let reply=commit();let auto=reply["state"].clone();
        check("auto visible before Resume CLI confirmation cannot enqueue takeover",matches!(s.accept(&b,auto.clone(),2,false),Ok(Acceptance::Stale)) && !s.session.lock().unwrap().may_reassert(2,2));
        check("already queued old Reassert is rejected before dispatch",control_guarded(&s,"takeover","fixture protection",Some(1)).is_err() && s.children.lock().unwrap().is_empty() && s.epoch.load(Ordering::SeqCst)==2);
        s.authorize_resume(&b,&reply,2).unwrap();
        check("authenticated CAS proof still waits for explicit accept and cannot close",matches!(s.accept(&b,auto.clone(),2,false),Ok(Acceptance::Stale)) && !s.can_close());
        check("only the authenticated explicit reply clears the old latch",matches!(s.accept(&b,auto.clone(),2,true),Ok(Acceptance::Applied)) && {let v=s.session.lock().unwrap();!v.manual && v.resume.is_none() && v.resumed.is_some() && !v.release_confirmed});
        s.session.lock().unwrap().pending.remove("resume");
        check("same-sequence delayed old manual does not relatch",matches!(s.accept(&b,manual.clone(),2,false),Ok(Acceptance::Stale)) && !s.session.lock().unwrap().manual);
        let mut transient=auto.clone();transient["control_mode"]=json!("manual");transient["reason"]=manual["reason"].clone();
        check("worker transient manual with the consumed current epoch does not relatch",matches!(s.accept(&b,transient,2,false),Ok(Acceptance::Stale)) && !s.session.lock().unwrap().manual);
        check("queued same-epoch Reassert after success is rejected by the current latch",control_guarded(&s,"takeover","fixture protection",Some(2)).is_err() && s.epoch.load(Ordering::SeqCst)==2);
        write(&format!("manual-intents/{newer}.json"),&json!({"manual_id":newer,"reason":"用户暂停","priority_ns":0}));
        check("new immutable ID after success is never filtered as the consumed old manual",matches!(s.accept(&b,manual.clone(),2,false),Ok(Acceptance::Applied)) && {let v=s.session.lock().unwrap();v.manual && v.resumed.is_none() && !v.release_confirmed});

        let (s,b)=setup();begin(&s);
        write(&format!("manual-intents/{newer}.json"),&json!({"manual_id":newer,"reason":"用户暂停","priority_ns":0}));
        check("pending lower-priority new ID with identical reason revokes the old epoch",matches!(s.accept(&b,manual.clone(),2,false),Ok(Acceptance::Stale)) && s.epoch.load(Ordering::SeqCst)==3 && {let v=s.session.lock().unwrap();v.manual && v.resume.is_none() && !v.release_confirmed});
        let (s,b)=setup();begin(&s);write("manual-pause.json",&json!({"pause_id":"new-pause","chat_id":"resume-fixture","run_token":"fixture-only"}));
        check("a new actual broker pause without an immutable runner ID also revokes resume",matches!(s.accept(&b,manual.clone(),2,false),Ok(Acceptance::Stale)) && s.epoch.load(Ordering::SeqCst)==3);
        let (s,b)=setup();begin(&s);write("runner-stop",&json!({}));
        check("an actual stop flag immediately revokes pending resume",matches!(s.accept(&b,manual.clone(),2,false),Ok(Acceptance::Stale)) && s.epoch.load(Ordering::SeqCst)==3 && !s.can_close());
        for action in ["pause","takeover","stop"]{
            let (s,b)=setup();begin(&s);let reply=commit();s.epoch.store(3,Ordering::SeqCst);s.session.lock().unwrap().begin_control(action,3,0);
            check(&format!("new {action} epoch rejects late Resume proof and reply"),s.authorize_resume(&b,&reply,2).is_err() && matches!(s.accept(&b,reply["state"].clone(),2,true),Ok(Acceptance::Stale)) && {let v=s.session.lock().unwrap();v.manual && v.resume.is_none() && !v.release_confirmed});
        }
        let (s,b)=setup();begin(&s);let mut reply=commit();reply["resume_result"]["requested_guard"]["pending_manual_ids"]=json!([newer]);
        check("successful flags with a substituted old guard cannot authorize resume",s.authorize_resume(&b,&reply,2).is_err() && s.session.lock().unwrap().manual);
        let (s,b)=setup();begin(&s);let reply=commit();write("runner-resume-epoch.json",&json!({"id":command,"consumed_manual_ids":[init]}));
        check("missing actual old-ID consumption cannot authorize resume",s.authorize_resume(&b,&reply,2).is_err() && !s.can_close());
        let (s,b)=setup();let mut broken=manual.clone();broken["resume_guard"]=Value::Null;s.accept(&b,broken,1,false).unwrap();
        check("null optional Resume guard never hides a true emergency release ACK",s.can_close() && {let v=s.session.lock().unwrap();v.manual && v.release_confirmed && v.manual_guard.is_none()});
        let mut absent=manual.clone();absent["resume_guard"].as_object_mut().unwrap().remove("resume_epoch");
        check("missing guard null field and foreign run are not treated as authorization",b.resume_guard(&absent).is_err() && {absent["resume_guard"]=old_guard.clone();absent["resume_guard"]["run_id"]=json!("foreign");b.resume_guard(&absent).is_err()});
    }
    #[test]
    fn startup_close_guard_initial_ack_requires_takeover(){
        let _fixture_guard=STARTUP_FIXTURE_LOCK.lock().unwrap();
        let root=PathBuf::from(std::env::var("CW_GUI_STARTUP_FIXTURE").expect("run build.py --test-filter startup_close_guard"));
        let marker=read_json(&root.join(".agent-workflow-owner.json")).unwrap();let run=text(&marker,"run_id");
        let creation=text(&marker,"process_identity").trim_start_matches("windows:");let launch="0123456789abcdef0123456789abcdef";
        let write=|file:&str,value:&Value|fs::write(root.join(file),serde_json::to_vec(value).unwrap()).unwrap();
        write("runner-owner.json",&json!({"owner":"currency-wars-runner","chat_id":"close-fixture","run_id":run,"run_token":"fixture-only","runner_pid":marker["pid"],"runner_creation_id":creation,"artifact_chat_id":marker["session_hint"]["id"],"launch_id":launch}));
        write("owner.json",&json!({"owner":"currency-wars-control","chat_id":"close-fixture","run_token":"fixture-only","artifact_run_id":run}));
        fs::create_dir_all(root.join("manual-intents")).unwrap();
        let initial=json!({"manual_id":"11111111111111111111111111111111","reason":"初始化；等待唯一broker与一次受控交接","time":"fixture","priority_ns":1});
        write("manual-intents/11111111111111111111111111111111.json",&initial);write("runner-manual.json",&initial);
        write("broker-process.json",&json!({"protocol_version":2,"pid":39,"creation_id":49,"chat_id":"close-fixture","run_token":"fixture-only"}));
        let mut pause=json!({"pause_id":"original-pause","reason":"新本地worker初始安全暂停","chat_id":"close-fixture","run_token":"fixture-only"});write("manual-pause.json",&pause);
        let mut ack=json!({"pause_id":"original-pause","broker_pid":39,"broker_creation_time":49,"chat_id":"close-fixture","run_token":"fixture-only","owned_inputs_released":true});write("pause-ack.json",&ack);
        let mut state=json!({"protocol_version":1,"owner":"currency-wars-runner","chat_id":"close-fixture","run_id":run,"run_dir":root,"runner_pid":marker["pid"],"runner_creation_id":creation,"launch_id":launch,"state_sequence":1,"control_mode":"starting","broker":{"protocol_version":2,"broker_pid":39,"broker_creation_time":49,"chat_id":"close-fixture","run_dir":root,"paused":true,"acknowledged":true,"pause_id":"original-pause","broker_state":{"state":"running"}},"last_command":{"kind":"start","id":launch}});write("runner-state.json",&state);
        let s=Shared{config:Config{project:root.clone(),python:PathBuf::new(),runner:PathBuf::new(),chat:"close-fixture".into(),runtime:root.clone(),test_mode:true,debug_port:None},session:Mutex::new(Session::new(true)),epoch:AtomicU64::new(1),closed:AtomicBool::new(false),closing:AtomicBool::new(false),hwnd:AtomicUsize::new(0),children:Mutex::new(HashMap::new()),app:Mutex::new(None),message_lock:Mutex::new(()),message_sequence:AtomicU64::new(0)};
        let b=Binding::load(&root,"close-fixture").unwrap();s.bind(b.clone()).unwrap();s.session.lock().unwrap().begin_control("start",1,0);s.authorize_start(&b,&state,1).unwrap();
        state["control_mode"]=json!("manual");state["reason"]=initial["reason"].clone();assert!(b.pause_confirmed(&state).is_ok());
        assert!(matches!(s.accept(&b,state.clone(),1,false),Ok(Acceptance::Applied)));s.session.lock().unwrap().pending.remove("start");
        let release=s.session.lock().unwrap().release_confirmed;
        assert!(!release&&!s.can_close());s.closing.store(true,Ordering::SeqCst);s.finish_close();assert!(!s.closed.load(Ordering::SeqCst));
        println!("STARTUP_CLOSE_CHECK valid v2 initial ACK after Start done cannot close before a real takeover");
        // The unchanged CloseRequested branch now enters the existing takeover
        // handler; its new epoch invalidates initialization before any reply.
        let epoch=s.epoch.fetch_add(1,Ordering::SeqCst)+1;s.session.lock().unwrap().begin_control("takeover",epoch,0);
        assert!(matches!(s.accept(&b,state.clone(),1,false),Ok(Acceptance::Stale)));assert!(s.session.lock().unwrap().startup.is_none());
        println!("STARTUP_CLOSE_CHECK window-close takeover revokes the initial epoch");
        let user=json!({"manual_id":"22222222222222222222222222222222","reason":"关闭界面前保持手动","time":"fixture","priority_ns":2});write("manual-intents/22222222222222222222222222222222.json",&user);write("runner-manual.json",&user);
        pause["pause_id"]=json!("user-pause");pause["reason"]=user["reason"].clone();write("manual-pause.json",&pause);ack["pause_id"]=json!("user-pause");write("pause-ack.json",&ack);
        state["state_sequence"]=json!(2);state["reason"]=user["reason"].clone();state["broker"]["pause_id"]=json!("user-pause");
        assert!(b.pause_confirmed(&state).is_ok());assert!(matches!(s.accept(&b,state,epoch,false),Ok(Acceptance::Applied)));s.session.lock().unwrap().pending.remove("takeover");
        assert!(s.can_close());let v=s.session.lock().unwrap();assert!(v.manual&&v.release_confirmed);
        println!("STARTUP_CLOSE_CHECK real user takeover retains valid release ACK and close permission");
    }
    #[test]
    fn startup_handshake_distinguishes_initialization_from_new_manual_intents(){
        let _fixture_guard=STARTUP_FIXTURE_LOCK.lock().unwrap();
        // The Python build owner creates this standard scratch run and keeps
        // its marker/identity intact. No GUI, broker, hooks or game are started.
        let root=PathBuf::from(std::env::var("CW_GUI_STARTUP_FIXTURE").expect("run build.py --test-filter startup_handshake"));
        let marker=read_json(&root.join(".agent-workflow-owner.json")).unwrap();
        let run=text(&marker,"run_id").to_string();let creation=text(&marker,"process_identity").trim_start_matches("windows:").to_string();
        let launch="0123456789abcdef0123456789abcdef";let init="11111111111111111111111111111111";let newer="22222222222222222222222222222222";
        let write=|file:&str,value:&Value|fs::write(root.join(file),serde_json::to_vec(value).unwrap()).unwrap();
        let owner=json!({"owner":"currency-wars-runner","chat_id":"startup-fixture","run_id":run,"run_token":"fixture-only","runner_pid":marker["pid"],"runner_creation_id":creation,"artifact_chat_id":marker["session_hint"]["id"],"launch_id":launch});
        write("runner-owner.json",&owner);
        write("owner.json",&json!({"owner":"currency-wars-control","chat_id":"startup-fixture","run_token":"fixture-only","artifact_run_id":run}));
        fs::create_dir_all(root.join("manual-intents")).unwrap();
        let mut raw=json!({"protocol_version":1,"owner":"currency-wars-runner","chat_id":"startup-fixture","run_id":run,"run_dir":root,"runner_pid":marker["pid"],"runner_creation_id":creation,"launch_id":launch,"state_sequence":1,"control_mode":"starting","broker":{},"last_command":{"kind":"start","id":launch}});
        let broker_record=json!({"protocol_version":2,"pid":39,"creation_id":49,"chat_id":"startup-fixture","run_token":"fixture-only"});
        let broker_state=json!({"protocol_version":2,"broker_pid":39,"broker_creation_time":49});
        let initial=json!({"manual_id":init,"reason":"初始化；等待唯一broker与一次受控交接","time":"2026-10-04T07:30:00Z","priority_ns":1});
        let pause=json!({"pause_id":"original-pause","reason":"新本地worker初始安全暂停","chat_id":"startup-fixture","run_token":"fixture-only"});
        let reset=|raw:&Value|{
            for file in ["runner-resume-epoch.json","runner-resuming.json","broker-process.json","pause-ack.json","runner-stop"]{let _=fs::remove_file(root.join(file));}
            for entry in fs::read_dir(root.join("manual-intents")).unwrap(){fs::remove_file(entry.unwrap().path()).unwrap();}
            write(&format!("manual-intents/{init}.json"),&initial);write("runner-manual.json",&initial);write("manual-pause.json",&pause);write("runner-state.json",raw);
        };
        let shared=||Shared{config:Config{project:root.clone(),python:PathBuf::new(),runner:PathBuf::new(),chat:"startup-fixture".into(),runtime:root.clone(),test_mode:true,debug_port:None},session:Mutex::new(Session::new(true)),epoch:AtomicU64::new(1),closed:AtomicBool::new(false),closing:AtomicBool::new(false),hwnd:AtomicUsize::new(0),children:Mutex::new(HashMap::new()),app:Mutex::new(None),message_lock:Mutex::new(()),message_sequence:AtomicU64::new(0)};
        let setup=|raw:&Value|{reset(raw);let s=shared();let b=Binding::load(&root,"startup-fixture").unwrap();s.bind(b.clone()).unwrap();s.session.lock().unwrap().begin_control("start",1,0);s.authorize_start(&b,raw,1).unwrap();(s,b)};
        let manual=|raw:&Value|{let mut s=raw.clone();s["control_mode"]=json!("manual");s["reason"]=json!("初始化；等待唯一broker与一次受控交接");s};
        let check=|name:&str,condition:bool|{assert!(condition,"{name}");println!("STARTUP_CHECK {name}");};
        let flags=|s:&Shared|{let v=s.session.lock().unwrap();(v.manual,v.startup.is_none())};

        reset(&raw);let s=shared();let b=Binding::load(&root,"startup-fixture").unwrap();s.bind(b.clone()).unwrap();s.session.lock().unwrap().begin_control("start",1,0);
        check("discovery before Start reply cannot authorize a handshake",matches!(s.accept(&b,manual(&raw),1,false),Ok(Acceptance::Stale))&&!s.session.lock().unwrap().manual);
        s.authorize_start(&b,&raw,1).unwrap();
        check("authenticated initialization remains starting without user latch",matches!(s.accept(&b,manual(&raw),1,false),Ok(Acceptance::Applied))&&{let v=s.session.lock().unwrap();!v.manual&&v.mode=="starting"});
        raw["state_sequence"]=json!(2);write("runner-state.json",&raw);
        check("same initialization can be polled repeatedly",matches!(s.accept(&b,manual(&raw),1,false),Ok(Acceptance::Applied))&&!s.session.lock().unwrap().manual);
        let queued=manual(&raw);
        write("broker-process.json",&broker_record);write("pause-ack.json",&json!({"pause_id":"original-pause","broker_pid":39,"broker_creation_time":49,"chat_id":"startup-fixture","run_token":"fixture-only","owned_inputs_released":true}));
        fs::remove_file(root.join("manual-pause.json")).unwrap();
        write("runner-resuming.json",&json!({"id":"handoff","manual_id":init,"time":"fixture"}));
        check("late initial snapshot during broker-to-worker handoff is not user intent",matches!(s.accept(&b,{let mut v=queued.clone();v["broker"]=broker_state.clone();v},1,false),Ok(Acceptance::Applied))&&!s.session.lock().unwrap().manual);
        fs::remove_file(root.join("runner-resuming.json")).unwrap();fs::remove_file(root.join("runner-manual.json")).unwrap();
        write("runner-resume-epoch.json",&json!({"id":"handoff","time":"fixture","consumed_manual_id":init,"consumed_manual_ids":[init]}));
        raw["state_sequence"]=json!(3);raw["control_mode"]=json!("auto");raw["broker"]=broker_state.clone();write("runner-state.json",&raw);
        let fast=shared();let known=Binding::load(&root,"startup-fixture").unwrap();fast.bind(known.clone()).unwrap();fast.session.lock().unwrap().begin_control("start",1,0);
        let mut fast_reply=queued.clone();fast_reply["control_mode"]=json!("starting");fast_reply["broker"]=json!({});
        fast.authorize_start(&known,&fast_reply,1).unwrap();
        check("fast core handoff before Start reply still binds its initial intent",matches!(fast.accept(&known,{let mut v=queued.clone();v["broker"]=broker_state.clone();v},1,false),Ok(Acceptance::Applied))&&!flags(&fast).0);
        check("core auto before GUI observes it does not turn queued initial manual into takeover",matches!(s.accept(&b,{let mut v=queued.clone();v["broker"]=broker_state.clone();v},1,false),Ok(Acceptance::Applied))&&!s.session.lock().unwrap().manual);
        check("initial auto is applied without ReassertManual",matches!(s.accept(&b,raw.clone(),1,false),Ok(Acceptance::Applied))&&flags(&s)==(false,true));
        let mut replay=queued.clone();replay["broker"]=broker_state.clone();replay["state_sequence"]=json!(4);
        check("initial replay after accepted auto cannot regain startup privilege",matches!(s.accept(&b,replay,1,false),Ok(Acceptance::Applied))&&s.session.lock().unwrap().manual);
        raw["state_sequence"]=json!(5);
        check("auto following replay remains blocked by the latch",matches!(s.accept(&b,raw.clone(),1,false),Ok(Acceptance::ReassertManual)));
        raw["control_mode"]=json!("starting");raw["state_sequence"]=json!(1);raw["broker"]=json!({});
        for action in ["pause","takeover","stop"]{
            let (s,b)=setup(&raw);s.accept(&b,manual(&raw),1,false).unwrap();
            s.epoch.store(2,Ordering::SeqCst);s.session.lock().unwrap().begin_control(action,2,0);
            check(&format!("new {action} revokes initialization before accepting old reply"),matches!(s.accept(&b,manual(&raw),1,false),Ok(Acceptance::Stale))&&s.session.lock().unwrap().startup.is_none());
            let mut auto=raw.clone();auto["control_mode"]=json!("auto");auto["state_sequence"]=json!(2);
            check(&format!("new {action} still wins over later auto"),matches!(s.accept(&b,auto,2,false),Ok(Acceptance::ReassertManual)));
        }
        let (s,b)=setup(&raw);let mut extra=initial.clone();extra["manual_id"]=json!(newer);extra["reason"]=json!("用户手动接管");
        write(&format!("manual-intents/{newer}.json"),&extra);write("runner-manual.json",&extra);
        let mut external=manual(&raw);external["reason"]=extra["reason"].clone();
        check("new external immutable manual intent closes initialization",matches!(s.accept(&b,external,1,false),Ok(Acceptance::Applied))&&flags(&s)==(true,true));
        let (s,b)=setup(&raw);extra["reason"]=initial["reason"].clone();write(&format!("manual-intents/{newer}.json"),&extra);
        check("initialization wording alone cannot bypass an extra manual ID",matches!(s.accept(&b,manual(&raw),1,false),Ok(Acceptance::Applied))&&s.session.lock().unwrap().manual);
        let (s,b)=setup(&raw);fs::remove_file(root.join(format!("manual-intents/{init}.json"))).unwrap();write(&format!("manual-intents/{newer}.json"),&extra);write("runner-manual.json",&extra);
        check("replacing the initialization intent identity cannot reuse the grant",matches!(s.accept(&b,manual(&raw),1,false),Ok(Acceptance::Applied))&&s.session.lock().unwrap().manual);
        let (s,b)=setup(&raw);let mut changed=pause.clone();changed["pause_id"]=json!("new-pause");write("manual-pause.json",&changed);
        check("replacing the broker pause identity cannot reuse the grant",matches!(s.accept(&b,manual(&raw),1,false),Ok(Acceptance::Applied))&&s.session.lock().unwrap().manual);
        let (s,b)=setup(&raw);let mut wrong=manual(&raw);wrong["run_id"]=json!("wrong-run");
        check("wrong run cannot use initialization permission",s.accept(&b,wrong,1,false).is_err());
        let mut wrong=manual(&raw);wrong["launch_id"]=json!("wrong-launch");
        check("wrong launch cannot use initialization permission",s.accept(&b,wrong,1,false).is_err());
        reset(&raw);let s=shared();let b=Binding::load(&root,"startup-fixture").unwrap();s.bind(b.clone()).unwrap();
        check("existing manual without this explicit Start remains latched",matches!(s.accept(&b,manual(&raw),1,false),Ok(Acceptance::Applied))&&s.session.lock().unwrap().manual);
        let (s,b)=setup(&raw);s.epoch.store(2,Ordering::SeqCst);s.session.lock().unwrap().begin_control("takeover",2,0);
        check("takeover during Start prevents late Start authorization",s.authorize_start(&b,&raw,1).is_err()&&s.session.lock().unwrap().manual);
    }
    #[test]
    fn late_prebroker_binding_cannot_erase_known_identity_or_accept_sentinel(){
        let shared=Shared{config:Config{project:PathBuf::from(r"C:\Temp\project"),python:PathBuf::new(),runner:PathBuf::new(),chat:"chat".into(),runtime:PathBuf::new(),test_mode:true,debug_port:None},session:Mutex::new(Session::new(true)),epoch:AtomicU64::new(0),closed:AtomicBool::new(false),closing:AtomicBool::new(false),hwnd:AtomicUsize::new(0),children:Mutex::new(HashMap::new()),app:Mutex::new(None),message_lock:Mutex::new(()),message_sequence:AtomicU64::new(0)};
        let early=Binding{root:PathBuf::from(r"C:\Temp\run"),chat:"chat".into(),run_id:"run".into(),token:"token".into(),pid:31,creation:"41".into(),marker_id:"run".into(),broker:None,launch_id:"launch".into()};
        let mut known=early.clone();known.broker=Some(protocol::ProcessIdentity{pid:32,creation:"42".into()});
        shared.bind(known.clone()).unwrap();shared.bind(early.clone()).unwrap();
        assert_eq!(shared.session.lock().unwrap().binding.as_ref().unwrap().broker.as_ref().unwrap().pid,32);
        let mut terminal=json!({"protocol_version":1,"owner":"currency-wars-runner","chat_id":"chat","run_id":"run","run_dir":r"C:\Temp\run","runner_pid":31,"runner_creation_id":"41","launch_id":"launch","state_sequence":0,"control_mode":"stopped","exit_evidence":{"worker":{"pid":31,"state":"absent","error":87,"expected_creation_id":"41"},"broker":{"state":"not_launched","launch_attempted":false,"identity_observed":false,"run_id":"run","worker_pid":31,"worker_creation_id":"41","launch_id":"launch"}}});
        assert!(shared.accept(&early,terminal.clone(),0,false).is_err());
        terminal["exit_evidence"]["broker"]=json!({"pid":32,"state":"exited","creation_id":"42","expected_creation_id":"42"});
        assert!(matches!(shared.accept(&early,terminal,0,false),Ok(Acceptance::Applied)));
    }
}
