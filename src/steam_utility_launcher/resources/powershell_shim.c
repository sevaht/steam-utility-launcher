/*
 * Minimal powershell.exe replacement for Wine/Proton prefixes.
 *
 * Wine's real powershell.exe is an unimplemented stub: it never runs the
 * script passed via -C and always exits 0. Some NSIS/electron-builder
 * installers use a specific, narrow family of PowerShell one-liners to
 * detect and close an already-running instance of the app before
 * installing, e.g.:
 *
 *   Get-Command <cmdlet> -ErrorAction SilentlyContinue
 *   Get-ExecutionPolicy -Scope Process
 *   (Get-CimInstance -ClassName Win32_Process |
 *       ? {$_.Path -and $_.Path.StartsWith('<PREFIX>', ...)}).Count -gt 0
 *   Get-CimInstance -ClassName Win32_Process |
 *       ? {$_.Path -and $_.Path.StartsWith('<PREFIX>', ...)} |
 *       % { Stop-Process -Id $_.ProcessId [-Force] }
 *
 * Since Wine's stub always exits 0, these installers permanently believe
 * their own app is running and can never be closed. This program does not
 * implement PowerShell; it pattern-matches this specific script family and
 * answers each one using real Win32 process enumeration, so the checks
 * give correct answers instead of a hardcoded "yes, running".
 *
 * Anything that doesn't match a known pattern exits 1 (a conservative
 * "false"/"not applicable" default) rather than pretending to succeed.
 */
#define _WIN32_WINNT 0x0600

#include <windows.h>
#include <tlhelp32.h>
#include <wchar.h>
#include <wctype.h>
#include <stdlib.h>

static int wchar_prefix_ci(const wchar_t *s, const wchar_t *prefix) {
    while (*prefix) {
        if (!*s || towlower((wint_t)*s) != towlower((wint_t)*prefix)) {
            return 0;
        }
        s++;
        prefix++;
    }
    return 1;
}

/* Extracts the single-quoted string following the first "StartsWith(" in
 * the script. Caller must free() the result. */
static wchar_t *extract_path_prefix(const wchar_t *script) {
    const wchar_t *marker = wcsstr(script, L"StartsWith(");
    if (!marker) {
        return NULL;
    }
    const wchar_t *quote_start = wcschr(marker, L'\'');
    if (!quote_start) {
        return NULL;
    }
    quote_start++;
    const wchar_t *quote_end = wcschr(quote_start, L'\'');
    if (!quote_end) {
        return NULL;
    }
    size_t len = (size_t)(quote_end - quote_start);
    wchar_t *out = (wchar_t *)malloc((len + 1) * sizeof(wchar_t));
    if (!out) {
        return NULL;
    }
    wcsncpy(out, quote_start, len);
    out[len] = 0;
    return out;
}

/* Enumerates running processes, matching full image paths against `prefix`
 * (case-insensitive). If `terminate` is set, matching processes are
 * killed. Returns non-zero via `*found_any` if any process matched. */
static void scan_processes(const wchar_t *prefix, int terminate, int *found_any) {
    *found_any = 0;
    HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    if (snap == INVALID_HANDLE_VALUE) {
        return;
    }
    DWORD self_pid = GetCurrentProcessId();
    PROCESSENTRY32W entry;
    entry.dwSize = sizeof(entry);
    if (Process32FirstW(snap, &entry)) {
        do {
            if (entry.th32ProcessID == 0 || entry.th32ProcessID == self_pid) {
                continue;
            }
            DWORD access = PROCESS_QUERY_LIMITED_INFORMATION;
            if (terminate) {
                access |= PROCESS_TERMINATE;
            }
            HANDLE proc = OpenProcess(access, FALSE, entry.th32ProcessID);
            if (!proc) {
                continue;
            }
            wchar_t path[MAX_PATH];
            DWORD size = MAX_PATH;
            if (QueryFullProcessImageNameW(proc, 0, path, &size)) {
                if (wchar_prefix_ci(path, prefix)) {
                    *found_any = 1;
                    if (terminate) {
                        TerminateProcess(proc, 1);
                    }
                }
            }
            CloseHandle(proc);
        } while (Process32NextW(snap, &entry));
    }
    CloseHandle(snap);
}

static const wchar_t *find_script_argument(int argc, wchar_t **argv) {
    for (int i = 1; i < argc; i++) {
        if (wchar_prefix_ci(argv[i], L"-c") && i + 1 < argc) {
            return argv[i + 1];
        }
    }
    return argc > 1 ? argv[argc - 1] : NULL;
}

int wmain(int argc, wchar_t **argv) {
    const wchar_t *script = find_script_argument(argc, argv);
    if (!script) {
        return 0;
    }

    if (wcsstr(script, L"Stop-Process")) {
        wchar_t *prefix = extract_path_prefix(script);
        if (prefix) {
            int found;
            scan_processes(prefix, 1, &found);
            free(prefix);
        }
        return 0;
    }

    if (wcsstr(script, L"Count -gt 0")) {
        wchar_t *prefix = extract_path_prefix(script);
        if (!prefix) {
            return 1;
        }
        int found;
        scan_processes(prefix, 0, &found);
        free(prefix);
        return found ? 0 : 1;
    }

    if (wcsstr(script, L"Get-Command")) {
        return 0;
    }

    if (wcsstr(script, L"Get-ExecutionPolicy")) {
        return 0;
    }

    return 1;
}
