"""Minimal Windows screenshot/mouse/keyboard helper for Codex coaching.

The image returned by 'shot' or an action is 1920x1080; coordinates for all
subsequent actions use that preview. This tool makes no strategic decisions.
"""
from __future__ import annotations

import argparse
import contextlib
import ctypes as c
from ctypes import wintypes as w
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
CAPTURES = ROOT / "captures"
STOP = CAPTURES / "STOP"
LATEST = CAPTURES / "latest.json"
TITLE = "崩坏：星穹铁道"
SIZE = (1920, 1080)


class Rejected(RuntimeError):
    pass


class MI(c.Structure):
    _fields_ = [("dx", w.LONG), ("dy", w.LONG), ("mouseData", w.DWORD),
                ("dwFlags", w.DWORD), ("time", w.DWORD), ("dwExtraInfo", c.c_size_t)]


class KI(c.Structure):
    _fields_ = [("wVk", w.WORD), ("wScan", w.WORD), ("dwFlags", w.DWORD),
                ("time", w.DWORD), ("dwExtraInfo", c.c_size_t)]


class Payload(c.Union):
    _fields_ = [("mi", MI), ("ki", KI)]


class INPUT(c.Structure):
    _fields_ = [("type", w.DWORD), ("data", Payload)]


def map_point(x, y, rect):
    if not 0 <= x < SIZE[0] or not 0 <= y < SIZE[1]:
        raise Rejected("Coordinates must be within the 1920x1080 screenshot")
    left, top, right, bottom = rect
    if right <= left or bottom <= top:
        raise Rejected("Invalid game client rectangle")
    return (min(right - 1, left + round(x * (right - left) / SIZE[0])),
            min(bottom - 1, top + round(y * (bottom - top) / SIZE[1])))


class Game:
    def __init__(self):
        if sys.platform != "win32":
            raise Rejected("This tool only runs on an interactive Windows desktop")
        self.u = c.WinDLL("user32", use_last_error=True)
        self.k = c.WinDLL("kernel32", use_last_error=True)
        with contextlib.suppress(Exception):
            self.u.SetProcessDpiAwarenessContext(c.c_void_p(-4))
        self.u.GetForegroundWindow.restype = w.HWND
        self.u.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, c.c_int]
        self.u.GetClientRect.argtypes = [w.HWND, c.POINTER(w.RECT)]
        self.u.ClientToScreen.argtypes = [w.HWND, c.POINTER(w.POINT)]
        self.u.IsWindowVisible.argtypes = [w.HWND]
        self.u.GetWindowThreadProcessId.argtypes = [w.HWND, c.POINTER(w.DWORD)]
        self.u.GetCursorPos.argtypes = [c.POINTER(w.POINT)]
        self.u.GetAsyncKeyState.argtypes = [c.c_int]
        self.u.GetAsyncKeyState.restype = c.c_short
        self.u.GetSystemMetrics.argtypes = [c.c_int]
        self.u.MapVirtualKeyW.argtypes = [w.UINT, w.UINT]
        self.u.SendInput.argtypes = [w.UINT, c.POINTER(INPUT), c.c_int]
        self.u.SendInput.restype = w.UINT
        self.k.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        self.k.OpenProcess.restype = w.HANDLE
        self.k.GetProcessTimes.argtypes = [w.HANDLE] + [c.POINTER(w.FILETIME)] * 4
        self.k.CloseHandle.argtypes = [w.HANDLE]
        self.k.CreateMutexW.argtypes = [w.LPVOID, w.BOOL, w.LPCWSTR]
        self.k.CreateMutexW.restype = w.HANDLE
        self.k.ReleaseMutex.argtypes = [w.HANDLE]
        self.k.OpenMutexW.argtypes = [w.DWORD, w.BOOL, w.LPCWSTR]
        self.k.OpenMutexW.restype = w.HANDLE
        self.bound = self.find()

    def find(self):
        matches = []

        @c.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
        def visit(hwnd, unused):
            if not self.u.IsWindowVisible(hwnd):
                return True
            title = c.create_unicode_buffer(256)
            self.u.GetWindowTextW(hwnd, title, 256)
            rect = w.RECT()
            if (title.value == TITLE and self.u.GetClientRect(hwnd, c.byref(rect))
                    and rect.right >= 640 and rect.bottom >= 360):
                matches.append(hwnd)
            return True

        if not self.u.EnumWindows(visit, 0) or len(matches) != 1:
            raise Rejected("Expected exactly one visible 崩坏：星穹铁道 window")
        hwnd = int(matches[0])
        pid = w.DWORD()
        if not self.u.GetWindowThreadProcessId(hwnd, c.byref(pid)):
            raise Rejected("Game PID is unavailable")
        handle = self.k.OpenProcess(0x1000, False, pid.value)
        if not handle:
            raise Rejected("Game process creation identity is inaccessible")
        try:
            created, exited, kernel, user = (w.FILETIME() for _ in range(4))
            if not self.k.GetProcessTimes(handle, c.byref(created), c.byref(exited),
                                          c.byref(kernel), c.byref(user)):
                raise Rejected("Cannot verify game process creation time")
            creation = str((created.dwHighDateTime << 32) | created.dwLowDateTime)
        finally:
            self.k.CloseHandle(handle)
        bounds, origin = w.RECT(), w.POINT(0, 0)
        if (not self.u.GetClientRect(hwnd, c.byref(bounds))
                or not self.u.ClientToScreen(hwnd, c.byref(origin))):
            raise Rejected("Game client bounds unavailable")
        return dict(hwnd=hwnd, pid=pid.value, creation=creation,
                    rect=(origin.x, origin.y, origin.x + bounds.right, origin.y + bounds.bottom))

    def check(self, input=False, foreground=True):
        now = self.find()
        if any(now[key] != self.bound[key] for key in ("hwnd", "pid", "creation")):
            raise Rejected("The game process/window changed")
        if foreground and int(self.u.GetForegroundWindow() or 0) != now["hwnd"]:
            raise Rejected("Game is not in foreground; switch to it yourself")
        if input:
            if STOP.exists():
                raise Rejected("STOP is latched; user must explicitly resume")
            if self.u.GetAsyncKeyState(0x77) & 0x8000:  # F8 during a tool call
                STOP.touch(exist_ok=True)
                raise Rejected("F8 held; STOP latched")
            self.reject_legacy(now)
        return now

    def reject_legacy(self, win):
        name = ("Local\\CurrencyWarsControl-P" + str(win["pid"])
                + "-T" + win["creation"] + "-H" + str(win["hwnd"]))
        c.set_last_error(0)
        existing = self.k.OpenMutexW(0x00100000, False, name)
        if existing:
            self.k.CloseHandle(existing)
            raise Rejected("Legacy Worker/broker is active; cannot control concurrently")
        if c.get_last_error() not in (0, 2):
            raise Rejected("Cannot verify whether legacy controller is active")

    @contextlib.contextmanager
    def exclusive(self):
        name = f"Local\\CurrencyWarsCoaching-P{self.bound['pid']}-H{self.bound['hwnd']}"
        c.set_last_error(0)
        handle = self.k.CreateMutexW(None, True, name)
        if not handle:
            raise Rejected("Cannot acquire coaching input mutex")
        already_exists = c.get_last_error() == 183
        try:
            if already_exists:
                raise Rejected("Another coaching tool is using this window")
            yield
        finally:
            if not already_exists:
                self.k.ReleaseMutex(handle)
            self.k.CloseHandle(handle)

    def send(self, value):
        if self.u.SendInput(1, c.byref(value), c.sizeof(INPUT)) != 1:
            raise Rejected("SendInput failed; do not blindly repeat the operation")

    def mouse(self, flags, delta=0, x=0, y=0):
        self.send(INPUT(0, Payload(mi=MI(x, y, delta & 0xFFFFFFFF, flags, 0, 0))))

    def move(self, x, y):
        rect = self.check(input=True)["rect"]
        px, py = map_point(x, y, rect)
        left, top, width, height = (self.u.GetSystemMetrics(i) for i in (76, 77, 78, 79))
        if width < 2 or height < 2:
            raise Rejected("Virtual desktop geometry unavailable")
        vx, vy = round((px - left) * 65535 / (width - 1)), round((py - top) * 65535 / (height - 1))
        self.mouse(0xC001, x=vx, y=vy)
        time.sleep(0.12)
        cursor = w.POINT()
        if (not self.u.GetCursorPos(c.byref(cursor))
                or abs(cursor.x - px) > 3 or abs(cursor.y - py) > 3):
            raise Rejected("Mouse contention; do not click/retry")

    def click(self, x, y):
        self.move(x, y)
        self.check(input=True)
        self.mouse(2)
        try:
            time.sleep(.09)
        finally:
            self.mouse(4)

    def drag(self, x, y, tx, ty):
        self.move(x, y)
        self.check(input=True)
        self.mouse(2)
        try:
            time.sleep(.15)
            l, t, width, height = (self.u.GetSystemMetrics(i) for i in (76, 77, 78, 79))
            if width < 2 or height < 2:
                raise Rejected("Virtual screen unavailable")
            for index in range(1, 9):
                self.check(input=True)
                px, py = map_point(round(x + (tx - x) * index / 8),
                                   round(y + (ty - y) * index / 8), self.bound["rect"])
                vx = round((px - l) * 65535 / (width - 1))
                vy = round((py - t) * 65535 / (height - 1))
                self.mouse(0xC001, x=vx, y=vy)
                time.sleep(.06)
        finally:
            self.mouse(4)

    def key(self, name):
        self.check(input=True)
        vk = {"ESC": 27, "D": 68, "F": 70, "SPACE": 32, "R": 82}[name]
        scan = self.u.MapVirtualKeyW(vk, 0)
        self.send(INPUT(1, Payload(ki=KI(0, scan, 8, 0, 0))))
        try:
            time.sleep(.09)
        finally:
            self.send(INPUT(1, Payload(ki=KI(0, scan, 10, 0, 0))))


def write_json(path, obj):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def screenshot(game):
    from PIL import ImageGrab
    win = game.check()
    image = ImageGrab.grab(bbox=win["rect"], all_screens=True)
    try:
        if image.size != SIZE:
            scaled = image.resize(SIZE)
            image.close()
            image = scaled
        CAPTURES.mkdir(parents=True, exist_ok=True)
        ident = uuid.uuid4().hex
        file = CAPTURES / (ident + ".png")
        with file.open("xb") as fd:
            image.save(fd, format="PNG")
    finally:
        image.close()
    meta = dict(frame=ident, image=str(file), sha256=hashlib.sha256(file.read_bytes()).hexdigest(),
                hwnd=win["hwnd"], pid=win["pid"], creation=win["creation"], size=list(SIZE))
    write_json(CAPTURES / (ident + ".json"), meta)
    write_json(LATEST, meta)
    return meta


def require_frame(game, ident):
    if not LATEST.exists():
        raise Rejected("First take a screenshot with the 'shot' command")
    meta = json.loads(LATEST.read_text(encoding="utf-8"))
    if ident != meta.get("frame"):
        raise Rejected("Frame is superseded; use latest screenshot's frame ID")
    now = game.check(input=True)
    if any(meta.get(k) != now[k] for k in ("hwnd", "pid", "creation")):
        raise Rejected("Frame's game identity is obsolete")
    file = CAPTURES / (ident + ".png")
    if not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest() != meta.get("sha256"):
        raise Rejected("Screenshot has been removed or modified")


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    for cmd in ("status", "shot", "stop", "resume"):
        actions.add_parser(cmd)
    for cmd in ("hover", "inspect", "scroll", "click", "drag", "key"):
        p = actions.add_parser(cmd)
        p.add_argument("--frame", required=True, help="ID returned by latest screenshot/action")
        if cmd == "key":
            p.add_argument("name", choices=("ESC", "D", "F", "SPACE", "R"))
        else:
            p.add_argument("x", type=int)
            p.add_argument("y", type=int)
            if cmd == "scroll":
                p.add_argument("delta", type=int)
            if cmd == "drag":
                p.add_argument("to_x", type=int)
                p.add_argument("to_y", type=int)
    return parser.parse_args()


def run(args, game):
    if args.action == "status":
        return dict(game=game.check(foreground=False),
                    foreground=int(game.u.GetForegroundWindow() or 0), stopped=STOP.exists())
    with game.exclusive():
        if args.action == "shot":
            return dict(observation=screenshot(game), input_attempted=False)
        require_frame(game, args.frame)
        if args.action != "key":
            map_point(args.x, args.y, game.bound["rect"])
        if args.action == "drag":
            map_point(args.to_x, args.to_y, game.bound["rect"])
        if args.action == "scroll" and (args.delta == 0 or abs(args.delta) > 1200 or args.delta % 120):
            raise Rejected("Scroll delta must be a nonzero multiple of 120, magnitude <= 1200")
        # Once an input was attempted, failure to grab the next image must
        # never be interpreted as permission to replay the mouse/keyboard.
        try:
            if args.action == "hover":
                game.move(args.x, args.y)
                time.sleep(.65)
            elif args.action in ("inspect", "click"):
                game.click(args.x, args.y)
                time.sleep(.4)
            elif args.action == "drag":
                game.drag(args.x, args.y, args.to_x, args.to_y)
                time.sleep(.4)
            elif args.action == "scroll":
                game.move(args.x, args.y)
                game.check(input=True)
                game.mouse(0x800, delta=args.delta)
                time.sleep(.35)
            elif args.action == "key":
                game.key(args.name)
                time.sleep(.35)
            return dict(operation=args.action, input_attempted=True,
                        effect_verified=False, observation=screenshot(game))
        except Exception as exc:
            return dict(operation=args.action, input_attempted=True,
                        effect_verified=False, error=str(exc),
                        warning="Action may have occurred; observe before any new input, never blind-retry")


def main():
    args = arguments()
    try:
        if args.action == "stop":
            CAPTURES.mkdir(parents=True, exist_ok=True)
            STOP.write_text("User stop: do not resume without explicit user permission\n", encoding="utf-8")
            result = dict(stopped=True)
        elif args.action == "resume":
            STOP.unlink(missing_ok=True)
            result = dict(stopped=False, warning="Resume only when user explicitly authorizes it")
        else:
            result = run(args, Game())
        print(json.dumps(result, ensure_ascii=False))
        return 2 if result.get("error") else 0
    except Exception as exc:
        print(json.dumps(dict(error=str(exc), input_attempted="unknown"), ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
