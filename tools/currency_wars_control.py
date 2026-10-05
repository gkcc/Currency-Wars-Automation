"""One-window, bounded Star Rail broker with latched pause and explicit resume."""
import argparse, contextlib, ctypes as C, json, math, os, secrets, shutil
import subprocess, sys, tempfile, time, uuid
from ctypes import wintypes as W
from datetime import datetime, timezone
from pathlib import Path
from PIL import ImageGrab

u = C.WinDLL('user32', use_last_error=True)
k = C.WinDLL('kernel32', use_last_error=True)
a = C.WinDLL('advapi32', use_last_error=True)
u.SetProcessDpiAwarenessContext(C.c_void_p(-4))
u.GetForegroundWindow.restype = W.HWND
u.GetWindowTextW.argtypes = [W.HWND, W.LPWSTR, C.c_int]
u.GetClientRect.argtypes = [W.HWND, C.POINTER(W.RECT)]
u.ClientToScreen.argtypes = [W.HWND, C.POINTER(W.POINT)]
u.GetWindowThreadProcessId.argtypes = [W.HWND, C.POINTER(W.DWORD)]
u.SetForegroundWindow.argtypes = [W.HWND]
u.IsWindowVisible.argtypes = [W.HWND]
u.GetCursorPos.argtypes = [C.POINTER(W.POINT)]
u.MapVirtualKeyW.argtypes = [W.UINT, W.UINT]
k.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
k.OpenProcess.restype = W.HANDLE
k.CloseHandle.argtypes = [W.HANDLE]
k.GetExitCodeProcess.argtypes = [W.HANDLE, C.POINTER(W.DWORD)]
k.GetProcessTimes.argtypes = [W.HANDLE] + [C.POINTER(W.FILETIME)] * 4
k.CreateMutexW.argtypes = [W.LPVOID, W.BOOL, W.LPCWSTR]
k.CreateMutexW.restype = W.HANDLE
k.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]
k.ReleaseMutex.argtypes = [W.HANDLE]
a.OpenProcessToken.argtypes = [W.HANDLE, W.DWORD, C.POINTER(W.HANDLE)]
a.GetTokenInformation.argtypes = [W.HANDLE, C.c_int, W.LPVOID, W.DWORD, C.POINTER(W.DWORD)]
a.GetSidSubAuthorityCount.argtypes = [W.LPVOID]
a.GetSidSubAuthorityCount.restype = C.POINTER(C.c_ubyte)
a.GetSidSubAuthority.argtypes = [W.LPVOID, W.DWORD]
a.GetSidSubAuthority.restype = C.POINTER(W.DWORD)

class LABEL(C.Structure):
    _fields_ = [('Sid', W.LPVOID), ('Attributes', W.DWORD)]
class MI(C.Structure):
    _fields_ = [('dx', W.LONG), ('dy', W.LONG), ('mouseData', W.DWORD), ('dwFlags', W.DWORD), ('time', W.DWORD), ('dwExtraInfo', C.c_size_t)]
class KI(C.Structure):
    _fields_ = [('wVk', W.WORD), ('wScan', W.WORD), ('dwFlags', W.DWORD), ('time', W.DWORD), ('dwExtraInfo', C.c_size_t)]
class II(C.Union):
    _fields_ = [('mi', MI), ('ki', KI)]
class INPUT(C.Structure):
    _fields_ = [('type', W.DWORD), ('value', II)]
class GUI_INFO(C.Structure):
    _fields_ = [('cbSize', W.DWORD), ('flags', W.DWORD), ('hwndActive', W.HWND), ('hwndFocus', W.HWND), ('hwndCapture', W.HWND), ('hwndMenuOwner', W.HWND), ('hwndMoveSize', W.HWND), ('hwndCaret', W.HWND), ('rcCaret', W.RECT)]
u.SendInput.argtypes = [W.UINT, C.POINTER(INPUT), C.c_int]
u.SendInput.restype = W.UINT

ROOT = None
OWNER = None
BINDING = None
BATCH_DEADLINE = None
BROKER_IDENTITY = None
RESUMING = False
RESUME_PAUSE_ID = None
OWNED_HELD = 0
PROTOCOL_VERSION = 2
SEEN_RESUME_IDS = set()

class GAMEPAD(C.Structure):
    _fields_ = [('buttons', W.WORD), ('left_trigger', C.c_ubyte), ('right_trigger', C.c_ubyte), ('lx', C.c_short), ('ly', C.c_short), ('rx', C.c_short), ('ry', C.c_short)]
class PAD_STATE(C.Structure):
    _fields_ = [('packet', W.DWORD), ('pad', GAMEPAD)]

XINPUT = None
for _dll in ('xinput1_4', 'xinput9_1_0'):
    try:
        XINPUT = C.WinDLL(_dll)
        XINPUT.XInputGetState.argtypes = [W.DWORD, C.POINTER(PAD_STATE)]
        XINPUT.XInputGetState.restype = W.DWORD
        break
    except OSError:
        pass

class StopUnverified(RuntimeError):
    pass

def utc_now():
    return datetime.now(timezone.utc).isoformat()

def read_optional(name):
    try:
        return json.loads(Path(ROOT, name).read_text(encoding='utf8'))
    except FileNotFoundError:
        return None

def gamepad_activity():
    if XINPUT is None:
        return {'available': False, 'active': False, 'connected': 0, 'errors': []}
    connected, active, errors = 0, False, []
    for index in range(4):
        state = PAD_STATE()
        code = XINPUT.XInputGetState(index, C.byref(state))
        if code == 1167:
            continue
        if code:
            errors.append({'index': index, 'code': int(code)})
            continue
        connected += 1
        pad = state.pad
        active |= bool(pad.buttons or pad.left_trigger > 30 or pad.right_trigger > 30 or abs(pad.lx) > 7849 or abs(pad.ly) > 7849 or abs(pad.rx) > 8689 or abs(pad.ry) > 8689)
    return {'available': True, 'active': active, 'connected': connected, 'errors': errors}

@contextlib.contextmanager
def control_state_lock():
    path = Path(ROOT, 'control-state.lock')
    end = time.monotonic() + .5
    while True:
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            if time.monotonic() >= end:
                raise RuntimeError('control state busy; no state change')
            time.sleep(.01)
    try:
        os.close(descriptor)
        yield
    finally:
        path.unlink(missing_ok=True)

def latch_pause(reason):
    if not isinstance(reason, str) or not 1 <= len(reason) <= 200:
        raise ValueError('pause reason must contain 1..200 characters')
    value = {'pause_id': uuid.uuid4().hex, 'reason': reason, 'time': utc_now(), 'chat_id': OWNER['chat_id'], 'run_token': OWNER['run_token']}
    with control_state_lock():
        write_json(Path(ROOT, 'manual-pause.json'), value)
    return value

def acknowledge_pause():
    pause = read_optional('manual-pause.json')
    if pause is None or BROKER_IDENTITY is None or OWNED_HELD:
        return
    assert_owner(pause.get('chat_id'), pause.get('run_token'))
    previous = read_optional('pause-ack.json')
    if previous and previous.get('pause_id') == pause['pause_id']:
        return
    write_json(Path(ROOT, 'pause-ack.json'), {'pause_id': pause['pause_id'], 'acknowledged': True, 'time': utc_now(), 'broker_pid': BROKER_IDENTITY['pid'], 'broker_creation_time': BROKER_IDENTITY['creation_id'], 'chat_id': OWNER['chat_id'], 'run_token': OWNER['run_token'], 'owned_inputs_released': True})

def input_guard():
    if BATCH_DEADLINE is not None and time.monotonic() >= BATCH_DEADLINE:
        raise RuntimeError('batch deadline reached; input stopped')
    if ROOT is None:
        return
    if Path(ROOT, 'broker-stop').exists():
        raise RuntimeError('stop requested')
    pad = gamepad_activity()
    if pad['active'] or pad['errors']:
        latch_pause('gamepad activity' if pad['active'] else 'gamepad state unverified')
    pause = read_optional('manual-pause.json')
    if pause:
        assert_owner(pause.get('chat_id'), pause.get('run_token'))
    pause_id = pause.get('pause_id') if pause else None
    if RESUMING:
        if pause_id != RESUME_PAUSE_ID:
            acknowledge_pause()
            raise RuntimeError('new manual pause during resume; locks preserved')
    elif pause:
        acknowledge_pause()
        raise RuntimeError('manual pause latched: ' + pause['reason'])

def write_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf8')
        # A Windows reader can briefly deny replacement of the previous result.
        deadline = time.monotonic() + .75
        while True:
            try:
                os.replace(temporary, path)
                break
            except OSError as exc:
                if getattr(exc, 'winerror', None) not in (5, 32) or time.monotonic() >= deadline:
                    raise
                time.sleep(min(.025, max(0, deadline - time.monotonic())))
    finally:
        temporary.unlink(missing_ok=True)

def process_probe(pid, expected_creation=None):
    C.set_last_error(0)
    handle = k.OpenProcess(0x1000, False, int(pid))
    if not handle:
        error = C.get_last_error()
        return {'pid': int(pid), 'state': 'absent' if error == 87 else 'unknown', 'error': error}
    try:
        created, exited, kernel, user = (W.FILETIME() for _ in range(4))
        if not k.GetProcessTimes(handle, C.byref(created), C.byref(exited), C.byref(kernel), C.byref(user)):
            return {'pid': int(pid), 'state': 'unknown', 'error': C.get_last_error()}
        identity = (created.dwHighDateTime << 32) | created.dwLowDateTime
        if expected_creation is not None and identity != int(expected_creation):
            return {'pid': int(pid), 'state': 'reused', 'creation_id': identity}
        code = W.DWORD()
        if not k.GetExitCodeProcess(handle, C.byref(code)):
            return {'pid': int(pid), 'state': 'unknown', 'creation_id': identity, 'error': C.get_last_error()}
        return {'pid': int(pid), 'state': 'running' if code.value == 259 else 'exited', 'creation_id': identity, 'exit_code': code.value}
    finally:
        k.CloseHandle(handle)

def integrity(pid):
    handle = k.OpenProcess(0x1000, False, int(pid))
    if not handle:
        return {'pid': int(pid), 'error': C.get_last_error()}
    token = W.HANDLE()
    try:
        if not a.OpenProcessToken(handle, 8, C.byref(token)):
            return {'pid': int(pid), 'error': C.get_last_error()}
        size = W.DWORD()
        a.GetTokenInformation(token, 25, None, 0, C.byref(size))
        data = C.create_string_buffer(size.value)
        if not a.GetTokenInformation(token, 25, data, size, C.byref(size)):
            return {'pid': int(pid), 'error': C.get_last_error()}
        sid = C.cast(data, C.POINTER(LABEL)).contents.Sid
        count = a.GetSidSubAuthorityCount(sid)[0]
        return {'pid': int(pid), 'integrity_rid': a.GetSidSubAuthority(sid, count - 1)[0]}
    finally:
        if token:
            k.CloseHandle(token)
        k.CloseHandle(handle)

def win():
    matches = []
    @C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
    def enum(hwnd, unused):
        if not u.IsWindowVisible(hwnd):
            return True
        title = C.create_unicode_buffer(256)
        u.GetWindowTextW(hwnd, title, 256)
        rect = W.RECT()
        u.GetClientRect(hwnd, C.byref(rect))
        if title.value == '崩坏：星穹铁道' and rect.right >= 640 and rect.bottom >= 360:
            matches.append(hwnd)
        return True
    u.EnumWindows(enum, 0)
    if len(matches) != 1:
        raise RuntimeError('visible game window count: ' + str(len(matches)))
    hwnd = matches[0]
    pid = W.DWORD()
    u.GetWindowThreadProcessId(hwnd, C.byref(pid))
    rect, origin = W.RECT(), W.POINT(0, 0)
    u.GetClientRect(hwnd, C.byref(rect))
    u.ClientToScreen(hwnd, C.byref(origin))
    if BINDING is not None:
        state = process_probe(pid.value, BINDING['creation_id'])
        if int(hwnd) != BINDING['hwnd'] or pid.value != BINDING['pid'] or state['state'] != 'running':
            raise RuntimeError('game process/window identity changed or unverified')
    return hwnd, pid.value, (origin.x, origin.y, origin.x + rect.right, origin.y + rect.bottom)

def observe():
    hwnd, pid, rect = win()
    im = ImageGrab.grab(bbox=rect, all_screens=True)
    im.save(Path(ROOT, 'game-current.png'))
    im.resize((1920, 1080)).save(Path(ROOT, 'game-preview.png'))
    return {'hwnd': int(hwnd), 'pid': pid, 'rect': rect, 'foreground': int(u.GetForegroundWindow() or 0), 'snapshot': str(Path(ROOT, 'game-preview.png'))}

def point(x, y, rect):
    if not all(math.isfinite(v) for v in (x, y)) or not (0 <= x < 1920 and 0 <= y < 1080):
        raise ValueError('coordinates must be finite and within the 1920x1080 game client')
    xx = round(rect[0] + x / 1920 * (rect[2] - rect[0]))
    yy = round(rect[1] + y / 1080 * (rect[3] - rect[1]))
    return max(rect[0], min(rect[2] - 1, xx)), max(rect[1], min(rect[3] - 1, yy))

def focus():
    input_guard()
    hwnd, pid, rect = win()
    if u.GetForegroundWindow() != hwnd:
        raise RuntimeError('unexpected foreground loss; input stopped')
    return hwnd, pid, rect

def handoff_focus(hwnd):
    # Only an explicitly authorized initial handoff may activate another window.
    input_guard()
    original = u.GetForegroundWindow()
    before, after = W.POINT(), W.POINT()
    if not u.GetCursorPos(C.byref(before)):
        raise RuntimeError('cursor unavailable; handoff stopped')
    if any(u.GetAsyncKeyState(vk) & 0x8000 for vk in range(1, 256)):
        raise RuntimeError('held key or mouse button; handoff stopped')
    info = GUI_INFO()
    info.cbSize = C.sizeof(info)
    thread = u.GetWindowThreadProcessId(original, None)
    if not u.GetGUIThreadInfo(thread, C.byref(info)) or info.flags & 0x1c:
        raise RuntimeError('active menu or unverifiable foreground; handoff stopped')
    time.sleep(.25)
    input_guard()
    if not u.GetCursorPos(C.byref(after)) or u.GetForegroundWindow() != original:
        raise RuntimeError('foreground changed or cursor unavailable; handoff stopped')
    if abs(after.x - before.x) > 3 or abs(after.y - before.y) > 3 or any(u.GetAsyncKeyState(vk) & 0x8000 for vk in range(1, 256)):
        raise RuntimeError('external input activity; handoff stopped')
    input_guard()
    win()
    if original == hwnd:
        return
    try:
        send(INPUT(1, II(ki=KI(0x12, 0, 0, 0, 0))))
        u.SetForegroundWindow(hwnd)
    finally:
        send(INPUT(1, II(ki=KI(0x12, 0, 2, 0, 0))))
    time.sleep(.25)

def begin_batch(handoff):
    hwnd, pid, rect = win()
    if handoff:
        handoff_focus(hwnd)
    focus()
    return hwnd

def wait_guarded(seconds, deadline, hwnd):
    end = min(time.monotonic() + seconds, deadline)
    while time.monotonic() < end:
        input_guard()
        if u.GetForegroundWindow() != hwnd:
            raise RuntimeError('foreground lost during wait; input stopped')
        if Path(ROOT, 'broker-stop').exists():
            raise RuntimeError('stop requested')
        time.sleep(min(.05, max(0, end - time.monotonic())))
    input_guard()
    if u.GetForegroundWindow() != hwnd:
        raise RuntimeError('foreground lost during wait; input stopped')
    if time.monotonic() >= deadline:
        raise RuntimeError('batch deadline reached')

def send(value):
    global OWNED_HELD
    release = value.type == 1 and bool(value.value.ki.dwFlags & 2) or value.type == 0 and bool(value.value.mi.dwFlags & 4)
    down = value.type == 1 and not release or value.type == 0 and bool(value.value.mi.dwFlags & 2)
    if not release:
        input_guard()
    C.set_last_error(0)
    if u.SendInput(1, C.byref(value), C.sizeof(INPUT)) != 1:
        raise RuntimeError('SendInput blocked: ' + str(C.get_last_error()))
    if down:
        OWNED_HELD += 1
    elif release:
        OWNED_HELD = max(0, OWNED_HELD - 1)
        acknowledge_pause()

def mouse(flags):
    send(INPUT(0, II(mi=MI(0, 0, 0, flags, 0, 0))))

def move(x, y, rect):
    input_guard()
    xx, yy = point(x, y, rect)
    left, top, width, height = (u.GetSystemMetrics(index) for index in (76, 77, 78, 79))
    send(INPUT(0, II(mi=MI(round((xx - left) * 65535 / (width - 1)), round((yy - top) * 65535 / (height - 1)), 0, 0xC001, 0, 0))))
    time.sleep(.15)
    cursor = W.POINT()
    u.GetCursorPos(C.byref(cursor))
    if abs(cursor.x - xx) > 3 or abs(cursor.y - yy) > 3:
        raise RuntimeError('cursor contention: target ' + str([xx, yy]) + ' actual ' + str([cursor.x, cursor.y]))

def click(x, y):
    hwnd, pid, rect = focus()
    move(x, y, rect)
    focus()
    mouse(2)
    try:
        time.sleep(.09)
    finally:
        mouse(4)
    focus()

def key(vk):
    focus()
    scan = u.MapVirtualKeyW(vk, 0)
    send(INPUT(1, II(ki=KI(0, scan, 8, 0, 0))))
    try:
        time.sleep(.09)
    finally:
        send(INPUT(1, II(ki=KI(0, scan, 10, 0, 0))))
    focus()

def drag(x, y, to_x, to_y):
    hwnd, pid, rect = focus()
    move(x, y, rect)
    focus()
    mouse(2)
    try:
        time.sleep(.25)
        for index in range(1, 9):
            focus()
            move(x + (to_x - x) * index / 8, y + (to_y - y) * index / 8, rect)
    finally:
        mouse(4)
    focus()

def scroll(x, y, delta):
    hwnd, pid, rect = focus()
    move(x, y, rect)
    focus()
    send(INPUT(0, II(mi=MI(0, 0, int(delta) & 0xFFFFFFFF, 0x800, 0, 0))))
    focus()

def validate_actions(actions):
    if not isinstance(actions, list) or not 1 <= len(actions) <= 8:
        raise ValueError('a batch requires 1..8 actions')
    normalized, total_wait = [], 0.0
    arities = {'click': 2, 'drag': 4, 'scroll': 3, 'key': 1, 'wait': 1, 'observe': 0}
    for action in actions:
        kind, values = action.get('type'), action.get('args', [])
        if kind not in arities or len(values) != arities[kind]:
            raise ValueError('unsupported action or argument count')
        values = [float(value) for value in values]
        if not all(math.isfinite(value) for value in values):
            raise ValueError('non-finite action argument')
        if kind == 'wait':
            if not 0 <= values[0] <= 20:
                raise ValueError('wait must be within 0..20 seconds')
            total_wait += values[0]
        elif kind == 'key':
            if values[0] not in (27, 32, 68, 69, 70, 82):
                raise ValueError('key is outside the game whitelist')
            values[0] = int(values[0])
        elif kind != 'observe':
            for index in range(0, 4 if kind == 'drag' else 2, 2):
                point(values[index], values[index + 1], (0, 0, 1920, 1080))
            if kind == 'scroll' and (abs(values[2]) > 1200 or not values[2].is_integer()):
                raise ValueError('scroll must be an integer within -1200..1200')
        normalized.append({'type': kind, 'args': values})
    if total_wait > 20:
        raise ValueError('normalized total wait exceeds 20 seconds')
    return normalized

def mutex_name(binding):
    return 'Local\\CurrencyWarsControl-P' + str(binding['pid']) + '-T' + str(binding['creation_id']) + '-H' + str(binding['hwnd'])

def claim_start_mutex(binding):
    C.set_last_error(0)
    handle = k.CreateMutexW(None, True, mutex_name(binding))
    error = C.get_last_error()
    if not handle:
        raise RuntimeError('cannot create exclusive game controller mutex: ' + str(error))
    if error == 183:
        k.CloseHandle(handle)
        raise RuntimeError('another controller already owns this game process/window')
    return handle

def assert_owner(chat_id, run_token):
    if OWNER is None or chat_id != OWNER['chat_id'] or not secrets.compare_digest(str(run_token), OWNER['run_token']):
        raise ValueError('actual chat or run token ownership mismatch')

def load_run(path, chat_id, run_token):
    global ROOT, OWNER, BINDING
    root = Path(path).resolve()
    if root.parent != Path(tempfile.gettempdir()).resolve() or not root.name.startswith('currency-wars-control-') or root.is_symlink():
        raise ValueError('not an independently owned OS temp run directory')
    owner = json.loads((root / 'owner.json').read_text(encoding='utf8'))
    if owner.get('owner') != 'currency-wars-control' or owner.get('chat_id') != chat_id or not secrets.compare_digest(str(run_token), owner.get('run_token', '')):
        raise ValueError('actual chat or run token ownership mismatch')
    ROOT, OWNER = str(root), owner
    BINDING = json.loads((root / 'binding.json').read_text(encoding='utf8'))

def execute_batch(request):
    global BATCH_DEADLINE
    assert_owner(request.get('chat_id'), request.get('run_token'))
    actions = validate_actions(request.get('actions'))
    active = any(action['type'] != 'observe' for action in actions)
    if read_optional('manual-pause.json') is not None and active:
        acknowledge_pause()
        raise RuntimeError('manual pause latched; only explicit resume may unlock')
    if Path(ROOT, 'input-halted.json').exists() and active:
        raise RuntimeError('inputs halted; resolve contention and explicitly resume')
    deadline = time.monotonic() + 30
    BATCH_DEADLINE = deadline
    completed = []
    try:
        hwnd = begin_batch(bool(request.get('handoff'))) if active else None
        for action in actions:
            if time.monotonic() >= deadline or Path(ROOT, 'broker-stop').exists():
                raise RuntimeError('batch deadline reached or stop requested')
            kind, values = action['type'], action['args']
            if active:
                input_guard()
            if kind == 'wait':
                wait_guarded(values[0], deadline, hwnd)
            elif kind == 'observe':
                pass
            else:
                {'click': click, 'key': key, 'drag': drag, 'scroll': scroll}[kind](*values)
            completed.append(action)
            if active:
                wait_guarded(.3, deadline, hwnd)
        return {'id': request['id'], 'ok': True, 'completed': completed, 'observation': observe()}
    except Exception as exc:
        result = {'id': request['id'], 'ok': False, 'error': str(exc), 'completed': completed}
        write_json(Path(ROOT, 'input-halted.json'), result)
        try:
            result['observation'] = observe()
        except Exception:
            pass
        return result
    finally:
        BATCH_DEADLINE = None

def execute_resume(request):
    global BATCH_DEADLINE, RESUMING, RESUME_PAUSE_ID
    assert_owner(request.get('chat_id'), request.get('run_token'))
    if set(request) != {'id', 'chat_id', 'run_token', 'kind', 'handoff'} or request.get('kind') != 'resume' or request.get('handoff') is not True:
        raise ValueError('resume only accepts the controlled handoff; game actions are forbidden')
    if not isinstance(request['id'], str) or not 1 <= len(request['id']) <= 100:
        raise ValueError('resume id must contain 1..100 characters')
    if request['id'] in SEEN_RESUME_IDS or len(SEEN_RESUME_IDS) >= 128:
        raise ValueError('duplicate resume or resume limit reached; no handoff repeated')
    SEEN_RESUME_IDS.add(request['id'])
    pause = read_optional('manual-pause.json')
    if pause:
        assert_owner(pause.get('chat_id'), pause.get('run_token'))
    RESUME_PAUSE_ID = pause.get('pause_id') if pause else None
    RESUMING = True
    BATCH_DEADLINE = time.monotonic() + 5
    try:
        hwnd, unused_pid, unused_rect = win()
        handoff_focus(hwnd)
        focus()
        observation = observe()
        # A new manual request takes priority even if activation already succeeded.
        input_guard()
        with control_state_lock():
            current = read_optional('manual-pause.json')
            if (current.get('pause_id') if current else None) != RESUME_PAUSE_ID:
                raise RuntimeError('new manual pause during resume; locks preserved')
            win()
            if u.GetForegroundWindow() != hwnd or Path(ROOT, 'broker-stop').exists() or time.monotonic() >= BATCH_DEADLINE:
                raise RuntimeError('resume final foreground/deadline/stop verification failed')
            Path(ROOT, 'manual-pause.json').unlink(missing_ok=True)
            Path(ROOT, 'input-halted.json').unlink(missing_ok=True)
        return {'id': request['id'], 'ok': True, 'resumed': True, 'completed': [], 'observation': observation}
    except Exception as exc:
        result = {'id': request['id'], 'ok': False, 'resumed': False, 'error': str(exc), 'completed': []}
        write_json(Path(ROOT, 'input-halted.json'), result)
        return result
    finally:
        RESUMING = False
        RESUME_PAUSE_ID = None
        BATCH_DEADLINE = None
        acknowledge_pause()

def execute_request(request):
    if request.get('kind', 'actions') == 'resume':
        return execute_resume(request)
    if request.get('kind', 'actions') != 'actions':
        raise ValueError('unsupported request kind')
    return execute_batch(request)

def serve():
    global BROKER_IDENTITY
    win()
    game, own = integrity(BINDING['pid']), integrity(os.getpid())
    if 'integrity_rid' not in game or 'integrity_rid' not in own or own['integrity_rid'] < game['integrity_rid']:
        raise RuntimeError('game/controller permission comparison failed')
    handle = k.CreateMutexW(None, False, mutex_name(BINDING))
    if not handle:
        raise RuntimeError('game controller mutex unavailable')
    owned = False
    try:
        if k.WaitForSingleObject(handle, 15000) not in (0, 0x80):
            raise RuntimeError('another controller owns this game process/window')
        owned = True
        identity = process_probe(os.getpid())
        if identity['state'] != 'running':
            raise RuntimeError('broker process identity unverified')
        identity.update({'chat_id': OWNER['chat_id'], 'run_token': OWNER['run_token'], 'started': utc_now(), 'protocol_version': PROTOCOL_VERSION})
        BROKER_IDENTITY = identity
        write_json(Path(ROOT, 'broker-process.json'), identity)
        write_json(Path(ROOT, 'broker-ready.json'), identity)
        started = last_request = time.monotonic()
        reason = 'stop_requested'
        while not Path(ROOT, 'broker-stop').exists():
            acknowledge_pause()
            if time.monotonic() - started >= 7200 or time.monotonic() - last_request >= 600:
                reason = 'ttl_expired'
                break
            path = Path(ROOT, 'request.json')
            if not path.exists():
                time.sleep(.1)
                continue
            request = json.loads(path.read_text(encoding='utf8'))
            path.unlink()
            last_request = time.monotonic()
            try:
                result = execute_request(request)
            except Exception as exc:
                result = {'id': request.get('id'), 'ok': False, 'error': str(exc), 'completed': []}
                write_json(Path(ROOT, 'input-halted.json'), result)
            write_json(Path(ROOT, 'result.json'), result)
        write_json(Path(ROOT, 'broker-exit.json'), {'pid': identity['pid'], 'creation_id': identity['creation_id'], 'reason': reason, 'time': utc_now()})
    finally:
        if owned:
            Path(ROOT, 'broker-ready.json').unlink(missing_ok=True)
            k.ReleaseMutex(handle)
        k.CloseHandle(handle)

def start(chat_id, paused=False):
    global ROOT, OWNER, BINDING
    if not chat_id or len(chat_id) > 100:
        raise ValueError('the actual current chat id is required')
    hwnd, pid, rect = win()
    identity, game, own = process_probe(pid), integrity(pid), integrity(os.getpid())
    if identity['state'] != 'running' or 'integrity_rid' not in game or 'integrity_rid' not in own:
        raise RuntimeError('game process identity or permissions unverified')
    BINDING = {'pid': pid, 'creation_id': identity['creation_id'], 'hwnd': int(hwnd), 'rect': rect, 'game_integrity': game}
    mutex = claim_start_mutex(BINDING)
    launched = False
    start_owned = True
    try:
        ROOT = tempfile.mkdtemp(prefix='currency-wars-control-')
        OWNER = {'owner': 'currency-wars-control', 'chat_id': chat_id, 'run_token': secrets.token_hex(24), 'started': utc_now(), 'source': str(Path(__file__).resolve())}
        write_json(Path(ROOT, 'owner.json'), OWNER)
        write_json(Path(ROOT, 'binding.json'), BINDING)
        if paused:
            latch_pause('initial manual pause')
        args = subprocess.list2cmdline(['-B', '-X', 'utf8', str(Path(__file__).resolve()), 'serve', '--run-dir', ROOT, '--chat-id', chat_id, '--run-token', OWNER['run_token']])
        quote = lambda value: "'" + value.replace("'", "''") + "'"
        verb = ' -Verb RunAs' if own['integrity_rid'] < game['integrity_rid'] else ''
        command = 'Start-Process -FilePath ' + quote(sys.executable) + ' -ArgumentList ' + quote(args) + verb + ' -WindowStyle Hidden'
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command], capture_output=True, text=True)
        launched = result.returncode == 0
        k.ReleaseMutex(mutex)
        start_owned = False
        if not launched:
            raise StopUnverified('Windows launch failed; run retained for inspection: ' + ROOT + ' ' + result.stderr.strip())
        end = time.monotonic() + 15
        while time.monotonic() < end:
            if Path(ROOT, 'broker-ready.json').exists():
                print(json.dumps({'ready': True, 'protocol_version': PROTOCOL_VERSION, 'paused': paused, 'run_dir': ROOT, 'chat_id': chat_id, 'run_token': OWNER['run_token'], 'game': BINDING}, ensure_ascii=False))
                return
            time.sleep(.2)
        raise StopUnverified('broker did not become ready; run retained: ' + ROOT)
    finally:
        if start_owned:
            k.ReleaseMutex(mutex)
        k.CloseHandle(mutex)

@contextlib.contextmanager
def submission_lock():
    path = Path(ROOT, 'client-submit.lock')
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise RuntimeError('another request is pending; no concurrent submission')
    try:
        os.write(descriptor, json.dumps({'pid': os.getpid(), 'chat_id': OWNER['chat_id'], 'run_token': OWNER['run_token']}).encode('utf8'))
        os.close(descriptor)
        descriptor = None
        yield
    finally:
        if descriptor is not None:
            os.close(descriptor)
        path.unlink(missing_ok=True)

def publish_request(request):
    assert_owner(request['chat_id'], request['run_token'])
    if Path(ROOT, 'request.json').exists():
        raise RuntimeError('a request is already pending')
    write_json(Path(ROOT, 'request.json'), request)

def status():
    identity = read_optional('broker-process.json')
    probe = {'state': 'unknown', 'error': 'broker identity missing'}
    if identity:
        assert_owner(identity.get('chat_id'), identity.get('run_token'))
        probe = process_probe(identity['pid'], identity['creation_id'])
    pause, halted, ack = (read_optional(name) for name in ('manual-pause.json', 'input-halted.json', 'pause-ack.json'))
    if pause:
        assert_owner(pause.get('chat_id'), pause.get('run_token'))
    if ack:
        assert_owner(ack.get('chat_id'), ack.get('run_token'))
    alive = probe['state'] == 'running'
    acknowledged = bool(pause and ack and alive and ack.get('pause_id') == pause['pause_id'] and ack.get('broker_pid') == identity['pid'] and ack.get('broker_creation_time') == identity['creation_id'])
    foreground = int(u.GetForegroundWindow() or 0)
    try:
        hwnd, pid, rect = win()
        verified_game = {'pid': pid, 'hwnd': int(hwnd), 'creation_id': BINDING['creation_id'], 'rect': rect}
    except Exception as exc:
        verified_game = {'error': str(exc)}
    return {'protocol_version': identity.get('protocol_version', 1) if identity else PROTOCOL_VERSION, 'ready': alive and Path(ROOT, 'broker-ready.json').exists(), 'run_dir': ROOT, 'chat_id': OWNER['chat_id'], 'paused': bool(pause), 'input_halted': bool(halted), 'reason': pause['reason'] if pause else halted.get('error') if halted else None, 'pause_id': pause.get('pause_id') if pause else None, 'acknowledged': acknowledged, 'ack_time': ack.get('time') if acknowledged else None, 'broker_pid': identity.get('pid') if identity else None, 'broker_creation_time': identity.get('creation_id') if identity else None, 'broker_state': probe, 'foreground': foreground, 'game_foreground': foreground == BINDING['hwnd'], 'game': verified_game, 'gamepad': gamepad_activity(), 'pending_request': Path(ROOT, 'request.json').exists()}

def pause(reason):
    value = latch_pause(reason)
    end = time.monotonic() + 2
    while True:
        state = status()
        if state['pause_id'] != value['pause_id']:
            return {'ok': False, 'error': 'superseded by newer pause; manual lock retained', **state}
        if state['acknowledged']:
            return {'ok': True, **state}
        if state['broker_state']['state'] in ('absent', 'exited', 'reused') or time.monotonic() >= end:
            return {'ok': False, 'error': 'manual lock written; broker pause acknowledgement unverified', **state}
        time.sleep(.025)

def request_reply(request):
    state = status()
    if not state['ready']:
        raise RuntimeError('broker not ready; no request written')
    if request.get('kind') == 'resume' and state['protocol_version'] != PROTOCOL_VERSION:
        raise RuntimeError('loaded broker does not support safe resume; no request written')
    with submission_lock():
        rid = uuid.uuid4().hex
        request.update({'id': rid, 'chat_id': OWNER['chat_id'], 'run_token': OWNER['run_token']})
        publish_request(request)
        end = time.monotonic() + 40
        while time.monotonic() < end:
            try:
                result = json.loads(Path(ROOT, 'result.json').read_text(encoding='utf8'))
                if result.get('id') == rid:
                    if request.get('kind') == 'resume':
                        result['status'] = status()
                        result['resumed'] = bool(result.get('ok') and not result['status']['paused'] and not result['status']['input_halted'])
                    print(json.dumps(result, ensure_ascii=False))
                    return bool(result.get('ok') and (request.get('kind') != 'resume' or result['resumed']))
            except (FileNotFoundError, json.JSONDecodeError):
                pass
            if not Path(ROOT, 'broker-ready.json').exists():
                raise StopUnverified('broker exited before replying; inspect before another submission')
            time.sleep(.1)
        raise StopUnverified('request timed out; inspect and stop before submitting again')

def submit(tokens, handoff):
    actions = validate_actions([{'type': parts[0], 'args': parts[1:]} for parts in (token.split(':') for token in tokens)])
    return request_reply({'kind': 'actions', 'handoff': handoff, 'actions': actions})

def resume(handoff, tokens):
    if not handoff or tokens:
        raise ValueError('resume requires --handoff and forbids all game actions')
    return request_reply({'kind': 'resume', 'handoff': True})

def stop(keep):
    path = Path(ROOT, 'broker-process.json')
    if not path.exists():
        raise StopUnverified('broker identity missing; exit and cleanup unverified, run retained')
    identity = json.loads(path.read_text(encoding='utf8'))
    assert_owner(identity.get('chat_id'), identity.get('run_token'))
    Path(ROOT, 'broker-stop').touch()
    end = time.monotonic() + 10
    while True:
        state = process_probe(identity['pid'], identity['creation_id'])
        if state['state'] in ('exited', 'absent', 'reused'):
            break
        if state['state'] == 'unknown' or time.monotonic() >= end:
            raise StopUnverified('broker exit unverified; run retained: ' + json.dumps(state))
        time.sleep(.1)
    # Recheck ownership immediately before removing only this independent run.
    load_run(ROOT, OWNER['chat_id'], OWNER['run_token'])
    root = ROOT
    if not keep:
        shutil.rmtree(root)
    print(json.dumps({'stopped': True, 'exit_evidence': state, 'owned_run_removed': not keep, 'run_dir': root}))

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['start', 'serve', 'status', 'observe', 'submit', 'pause', 'resume', 'stop'])
    parser.add_argument('--run-dir')
    parser.add_argument('--chat-id', required=True)
    parser.add_argument('--run-token')
    parser.add_argument('--handoff', action='store_true')
    parser.add_argument('--keep', action='store_true')
    parser.add_argument('--paused', action='store_true')
    parser.add_argument('--reason', default='manual takeover')
    parser.add_argument('args', nargs='*')
    opts = parser.parse_intermixed_args()
    if opts.action == 'start':
        if opts.args:
            raise ValueError('start forbids game actions')
        start(opts.chat_id, opts.paused)
        return
    if not opts.run_dir or not opts.run_token:
        raise ValueError('existing runs require --run-dir, --chat-id and --run-token')
    load_run(opts.run_dir, opts.chat_id, opts.run_token)
    if opts.action == 'serve':
        serve()
    elif opts.action == 'observe':
        print(json.dumps(observe(), ensure_ascii=False))
    elif opts.action == 'submit':
        if not submit(opts.args, opts.handoff):
            sys.exit(2)
    elif opts.action == 'resume':
        if not resume(opts.handoff, opts.args):
            sys.exit(2)
    elif opts.action == 'pause':
        if opts.args:
            raise ValueError('pause forbids game actions')
        result = pause(opts.reason)
        print(json.dumps(result, ensure_ascii=False))
        if not result['ok']:
            sys.exit(2)
    elif opts.action == 'stop':
        stop(opts.keep)
    else:
        print(json.dumps(status(), ensure_ascii=False))

if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False))
        sys.exit(2)
