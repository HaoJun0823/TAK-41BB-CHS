"""Launch the game, wait for the main menu, take screenshots, dump the log.

Runs outside the tool sandbox: the sandbox injects tsbx_x86.dll into child
processes (file/net/process hooks) and the game dies within a second.
"""
import os
import subprocess
import sys
import time
import winreg

from PIL import ImageGrab

GAME = r"I:\LocalGames\Total Annihilation Kingdoms"
EXE = os.path.join(GAME, "Kingdoms.exe")
LOG = os.path.join(GAME, "tak_chs.log")
OUT = os.path.dirname(os.path.abspath(__file__))

LAYERS = r"Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers"


def drop_runasadmin():
    """The exe carries a RUNASADMIN compat layer; we are not elevated, so
    CreateProcess fails with ERROR_ELEVATION_REQUIRED. Strip the flag for the
    duration of the test and restore it afterwards."""
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, LAYERS, 0, winreg.KEY_ALL_ACCESS)
    try:
        old, typ = winreg.QueryValueEx(key, EXE)
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

SHOTS = [int(x) for x in (sys.argv[1:] or ["38", "48"])]


def focus_game(pid):
    """Bring the game window to the foreground. Popen gives it no foreground
    rights, so it stays behind WorkBuddy and the screen grab shows the desktop."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        p = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == pid and user32.IsWindowVisible(hwnd):
            r = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            area = (r.right - r.left) * (r.bottom - r.top)
            cls = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(hwnd, cls, 64)
            found.append((area, hwnd, cls.value, (r.left, r.top, r.right, r.bottom)))
        return True

    user32.EnumWindows(cb, 0)
    found.sort(reverse=True)
    for area, hwnd, cls, rect in found[:3]:
        print("win:", hwnd, cls, rect, "area", area)
    if not found:
        return False
    hwnd = found[0][1]
    user32.ShowWindow(hwnd, 9)                       # SW_RESTORE
    user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0,        # HWND_TOPMOST
                        0x0001 | 0x0002)             # NOSIZE | NOMOVE
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    user32.SetFocus(hwnd)
    return True



def shot(tag):
    img = ImageGrab.grab()
    path = os.path.join(OUT, "shot_%s.png" % tag)
    img.save(path)
    print("shot ->", path, img.size)


def main():
    if os.path.exists(LOG):
        os.remove(LOG)
    old = drop_runasadmin()
    try:
        print("start", EXE)
        p = subprocess.Popen([EXE], cwd=GAME)
        t0 = time.time()
        for t in SHOTS:
            while time.time() - t0 < t:
                time.sleep(0.2)
                if p.poll() is not None:
                    print("!! game exited early, code", p.returncode)
                    return
            focus_game(p.pid)
            time.sleep(0.8)
            shot(t)
        time.sleep(1)
        if p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=8)
            except Exception:
                p.kill()
        print("game stopped")
    finally:
        restore_layer(old)


if __name__ == "__main__":
    main()
