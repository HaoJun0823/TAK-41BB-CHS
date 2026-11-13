@echo off
setlocal
set VSDIR=C:\Program Files\Microsoft Visual Studio\18\Community
set MSVCVER=14.16.27023
set SDKVER=10.0.19041.0
set INCLUDE=%VSDIR%\VC\Tools\MSVC\%MSVCVER%\include;C:\Program Files (x86)\Windows Kits\10\Include\%SDKVER%\ucrt;C:\Program Files (x86)\Windows Kits\10\Include\%SDKVER%\um;C:\Program Files (x86)\Windows Kits\10\Include\%SDKVER%\shared
set LIB=%VSDIR%\VC\Tools\MSVC\%MSVCVER%\lib\x86;C:\Program Files (x86)\Windows Kits\10\Lib\%SDKVER%\ucrt\x86;C:\Program Files (x86)\Windows Kits\10\Lib\%SDKVER%\um\x86
set PATH=%VSDIR%\VC\Tools\MSVC\%MSVCVER%\bin\Hostx86\x86;%PATH%
cd /d %~dp0
cl /nologo /LD /MT /O2 /source-charset:utf-8 /execution-charset:.936 /EHsc /W3 /DWIN32 /DNDEBUG /D_WINDOWS /D_USRDLL /Fobuild_dll\obj\ dllmain.cpp src\log.cpp src\dict.cpp src\hook.cpp src\translate.cpp src\gdi_probe.cpp src\cjk_font.cpp src\cd_patch.cpp src\fs_override.cpp src\target.cpp src\web_override.cpp src\proxy_version.cpp /link /DEF:version.def user32.lib gdi32.lib advapi32.lib /OUT:build_dll\version.dll /SUBSYSTEM:WINDOWS
dumpbin /exports build_dll\version.dll > build_dll\obj\exports.txt
