@echo off
setlocal
set MSVC=C:/Program Files\Microsoft Visual Studio\18\Community\VC\Tools\MSVC\14.16.27023
set SDK=C:/Program Files (x86)\Windows Kits\10
set PATH=%MSVC%\bin\Hostx86\x86;C:/Windows/system32;C:/Windows
set INCLUDE=%MSVC%\include;%SDK%\Include\10.0.19041.0\ucrt;%SDK%\Include\10.0.19041.0\um;%SDK%\Include\10.0.19041.0\shared
set LIB=%MSVC%\lib\x86;%SDK%\Lib\10.0.19041.0\ucrt\x86;%SDK%\Lib\10.0.19041.0\um\x86
cd /d "%~dp0"
cl /nologo /MT /O2 /source-charset:utf-8 /execution-charset:.936 /EHsc vtest.cpp /link /OUT:vtest.exe version.lib
