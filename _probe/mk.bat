@echo off
REM ASCII-only build driver.
REM vcvarsall.bat internally calls reg.exe, which the sandbox blacklists, so we
REM set INCLUDE/LIB/PATH by hand against the v141 (14.16) toolset + SDK 19041.
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

cl /nologo /LD /MT /O2 /source-charset:utf-8 /execution-charset:.936 /EHsc /W3 /DWIN32 /DNDEBUG /D_WINDOWS /D_USRDLL ^
   /Fobuild_dll\obj\ ^
   dllmain.cpp src\log.cpp src\dict.cpp src\hook.cpp src\translate.cpp ^
   src\gdi_probe.cpp src\cjk_font.cpp src\cd_patch.cpp ^
   /link /DEF:binkw32.def /OUT:build_dll\binkw32.dll /SUBSYSTEM:WINDOWS ^
   gdi32.lib user32.lib
if errorlevel 1 (
  echo [ERR] compile failed
  exit /b 1
)
echo [OK] build_dll\binkw32.dll
