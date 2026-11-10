#!/usr/bin/env bash
# Rebuilds the prebuilt dinput8.dll of `ds2 death-lag-fix dll` from
# src/steam_utility_launcher/resources/ds2_death_lag_dinput8.c. Only needed when
# that source changes; end users never need this or a mingw-w64
# cross-compiler, since the built DLL is shipped with the package.
#
# On Arch Linux, the required compiler comes from: mingw-w64-gcc
set -euo pipefail
cd "$(dirname "$0")"

compiler=x86_64-w64-mingw32-gcc
if ! command -v "$compiler" >/dev/null; then
    echo "Missing cross-compiler: $compiler" >&2
    exit 1
fi
resources=src/steam_utility_launcher/resources
output="$resources/ds2_death_lag_dinput8.dll"
# No C runtime, no start files and no padding: the DLL is only a few hundred
# bytes of code.
"$compiler" -shared -nostdlib -nostartfiles -Os -Wall -Wextra -s \
    -ffreestanding -fno-asynchronous-unwind-tables -fno-ident \
    -fno-tree-loop-distribute-patterns \
    -Wl,--entry,DllMain -Wl,--gc-sections \
    -Wl,--file-alignment,512 -Wl,--section-alignment,512 \
    -Wl,--no-insert-timestamp -Wl,--disable-reloc-section \
    -o "$output" "$resources/ds2_death_lag_dinput8.c" -lkernel32
echo "Built $output ($(stat -c %s "$output") bytes)"
