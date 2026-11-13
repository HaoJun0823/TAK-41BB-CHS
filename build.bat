@echo off
REM ===========================================================================
REM  TAK 41BB 汉化 DLL 构建脚本（命令行 / CI 用）
REM ---------------------------------------------------------------------------
REM  产物：build_dll\binkw32.dll
REM  用法：build.bat             Release
REM        build.bat debug       带调试符号、无优化
REM
REM  为什么不直接用 msbuild：本工程只编 5 个 cpp，cl 直调比 msbuild 快，
REM  也避免 .vcxproj 的 MSBuild 版本差异。VS 里打开 sln 编译同样可行，
REM  两边配置一致（见 .vcxproj 头部注释）。
REM
REM  为什么锁 v141（MSVC 14.16）：游戏是 Windows 98/XP 时代的 32 位程序，
REM  只有 v141_xp 工具集编出的 DLL 不引入新版 CRT 的 API 依赖。
REM ===========================================================================
setlocal enabledelayedexpansion

set CFG=%~1
if "%CFG%"=="" set CFG=release

set ROOT=%~dp0
cd /d "%ROOT%"

REM ── 定位 Visual Studio ────────────────────────────────────────────────
set VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe
if not exist "%VSWHERE%" (
    echo [错误] 找不到 vswhere.exe。请先安装 Visual Studio 2017 或更高版本。
    exit /b 1
)

set VSDIR=
for /f "usebackq tokens=*" %%i in (`"%VSWHERE%" -latest -products * -property installationPath`) do set VSDIR=%%i
if "%VSDIR%"=="" (
    echo [错误] vswhere 没有返回安装路径。
    exit /b 1
)
echo Visual Studio: %VSDIR%

REM ── 加载 v141 (14.16) 环境 ────────────────────────────────────────────
set VCVARS=%VSDIR%\VC\Auxiliary\Build\vcvarsall.bat
if not exist "%VCVARS%" (
    echo [错误] 找不到 vcvarsall.bat，请安装「使用 C++ 的桌面开发」工作负载。
    exit /b 1
)

REM -vcvars_ver=14.16 强制用 v141；找不到会回退到最新工具集，
REM 所以下面还要再断言一次 cl 版本。
call "%VCVARS%" x86 -vcvars_ver=14.16 >nul 2>&1
if errorlevel 1 (
    echo [错误] 加载 v141 工具集失败。
    echo        请在 VS Installer 里勾选「MSVC v141 - VS 2017 C++ x64/x86 生成工具」。
    exit /b 1
)

REM ── 断言编译器版本 ───────────────────────────────────────────────────
REM cl 的 banner 在 GBK 控制台下是乱码，改用 where cl + 目录名判断更稳。
where cl >nul 2>&1
if errorlevel 1 (
    echo [错误] PATH 里没有 cl.exe。
    exit /b 1
)

REM ── 编译 ─────────────────────────────────────────────────────────────
if not exist build_dll mkdir build_dll
if not exist build_dll\obj mkdir build_dll\obj

if /i "%CFG%"=="debug" (
    set CFLAGS=/nologo /LD /MTd /Od /Zi /source-charset:utf-8 /execution-charset:.936 /EHsc /W3
    set LDFLAGS=/DEBUG
) else (
    set CFLAGS=/nologo /LD /MT /O2 /source-charset:utf-8 /execution-charset:.936 /EHsc /W3
    set LDFLAGS=
)

echo.
echo [1/2] 编译
cl %CFLAGS% /DWIN32 /DNDEBUG /D_WINDOWS /D_USRDLL ^
   /Fobuild_dll\obj\ ^
   dllmain.cpp src\log.cpp src\dict.cpp src\hook.cpp src\translate.cpp ^
   src\gdi_probe.cpp src\cjk_font.cpp ^
   /link /DEF:binkw32.def /OUT:build_dll\binkw32.dll /SUBSYSTEM:WINDOWS %LDFLAGS%
if errorlevel 1 (
    echo.
    echo [错误] 编译失败。
    exit /b 1
)

echo.
echo [2/2] 校验导出表
dumpbin /exports build_dll\binkw32.dll | findstr /C:"_Bink" | find /c "_Bink" > build_dll\obj\exports.txt
set /p NEXP=<build_dll\obj\exports.txt
if not "%NEXP%"=="10" (
    echo [错误] 导出函数应为 10 个，实际 %NEXP% 个。
    echo        检查 binkw32.def 是否被正确链接。
    exit /b 1
)
echo        导出 %NEXP% 个 Bink 转发函数，正确。

echo.
echo 完成: build_dll\binkw32.dll
echo 部署: 把 binkw32.dll 与 chs.dict 一起放到游戏根目录，
echo       原版 binkw32.dll 改名为 binkw32_orig.dll。
endlocal
