//! Passive native input observation. No SendInput, cursor movement or game calls.
use crate::{control, control_guarded, SharedState};
use serde_json::json;
use tauri::Manager;
use std::{collections::VecDeque,mem::{size_of,zeroed},ptr::{null,null_mut},sync::atomic::Ordering,thread,time::{Duration,Instant}};
use windows_sys::Win32::{Foundation::FILETIME,
    System::{Threading::{GetCurrentProcess,GetProcessTimes,GetCurrentThreadId},SystemInformation::GetTickCount},
    UI::{Input::{GetRawInputData,RegisterRawInputDevices,RAWINPUTDEVICE,RAWINPUTHEADER,RAWINPUT,RID_HEADER,RID_INPUT,RIDEV_INPUTSINK,RIDEV_REMOVE,
                 KeyboardAndMouse::{GetAsyncKeyState,RegisterHotKey,UnregisterHotKey,MOD_ALT,MOD_CONTROL,MOD_NOREPEAT,MOD_SHIFT,MOD_WIN,VK_F8,VK_F9,VK_O,VK_P,VK_CONTROL,VK_LCONTROL,VK_RCONTROL,VK_MENU,VK_LMENU,VK_RMENU,VK_TAB,VK_LWIN,VK_RWIN},
                 XboxController::{XInputGetState,XINPUT_STATE}},
         WindowsAndMessaging::{CreateWindowExW,DestroyWindow,DispatchMessageW,PeekMessageW,TranslateMessage,MessageBoxW,MSG,HWND_MESSAGE,PM_REMOVE,WM_HOTKEY,WM_INPUT}}};

pub fn current_creation() -> String {
    unsafe {
        let mut created:FILETIME=zeroed();let mut exited=zeroed();let mut kernel=zeroed();let mut user=zeroed();
        if GetProcessTimes(GetCurrentProcess(),&mut created,&mut exited,&mut kernel,&mut user)==0{return String::new();}
        (((created.dwHighDateTime as u64)<<32)|created.dwLowDateTime as u64).to_string()
    }
}

fn wide(text:&str)->Vec<u16>{text.encode_utf16().chain(Some(0)).collect()}
pub fn current_tick()->u32{unsafe{GetTickCount()}}
pub fn show_error(message:&str){unsafe{MessageBoxW(null_mut(),wide(message).as_ptr(),wide("货币战争助手 · 启动未完成").as_ptr(),0x10);}}

// One physical press can arrive through WM_HOTKEY, raw input and polling.
// Keep observed press intervals, rather than swallowing a second press by time.
struct PressInterval {start:u32,end:Option<u32>,wm_seen:bool}
pub(crate) struct PressEdge {
    down:bool,ignore_held:bool,last_poll:u32,handled:VecDeque<PressInterval>,
}
impl PressEdge {
    pub(crate) fn new(down:bool,tick:u32)->Self{
        let mut value=Self{down,ignore_held:down,last_poll:tick,handled:VecDeque::new()};
        if down{value.remember(tick,None,false);}
        value
    }
    fn inside(tick:u32,start:u32,end:Option<u32>)->bool{
        (tick.wrapping_sub(start) as i32)>=0&&end.map(|end|(end.wrapping_sub(tick) as i32)>=0).unwrap_or(true)
    }
    fn remember(&mut self,start:u32,end:Option<u32>,wm_seen:bool){
        if self.handled.len()==8{self.handled.pop_front();}
        self.handled.push_back(PressInterval{start,end,wm_seen});
    }
    pub(crate) fn message(&mut self,tick:u32)->bool{
        if self.ignore_held{
            self.ignore_held=false;
            if let Some(interval)=self.handled.back_mut(){interval.wm_seen=true;}
            return false;
        }
        // MOD_NOREPEAT provides one WM_HOTKEY per physical press. Consume
        // only one matching notification for an already handled polling edge.
        if let Some(interval)=self.handled.iter_mut().rev().find(|interval|!interval.wm_seen&&Self::inside(tick,interval.start,interval.end)){
            interval.wm_seen=true;return false;
        }
        self.remember(tick,Some(tick),true);true
    }
    pub(crate) fn poll(&mut self,down:bool,tick:u32)->bool{
        let edge=down&&!self.down;
        let start=if (tick.wrapping_sub(self.last_poll) as i32)<0{tick}else{self.last_poll};
        let wm_seen=edge&&self.handled.iter().any(|interval|interval.wm_seen&&Self::inside(interval.start,start,Some(tick)));
        if edge{self.remember(start,None,wm_seen);}
        if !down&&self.down{
            if let Some(interval)=self.handled.iter_mut().rev().find(|interval|interval.end.is_none()){interval.end=Some(tick);}
            self.ignore_held=false;
        }
        self.down=down;self.last_poll=tick;edge&&!wm_seen
    }
}
fn recovery_shortcuts(shared:&SharedState)->(bool,bool){
    let Some(app)=shared.app.lock().unwrap().clone() else{return (false,false);};
    let state=app.state::<crate::FloatingState>();let model=state.0.lock().unwrap();
    let Some(hotkey)=model.hotkey.as_ref().filter(|hotkey|model.full.is_some()&&hotkey.ready()) else{return (false,false);};
    let info=hotkey.info();(info["f9_registered"]==true,info["fallback_registered"]==true)
}
fn manual_shortcut(shared:&SharedState,last:&mut Instant){
    crate::attention::block(shared,"暂停快捷键手动接管；等待明确继续。");
    *last=Instant::now();let cloned=shared.clone();
    thread::spawn(move||{let _=control(&cloned,"takeover","暂停快捷键手动接管，保持手动");});
}

fn physical_input(shared:&SharedState,last:&mut Instant){
    if let Some((epoch,pause))=crate::attention::temporary_input(shared){
        if pause{
            *last=Instant::now();let cloned=shared.clone();
            thread::spawn(move||{
                let _=control_guarded(&cloned,"takeover",crate::attention::INPUT_REASON,Some(epoch));
                // begin_control replaces the reservation with its own epoch.
                // Never clear a later F8/Stop/control request's pending slot.
                let mut session=cloned.session.lock().unwrap();
                if session.pending.get("takeover")==Some(&epoch){session.pending.remove("takeover");}
            });
        }
        return;
    }
    let active={let session=shared.session.lock().unwrap();session.binding.is_some()&&!session.manual||session.pending.contains_key("resume")};
    crate::attention::block(shared,"真实输入不在可恢复范围或存在明确暂停；等待明确继续。");
    if active&&last.elapsed()>Duration::from_millis(300){
        *last=Instant::now();let cloned=shared.clone();
        thread::spawn(move||{let _=control(&cloned,"takeover","真实鼠标/键盘输入，保持手动");});
    }
}

pub fn watch(shared:SharedState) {
    thread::spawn(move||unsafe {
        let class=wide("STATIC");let title=wide("CurrencyWarsPassiveInput");
        let window=CreateWindowExW(0,class.as_ptr(),title.as_ptr(),0,0,0,0,0,HWND_MESSAGE,null_mut(),null_mut(),null());
        let f8=RegisterHotKey(null_mut(),0x4358,MOD_NOREPEAT,VK_F8 as u32)!=0;
        let backup=RegisterHotKey(null_mut(),0x435B,MOD_CONTROL|MOD_ALT|MOD_NOREPEAT,VK_P as u32)!=0;
        let devices=[RAWINPUTDEVICE{usUsagePage:1,usUsage:2,dwFlags:RIDEV_INPUTSINK,hwndTarget:window},RAWINPUTDEVICE{usUsagePage:1,usUsage:6,dwFlags:RIDEV_INPUTSINK,hwndTarget:window}];
        let raw=!window.is_null()&&RegisterRawInputDevices(devices.as_ptr(),2,size_of::<RAWINPUTDEVICE>() as u32)!=0;
        let mut last=Instant::now()-Duration::from_secs(1);
        let initial_f8_down=GetAsyncKeyState(VK_F8 as i32)<0;
        if initial_f8_down{manual_shortcut(&shared,&mut last);}
        let mut f8_edge=PressEdge::new(initial_f8_down,current_tick());
        {let mut session=shared.session.lock().unwrap();session.hardware=json!({"f8":f8,"pause_backup_registered":backup,"hotkey_thread_id":GetCurrentThreadId(),"hotkey_owner_hwnd":window as usize,"raw_input":raw,"test_mode":false});session.log(if f8{"F8全局接管已启用。"}else{"F8注册失败，请使用手动接管按钮。"});session.log(if backup{"Ctrl+Alt+P 暂停备用键已启用；F8 和备用键均不恢复自动。"}else{"Ctrl+Alt+P 注册失败；F8 暂停与界面按钮仍保留。"});session.log(if raw{"真实鼠标/键盘被动监听已启用。"}else{"真实鼠标/键盘监听未确认，请使用接管按钮。"});}
        let mut navigation:Option<(Instant,u64,u32)>=None;
        let mut backup_edge=PressEdge::new(false,current_tick());
        let mut raw_f8_down=false;let mut recovery_chord=false;
        let mut pad_at=Instant::now()-Duration::from_secs(1);
        while !shared.closed.load(Ordering::SeqCst) {
            let (f9_key,recovery_key)=recovery_shortcuts(&shared);
            let ctrl=GetAsyncKeyState(VK_CONTROL as i32)<0;let alt=GetAsyncKeyState(VK_MENU as i32)<0;
            let o_down=GetAsyncKeyState(VK_O as i32)<0;
            recovery_chord=recovery_key&&((ctrl&&alt&&o_down)||(recovery_chord&&(ctrl||alt||o_down)));
            let mut pause_pressed=f8_edge.poll(GetAsyncKeyState(VK_F8 as i32)<0||raw_f8_down,current_tick());
            pause_pressed|=backup&&backup_edge.poll(ctrl&&alt&&GetAsyncKeyState(VK_P as i32)<0,current_tick());
            if pause_pressed{navigation=None;manual_shortcut(&shared,&mut last);pause_pressed=false;}
            let mut message:MSG=zeroed();
            while PeekMessageW(&mut message,null_mut(),0,0,PM_REMOVE)!=0 {
                if message.message==WM_HOTKEY{
                    let key=((message.lParam as usize>>16)&0xFFFF) as u16;
                    let modifiers=message.lParam as u32&(MOD_ALT|MOD_CONTROL|MOD_SHIFT|MOD_WIN);
                    if f8&&message.wParam==0x4358&&key==VK_F8&&modifiers==0{pause_pressed|=f8_edge.message(message.time);}
                    if backup&&message.wParam==0x435B&&key==VK_P&&modifiers==(MOD_CONTROL|MOD_ALT){pause_pressed|=backup_edge.message(message.time);}
                }
                if pause_pressed{navigation=None;manual_shortcut(&shared,&mut last);pause_pressed=false;}
                if raw&&message.message==WM_INPUT {
                    let mut header:RAWINPUTHEADER=zeroed();let mut length=size_of::<RAWINPUTHEADER>() as u32;
                    let result=GetRawInputData(message.lParam as _,RID_HEADER,&mut header as *mut _ as _,&mut length,size_of::<RAWINPUTHEADER>() as u32);
                    // Ignore raw events already queued before the explicit
                    // Start/Resume click, while keeping later hardware input safe.
                    let (eligible,guard_tick)={let session=shared.session.lock().unwrap();(session.binding.is_some()&&!session.terminal&&(message.time.wrapping_sub(session.input_guard_tick) as i32)>0,session.input_guard_tick)};
                    if result!=u32::MAX&&!header.hDevice.is_null()&&header.dwType<=1{
                        let mut switching=false;let mut shortcut=false;
                        if header.dwType==0{
                            let mut data:RAWINPUT=zeroed();let mut size=size_of::<RAWINPUT>() as u32;
                            if GetRawInputData(message.lParam as _,RID_INPUT,&mut data as *mut _ as _,&mut size,size_of::<RAWINPUTHEADER>() as u32)!=u32::MAX&&data.data.mouse.Anonymous.Anonymous.usButtonFlags==0{
                                // Pure pointer motion neither pauses nor cancels a return.
                                // GetLastInputInfo still delays recovery while the mouse moves.
                                TranslateMessage(&message);DispatchMessageW(&message);continue;
                            }
                        }
                        if header.dwType==1{
                            let mut data:RAWINPUT=zeroed();let mut size=size_of::<RAWINPUT>() as u32;
                            if GetRawInputData(message.lParam as _,RID_INPUT,&mut data as *mut _ as _,&mut size,size_of::<RAWINPUTHEADER>() as u32)!=u32::MAX{
                                let keyboard=data.data.keyboard;let key=keyboard.VKey;
                                if key==VK_F8{
                                    let down=keyboard.Flags&1==0;
                                    if !down||!raw_f8_down{pause_pressed|=f8_edge.poll(down,message.time);}
                                    raw_f8_down=down;shortcut=true;
                                }
                                // Only our registered HUD recovery keys bypass ordinary input
                                // takeover; all other real input retains the existing guard.
                                shortcut|=f9_key&&key==VK_F9||recovery_chord&&[VK_O,VK_CONTROL,VK_LCONTROL,VK_RCONTROL,VK_MENU,VK_LMENU,VK_RMENU].contains(&key);
                                switching=[VK_MENU,VK_LMENU,VK_RMENU,VK_TAB,VK_LWIN,VK_RWIN].contains(&key);
                            }
                        }
                        if pause_pressed{navigation=None;manual_shortcut(&shared,&mut last);pause_pressed=false;}
                        if !eligible||shortcut{TranslateMessage(&message);DispatchMessageW(&message);continue;}
                        let epoch=shared.epoch.load(Ordering::SeqCst);
                        if switching&&crate::attention::begin_navigation(&shared,epoch){navigation.get_or_insert((Instant::now()+Duration::from_millis(250),epoch,guard_tick));}
                        else{physical_input(&shared,&mut last);}
                    }
                }
                TranslateMessage(&message);DispatchMessageW(&message);
            }
            if let Some((deadline,epoch,guard_tick))=navigation{
                if Instant::now()>=deadline{
                    navigation=None;crate::attention::finish_navigation(&shared,epoch);
                    let current={let session=shared.session.lock().unwrap();session.input_guard_tick==guard_tick};
                    if current&&epoch==shared.epoch.load(Ordering::SeqCst)&&!shared.closing.load(Ordering::SeqCst){physical_input(&shared,&mut last);}
                }
            }
            if pad_at.elapsed()>Duration::from_millis(120) {
                pad_at=Instant::now();let mut connected=0;let mut active=false;let mut errors=vec![];
                for index in 0..4 {let mut pad:XINPUT_STATE=zeroed();let code=XInputGetState(index,&mut pad);if code==0 {connected+=1;let p=pad.Gamepad;active|=p.wButtons!=0||p.bLeftTrigger>30||p.bRightTrigger>30||i32::from(p.sThumbLX).abs()>7849||i32::from(p.sThumbLY).abs()>7849||i32::from(p.sThumbRX).abs()>8689||i32::from(p.sThumbRY).abs()>8689;}else if code!=1167{errors.push(code);}}
                let (take,revoke)={let mut session=shared.session.lock().unwrap();session.gamepad=json!({"available":true,"active":active,"connected":connected,"errors":errors});let revoke=(active||!errors.is_empty())&&session.binding.is_some()&&!session.terminal;(revoke&&(!session.manual||session.pending.contains_key("resume")),revoke)};
                if revoke{crate::attention::block(&shared,"手柄输入或监测错误；等待明确继续。");}
                if take&&last.elapsed()>Duration::from_millis(300) {last=Instant::now();let cloned=shared.clone();thread::spawn(move||{let _=control(&cloned,"takeover","手柄输入或监测错误，保持手动接管");});}
            }
            thread::sleep(Duration::from_millis(20));
        }
        if raw {let removed=[RAWINPUTDEVICE{usUsagePage:1,usUsage:2,dwFlags:RIDEV_REMOVE,hwndTarget:null_mut()},RAWINPUTDEVICE{usUsagePage:1,usUsage:6,dwFlags:RIDEV_REMOVE,hwndTarget:null_mut()}];RegisterRawInputDevices(removed.as_ptr(),2,size_of::<RAWINPUTDEVICE>() as u32);}
        if f8 {UnregisterHotKey(null_mut(),0x4358);}
        if backup {UnregisterHotKey(null_mut(),0x435B);}
        if !window.is_null(){DestroyWindow(window);}
    });
}
