"""Diagnose which process owns the game window.

Usage: python _probe/win_dump.py
Launches the game, waits until the menu has been drawn, then dumps every
top-level window plus the process list.
"""
import ctypes
import os
import subprocess
import time
import winreg
from ctypes import wintypes

GAME = r"I:\LocalGames\Total Annihilation Kingdoms"
EXE = os.path.join(GAME, "Kingdoms.exe")
LOG = os.path.join(GAME, "tak_chs.log")
LAYERS = r"Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers"
user32 = ctypes.windll.user32


def drop():
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, LAYERS, 0, winreg.KEY_ALL_ACCESS)
    try:
        old, _ = winreg.QueryValueEx(key, EXE)
    except OSError:
        old = None
    if old and "RUNASADMIN" in old.upper():
        winreg.SetValueEx(key, EXE, 0, winreg.REG_SZ,
                          " ".join(t for t in old.split() if t.upper() != "RUNASADMIN"))
    return old


def restore(old):
    if old is None:
        return
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, LAYERS, 0, winreg.KEY_ALL_ACCESS)
    winreg.SetValueEx(key, EXE, 0, winreg.REG_SZ, old)


def dump():
    rows = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        area = (r.right - r.left) * (r.bottom - r.top)
        if area < 50000:
            return True
        cls = ctypes.create_unicode_buffer(128)
        user32.GetClassNameW(hwnd, cls, 128)
        title = ctypes.create_unicode_buffer(128)
        user32.GetWindowTextW(hwnd, title, 128)
        rows.append((area, hwnd, pid.value, cls.value, title.value,
                     bool(user32.IsWindowVisible(hwnd)),
                     (r.left, r.top, r.right, r.bottom),
                     hex(user32.GetWindowLongW(hwnd, -16) & 0xFFFFFFFF)))
        return True

    user32.EnumWindows(cb, 0)
    rows.sort(reverse=True)
    print("%-10s %-8s %-6s %-22s %-6s %-28s %s" %
          ("hwnd", "pid", "vis", "class", "style", "rect", "title"))
    for area, hwnd, pid, cls, title, vis, rect, style in rows:
        print("%-10s %-8d %-6s %-22s %-6s %-28s %s" %
              (hwnd, pid, vis, cls, style, rect, title))
    fg = user32.GetForegroundWindow()
    p = wintypes.DWORD()
    user32.GetWindowThreadProcessId(fg, ctypes.byref(p))
    print("foreground hwnd=%s pid=%s" % (fg, p.value))


def main():
    if os.path.exists(LOG):
        os.remove(LOG)
    old = drop()
    p = None
    try:
        p = subprocess.Popen([EXE], cwd=GAME)
        print("launcher pid", p.pid)
        for _ in range(180):
            time.sleep(0.5)
            if os.path.exists(LOG):
                with open(LOG, "rb") as f:
                    if b"DrawFrame" in f.read():
                        break
        time.sleep(1.5)
        print("launcher alive:", p.poll() is None)
        dump()
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Kingdoms.exe"],
                             capture_output=True, text=True)
        print(out.stdout)
    finally:
        if p is not None and p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=8)
            except Exception:
                p.kill()
        restore(old)


if __name__ == "__main__":
    main()
