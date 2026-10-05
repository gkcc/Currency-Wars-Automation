use super::{apply_floating_options, FloatingWindow};
use serde_json::{json, Value};
use std::{
    mem::{size_of, zeroed},
    ptr::null_mut,
    sync::{
        atomic::{AtomicBool, AtomicU32, AtomicU64, Ordering},
        mpsc, Arc, Mutex,
    },
    thread,
    time::{Duration, Instant},
};
use windows_sys::Win32::{
    Foundation::{GetLastError, SetLastError},
    System::{
        SystemInformation::{GetTickCount, OSVERSIONINFOW},
        Threading::GetCurrentThreadId,
    },
    UI::{
        Input::KeyboardAndMouse::{
            GetAsyncKeyState, RegisterHotKey, UnregisterHotKey, MOD_ALT, MOD_CONTROL, VK_CONTROL, VK_MENU,
            MOD_NOREPEAT, MOD_SHIFT, MOD_WIN, VK_F9,
        },
        WindowsAndMessaging::{
            GetAncestor, GetLayeredWindowAttributes, GetWindowDisplayAffinity,
            GetWindowLongPtrW, GetWindowThreadProcessId, PeekMessageW,
            SetLayeredWindowAttributes, SetWindowDisplayAffinity, SetWindowLongPtrW,
            SetWindowPos, GA_ROOT, GWL_EXSTYLE, LWA_ALPHA, LWA_COLORKEY, MSG, PM_REMOVE,
            SWP_FRAMECHANGED, SWP_NOACTIVATE, SWP_NOMOVE, SWP_NOSIZE, SWP_NOZORDER,
            WDA_EXCLUDEFROMCAPTURE, WDA_NONE, WM_HOTKEY, WS_EX_LAYERED, WS_EX_TRANSPARENT,
        },
    },
};

// The existing Windows features expose OSVERSIONINFOW; no new Cargo feature is
// needed for the ntdll version query, which avoids compatibility-manifest lies.
#[link(name = "ntdll")]
extern "system" {
    fn RtlGetVersion(version: *mut OSVERSIONINFOW) -> i32;
}

fn failure(label: &str) -> String {
    format!("{label}（Windows 错误 {}）", unsafe { GetLastError() })
}

pub struct Appearance {
    hwnd: usize,
    window_thread: u32,
    style: isize,
    layered: Option<(u32, u8, u32)>,
    original_affinity: u32,
    observed_affinity: AtomicU32,
    capture_verified: AtomicBool,
}

impl Appearance {
    fn owned(&self) -> Result<(), String> {
        let mut pid = 0;
        let window_thread = unsafe { GetWindowThreadProcessId(self.hwnd as _, &mut pid) };
        if window_thread == 0 || pid != std::process::id() || window_thread != self.window_thread {
            return Err("浮窗句柄已失效，未改变其他窗口".into());
        }
        if unsafe { GetCurrentThreadId() } != window_thread {
            return Err("浮窗样式操作必须在窗口主线程执行".into());
        }
        if unsafe { GetAncestor(self.hwnd as _, GA_ROOT) } != self.hwnd as _ {
            return Err("截图排除仅可用于本进程的顶层浮窗".into());
        }
        Ok(())
    }

    fn style(&self) -> Result<isize, String> {
        self.owned()?;
        unsafe {
            SetLastError(0);
            let value = GetWindowLongPtrW(self.hwnd as _, GWL_EXSTYLE);
            if value == 0 && GetLastError() != 0 {
                return Err(failure("无法读取浮窗样式"));
            }
            Ok(value)
        }
    }

    fn set_style(&self, value: isize) -> Result<(), String> {
        self.owned()?;
        unsafe {
            SetLastError(0);
            if SetWindowLongPtrW(self.hwnd as _, GWL_EXSTYLE, value) == 0 && GetLastError() != 0 {
                return Err(failure("无法设置浮窗样式"));
            }
        }
        if self.style()? != value {
            return Err("浮窗样式未通过回读核验".into());
        }
        Ok(())
    }

    fn refresh(&self) -> Result<(), String> {
        self.owned()?;
        unsafe {
            SetLastError(0);
            if SetWindowPos(
                self.hwnd as _, null_mut(), 0, 0, 0, 0,
                SWP_FRAMECHANGED | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE,
            ) == 0 {
                return Err(failure("无法刷新浮窗样式"));
            }
        }
        Ok(())
    }

    fn layered_attributes(&self) -> Result<(u32, u8, u32), String> {
        self.owned()?;
        let (mut color, mut alpha, mut flags) = (0, 0, 0);
        unsafe {
            SetLastError(0);
            if GetLayeredWindowAttributes(self.hwnd as _, &mut color, &mut alpha, &mut flags) == 0 {
                return Err(failure("无法读取浮窗透明属性"));
            }
        }
        Ok((color, alpha, flags))
    }

    fn set_layered(&self, color: u32, alpha: u8, flags: u32) -> Result<(), String> {
        self.owned()?;
        unsafe {
            SetLastError(0);
            if SetLayeredWindowAttributes(self.hwnd as _, color, alpha, flags) == 0 {
                return Err(failure("无法设置浮窗透明属性"));
            }
        }
        let (observed_color, observed_alpha, observed_flags) = self.layered_attributes()?;
        if observed_flags != flags
            || (flags & LWA_ALPHA != 0 && observed_alpha != alpha)
            || (flags & LWA_COLORKEY != 0 && observed_color != color)
        {
            return Err("浮窗透明属性未通过回读核验".into());
        }
        Ok(())
    }

    fn affinity(&self) -> Result<u32, String> {
        self.owned()?;
        let mut value = WDA_NONE;
        unsafe {
            SetLastError(0);
            if GetWindowDisplayAffinity(self.hwnd as _, &mut value) == 0 {
                return Err(failure("无法回读浮窗截图排除设置（需 DWM 桌面合成）"));
            }
        }
        self.observed_affinity.store(value, Ordering::SeqCst);
        Ok(value)
    }

    fn set_affinity(&self, expected: u32) -> Result<(), String> {
        self.owned()?;
        unsafe {
            SetLastError(0);
            if SetWindowDisplayAffinity(self.hwnd as _, expected) == 0 {
                return Err(failure("无法设置浮窗截图排除"));
            }
        }
        let actual = self.affinity()?;
        if actual != expected {
            return Err(format!("浮窗截图排除回读不一致（期望 0x{expected:02X}，实际 0x{actual:02X}）"));
        }
        Ok(())
    }

    pub fn capture(window: &tauri::WebviewWindow) -> Result<Self, String> {
        let hwnd = window.hwnd().map_err(|error| error.to_string())?.0 as usize;
        let mut pid = 0;
        let window_thread = unsafe { GetWindowThreadProcessId(hwnd as _, &mut pid) };
        let mut value = Self {
            hwnd, window_thread, style: 0, layered: None, original_affinity: WDA_NONE,
            observed_affinity: AtomicU32::new(WDA_NONE), capture_verified: AtomicBool::new(false),
        };
        value.style = value.style()?;
        if value.style & WS_EX_LAYERED as isize != 0 {
            value.layered = Some(value.layered_attributes()?);
            value.original_affinity = value.affinity()?;
        } else {
            // GetWindowDisplayAffinity requires a layered window. Save the
            // baseline before setting exclusion, then restore our temporary bit.
            let captured = (|| {
                value.set_style(value.style | WS_EX_LAYERED as isize)?;
                value.set_layered(0, 255, LWA_ALPHA)?;
                value.affinity()
            })();
            let restored = value.set_style(value.style).and_then(|_| value.refresh());
            value.original_affinity = match (captured, restored) {
                (Ok(affinity), Ok(())) => affinity,
                (Err(error), Ok(())) => return Err(error),
                (Ok(_), Err(error)) => return Err(error),
                (Err(error), Err(restore)) => return Err(format!("{error}；原窗口样式恢复失败：{restore}")),
            };
        }
        Ok(value)
    }

    pub fn apply(&self, opacity: u8, through: bool) -> Result<(), String> {
        // Recovery must remain possible even when a later capture check fails.
        self.capture_verified.store(false, Ordering::SeqCst);
        self.set_style((self.style()? | WS_EX_LAYERED as isize) & !(WS_EX_TRANSPARENT as isize))?;
        self.refresh()?;
        if !(40..=100).contains(&opacity) {
            return Err("透明度需要为 40%～100%".into());
        }
        let alpha = ((u16::from(opacity) * 255 + 50) / 100) as u8;
        self.set_layered(0, alpha, LWA_ALPHA)?;
        let mut version: OSVERSIONINFOW = unsafe { zeroed() };
        version.dwOSVersionInfoSize = size_of::<OSVERSIONINFOW>() as u32;
        let status = unsafe { RtlGetVersion(&mut version) };
        if status < 0 {
            return Err(format!("无法核验截图排除的 Windows 版本（NTSTATUS 0x{:08X}）", status as u32));
        }
        if version.dwMajorVersion < 10 || (version.dwMajorVersion == 10 && version.dwBuildNumber < 19041) {
            return Err(format!("浮窗截图排除需要 Windows 10 2004 或更高版本（实际 {}.{}，内部版本 {}）", version.dwMajorVersion, version.dwMinorVersion, version.dwBuildNumber));
        }
        self.set_affinity(WDA_EXCLUDEFROMCAPTURE)?;
        self.capture_verified.store(true, Ordering::SeqCst);
        if through {
            self.set_style(self.style()? | WS_EX_TRANSPARENT as isize)?;
        }
        self.refresh()
    }

    pub fn restore(&self) -> Result<(), String> {
        self.capture_verified.store(false, Ordering::SeqCst);
        let mask = (WS_EX_LAYERED | WS_EX_TRANSPARENT) as isize;
        self.set_style((self.style()? | WS_EX_LAYERED as isize) & !(WS_EX_TRANSPARENT as isize))?;
        self.refresh()?;
        // Continue style recovery even if restoring affinity or alpha fails.
        let mut errors = Vec::new();
        if let Err(error) = self.set_affinity(self.original_affinity) { errors.push(error); }
        if let Some((color, alpha, flags)) = self.layered {
            if let Err(error) = self.set_layered(color, alpha, flags) { errors.push(error); }
        }
        let restored = self.style().and_then(|current| self.set_style((current & !mask) | (self.style & mask))).and_then(|_| self.refresh());
        if let Err(error) = restored { errors.push(error); }
        if errors.is_empty() { Ok(()) } else { Err(errors.join("；")) }
    }

    pub fn click_through(&self) -> Result<bool, String> {
        Ok(self.style()? & WS_EX_TRANSPARENT as isize != 0)
    }

    // Status reads use the last main-thread readback, not cross-thread WinAPI.
    pub fn capture_info(&self) -> Value {
        json!({
            "excluded": self.capture_verified.load(Ordering::SeqCst),
            "display_affinity": self.observed_affinity.load(Ordering::SeqCst),
            "verification": "SetWindowDisplayAffinity/GetWindowDisplayAffinity",
        })
    }
}

const F9_ID: i32 = 0x4359;
const FALLBACK_ID: i32 = 0x435A;
const FALLBACK_KEY: u32 = 0x4F;
const MODIFIERS: u32 = MOD_ALT | MOD_CONTROL | MOD_SHIFT | MOD_WIN;

#[derive(Default)]
struct ShortcutStatus {
    f9_registered: bool,
    fallback_registered: bool,
    errors: Vec<String>,
}

struct HotkeyShared {
    stop: AtomicBool,
    alive: AtomicBool,
    status: Mutex<ShortcutStatus>,
    f9_presses: AtomicU64,
    fallback_presses: AtomicU64,
    toggles: AtomicU64,
    thread_id: AtomicU32,
}

struct Registrations {
    f9: bool,
    fallback: bool,
    shared: Arc<HotkeyShared>,
}

impl Drop for Registrations {
    fn drop(&mut self) {
        self.shared.alive.store(false, Ordering::SeqCst);
        for (registered, id, label) in [(self.f9, F9_ID, "F9"), (self.fallback, FALLBACK_ID, "Ctrl+Alt+O")] {
            if registered && unsafe { UnregisterHotKey(null_mut(), id) } == 0 {
                let error = failure(&format!("{label} 注销失败"));
                if let Ok(mut status) = self.shared.status.lock() {
                    status.errors.push(error);
                }
            }
        }
        if let Ok(mut status) = self.shared.status.lock() {
            status.f9_registered = false;
            status.fallback_registered = false;
        }
    }
}

fn queue_toggle(window: &tauri::WebviewWindow, state: &Arc<Mutex<FloatingWindow>>, generation: u64, shared: &Arc<HotkeyShared>, f9: bool) {
    let target = state.clone();
    let online = shared.clone();
    if let Err(error) = window.run_on_main_thread(move || {
        if !online.alive.load(Ordering::SeqCst) || online.stop.load(Ordering::SeqCst) { return; }
        if let Ok(mut model) = target.lock() {
            if model.full.is_some() && model.generation == generation {
                if f9 { online.f9_presses.fetch_add(1, Ordering::SeqCst); }
                else { online.fallback_presses.fetch_add(1, Ordering::SeqCst); }
                let opacity = model.opacity;
                let enabled = !model.click_through;
                if apply_floating_options(&mut model, opacity, enabled).is_ok() {
                    online.toggles.fetch_add(1, Ordering::SeqCst);
                }
            }
        }
    }) {
        shared.stop.store(true, Ordering::SeqCst);
        if let Ok(mut status) = shared.status.lock() { status.errors.push(format!("浮窗主线程调度失败：{error}")); }
    }
}

pub struct Hotkey {
    shared: Arc<HotkeyShared>,
    thread: Option<thread::JoinHandle<()>>,
}

impl Hotkey {
    pub fn ready(&self) -> bool {
        self.shared.alive.load(Ordering::SeqCst) && !self.shared.stop.load(Ordering::SeqCst)
    }

    pub fn info(&self) -> Value {
        let status = self.shared.status.lock().unwrap_or_else(|error| error.into_inner());
        json!({
            "ready": self.ready(), "thread_id": self.shared.thread_id.load(Ordering::SeqCst), "registration_owner": "thread_queue", "f9_hotkey_id": F9_ID, "fallback_hotkey_id": FALLBACK_ID, "f9_registered": status.f9_registered,
            "f9_polling": self.ready() && status.f9_registered, "fallback_registered": status.fallback_registered,
            "fallback_shortcut": "Ctrl+Alt+O", "errors": status.errors,
            "f9_presses": self.shared.f9_presses.load(Ordering::SeqCst),
            "fallback_presses": self.shared.fallback_presses.load(Ordering::SeqCst),
            "toggles": self.shared.toggles.load(Ordering::SeqCst),
        })
    }

    pub fn start(window: &tauri::WebviewWindow, state: Arc<Mutex<FloatingWindow>>, generation: u64) -> Result<Self, String> {
        let shared = Arc::new(HotkeyShared {
            stop: AtomicBool::new(false), alive: AtomicBool::new(false), status: Mutex::new(ShortcutStatus::default()),
            f9_presses: AtomicU64::new(0), fallback_presses: AtomicU64::new(0), toggles: AtomicU64::new(0), thread_id: AtomicU32::new(0),
        });
        let online = shared.clone();
        let (sent, received) = mpsc::sync_channel(1);
        let window = window.clone();
        let handle = thread::spawn(move || {
            online.thread_id.store(unsafe { GetCurrentThreadId() }, Ordering::SeqCst);
            let mut message: MSG = unsafe { zeroed() };
            unsafe { PeekMessageW(&mut message, null_mut(), 0, 0, 0); }
            let f9 = unsafe { RegisterHotKey(null_mut(), F9_ID, MOD_NOREPEAT, VK_F9 as u32) } != 0;
            let f9_error = if f9 { None } else { Some(failure("F9 注册失败")) };
            let fallback = unsafe { RegisterHotKey(null_mut(), FALLBACK_ID, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, FALLBACK_KEY) } != 0;
            let fallback_error = if fallback { None } else { Some(failure("Ctrl+Alt+O 注册失败")) };
            let _registrations = Registrations { f9, fallback, shared: online.clone() };
            {
                let mut status = online.status.lock().unwrap_or_else(|error| error.into_inner());
                status.f9_registered = f9;
                status.fallback_registered = fallback;
                status.errors.extend(f9_error);
                status.errors.extend(fallback_error);
            }
            let registered = f9 || fallback;
            online.alive.store(registered, Ordering::SeqCst);
            let started = if registered { Ok(()) } else {
                let status = online.status.lock().unwrap_or_else(|error| error.into_inner());
                Err(format!("没有可用的全局恢复快捷键，鼠标穿透不可用：{}", status.errors.join("；")))
            };
            let _ = sent.send(started);
            let initial_down = unsafe { GetAsyncKeyState(VK_F9 as i32) } < 0;
            let mut press = crate::input::PressEdge::new(initial_down, unsafe { GetTickCount() });
            let mut backup_press = crate::input::PressEdge::new(false, unsafe { GetTickCount() });
            while registered && !online.stop.load(Ordering::SeqCst) {
                // Poll only the registered shortcuts; WM_HOTKEY retains short taps.
                let down = f9 && unsafe { GetAsyncKeyState(VK_F9 as i32) } < 0;
                let backup_down = fallback && unsafe { GetAsyncKeyState(FALLBACK_KEY as i32) < 0 && GetAsyncKeyState(VK_CONTROL as i32) < 0 && GetAsyncKeyState(VK_MENU as i32) < 0 };
                let tick = unsafe { GetTickCount() };
                if press.poll(down, tick) { queue_toggle(&window, &state, generation, &online, true); }
                if backup_press.poll(backup_down, tick) { queue_toggle(&window, &state, generation, &online, false); }
                for _ in 0..32 {
                    if unsafe { PeekMessageW(&mut message, null_mut(), 0, 0, PM_REMOVE) } == 0 { break; }
                    if message.message != WM_HOTKEY { continue; }
                    let key = ((message.lParam as usize >> 16) & 0xFFFF) as u32;
                    let modifiers = message.lParam as u32 & MODIFIERS;
                    if f9 && message.wParam == F9_ID as usize && key == VK_F9 as u32 && modifiers == 0 {
                        if press.message(message.time) { queue_toggle(&window, &state, generation, &online, true); }
                    } else if fallback && message.wParam == FALLBACK_ID as usize && key == FALLBACK_KEY && modifiers == (MOD_CONTROL | MOD_ALT) {
                        if backup_press.message(message.time) { queue_toggle(&window, &state, generation, &online, false); }
                    }
                }
                thread::sleep(Duration::from_millis(15));
            }
        });
        let mut value = Self { shared, thread: Some(handle) };
        match received.recv_timeout(Duration::from_millis(500)) {
            Ok(Ok(())) => Ok(value),
            Ok(Err(error)) => { let _ = value.shutdown(); Err(error) }
            Err(_) => { let _ = value.shutdown(); Err("浮窗恢复快捷键未在时限内确认，鼠标穿透不可用".into()) }
        }
    }

    pub fn shutdown(&mut self) -> Result<(), String> {
        self.shared.stop.store(true, Ordering::SeqCst);
        let deadline = Instant::now() + Duration::from_millis(500);
        if let Some(handle) = self.thread.as_ref() {
            while !handle.is_finished() && Instant::now() < deadline { thread::sleep(Duration::from_millis(5)); }
            if !handle.is_finished() { return Err("浮窗快捷键线程尚未退出，请重试返回完整界面".into()); }
        }
        if let Some(handle) = self.thread.take() { handle.join().map_err(|_| "浮窗快捷键线程退出未确认".to_string())?; }
        Ok(())
    }
}

impl Drop for Hotkey {
    fn drop(&mut self) { self.shared.stop.store(true, Ordering::SeqCst); }
}
