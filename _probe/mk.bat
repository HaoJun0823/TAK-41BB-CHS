@echo off
REM ASCII-only build driver.
REM vcvarsall.bat internally calls reg.exe, which the sandbox blacklists, so we
REM set INCLUDE/LIB/PATH by hand against the v141 (14.16) toolset + SDK 19041.
REM
REM Builds two proxies from the same sources:
REM   build_dll\version.dll   <- default host (17 exports, forwards to SysWOW64)
REM   build_dll\binkw32.dll   <- legacy host (10 forwards to binkw32_orig.dll)
setlocal
set ROOT=%~dp0..
cd /d "%ROOT%"

set MSVC=C:\Program Files\Microsoft Visual Studio\18\Community\VC\Tools\MSVC\14.16.27023
set SDK=C:\Program Files (x86)\Windows Kits\10

set PATH=%MSVC%\bin\Hostx86\x86;C:\Windows\system32;C:\Windows
set INCLUDE=%MSVC%\include;%SDK%\Include\10.0.19041.0\ucrt;%SDK%\Include\10.0.19041.0\um;%SDK%\Include\10.0.19041.0\shared
set LIB=%MSVC%\lib\x86;%SDK%\Lib\10.0.19041.0\ucrt\x86;%SDK%\Lib\10.0.19041.0\um\x86

if not exist build_dll mkdir build_dll
if not exist build_dll\obj mkdir build_dll\obj

set SRC=dllmain.cpp src\log.cpp src\dict.cpp src\hook.cpp src\translate.cpp ^
   src\gdi_probe.cpp src\cjk_font.cpp src\cd_patch.cpp src\target.cpp ^
   src\proxy_version.cpp

set CFLAGS=/nologo /LD /MT /O2 /source-charset:utf-8 /execution-charset:.936 /EHsc /W3 /DWIN32 /DNDEBUG /D_WINDOWS /D_USRDLL /Fobuild_dll\obj\

echo [1/2] version.dll (default proxy)
cl %CFLAGS% %SRC% /link /DEF:version.def /OUT:build_dll\version.dll /SUBSYSTEM:WINDOWS gdi32.lib user32.lib
if errorlevel 1 (
  echo [ERR] version.dll build failed
  exit /b 1
)

echo [2/2] binkw32.dll (legacy proxy)
cl %CFLAGS% %SRC% /link /DEF:binkw32.def /OUT:build_dll\binkw32.dll /SUBSYSTEM:WINDOWS gdi32.lib user32.lib
if errorlevel 1 (
  echo [ERR] binkw32.dll build failed
  exit /b 1
)

echo [OK] build_dll\version.dll  build_dll\binkw32.dll
