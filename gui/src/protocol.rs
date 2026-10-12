use serde_json::{json, Value};
use std::{fs, path::{Path, PathBuf}, thread, time::{Duration, Instant}};
use sha2::{Digest,Sha256};

#[derive(Clone,PartialEq,Eq)]
pub struct ProcessIdentity {pub pid:u64,pub creation:String}

#[derive(Clone,PartialEq,Eq)]
pub struct InitializationPause {pub intent_id:String,pub pause_id:String}

#[derive(Clone,PartialEq,Eq)]
pub struct ResumeGuard {
    pub run_id:String,pub broker:ProcessIdentity,pub pending_ids:Vec<String>,
    pub pause_id:Option<String>,pub resume_epoch:Option<String>,
}
#[derive(Clone)]
pub struct ResumeProof {pub before:ResumeGuard,pub epoch:String,pub consumed_ids:Vec<String>}

/// A root selected by the verified parent launch, never by the worker owner.
#[derive(Clone,PartialEq)]
pub struct RuntimeAuthority {
    location: Option<Value>,
    legacy_temp: Option<PathBuf>,
    runtime_provider: Value,
}

fn check_location(value:&Value)->Result<(),String>{
    let object=value.as_object().ok_or("运行位置不是对象")?;
    if object.len()!=4 || !["schema","source","runtime_root","installation_id"].iter().all(|key|object.contains_key(*key))
        || value["schema"].as_u64()!=Some(1) || !Path::new(text(value,"runtime_root")).is_absolute(){
        return Err("运行位置字段、版本或绝对根目录无效".into());
    }
    let root=Path::new(text(value,"runtime_root"));
    if root.parent().is_none() || root.file_name().is_none(){return Err("运行位置不能是磁盘根".into());}
    match text(value,"source"){
        "installed_bridge"=>{
            let id=text(value,"installation_id");
            if !same_path(root,Path::new(r"D:\Codex\Temp\codex-agent-workflow"))
                || id.len()!=32 || !id.bytes().all(|v|v.is_ascii_digit()||(b'a'..=b'f').contains(&v)){
                return Err("固定输入组件的批准根或安装身份不匹配".into());
            }
        },
        "standalone" if value["installation_id"].is_null()=>{},
        _=>return Err("运行位置来源或安装身份无效".into()),
    }
    Ok(())
}

fn gui_registration(marker:&Value,record:&Value,root:&Path,chat:&str,location:&Value,
                    gui_pid:u64,gui_creation:&str,parent_probe:&Value)->Result<Value,String>{
    let parent_pid=marker["pid"].as_u64().unwrap_or(0);
    let parent_creation=text(marker,"process_identity").strip_prefix("windows:").unwrap_or("");
    let children=marker["protected_children"].as_array().ok_or("GUI子进程登记缺失")?;
    if marker["schema"]!=1 || text(marker,"tool")!="codex-agent-workflow"
        || !text(marker,"purpose").starts_with("currency-wars-native-gui-")
        || !same_path(Path::new(text(marker,"path")),root)
        || !same_path(Path::new(text(marker,"root")),root.parent().ok_or("GUI目录没有父根")?)
        || parent_pid==0 || parent_creation.is_empty() || !parent_creation.bytes().all(|v|v.is_ascii_digit())
        || gui_pid==0 || gui_creation.is_empty() || !gui_creation.bytes().all(|v|v.is_ascii_digit())
        || marker["children_incomplete"]!=true
        || children.len()>32
        || !children.iter().any(|child|child["pid"].as_u64()==Some(gui_pid)
            && text(child,"process_identity")==format!("windows:{gui_creation}"))
        || record["schema"]!=1 || text(record,"owner")!="currency-wars-gui-runtime"
        || record["run_id"]!=marker["run_id"] || text(record,"run_id").len()!=32
        || !text(record,"run_id").bytes().all(|v|v.is_ascii_digit()||(b'a'..=b'f').contains(&v))
        || text(record,"chat_id")!=chat || record["runtime_location"]!=*location
        || record["launcher_pid"].as_u64()!=Some(parent_pid)
        || text(record,"launcher_creation_id")!=parent_creation
        || record["gui_pid"].as_u64()!=Some(gui_pid) || text(record,"gui_creation_id")!=gui_creation
        || parent_probe["pid"].as_u64()!=Some(parent_pid)
        || identity(parent_probe,"expected_creation_id")!=parent_creation
        || identity(parent_probe,"creation_id")!=parent_creation || text(parent_probe,"state")!="running"{
        return Err("GUI父启动链、真实进程登记或运行位置未通过核验".into());
    }
    Ok(record["runtime_provider"].clone())
}

impl RuntimeAuthority {
    pub fn legacy()->Result<Self,String>{
        Ok(Self{location:None,legacy_temp:Some(std::env::temp_dir().canonicalize().map_err(|e|e.to_string())?),runtime_provider:Value::Null})
    }

    pub fn location(&self)->Option<&Value>{self.location.as_ref()}

    pub fn runtime_provider(&self)->&Value{&self.runtime_provider}

    pub fn from_gui(root:&Path,chat:&str,declared:Option<&str>)->Result<Self,String>{
        no_links(root)?;
        let record_path=root.join("runtime-location.json");
        let declared=match declared{
            Some(raw)=>serde_json::from_str::<Value>(raw).map_err(|e|e.to_string())?,
            None=>{
                match fs::symlink_metadata(&record_path){
                    Ok(_)=>return Err("GUI新运行位置缺少父启动参数，拒绝降级旧协议".into()),
                    Err(error) if error.kind()==std::io::ErrorKind::NotFound=>{},
                    Err(error)=>return Err(error.to_string()),
                }
                let legacy=Self::legacy()?;
                legacy.check_root(root,None)?;
                return Ok(legacy);
            },
        };
        check_location(&declared)?;
        // Failure of an unrelated system TEMP disables legacy compatibility;
        // it must not block an explicitly verified installed/standalone root.
        let mut authority=Self{location:Some(declared.clone()),legacy_temp:std::env::temp_dir().canonicalize().ok(),runtime_provider:Value::Null};
        authority.check_canonical_root(root,Some(&declared))?;
        // Popen can run Rust before Python registers its child. Only absence
        // of this atomically published record is transient, once at startup.
        let deadline=Instant::now()+Duration::from_secs(3);
        loop{
            match fs::symlink_metadata(&record_path){
                Ok(_)=>break,
                Err(error) if error.kind()==std::io::ErrorKind::NotFound=>{
                    if Instant::now()>=deadline{return Err("GUI父启动登记超时，未核验运行位置".into());}
                    thread::sleep(Duration::from_millis(25));
                },
                Err(error)=>return Err(error.to_string()),
            }
        }
        let record=read_json(&record_path)?;
        let marker=read_json(&root.join(".agent-workflow-owner.json"))?;
        let parent_pid=marker["pid"].as_u64().unwrap_or(0);
        let parent_creation=text(&marker,"process_identity").strip_prefix("windows:").unwrap_or("");
        let creation=crate::input::current_creation();
        if creation.is_empty(){return Err("GUI本进程创建身份未知".into());}
        authority.runtime_provider=gui_registration(&marker,&record,root,chat,&declared,std::process::id() as u64,
                         &creation,&probe_process(parent_pid,parent_creation))?;
        Ok(authority)
    }

    fn check_root(&self,root:&Path,owner_location:Option<&Value>)->Result<(),String>{
        if !root.is_absolute(){return Err("运行目录不是绝对路径".into());}
        if let Some(actual)=owner_location{
            let expected=self.location.as_ref().ok_or("旧GUI没有授权此显式运行位置；请重新启动GUI")?;
            check_location(actual)?;
            if actual!=expected || !same_path(root.parent().ok_or("运行目录没有父根")?,Path::new(text(expected,"runtime_root"))){
                return Err("运行目录或位置不属于本GUI已验证的父启动选择".into());
            }
        }else{
            // Only complete absence is legacy. Null or malformed fields took
            // the explicit branch above and cannot widen the system boundary.
            let temp=self.legacy_temp.as_ref().ok_or("缺少旧临时目录边界")?;
            let canonical=root.canonicalize().map_err(|e|e.to_string())?;
            if !path_key(&canonical).starts_with(&(path_key(temp)+"\\")){
                return Err("旧协议运行目录不在原系统临时目录".into());
            }
        }
        Ok(())
    }

    fn check_canonical_root(&self,root:&Path,owner_location:Option<&Value>)->Result<(),String>{
        self.check_root(root,owner_location)?;
        if let Some(location)=owner_location{
            let canonical=root.canonicalize().map_err(|e|e.to_string())?;
            let expected=Path::new(text(location,"runtime_root")).canonicalize().map_err(|e|e.to_string())?;
            if canonical.parent().map(|parent|same_path(parent,&expected))!=Some(true){
                return Err("运行目录不是已验证根的直接实际子目录".into());
            }
        }
        Ok(())
    }
}

fn nullable_id(value:&Value,field:&str)->Result<Option<String>,String>{
    match value.get(field){Some(Value::Null)=>Ok(None),Some(Value::String(id)) if !id.is_empty()=>Ok(Some(id.clone())),_=>Err("交接守卫缺少明确身份或null".into())}
}
fn intent_ids(value:&Value)->Result<Vec<String>,String>{
    let values=value.as_array().ok_or("手动意图集合格式不符")?;
    if values.len()>2000{return Err("手动意图集合超过有界容量".into());}
    let mut ids=Vec::new();
    for value in values{
        let id=value.as_str().ok_or("手动意图ID不是字符串")?;
        if id.len()!=32 || !id.bytes().all(|v|v.is_ascii_hexdigit()){return Err("手动意图ID格式不符".into());}
        ids.push(id.to_owned());
    }
    ids.sort();if ids.windows(2).any(|pair|pair[0]==pair[1]){return Err("手动意图ID重复".into());}
    Ok(ids)
}
impl ResumeGuard {
    pub fn value(&self)->Value{json!({"run_id":self.run_id,"broker_pid":self.broker.pid,"broker_creation_id":self.broker.creation,"pending_manual_ids":self.pending_ids,"broker_pause_id":self.pause_id,"resume_epoch":self.resume_epoch})}
}

#[derive(Clone)]
pub struct Binding {
    pub root: PathBuf,
    pub chat: String,
    pub run_id: String,
    pub token: String,
    pub pid: u64,
    pub creation: String,
    pub marker_id: String,
    pub broker: Option<ProcessIdentity>,
    pub launch_id:String,
    pub runtime_location:Option<Value>,
    pub runtime_authority:RuntimeAuthority,
}

pub fn text<'a>(value: &'a Value, field: &str) -> &'a str {
    value.get(field).and_then(Value::as_str).unwrap_or("")
}
pub fn identity(value:&Value,field:&str)->String{
    value[field].as_str().map(str::to_owned).or_else(||value[field].as_u64().map(|v|v.to_string())).unwrap_or_default()
}

pub fn read_json(path: &Path) -> Result<Value, String> {
    let metadata = fs::symlink_metadata(path).map_err(|e| e.to_string())?;
    if !metadata.is_file() || metadata.file_type().is_symlink() || metadata.len() > 2_000_000 {
        return Err("状态文件类型或大小异常".into());
    }
    #[cfg(windows)] {
        use std::os::windows::fs::MetadataExt;
        if metadata.file_attributes() & 0x400 != 0 {
            return Err("拒绝链接或联接状态文件".into());
        }
    }
    let source = fs::read(path).map_err(|e| e.to_string())?;
    let source = source.strip_prefix(&[0xef, 0xbb, 0xbf]).unwrap_or(&source);
    let value: Value = serde_json::from_slice(source).map_err(|e| e.to_string())?;
    if !value.is_object() { return Err("状态必须是JSON对象".into()); }
    Ok(value)
}

pub fn path_key(path: &Path) -> String {
    path.to_string_lossy().trim_start_matches(r"\\?\").replace('/', "\\").to_lowercase()
}

pub fn same_path(left: &Path, right: &Path) -> bool { path_key(left) == path_key(right) }

fn no_links(path: &Path) -> Result<(), String> {
    for ancestor in path.ancestors() {
        let info = fs::symlink_metadata(ancestor).map_err(|e| e.to_string())?;
        if info.file_type().is_symlink() { return Err("运行路径包含链接".into()); }
        #[cfg(windows)] {
            use std::os::windows::fs::MetadataExt;
            if info.file_attributes() & 0x400 != 0 { return Err("运行路径包含目录联接".into()); }
        }
    }
    Ok(())
}

impl Binding {
    pub fn load_with_runtime(root: &Path, chat: &str, authority:&RuntimeAuthority) -> Result<Self, String> {
        if !root.is_absolute() { return Err("运行目录不是绝对路径".into()); }
        no_links(root)?;
        let marker = read_json(&root.join(".agent-workflow-owner.json"))?;
        let owner = read_json(&root.join("runner-owner.json"))?;
        authority.check_canonical_root(root,owner.get("runtime_location"))?;
        let pid = owner["runner_pid"].as_u64().unwrap_or(0);
        let creation = text(&owner, "runner_creation_id").to_string();
        if marker["schema"] != 1 || text(&marker, "tool") != "codex-agent-workflow"
            || !same_path(Path::new(text(&marker, "path")), root)
            || !same_path(Path::new(text(&marker, "root")), root.parent().ok_or("运行目录没有父路径")?)
            || text(&owner, "owner") != "currency-wars-runner" || text(&owner, "chat_id") != chat
            || pid == 0 || creation.is_empty() || text(&owner, "run_token").is_empty()
            || text(&owner, "run_id").is_empty() || marker["pid"].as_u64() != Some(pid)
            || text(&owner,"launch_id").is_empty()
            || marker["run_id"] != owner["run_id"]
            || text(&marker, "process_identity") != format!("windows:{creation}")
            || marker["session_hint"]["id"] != owner["artifact_chat_id"] {
            return Err("标准运行归属、聊天或执行器身份不匹配".into());
        }
        let broker_path=root.join("broker-process.json");
        let broker=if broker_path.exists(){
            let record=read_json(&broker_path)?;
            let broker_pid=record["pid"].as_u64().unwrap_or(0);let broker_creation=identity(&record,"creation_id");
            if record["protocol_version"]!=2 || broker_pid==0 || broker_creation.is_empty() || text(&record,"chat_id")!=chat || record["run_token"]!=owner["run_token"]{return Err("broker协议或所属进程身份未通过核验".into());}
            Some(ProcessIdentity{pid:broker_pid,creation:broker_creation})
        }else{None};
        Ok(Self { root: root.to_path_buf(), chat: chat.into(), run_id: text(&owner, "run_id").into(),
                  token: text(&owner, "run_token").into(), pid, creation,
                  marker_id: text(&marker, "run_id").into(),broker,launch_id:text(&owner,"launch_id").into(),
                  runtime_location:owner.get("runtime_location").cloned(),runtime_authority:authority.clone() })
    }

    #[cfg(test)]
    pub fn load(root:&Path,chat:&str)->Result<Self,String>{
        Self::load_with_runtime(root,chat,&RuntimeAuthority::legacy()?)
    }

    pub fn same_owner(&self,current:&Self)->Result<(),String>{
        if current.chat!=self.chat || !same_path(&current.root,&self.root)
            || current.run_id != self.run_id || current.token != self.token || current.pid != self.pid
            || current.creation != self.creation || current.marker_id != self.marker_id
            || current.launch_id!=self.launch_id
            || current.runtime_location!=self.runtime_location || current.runtime_authority!=self.runtime_authority
            || self.broker.as_ref().zip(current.broker.as_ref()).map(|(a,b)|a!=b).unwrap_or(false){return Err("运行归属或已绑定broker身份发生变化".into());}
        Ok(())
    }
    pub fn retain_known_broker(&self,current:&Self)->Result<Self,String>{
        self.same_owner(current)?;
        let mut merged=self.clone();
        if merged.broker.is_none(){merged.broker=current.broker.clone();}
        Ok(merged)
    }
    pub fn verify_header(&self,state:&Value)->Result<(),String>{
        if state["protocol_version"] != 1 || text(state, "owner") != "currency-wars-runner"
            || text(state, "chat_id") != self.chat || text(state, "run_id") != self.run_id
            || !same_path(Path::new(text(state, "run_dir")), &self.root)
            || state["runner_pid"].as_u64() != Some(self.pid) || text(state, "runner_creation_id") != self.creation
            || text(state,"launch_id")!=self.launch_id
            || state.get("runtime_location")!=self.runtime_location.as_ref()
            || state["state_sequence"].as_u64().is_none()
            || !["starting", "auto", "waiting_decision", "manual", "halted", "stopping", "stopped", "failed", "completed"].contains(&text(state, "control_mode")) {
            return Err("执行器回执版本、代际或PID创建身份未通过核验".into());
        }
        Ok(())
    }
    pub fn verify(&self, state: &Value) -> Result<(), String> {
        let current=Self::load_with_runtime(&self.root,&self.chat,&self.runtime_authority)?;self.same_owner(&current)?;self.verify_header(state)?;
        if let Some(broker)=&current.broker{
            let pending=text(state,"control_mode")=="starting" && state["broker"]["broker_pid"].is_null();
            if !pending&&(state["broker"]["protocol_version"]!=2 || state["broker"]["broker_pid"].as_u64()!=Some(broker.pid) || identity(&state["broker"],"broker_creation_time")!=broker.creation){return Err("状态中的broker不是当前已绑定进程".into());}
        }
        Ok(())
    }
    pub fn resume_guard(&self,state:&Value)->Result<ResumeGuard,String>{
        self.verify_header(state)?;
        let value=&state["resume_guard"];
        let keys=["run_id","broker_pid","broker_creation_id","pending_manual_ids","broker_pause_id","resume_epoch"];
        let object=value.as_object().ok_or("执行器回执缺少本次交接守卫")?;
        if object.len()!=keys.len() || keys.iter().any(|key|!object.contains_key(*key)){return Err("交接守卫字段不完整".into());}
        let broker=self.broker.as_ref().ok_or("恢复前尚未绑定唯一broker")?;
        let pause_id=nullable_id(value,"broker_pause_id")?;
        if text(value,"run_id")!=self.run_id || value["broker_pid"].as_u64()!=Some(broker.pid)
            || text(value,"broker_creation_id")!=broker.creation
            || state["broker"]["protocol_version"]!=2 || state["broker"]["broker_pid"].as_u64()!=Some(broker.pid)
            || identity(&state["broker"],"broker_creation_time")!=broker.creation
            || nullable_id(&state["broker"],"pause_id")?!=pause_id{return Err("交接守卫与当前运行/broker/暂停身份不一致".into());}
        Ok(ResumeGuard{run_id:self.run_id.clone(),broker:broker.clone(),pending_ids:intent_ids(&value["pending_manual_ids"])?,pause_id,resume_epoch:nullable_id(value,"resume_epoch")?})
    }
    pub fn resume_proof(&self,before:&ResumeGuard,state:&Value,command_id:&str)->Result<ResumeProof,String>{
        let after=self.resume_guard(state)?;
        if before.run_id!=self.run_id || self.broker.as_ref()!=Some(&before.broker)
            || before.pending_ids.is_empty() || before.pause_id.is_none()
            || !["auto","waiting_decision"].contains(&text(state,"control_mode"))
            || text(&state["last_command"],"kind")!="resume" || text(&state["last_command"],"id")!=command_id
            || command_id.is_empty() || after.resume_epoch.as_deref()!=Some(command_id)
            || !after.pending_ids.is_empty() || after.pause_id.is_some()
            || state["broker"]["ready"]!=true || state["broker"]["paused"]!=false || state["broker"]["input_halted"]!=false
            || text(&state["broker"]["broker_state"],"state")!="running"{return Err("恢复回执缺少同一受控交接的确认".into());}
        let epoch=read_json(&self.root.join("runner-resume-epoch.json"))?;
        let consumed_ids=intent_ids(&epoch["consumed_manual_ids"])?;
        if text(&epoch,"id")!=command_id || before.pending_ids.iter().any(|id|!consumed_ids.contains(id)){return Err("恢复未消费本次授权的旧手动意图".into());}
        let proof=ResumeProof{before:before.clone(),epoch:command_id.into(),consumed_ids};
        if !self.resume_files_clear(&proof)?{return Err("恢复后已有新的手动/停止意图，仍保留锁".into());}
        Ok(proof)
    }
    fn resume_files_clear(&self,proof:&ResumeProof)->Result<bool,String>{
        if proof.before.run_id!=self.run_id || self.broker.as_ref()!=Some(&proof.before.broker){return Ok(false);}
        for file in ["manual-pause.json","runner-manual.json","input-halted.json","runner-stop","broker-stop"]{
            if self.root.join(file).try_exists().map_err(|e|e.to_string())?{return Ok(false);}
        }
        let epoch=read_json(&self.root.join("runner-resume-epoch.json"))?;
        if text(&epoch,"id")!=proof.epoch || intent_ids(&epoch["consumed_manual_ids"])?!=proof.consumed_ids{return Ok(false);}
        let pending=self.pending_manual_ids()?;
        Ok(pending.is_empty())
    }
    pub fn pending_manual_ids(&self)->Result<Vec<String>,String>{
        let directory=self.root.join("manual-intents");no_links(&directory)?;
        let mut ids=Vec::new();let mut scanned=0;
        for entry in fs::read_dir(directory).map_err(|e|e.to_string())?{
            scanned+=1;if scanned>2000{return Err("手动意图目录超过有界容量".into());}
            let path=entry.map_err(|e|e.to_string())?.path();
            if path.extension().and_then(|v|v.to_str())!=Some("json"){continue;}
            let value=read_json(&path)?;let id=text(&value,"manual_id");
            if path.file_stem().and_then(|v|v.to_str())!=Some(id) || text(&value,"reason").is_empty(){return Err("不可变手动意图身份不符".into());}
            ids.extend(intent_ids(&json!([id]))?);
        }
        ids.sort();if ids.windows(2).any(|pair|pair[0]==pair[1]){return Err("不可变手动意图重复".into());}
        let epoch_path=self.root.join("runner-resume-epoch.json");
        let consumed=if epoch_path.exists(){intent_ids(&read_json(&epoch_path)?["consumed_manual_ids"])?}else{vec![]};
        Ok(ids.into_iter().filter(|id|!consumed.contains(id)).collect())
    }
    pub fn resume_pending_changed(&self,before:&ResumeGuard)->Result<bool,String>{
        if before.run_id!=self.run_id || self.broker.as_ref()!=Some(&before.broker){return Err("恢复期间所属运行身份已变".into());}
        if self.pending_manual_ids()?.iter().any(|id|!before.pending_ids.contains(id)){return Ok(true);}
        for file in ["runner-stop","broker-stop"]{
            if self.root.join(file).try_exists().map_err(|e|e.to_string())?{return Ok(true);}
        }
        let pause_path=self.root.join("manual-pause.json");
        if pause_path.try_exists().map_err(|e|e.to_string())?{
            let pause=read_json(&pause_path)?;
            if text(&pause,"chat_id")!=self.chat || text(&pause,"run_token")!=self.token{return Err("恢复期间暂停归属不符".into());}
            if Some(text(&pause,"pause_id"))!=before.pause_id.as_deref(){return Ok(true);}
        }
        let manual_path=self.root.join("runner-manual.json");
        if manual_path.try_exists().map_err(|e|e.to_string())?{
            let manual=read_json(&manual_path)?;
            if !before.pending_ids.iter().any(|id|id==text(&manual,"manual_id")){return Ok(true);}
        }
        Ok(false)
    }
    pub fn obsolete_manual_after_resume(&self,proof:&ResumeProof,state:&Value)->Result<bool,String>{
        let guard=self.resume_guard(state)?;
        if text(state,"control_mode")!="manual" || guard.run_id!=proof.before.run_id || guard.broker!=proof.before.broker
            || guard.pending_ids.iter().any(|id|!proof.before.pending_ids.contains(id))
            || guard.pause_id.is_some() && guard.pause_id!=proof.before.pause_id
            || guard.resume_epoch!=proof.before.resume_epoch && guard.resume_epoch.as_deref()!=Some(proof.epoch.as_str()) {return Ok(false);}
        self.resume_files_clear(proof)
    }
    pub fn initialization_pause(&self,state:&Value)->Result<InitializationPause,String>{
        // This is an observation of the worker's first handshake, never an
        // authorization to resume. The GUI must separately bind a new Start.
        self.verify_header(state)?;
        if !["starting","manual"].contains(&text(state,"control_mode"))
            || text(&state["last_command"],"kind")!="start"
            || text(&state["last_command"],"id")!=self.launch_id
            || text(state,"control_mode")=="manual" && text(state,"reason")!="初始化；等待唯一broker与一次受控交接"
            || self.root.join("runner-stop").exists(){return Err("不属于初始化暂停".into());}
        let raw=read_json(&self.root.join("runner-state.json"))?;self.verify_header(&raw)?;
        if !["starting","auto","waiting_decision"].contains(&text(&raw,"control_mode")) || text(&raw["last_command"],"kind")!="start"
            || text(&raw["last_command"],"id")!=self.launch_id{return Err("初始worker阶段已结束".into());}
        let directory=self.root.join("manual-intents");no_links(&directory)?;
        let mut intents=Vec::new();let mut scanned=0;
        for entry in fs::read_dir(&directory).map_err(|e|e.to_string())?{
            scanned+=1;if scanned>2000{return Err("初始化意图目录超过有界容量".into());}
            let path=entry.map_err(|e|e.to_string())?.path();
            if path.extension().and_then(|v|v.to_str())==Some("json"){
                intents.push(path);if intents.len()>1{return Err("初始化后已有额外手动意图".into());}
            }
        }
        if intents.len()!=1{return Err("初始化意图身份不唯一".into());}
        let intent=read_json(&intents[0])?;let id=text(&intent,"manual_id");
        if id.len()!=32 || !id.bytes().all(|v|v.is_ascii_hexdigit())
            || intents[0].file_stem().and_then(|v|v.to_str())!=Some(id)
            || text(&intent,"reason")!="初始化；等待唯一broker与一次受控交接"
            || text(&intent,"time").is_empty() || intent["priority_ns"].as_u64().unwrap_or(0)==0{return Err("初始化意图缺少原始身份".into());}
        let display_path=self.root.join("runner-manual.json");
        if display_path.exists() && read_json(&display_path)?!=intent{return Err("初始手动展示身份已改变".into());}
        let owner=read_json(&self.root.join("owner.json"))?;
        if text(&owner,"owner")!="currency-wars-control"
            || text(&owner,"chat_id")!=self.chat || text(&owner,"run_token")!=self.token
            || text(&owner,"artifact_run_id")!=self.run_id{return Err("初始化broker归属不匹配".into());}
        let epoch_path=self.root.join("runner-resume-epoch.json");
        let consumed=if epoch_path.exists(){
            let epoch=read_json(&epoch_path)?;
            if epoch["consumed_manual_ids"]!=json!([id]) || text(&epoch,"consumed_manual_id")!=id
                || text(&epoch,"id").is_empty() || text(&epoch,"time").is_empty(){return Err("初始交接消费身份不匹配".into());}
            true
        }else{false};
        let pause_path=self.root.join("manual-pause.json");
        let pause_id=if pause_path.exists(){
            let pause=read_json(&pause_path)?;
            if consumed || text(&raw,"control_mode")!="starting"
                || text(&pause,"chat_id")!=self.chat || text(&pause,"run_token")!=self.token
                || text(&pause,"reason")!="新本地worker初始安全暂停" || text(&pause,"pause_id").is_empty(){return Err("初始化broker暂停身份不匹配".into());}
            text(&pause,"pause_id").to_owned()
        }else{
            // The broker removes its pause before the worker publishes auto.
            // A queued initial status remains the same handshake in that gap.
            let resuming_path=self.root.join("runner-resuming.json");
            let resuming=if resuming_path.exists(){
                let value=read_json(&resuming_path)?;
                text(&value,"manual_id")==id && !text(&value,"id").is_empty() && !text(&value,"time").is_empty()
            }else{false};
            if !consumed && !(resuming && text(&raw,"control_mode")=="starting"){return Err("无同一初始化交接证明".into());}
            let ack=read_json(&self.root.join("pause-ack.json"))?;
            let broker=read_json(&self.root.join("broker-process.json"))?;
            if broker["protocol_version"]!=2 || text(&broker,"chat_id")!=self.chat || text(&broker,"run_token")!=self.token
                || broker["pid"].as_u64().unwrap_or(0)==0 || identity(&broker,"creation_id").is_empty()
                || ack["broker_pid"]!=broker["pid"] || identity(&ack,"broker_creation_time")!=identity(&broker,"creation_id")
                || text(&ack,"chat_id")!=self.chat || text(&ack,"run_token")!=self.token
                || ack["owned_inputs_released"]!=true || text(&ack,"pause_id").is_empty(){return Err("初始交接缺少所属v2暂停ACK".into());}
            text(&ack,"pause_id").to_owned()
        };
        if !text(&state["broker"],"pause_id").is_empty() && text(&state["broker"],"pause_id")!=pause_id{return Err("初始快照pause身份已改变".into());}
        Ok(InitializationPause{intent_id:id.into(),pause_id})
    }
    pub fn verified_terminal(&self,state:&Value)->Result<(),String>{
        self.verify_header(state)?;
        if !["stopped","completed","failed"].contains(&text(state,"control_mode")) || !self.stop_confirmed(state){return Err("终态缺少同一worker与broker的所属退出证据".into());}
        Ok(())
    }
    pub fn observe_terminal(&self,state:&Value)->Result<Value,String>{
        self.verify_header(state)?;
        if !["stopped","completed","failed"].contains(&text(state,"control_mode")){return Err("当前记录不是流程终态".into());}
        let mut observed=state.clone();
        observed.get_mut("exit_evidence").and_then(Value::as_object_mut).ok_or("终态退出证据格式异常")?.insert("worker".into(),probe_process(self.pid,&self.creation));
        self.verified_terminal(&observed)?;
        if let Some(broker)=&self.broker{
            if !process_exited(&probe_process(broker.pid,&broker.creation),broker.pid,&broker.creation){return Err("所属broker的实际退出仍未确认".into());}
        }
        observed["gui_exit_verification"]=json!({"source":"Windows kernel PID/creation query","worker_confirmed":true,"broker_confirmed":true});
        Ok(observed)
    }
    pub fn stop_confirmed(&self,state:&Value)->bool{
        let evidence=&state["exit_evidence"];
        if !process_exited(&evidence["worker"],self.pid,&self.creation){return false;}
        if let Some(broker)=&self.broker{return process_exited(&evidence["broker"],broker.pid,&broker.creation);}
        // A missing broker identity never proves it died. Only the same
        // authenticated worker's explicit launch facts can certify no broker.
        let exit=&evidence["broker"];
        exit["identity_observed"]==false && exit["pid"].is_null()
            && text(exit,"run_id")==self.run_id && exit["worker_pid"].as_u64()==Some(self.pid)
            && identity(exit,"worker_creation_id")==self.creation && text(exit,"launch_id")==self.launch_id
            && match text(exit,"state"){
                "not_launched"=>exit["launch_attempted"]==false,
                "launch_failed"=>exit["launch_attempted"]==true && exit["launch_exit_code"].as_i64().map(|v|v!=0).unwrap_or(false),
                _=>false
            }
    }

    pub fn pause_confirmed(&self, state: &Value) -> Result<(), String> {
        let broker = &state["broker"];
        let owner = read_json(&self.root.join("owner.json"))?;
        let identity = read_json(&self.root.join("broker-process.json"))?;
        let pause = read_json(&self.root.join("manual-pause.json"))?;
        let ack = read_json(&self.root.join("pause-ack.json"))?;
        let pause_id = text(broker, "pause_id");
        if broker["protocol_version"]!=2 || identity["protocol_version"]!=2
            || text(&owner, "owner") != "currency-wars-control" || text(&owner, "chat_id") != self.chat
            || text(&owner, "run_token") != self.token || text(broker, "chat_id") != self.chat
            || !same_path(Path::new(text(broker, "run_dir")), &self.root)
            || broker["paused"] != true || broker["acknowledged"] != true
            || text(&broker["broker_state"], "state") != "running" || pause_id.is_empty()
            || text(&pause, "pause_id") != pause_id || text(&ack, "pause_id") != pause_id
            || ack["owned_inputs_released"] != true || identity["pid"] != broker["broker_pid"]
            || identity["creation_id"] != broker["broker_creation_time"]
            || ack["broker_pid"] != broker["broker_pid"] || ack["broker_creation_time"] != broker["broker_creation_time"]
            || text(&identity, "chat_id") != self.chat || text(&identity, "run_token") != self.token
            || text(&pause, "chat_id") != self.chat || text(&pause, "run_token") != self.token
            || text(&ack, "chat_id") != self.chat || text(&ack, "run_token") != self.token {
            return Err("暂停回执的ID、归属、PID创建时间或释放输入证据不匹配".into());
        }
        Ok(())
    }
}

pub fn probe_process(pid:u64,creation:&str)->Value{
    let mut proof=json!({"pid":pid,"expected_creation_id":creation,"state":"unknown"});
    if pid==0 || pid>u32::MAX as u64 || creation.parse::<u64>().map(|v|v==0).unwrap_or(true){return proof;}
    #[cfg(windows)] unsafe{
        use windows_sys::Win32::{Foundation::{CloseHandle,GetLastError,FILETIME},System::Threading::{OpenProcess,GetProcessTimes,GetExitCodeProcess,PROCESS_QUERY_LIMITED_INFORMATION}};
        let handle=OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION,0,pid as u32);
        if handle.is_null(){let error=GetLastError();proof["error"]=json!(error);if error==87{proof["state"]=json!("absent");}return proof;}
        let mut created:FILETIME=std::mem::zeroed();let mut exited=std::mem::zeroed();let mut kernel=std::mem::zeroed();let mut user=std::mem::zeroed();let mut code=0;
        if GetProcessTimes(handle,&mut created,&mut exited,&mut kernel,&mut user)==0 || GetExitCodeProcess(handle,&mut code)==0{
            let error=GetLastError();CloseHandle(handle);proof["error"]=json!(error);return proof;
        }
        CloseHandle(handle);
        let actual=(((created.dwHighDateTime as u64)<<32)|created.dwLowDateTime as u64).to_string();
        proof["creation_id"]=json!(actual);proof["exit_code"]=json!(code);
        proof["state"]=json!(if actual!=creation{"reused"}else if code==259{"running"}else{"exited"});
    }
    proof
}

pub fn process_exited(proof:&Value,pid:u64,creation:&str)->bool{
    if proof["pid"].as_u64()!=Some(pid) || identity(proof,"expected_creation_id")!=creation{return false;}
    match text(proof,"state"){
        "absent"=>proof["error"]==87,
        "exited"=>identity(proof,"creation_id")==creation,
        "reused"=>!identity(proof,"creation_id").is_empty()&&identity(proof,"creation_id")!=creation,
        _=>false
    }
}

pub fn request_png(binding:&Binding,request:&Value)->Result<Vec<u8>,String>{
    let file=Path::new(text(request,"original_png"));
    if !file.is_absolute(){return Err("原始战略画面路径未提供".into());}
    no_links(file)?;
    let path=file.canonicalize().map_err(|e|e.to_string())?;let root=binding.root.canonicalize().map_err(|e|e.to_string())?;
    let metadata=fs::metadata(&path).map_err(|e|e.to_string())?;
    if !path.starts_with(root)||path.extension().and_then(|v|v.to_str())!=Some("png")||!metadata.is_file()||metadata.len()>12_000_000{return Err("原始战略画面不属于当前运行或超过读取上限".into());}
    let bytes=fs::read(path).map_err(|e|e.to_string())?;
    if !bytes.starts_with(b"\x89PNG\r\n\x1a\n") || !format!("{:x}",Sha256::digest(&bytes)).eq_ignore_ascii_case(text(request,"snapshot_id")){return Err("战略画面与本次snapshot不匹配".into());}
    Ok(bytes)
}

pub fn public_failure(message: &str) -> Value {
    json!({"mode":"unconfirmed", "error":message, "input_release_confirmed":false})
}

#[cfg(test)]
mod tests {
    use super::*;
    fn installed_location()->Value{
        json!({"schema":1,"source":"installed_bridge","runtime_root":r"D:\Codex\Temp\codex-agent-workflow","installation_id":"0123456789abcdef0123456789abcdef"})
    }
    fn runtime_fixture()->PathBuf{
        let nonce=std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
        let root=std::env::temp_dir().join(format!("currency-wars-gui-runtime-test-{}-{nonce}",std::process::id()));
        fs::create_dir(&root).unwrap();root
    }
    #[test]
    fn runtime_location_requires_exact_schema_and_approved_installed_root(){
        let location=installed_location();assert!(check_location(&location).is_ok());
        for (key,value) in [("schema",json!(2)),("source",json!("owner")),("runtime_root",json!(r"E:\arbitrary")),
                            ("runtime_root",json!(r"D:\Codex\Temp\codex-agent-workflow-other")),
                            ("installation_id",Value::Null),("installation_id",json!("0123456789ABCDEF0123456789ABCDEF"))]{
            let mut wrong=location.clone();wrong[key]=value;assert!(check_location(&wrong).is_err(),"{key}");
        }
        let mut wrong=location.clone();wrong["extra"]=json!(true);assert!(check_location(&wrong).is_err());
        let mut standalone=location.clone();standalone["source"]=json!("standalone");standalone["installation_id"]=Value::Null;
        assert!(check_location(&standalone).is_ok());
        standalone["runtime_root"]=json!("relative");assert!(check_location(&standalone).is_err());
    }
    #[test]
    fn runtime_location_keeps_explicit_root_and_legacy_system_temp_separate(){
        let legacy_root=runtime_fixture();
        let location=installed_location();let mut authority=RuntimeAuthority::legacy().unwrap();authority.location=Some(location.clone());
        let approved=Path::new(r"D:\Codex\Temp\codex-agent-workflow\run");
        assert!(authority.check_root(approved,Some(&location)).is_ok());
        assert!(authority.check_root(Path::new(r"D:\Codex\Temp\codex-agent-workflow-other\run"),Some(&location)).is_err());
        assert!(authority.check_root(Path::new(r"D:\Codex\Temp\codex-agent-workflow\nested\run"),Some(&location)).is_err());
        // No owner field is the sole compatibility case; explicit null is not.
        assert!(authority.check_root(&legacy_root,None).is_ok());
        assert!(authority.check_root(&legacy_root,Some(&Value::Null)).is_err());
        let mut changed=location.clone();changed["installation_id"]=json!("ffffffffffffffffffffffffffffffff");
        assert!(authority.check_root(approved,Some(&changed)).is_err());
        assert!(RuntimeAuthority::legacy().unwrap().check_root(approved,Some(&location)).is_err());
        fs::remove_dir(legacy_root).unwrap();
    }
    #[test]
    fn runtime_location_explicit_root_does_not_require_legacy_temp(){
        let location=installed_location();
        let authority=RuntimeAuthority{location:Some(location.clone()),legacy_temp:None,runtime_provider:Value::Null};
        let approved=Path::new(r"D:\Codex\Temp\codex-agent-workflow\run");
        assert!(authority.check_root(approved,Some(&location)).is_ok());
        assert!(authority.check_root(approved,None).is_err());
    }
    #[test]
    fn runtime_location_bound_owner_and_terminal_header_reject_downgrade(){
        let location=installed_location();let mut authority=RuntimeAuthority::legacy().unwrap();authority.location=Some(location.clone());
        let binding=Binding{root:PathBuf::from(r"D:\Codex\Temp\codex-agent-workflow\run"),chat:"chat".into(),run_id:"run".into(),token:"token".into(),pid:31,creation:"41".into(),marker_id:"run".into(),broker:None,launch_id:"launch".into(),runtime_location:Some(location.clone()),runtime_authority:authority};
        let state=json!({"protocol_version":1,"owner":"currency-wars-runner","chat_id":"chat","run_id":"run","run_dir":binding.root,
                         "runner_pid":31,"runner_creation_id":"41","launch_id":"launch","state_sequence":1,"control_mode":"stopped","runtime_location":location});
        assert!(binding.verify_header(&state).is_ok());
        let mut missing=state.clone();missing.as_object_mut().unwrap().remove("runtime_location");assert!(binding.verify_header(&missing).is_err());
        let mut wrong=state.clone();wrong["runtime_location"]=Value::Null;assert!(binding.verify_header(&wrong).is_err());
        let mut current=binding.clone();current.runtime_location=None;assert!(binding.same_owner(&current).is_err());
        current=binding.clone();current.runtime_location.as_mut().unwrap()["installation_id"]=json!("ffffffffffffffffffffffffffffffff");
        assert!(binding.same_owner(&current).is_err());
    }
    #[test]
    fn runtime_location_registration_requires_exact_live_parent_and_child(){
        let location=installed_location();let root=Path::new(r"D:\Codex\Temp\codex-agent-workflow\gui");
        let marker=json!({"schema":1,"tool":"codex-agent-workflow","purpose":"currency-wars-native-gui-local","path":root,
                         "root":r"D:\Codex\Temp\codex-agent-workflow","pid":31,"process_identity":"windows:41",
                         "run_id":"0123456789abcdef0123456789abcdef","children_incomplete":true,
                         "protected_children":[{"pid":32,"process_identity":"windows:42"}]});
        let record=json!({"schema":1,"owner":"currency-wars-gui-runtime","run_id":marker["run_id"],"chat_id":"chat","runtime_location":location,
                         "launcher_pid":31,"launcher_creation_id":"41","gui_pid":32,"gui_creation_id":"42",
                         "runtime_provider":{"kind":"installed","path":"explicit local fixture","sha256":"fixture digest"}});
        let probe=json!({"pid":31,"expected_creation_id":"41","creation_id":"41","state":"running"});
        assert_eq!(gui_registration(&marker,&record,root,"chat",&location,32,"42",&probe).unwrap(),record["runtime_provider"]);
        for (key,value) in [("gui_pid",json!(33)),("gui_creation_id",json!("43")),("launcher_creation_id",json!("99")),
                            ("chat_id",json!("other")),("run_id",json!("ffffffffffffffffffffffffffffffff")),("runtime_location",Value::Null)]{
            let mut wrong=record.clone();wrong[key]=value;assert!(gui_registration(&marker,&wrong,root,"chat",&location,32,"42",&probe).is_err(),"{key}");
        }
        let mut unpublished=marker.clone();unpublished["protected_children"]=json!([]);
        assert!(gui_registration(&unpublished,&record,root,"chat",&location,32,"42",&probe).is_err());
        for state in ["unknown","exited","reused"]{
            let mut wrong=probe.clone();wrong["state"]=json!(state);assert!(gui_registration(&marker,&record,root,"chat",&location,32,"42",&wrong).is_err());
        }
    }
    #[test]
    fn runtime_location_published_registration_cannot_lose_launch_argument(){
        let root=runtime_fixture();
        assert!(RuntimeAuthority::from_gui(&root,"chat",None).is_ok());
        fs::write(root.join("runtime-location.json"),b"{}").unwrap();
        assert!(RuntimeAuthority::from_gui(&root,"chat",None).is_err());
        fs::remove_file(root.join("runtime-location.json")).unwrap();fs::remove_dir(root).unwrap();
    }
    #[test]
    fn stop_requires_both_real_exit_records() {
        assert!(!process_exited(&json!({"state":"exited"}),31,"41"));
        assert!(process_exited(&json!({"pid":31,"state":"exited","creation_id":41,"expected_creation_id":"41"}),31,"41"));
        assert!(!process_exited(&json!({"pid":32,"state":"exited","creation_id":41,"expected_creation_id":"41"}),31,"41"));
        assert!(process_exited(&json!({"pid":31,"state":"absent","error":87,"expected_creation_id":41}),31,"41"));
        assert!(!process_exited(&json!({"pid":31,"state":"absent","error":5,"expected_creation_id":41}),31,"41"));
        assert!(process_exited(&json!({"pid":31,"state":"reused","creation_id":42,"expected_creation_id":41}),31,"41"));
        assert!(!process_exited(&json!({"pid":31,"state":"reused","creation_id":41,"expected_creation_id":41}),31,"41"));
    }
    #[test]
    fn unobserved_broker_requires_bound_launch_facts() {
        let mut binding=Binding{root:PathBuf::from(r"C:\Temp\run"),chat:"chat".into(),run_id:"run".into(),token:"token".into(),pid:31,creation:"41".into(),marker_id:"run".into(),broker:None,launch_id:"launch".into(),runtime_location:None,runtime_authority:RuntimeAuthority::legacy().unwrap()};
        let mut state=json!({"exit_evidence":{"worker":{"pid":31,"state":"absent","error":87,"expected_creation_id":"41"},"broker":{"state":"not_launched","launch_attempted":false,"identity_observed":false,"run_id":"run","worker_pid":31,"worker_creation_id":"41","launch_id":"launch"}}});
        assert!(binding.stop_confirmed(&state));
        state["exit_evidence"]["broker"]["launch_id"]=json!("other");
        assert!(!binding.stop_confirmed(&state));
        state["exit_evidence"]["broker"]["launch_id"]=json!("launch");
        state["exit_evidence"]["broker"]["state"]=json!("launch_failed");
        state["exit_evidence"]["broker"]["launch_attempted"]=json!(true);
        assert!(!binding.stop_confirmed(&state));
        state["exit_evidence"]["broker"]["launch_exit_code"]=json!(1);
        assert!(binding.stop_confirmed(&state));
        state["exit_evidence"]["broker"]["launch_exit_code"]=json!(0);
        assert!(!binding.stop_confirmed(&state));
        state["exit_evidence"]["broker"]["launch_exit_code"]=json!(1);
        binding.broker=Some(ProcessIdentity{pid:32,creation:"42".into()});
        assert!(!binding.stop_confirmed(&state));
    }
    #[test]
    fn windows_path_comparison_preserves_boundaries() {
        assert!(same_path(Path::new(r"\\?\C:\Temp\Run"), Path::new(r"c:\temp\run")));
        assert!(!same_path(Path::new(r"C:\Temp\Run"), Path::new(r"C:\Temp\Run-other")));
    }
    #[test]
    fn malformed_public_data_is_unknown() {
        assert_eq!(text(&json!({"mode":123}), "mode"), "");
        assert_eq!(public_failure("timeout")["input_release_confirmed"], false);
    }
    #[test]
    fn passive_probe_does_not_confirm_a_running_worker() {
        let pid=std::process::id() as u64;
        let creation=crate::input::current_creation();
        let proof=probe_process(pid,&creation);
        assert_eq!(proof["state"],"running");
        assert!(!process_exited(&proof,pid,&creation));
        assert_eq!(probe_process(pid,"not-a-creation")["state"],"unknown");
    }
}
