"""诊断：代理 DLL 到底有没有进到游戏进程里。

上一轮的现象是「部署 version.dll 之后完全没日志」。但 vtest.exe 已经证明
代理本身能被加载、转发也通（VerQueryValueA 走跳板返回了正确结果）。所以
问题只可能在两处：
  A. 代理根本没被加载进游戏进程（加载路径/宿主 DLL 不参与）；
  B. 加载了，但 IsTargetGame() 判成「非目标进程」，DllMain 直接 break。

本脚本不猜，直接看进程与模块：把游戏启动起来，遍历所有 exe 在本目录下的
进程，打印它们加载的 version.dll 完整路径，并盯着 tak_chs.log 是否出现。
"""
import os
import subprocess
import sys
import time

GAME = r"I:\LocalGames\Total Annihilation Kingdoms"
EXE = os.path.join(GAME, sys.argv[2] if len(sys.argv) > 2 else "KINGDOMS.icd")
LAYERS = r"Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers"
LOG = os.path.join(GAME, "tak_chs.log")


def game_procs():
    """返回所有 exe 位于游戏目录的进程 (pid, name)。"""
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.windll.kernel32
    psapi = ctypes.windll.psapi
    out = []
    arr = (wintypes.DWORD * 4096)()
    need = wintypes.DWORD()
    if not psapi.EnumProcesses(arr, ctypes.sizeof(arr), ctypes.byref(need)):
        return out
    n = need.value // ctypes.sizeof(wintypes.DWORD)
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    for i in range(n):
        pid = arr[i]
        if not pid:
            continue
        h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            continue
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(1024)
            if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                path = buf.value
                if os.path.dirname(path).lower() == GAME.lower():
                    out.append((pid, os.path.basename(path), path))
        finally:
            k32.CloseHandle(h)
    return out


def modules_of(pid, want="version.dll"):
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.windll.kernel32
    psapi = ctypes.windll.psapi
    PROCESS_QUERY_INFORMATION = 0x0400
    PROCESS_VM_READ = 0x0010
    psapi.EnumProcessModulesEx.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), wintypes.DWORD]
    psapi.GetModuleFileNameExW.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_wchar_p, wintypes.DWORD]
    h = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not h:
        return ["<打不开进程 %d，可能已提权>" % pid]
    found = []
    try:
        arr = (wintypes.HMODULE * 2048)()
        need = wintypes.DWORD()
        if psapi.EnumProcessModulesEx(h, arr, ctypes.sizeof(arr),
                                      ctypes.byref(need), 3):
            n = min(need.value // ctypes.sizeof(wintypes.HMODULE), 2048)
            for i in range(n):
                buf = ctypes.create_unicode_buffer(1024)
                if psapi.GetModuleFileNameExW(h, arr[i], buf, 1024):
                    if os.path.basename(buf.value).lower() == want:
                        found.append(buf.value)
    finally:
        k32.CloseHandle(h)
    return found


def drop_runasadmin():
    """Kingdoms.exe 带 RUNASADMIN 兼容层，非提权进程 CreateProcess 会报 740。"""
    import winreg
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, LAYERS, 0, winreg.KEY_ALL_ACCESS)
    try:
        old, _ = winreg.QueryValueEx(key, EXE)
    except OSError:
        old = None
    if old and "RUNASADMIN" in old.upper():
        new = " ".join(t for t in old.split() if t.upper() != "RUNASADMIN")
        winreg.SetValueEx(key, EXE, 0, winreg.REG_SZ, new)
    return old


def restore_layer(old):
    if old is None:
        return
    import winreg
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, LAYERS, 0, winreg.KEY_ALL_ACCESS)
    winreg.SetValueEx(key, EXE, 0, winreg.REG_SZ, old)


def main():
    if os.path.exists(LOG):
        os.remove(LOG)
    print("start", EXE)
    old = drop_runasadmin()
    p = subprocess.Popen([EXE], cwd=GAME)
    t0 = time.time()
    seen = {}
    while time.time() - t0 < float(sys.argv[1] if len(sys.argv) > 1 else 15):
        procs = game_procs()
        for pid, name, path in procs:
            if pid not in seen:
                seen[pid] = {"name": name, "first": round(time.time() - t0, 1),
                             "ver": None}
            mods = modules_of(pid)
            seen[pid]["ver"] = mods
        if os.path.exists(LOG):
            print("LOG 出现 t=%.1fs size=%d" % (time.time() - t0,
                                               os.path.getsize(LOG)))
            break
        time.sleep(1)

    print("\n--- 进程清单 ---")
    for pid, info in seen.items():
        print("pid=%d %s  首次出现 t=%.1fs" % (pid, info["name"], info["first"]))
        for m in info["ver"] or ["<无 version.dll>"]:
            print("    version.dll -> %s" % m)
    print("\nlog 存在:", os.path.exists(LOG))
    restore_layer(old)

    # 收尾：关掉 Kingdoms.exe 及其派生进程
    try:
        p.terminate()
    except Exception:
        pass
    time.sleep(1)
    for pid, name, path in game_procs():
        os.system('taskkill /PID %d /F >nul 2>&1' % pid)


if __name__ == "__main__":
    main()
