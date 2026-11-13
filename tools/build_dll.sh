#!/usr/bin/env bash
# ============================================================================
# 用 msvc-wine（MSVC 14.16 / v141 + Win10 SDK 26100）编译 version.dll
# ----------------------------------------------------------------------------
# 与 build_version.bat 是同一组开关：/LD /MT /O2 /EHsc /W3 + GBK 执行字符集。
# 差异只在调用方式 —— CI 没有 Windows，cl.exe / link.exe 由 wine 跑。
#
# 为什么不走 MSBuild
#   工程只编十来个 cpp，直调 cl 比 msbuild 快，也避开 .vcxproj 的 MSBuild
#   版本差异。工具链版本由 install.sh 生成的 wrapper 固定；产物再经
#   tools/check_dll.py 验证 PE 头的 linker 版本必须是 14.16，防止静默回退
#   到新版工具集（那会引入新版 CRT 依赖，老系统上直接加载失败）。
#
# 用法（CI）
#   MSVC_WINE_PREFIX=$PWD/.msvc tools/build_dll.sh
# 用法（本机 Linux + msvc-wine）
#   同上；产物在 build_dll/version.dll
# ============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [ -z "${MSVC_WINE_PREFIX:-}" ]; then
  echo "!! 需要 MSVC_WINE_PREFIX（msvc-wine 安装目录）" >&2
  exit 1
fi
export PATH="${MSVC_WINE_PREFIX}/bin/x86:${PATH}"

SOURCES=(
  dllmain.cpp
  src/log.cpp
  src/dict.cpp
  src/hook.cpp
  src/translate.cpp
  src/gdi_probe.cpp
  src/cjk_font.cpp
  src/cd_patch.cpp
  src/fs_override.cpp
  src/target.cpp
  src/web_override.cpp
  src/proxy_version.cpp
)

rm -rf build_dll
mkdir -p build_dll/obj

echo "==> cl $(cl 2>&1 | head -1)"
cl /nologo /LD /MT /O2 /source-charset:utf-8 /execution-charset:.936 /EHsc /W3 \
   /DWIN32 /DNDEBUG /D_WINDOWS /D_USRDLL \
   /Fobuild_dll/obj/ \
   "${SOURCES[@]}" \
   /link /DEF:version.def user32.lib gdi32.lib advapi32.lib \
   /OUT:build_dll/version.dll /SUBSYSTEM:WINDOWS

if [ ! -f build_dll/version.dll ]; then
  echo "!! 没有产出 build_dll/version.dll" >&2
  exit 1
fi
ls -l build_dll/version.dll
