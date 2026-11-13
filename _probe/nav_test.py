"""Launch the game, click through menus, screenshot each step.

Usage:  python _probe/nav_test.py "8" "click:320,460" "wait:3" "shot:menu2" ...
Steps are executed in order after the window is focused.
  wait:SECONDS        sleep
  click:X,Y           click at window-relative client coords
  move:X,Y            move the pointer (for hover tooltips)
  shot:TAG            grab the screen into shot_TAG.png
"""
import ctypes
import os
import subprocess
import sys
import time
import winreg
from ctypes import wintypes

from PIL import ImageGrab

GAME = r"I:\LocalGames\Total Annihilation Kingdoms"
EXE = os.path.join(GAME, "Kingdoms.exe")
LOG = os.path.join(GAME, "tak_chs.log")
OUT = os.path.dirname(os.path.abspath(__file__))
LAYERS = r"Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers"

user32 = ctypes.windll.user32


def drop_runasadmin():
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, LAYERS, 0, winreg.KEY_ALL_ACCESS)
    try:
        old, _ = winreg.QueryValueEx(key, EXE)
    except OSError:
        old = None
    if old and "RUNASADMIN" in old.upper():
        new = " ".join(t for t in old.split() if t.upper() != "RUNASADMIN")
        winreg.SetValueEx(key, EXE, 0, winreg.REG_SZ, new)
        print("compat layer: %r -> %r" % (old, new))
    return old


def restore_layer(old):
    if old is None:
        return
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, LAYERS, 0, winreg.KEY_ALL_ACCESS)
    winreg.SetValueEx(key, EXE, 0, winreg.REG_SZ, old)
    print("compat layer restored ->", old)


def find_window(pid=None):
    """The real game window has class 'Kingdoms Class' and may belong to a
    different process than the launcher we spawned, so match on class name."""
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        cls = ctypes.create_unicode_buffer(128)
        user32.GetClassNameW(hwnd, cls, 128)
        if "kingdom" not in cls.value.lower():
            return True
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        area = (r.right - r.left) * (r.bottom - r.top)
        p = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        found.append((area, hwnd, (r.left, r.top, r.right, r.bottom), p.value))
        return True

    user32.EnumWindows(cb, 0)
    found.sort(reverse=True)
    if not found:
        return None
    area, hwnd, rect, owner = found[0]
    return area, hwnd, rect, owner


def focus(hwnd):
    user32.ShowWindow(hwnd, 9)
    user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002)
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    user32.SetFocus(hwnd)


BUFFER = (640, 480)   # the game always renders a 640x480 back buffer


def client_size(hwnd):
    r = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(r))
    return r.right - r.left, r.bottom - r.top


def to_screen(hwnd, x, y):
    """(x, y) are in 640x480 game-buffer space. The game may run as a 640x480
    window on a larger desktop, or as a stretched fullscreen surface, so map
    onto the window's client rect and let Windows do the rest."""
    cw, ch = client_size(hwnd)
    if cw and ch:
        x = int(x * cw / BUFFER[0])
        y = int(y * ch / BUFFER[1])
    pt = wintypes.POINT(x, y)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    return pt


def click(hwnd, x, y):
    pt = to_screen(hwnd, x, y)
    user32.SetCursorPos(pt.x, pt.y)
    time.sleep(0.2)
    user32.mouse_event(0x0002, 0, 0, 0, 0)   # LEFTDOWN
    time.sleep(0.08)
    user32.mouse_event(0x0004, 0, 0, 0, 0)   # LEFTUP


def move(hwnd, x, y):
    pt = to_screen(hwnd, x, y)
    user32.SetCursorPos(pt.x, pt.y)


KEYS = {
    "enter": 0x0D, "esc": 0x1B, "space": 0x20, "tab": 0x09,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "back": 0x08, "del": 0x2E, "f1": 0x70, "f10": 0x79,
}


def key(hwnd, name):
    """The menu binds Enter=Play / Esc=Previous (see Keys.TDF), and synthetic
    mouse events do not reach the DirectInput layer, so drive it from the
    keyboard instead."""
    vk = KEYS.get(name.lower())
    if vk is None:
        if len(name) == 1:
            vk = ord(name.upper())
        else:
            print("?? unknown key", name)
            return
    user32.PostMessageW(hwnd, 0x0100, vk, 0)          # WM_KEYDOWN
    time.sleep(0.12)
    user32.PostMessageW(hwnd, 0x0101, vk, 0xC0000001)  # WM_KEYUP


def keybd(vk):
    """Fallback: inject at driver level so DirectInput sees it too."""
    user32.keybd_event(vk, 0, 0, 0)
    time.sleep(0.1)
    user32.keybd_event(vk, 0, 0x0002, 0)


def shot(tag, hwnd=None):
    """Grab only the game's client area so the capture is always the game,
    whether it runs as a 640x480 window or as a fullscreen surface."""
    box = None
    if hwnd:
        p0 = to_screen(hwnd, 0, 0)
        cw, ch = client_size(hwnd)
        if cw and ch:
            box = (p0.x, p0.y, p0.x + cw, p0.y + ch)
    img = ImageGrab.grab(bbox=box) if box else ImageGrab.grab()
    path = os.path.join(OUT, "shot_%s.png" % tag)
    img.save(path)
    print("shot ->", path, img.size)


def screen_size():
    return (user32.GetSystemMetrics(0), user32.GetSystemMetrics(1))


def log_ready():
    """The game only starts rendering the menu after the intro movie. The DLL
    logs 'cjk: DrawFrame' on the first menu text draw, so that line is a
    reliable 'main menu is up' marker."""
    try:
        with open(LOG, "rb") as f:
            return b"DrawFrame" in f.read()
    except OSError:
        return False


def wait_ready(p, timeout=90):
    """Do NOT touch the game before it renders: stealing focus during the
    intro makes it sit on a black screen. The display-mode switch happens
    almost immediately (still black), so the real marker is the first menu
    text draw logged by the DLL."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        if p.poll() is not None:
            print("!! game exited early, code", p.returncode)
            return False
        if log_ready():
            print("ready after %.1fs (menu text drawn)" % (time.time() - t0))
            time.sleep(1.5)   # let the fade-in settle
            return True
        time.sleep(0.5)
    if screen_size() == BUFFER:
        print("ready (timeout, but display mode is 640x480)")
        return True
    print("!! never became ready (screen %dx%d)" % screen_size())
    return False


def main():
    steps = sys.argv[1:]
    if not steps:
        print(__doc__)
        return
    if os.path.exists(LOG):
        os.remove(LOG)
    old = drop_runasadmin()
    p = None
    try:
        p = subprocess.Popen([EXE], cwd=GAME)
        if not wait_ready(p):
            return
        # The window may be hidden while DirectDraw owns the display; focus is
        # best-effort and clicking works in screen space regardless.
        win = None
        for _ in range(16):
            win = find_window()
            if win:
                break
            time.sleep(0.5)
        if not win:
            print("!! no game window")
            return
        area, hwnd, rect, owner = win
        print("win %s owner_pid=%d rect=%s client=%s" %
              (hwnd, owner, rect, client_size(hwnd)))
        # Focus manipulation can break DirectDraw input capture; the game
        # already owns the foreground because we launched it.
        do_focus = os.environ.get("NAV_NOFOCUS") != "1"
        if do_focus:
            focus(hwnd)

        for step in steps:
            op, _, arg = step.partition(":")
            if do_focus:
                focus(hwnd)

            if op == "wait":
                time.sleep(float(arg))
            elif op == "click":
                x, y = (int(v) for v in arg.split(","))
                pt = to_screen(hwnd, x, y)
                print("click", x, y, "->", (pt.x, pt.y))
                click(hwnd, x, y)
            elif op == "move":
                x, y = (int(v) for v in arg.split(","))
                move(hwnd, x, y)
            elif op == "key":
                vk = KEYS.get(arg.lower())
                print("key", arg, "vk", vk)
                if vk is not None:
                    key(hwnd, arg)
                    keybd(vk)
            elif op == "shot":
                shot(arg, hwnd)
            else:
                print("?? unknown step", step)
        time.sleep(0.5)
        print("game stopped")
    finally:
        # The launcher and the actual game may be different processes; kill
        # both so the display mode is always restored between test runs.
        for image in ("Kingdoms.exe", "KINGDOMS.icd"):
            subprocess.run(["taskkill", "/F", "/IM", image],
                           capture_output=True)
        if p is not None and p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=8)
            except Exception:
                p.kill()
        restore_layer(old)


if __name__ == "__main__":
    main()
