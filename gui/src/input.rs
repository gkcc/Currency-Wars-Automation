//! Passive native input observation. No SendInput, cursor movement or game calls.
use crate::{control, SharedState};
use serde_json::json;
use std::{mem::{size_of,zeroed},ptr::{null,null_mut},sync::atomic::Ordering,thread,time::{Duration,Instant}};
use windows_sys::Win32::{Foundation::FILETIME,
    System::{Threading::{GetCurrentProcess,GetProcessTimes},SystemInformation::GetTickCount},
    UI::{Input::{GetRawInputData,RegisterRawInputDevices,RAWINPUTDEVICE,RAWINPUTHEADER,RID_HEADER,RIDEV_INPUTSINK,RIDEV_REMOVE,
                 KeyboardAndMouse::{RegisterHotKey,UnregisterHotKey,MOD_NOREPEAT,VK_F8},
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

pub fn watch(shared:SharedState) {
    thread::spawn(move||unsafe {
        let class=wide("STATIC");let title=wide("CurrencyWarsPassiveInput");
        let window=CreateWindowExW(0,class.as_ptr(),title.as_ptr(),0,0,0,0,0,HWND_MESSAGE,null_mut(),null_mut(),null());
        let f8=RegisterHotKey(null_mut(),0x4358,MOD_NOREPEAT,VK_F8 as u32)!=0;
        let devices=[RAWINPUTDEVICE{usUsagePage:1,usUsage:2,dwFlags:RIDEV_INPUTSINK,hwndTarget:window},RAWINPUTDEVICE{usUsagePage:1,usUsage:6,dwFlags:RIDEV_INPUTSINK,hwndTarget:window}];
        let raw=!window.is_null()&&RegisterRawInputDevices(devices.as_ptr(),2,size_of::<RAWINPUTDEVICE>() as u32)!=0;
        {let mut session=shared.session.lock().unwrap();session.hardware=json!({"f8":f8,"raw_input":raw,"test_mode":false});session.log(if f8{"F8全局接管已启用。"}else{"F8注册失败，请使用手动接管按钮。"});session.log(if raw{"真实鼠标/键盘被动监听已启用。"}else{"真实鼠标/键盘监听未确认，请使用接管按钮。"});}
        let mut last=Instant::now()-Duration::from_secs(1);
        let mut pad_at=Instant::now()-Duration::from_secs(1);
        while !shared.closed.load(Ordering::SeqCst) {
            let mut message:MSG=zeroed();
            while PeekMessageW(&mut message,null_mut(),0,0,PM_REMOVE)!=0 {
                let mut trigger=message.message==WM_HOTKEY&&message.wParam==0x4358;
                if raw&&message.message==WM_INPUT {
                    let mut header:RAWINPUTHEADER=zeroed();let mut length=size_of::<RAWINPUTHEADER>() as u32;
                    let result=GetRawInputData(message.lParam as _,RID_HEADER,&mut header as *mut _ as _,&mut length,size_of::<RAWINPUTHEADER>() as u32);
                    // Ignore raw events already queued before the explicit
                    // Start/Resume click, while keeping later hardware input safe.
                    let eligible={let session=shared.session.lock().unwrap();(session.binding.is_some()&&!session.manual||session.pending.contains_key("resume"))&&(message.time.wrapping_sub(session.input_guard_tick) as i32)>0};
                    trigger|=result!=u32::MAX&&!header.hDevice.is_null()&&header.dwType<=1&&eligible;
                }
                if trigger&&last.elapsed()>Duration::from_millis(300) {
                    last=Instant::now();let cloned=shared.clone();
                    thread::spawn(move||{let _=control(&cloned,"takeover","F8或真实鼠标/键盘输入，保持手动");});
                }
                TranslateMessage(&message);DispatchMessageW(&message);
            }
            if pad_at.elapsed()>Duration::from_millis(120) {
                pad_at=Instant::now();let mut connected=0;let mut active=false;let mut errors=vec![];
                for index in 0..4 {let mut pad:XINPUT_STATE=zeroed();let code=XInputGetState(index,&mut pad);if code==0 {connected+=1;let p=pad.Gamepad;active|=p.wButtons!=0||p.bLeftTrigger>30||p.bRightTrigger>30||i32::from(p.sThumbLX).abs()>7849||i32::from(p.sThumbLY).abs()>7849||i32::from(p.sThumbRX).abs()>8689||i32::from(p.sThumbRY).abs()>8689;}else if code!=1167{errors.push(code);}}
                let take={let mut session=shared.session.lock().unwrap();session.gamepad=json!({"available":true,"active":active,"connected":connected,"errors":errors});(active||!errors.is_empty())&&(session.binding.is_some()&&!session.manual||session.pending.contains_key("resume"))};
                if take&&last.elapsed()>Duration::from_millis(300) {last=Instant::now();let cloned=shared.clone();thread::spawn(move||{let _=control(&cloned,"takeover","手柄输入或监测错误，保持手动接管");});}
            }
            thread::sleep(Duration::from_millis(20));
        }
        if raw {let removed=[RAWINPUTDEVICE{usUsagePage:1,usUsage:2,dwFlags:RIDEV_REMOVE,hwndTarget:null_mut()},RAWINPUTDEVICE{usUsagePage:1,usUsage:6,dwFlags:RIDEV_REMOVE,hwndTarget:null_mut()}];RegisterRawInputDevices(removed.as_ptr(),2,size_of::<RAWINPUTDEVICE>() as u32);}
        if f8 {UnregisterHotKey(null_mut(),0x4358);}
        if !window.is_null(){DestroyWindow(window);}
    });
}
