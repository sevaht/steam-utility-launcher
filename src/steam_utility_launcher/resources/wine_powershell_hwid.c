/*
 * powershell.exe stand-in for Wine/Proton prefixes: answers ROTK Launcher's
 * hardware fingerprint query with this machine's REAL values.
 *
 * Wine's powershell.exe is an unimplemented stub, so ROTK Launcher's
 * fingerprint query (PowerShell/WMI) comes back empty, which the ROTK account
 * service now refuses (hwid_required). This program is dropped in at
 * ...\WindowsPowerShell\v1.0\powershell.exe for the life of a launcher run.
 *
 * It does not implement PowerShell and never executes the script it is given.
 * It only pattern-matches the exact shape ROTK Launcher generates
 *
 *   $ErrorActionPreference = 'Stop'
 *   $r = @{}
 *   try { $v = [string](<expr>); if ($v) { $r['<slot>'] = $v } } catch {}
 *   ...
 *   $r | ConvertTo-Json -Compress
 *
 * passed as -EncodedCommand, and reads only the slot names out of it.
 *
 * Every invocation is appended to a log (SUL_POWERSHELL_LOG, else
 * %TEMP%\sul-powershell-commands.log) with its verdict:
 *
 *   answered           the exact fingerprint query above (only slot names are
 *                      logged, never the hardware values), or one of the
 *                      installer's "is the app running / close it" one-liners
 *                      (see answer_instance_check), one of the diagnostics
 *                      commands (see answer_diagnostic), or one of the two TPM
 *                      proofs (see answer_tpm), answered for real.
 *   declined           anything else: exit 1 with no output, exactly as a
 *                      missing PowerShell would (the app's TPM and diagnostics
 *                      code rely on that).
 *   HWID-UNRECOGNIZED  a command that references hardware-identifier data
 *                      (the WMI classes and registry value the fingerprint
 *                      reads) but does not match exactly. It is refused, and
 *                      made impossible to miss: it is logged, written to
 *                      stderr, shown in a message box, and exits 190. This
 *                      means ROTK changed the query and this program must be
 *                      updated; it never guesses at a changed format.
 *
 * Rules:
 *   - only genuine values read from this machine are reported. A value that
 *     can't be read (for example root-only DMI files) is omitted; nothing is
 *     invented, defaulted, or copied from another slot;
 *   - the host's Linux filesystem is read through Wine's Z: drive.
 */
#define _WIN32_WINNT 0x0600
#define WIN32_LEAN_AND_MEAN
#define COBJMACROS
#include <windows.h>
#include <objbase.h>
#include <wbemcli.h>
#include <tlhelp32.h>
#include <ctype.h>
#include <stdint.h>

#include "tpm2.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>

#define VALUE_MAX 4096
#define SLOT_MAX 64
#define SLOT_NAME_MAX 48
#define CPUINFO_MAX (256 * 1024)

/* ------------------------------------------------------------ small utils */

static void trim(char *s) {
    size_t len = strlen(s);
    while (len > 0 && (s[len - 1] == '\n' || s[len - 1] == '\r' ||
                       s[len - 1] == ' ' || s[len - 1] == '\t' || s[len - 1] == 0))
        s[--len] = 0;
    size_t lead = 0;
    while (s[lead] == ' ' || s[lead] == '\t' || s[lead] == '\n' || s[lead] == '\r') lead++;
    if (lead) memmove(s, s + lead, strlen(s + lead) + 1);
}

/* Reads up to cap-1 bytes of a file (a /sys or /proc file, via Z:). */
static int read_file(const wchar_t *path, char *out, size_t cap) {
    HANDLE h = CreateFileW(path, GENERIC_READ,
                           FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, NULL,
                           OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) return 0;
    size_t total = 0;
    for (;;) {
        DWORD got = 0;
        if (total + 1 >= cap) break;
        if (!ReadFile(h, out + total, (DWORD)(cap - 1 - total), &got, NULL)) {
            if (total == 0) { CloseHandle(h); return 0; }
            break;
        }
        if (got == 0) break;
        total += got;
    }
    CloseHandle(h);
    out[total] = 0;
    return 1;
}

static int read_value(const wchar_t *path, char *out) {
    if (!read_file(path, out, VALUE_MAX)) return 0;
    trim(out);
    return out[0] != 0;
}

static int read_dmi(const wchar_t *name, char *out) {
    wchar_t path[MAX_PATH];
    swprintf(path, MAX_PATH, L"Z:\\sys\\class\\dmi\\id\\%ls", name);
    return read_value(path, out);
}

/* ---------------------------------------------------------------- readers */

typedef int (*reader_fn)(char *out);

static int r_machine_guid(char *out) {
    HKEY key;
    if (RegOpenKeyExW(HKEY_LOCAL_MACHINE, L"SOFTWARE\\Microsoft\\Cryptography", 0,
                      KEY_READ | KEY_WOW64_64KEY, &key) != ERROR_SUCCESS)
        return 0;
    wchar_t wide[128];
    DWORD size = sizeof wide, type = 0;
    LONG rc = RegQueryValueExW(key, L"MachineGuid", NULL, &type, (LPBYTE)wide, &size);
    RegCloseKey(key);
    if (rc != ERROR_SUCCESS || type != REG_SZ) return 0;
    wide[127] = 0;
    return WideCharToMultiByte(CP_UTF8, 0, wide, -1, out, VALUE_MAX, NULL, NULL) > 0 &&
           out[0] != 0;
}

static int r_volume_serial(char *out) {
    DWORD serial = 0;
    if (!GetVolumeInformationW(L"C:\\", NULL, 0, &serial, NULL, NULL, NULL, 0)) return 0;
    snprintf(out, VALUE_MAX, "%08lX", (unsigned long)serial);
    return 1;
}

static int r_smbios_uuid(char *out) { return read_dmi(L"product_uuid", out); }
static int r_baseboard_serial(char *out) { return read_dmi(L"board_serial", out); }
static int r_baseboard_product(char *out) { return read_dmi(L"board_name", out); }
static int r_bios_serial(char *out) { return read_dmi(L"product_serial", out); }
static int r_bios_version(char *out) { return read_dmi(L"bios_version", out); }
static int r_enclosure_serial(char *out) { return read_dmi(L"chassis_serial", out); }
static int r_system_sku(char *out) { return read_dmi(L"product_sku", out); }

static int r_bios_release_date(char *out) {
    char raw[VALUE_MAX];
    unsigned month, day, year;
    if (!read_dmi(L"bios_date", raw)) return 0;
    if (sscanf(raw, "%2u/%2u/%4u", &month, &day, &year) != 3) return 0;
    snprintf(out, VALUE_MAX, "%04u-%02u-%02u", year, month, day);
    return 1;
}

/* nvme<N>n<M>, sd<letters>, vd<letters> */
static int is_disk_name(const wchar_t *n) {
    if (wcsncmp(n, L"nvme", 4) == 0) {
        const wchar_t *p = n + 4;
        if (!(*p >= L'0' && *p <= L'9')) return 0;
        while (*p >= L'0' && *p <= L'9') p++;
        if (*p++ != L'n') return 0;
        if (!(*p >= L'0' && *p <= L'9')) return 0;
        while (*p >= L'0' && *p <= L'9') p++;
        return *p == 0;
    }
    if (wcsncmp(n, L"sd", 2) == 0 || wcsncmp(n, L"vd", 2) == 0) {
        const wchar_t *p = n + 2;
        if (!(*p >= L'a' && *p <= L'z')) return 0;
        while (*p >= L'a' && *p <= L'z') p++;
        return *p == 0;
    }
    return 0;
}

/* First whole disk by name, the analogue of Win32_DiskDrive Index 0. */
static int first_disk(wchar_t *out /* MAX_PATH */) {
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileW(L"Z:\\sys\\block\\*", &fd);
    int found = 0;
    if (h == INVALID_HANDLE_VALUE) return 0;
    do {
        if (is_disk_name(fd.cFileName) && (!found || wcscmp(fd.cFileName, out) < 0)) {
            wcsncpy(out, fd.cFileName, MAX_PATH - 1);
            out[MAX_PATH - 1] = 0;
            found = 1;
        }
    } while (FindNextFileW(h, &fd));
    FindClose(h);
    return found;
}

static int disk_attribute(const wchar_t *attribute, char *out) {
    wchar_t disk[MAX_PATH], path[MAX_PATH * 2];
    if (!first_disk(disk)) return 0;
    swprintf(path, MAX_PATH * 2, L"Z:\\sys\\block\\%ls\\device\\%ls", disk, attribute);
    return read_value(path, out);
}

static int r_disk_serial(char *out) { return disk_attribute(L"serial", out); }
static int r_disk_model(char *out) { return disk_attribute(L"model", out); }
static int r_disk_firmware(char *out) { return disk_attribute(L"firmware_rev", out); }

static int r_cpu_name(char *out) {
    char *buffer = (char *)malloc(CPUINFO_MAX);
    int ok = 0;
    if (!buffer) return 0;
    if (read_file(L"Z:\\proc\\cpuinfo", buffer, CPUINFO_MAX)) {
        char *line = strstr(buffer, "model name");
        char *colon = line ? strchr(line, ':') : NULL;
        if (colon) {
            char *end = strchr(colon, '\n');
            size_t len = end ? (size_t)(end - colon - 1) : strlen(colon + 1);
            if (len >= VALUE_MAX) len = VALUE_MAX - 1;
            memcpy(out, colon + 1, len);
            out[len] = 0;
            trim(out);
            ok = out[0] != 0;
        }
    }
    free(buffer);
    return ok;
}

static int compare_strings(const void *a, const void *b) {
    return strcmp(*(char *const *)a, *(char *const *)b);
}

/* Physical adapters only: they have a backing device node. */
static int r_mac_addresses(char *out) {
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileW(L"Z:\\sys\\class\\net\\*", &fd);
    char *macs[32];
    int count = 0;
    if (h == INVALID_HANDLE_VALUE) return 0;
    do {
        wchar_t path[MAX_PATH * 2];
        char mac[VALUE_MAX];
        int duplicate = 0;
        if (fd.cFileName[0] == L'.' || count >= 32) continue;
        swprintf(path, MAX_PATH * 2, L"Z:\\sys\\class\\net\\%ls\\device", fd.cFileName);
        if (GetFileAttributesW(path) == INVALID_FILE_ATTRIBUTES) continue;
        swprintf(path, MAX_PATH * 2, L"Z:\\sys\\class\\net\\%ls\\address", fd.cFileName);
        if (!read_value(path, mac) || strcmp(mac, "00:00:00:00:00:00") == 0) continue;
        for (int i = 0; i < count; i++)
            if (strcmp(macs[i], mac) == 0) duplicate = 1;
        if (!duplicate) macs[count++] = _strdup(mac);
    } while (FindNextFileW(h, &fd));
    FindClose(h);
    if (count == 0) return 0;
    qsort(macs, (size_t)count, sizeof macs[0], compare_strings);
    out[0] = 0;
    for (int i = 0; i < count; i++) {
        if (i) strncat(out, ",", VALUE_MAX - strlen(out) - 1);
        strncat(out, macs[i], VALUE_MAX - strlen(out) - 1);
        free(macs[i]);
    }
    return 1;
}

/* Every slot ROTK Launcher 2.0.24 can ask for, with the PowerShell expression it
 * uses to read it, copied verbatim from its machine-identity.ts (SLOT_READERS). The
 * query is only answered if every line's expression is byte-for-byte the one
 * listed here, so a changed expression is refused instead of answered with a value
 * computed for the old one. A NULL reader means this stand-in cannot supply that
 * slot (it is omitted, as on a machine whose WMI has nothing to report). */
static const struct { const char *slot; const char *expr; reader_fn read; } READERS[] = {
    {"machine_guid",
     "(Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Cryptography' -Name MachineGuid).MachineGuid",
     r_machine_guid},
    {"smbios_uuid",
     "(Get-CimInstance Win32_ComputerSystemProduct).UUID",
     r_smbios_uuid},
    {"baseboard_serial",
     "(Get-CimInstance Win32_BaseBoard).SerialNumber",
     r_baseboard_serial},
    {"baseboard_product",
     "(Get-CimInstance Win32_BaseBoard).Product",
     r_baseboard_product},
    {"disk_serial",
     "(Get-CimInstance Win32_DiskDrive | Where-Object { $_.Index -eq 0 } | Select-Object -First 1).SerialNumber",
     r_disk_serial},
    {"disk_model",
     "(Get-CimInstance Win32_DiskDrive | Where-Object { $_.Index -eq 0 } | Select-Object -First 1).Model",
     r_disk_model},
    {"disk_firmware",
     "(Get-CimInstance Win32_DiskDrive | Where-Object { $_.Index -eq 0 } | Select-Object -First 1).FirmwareRevision",
     r_disk_firmware},
    {"volume_serial",
     "(Get-CimInstance Win32_LogicalDisk -Filter \"DeviceID='$($env:SystemDrive)'\").VolumeSerialNumber",
     r_volume_serial},
    {"bios_serial",
     "(Get-CimInstance Win32_BIOS).SerialNumber",
     r_bios_serial},
    {"bios_version",
     "(Get-CimInstance Win32_BIOS).SMBIOSBIOSVersion",
     r_bios_version},
    {"bios_release_date",
     "(Get-CimInstance Win32_BIOS).ReleaseDate.ToString('yyyy-MM-dd')",
     r_bios_release_date},
    {"cpu_processor_id",
     "(Get-CimInstance Win32_Processor | Select-Object -First 1).ProcessorId",
     NULL},
    {"cpu_name",
     "(Get-CimInstance Win32_Processor | Select-Object -First 1).Name",
     r_cpu_name},
    {"ram_module_serials",
     "((Get-CimInstance Win32_PhysicalMemory | ForEach-Object { $_.SerialNumber } | Where-Object { $_ } | Sort-Object) -join ',')",
     NULL},
    {"gpu_pnp_device_id",
     "(Get-CimInstance Win32_VideoController | Select-Object -First 1).PNPDeviceID",
     NULL},
    {"gpu_name",
     "(Get-CimInstance Win32_VideoController | Select-Object -First 1).Name",
     NULL},
    {"mac_addresses",
     "((Get-CimInstance Win32_NetworkAdapter -Filter 'PhysicalAdapter=True' | ForEach-Object { $_.MACAddress } | Where-Object { $_ } | Sort-Object -Unique) -join ',')",
     r_mac_addresses},
    {"monitor_edid_serials",
     "((Get-CimInstance -Namespace root/wmi -ClassName WmiMonitorID | ForEach-Object { -join ($_.SerialNumberID | Where-Object { $_ -ne 0 } | ForEach-Object { [char]$_ }) } | Where-Object { $_ } | Sort-Object) -join ',')",
     NULL},
    {"os_install_date",
     "(Get-CimInstance Win32_OperatingSystem).InstallDate.ToString('yyyy-MM-dd')",
     NULL},
    {"enclosure_serial",
     "(Get-CimInstance Win32_SystemEnclosure | Select-Object -First 1).SerialNumber",
     r_enclosure_serial},
    {"system_sku",
     "(Get-CimInstance Win32_ComputerSystem).SystemSKUNumber",
     r_system_sku},
};

/* ------------------------------------------------ query recognition/decode */

static int b64_value(unsigned char c) {
    if (c >= 'A' && c <= 'Z') return c - 'A';
    if (c >= 'a' && c <= 'z') return c - 'a' + 26;
    if (c >= '0' && c <= '9') return c - '0' + 52;
    if (c == '+') return 62;
    if (c == '/') return 63;
    return -1;
}

/* Returns decoded length, or -1 if not valid base64. */
static long b64_decode(const wchar_t *in, unsigned char *out, size_t cap) {
    size_t len = wcslen(in), n = 0;
    unsigned int acc = 0;
    int bits = 0;
    while (len > 0 && in[len - 1] == L'=') len--;
    for (size_t i = 0; i < len; i++) {
        int v = in[i] < 128 ? b64_value((unsigned char)in[i]) : -1;
        if (v < 0) return -1;
        acc = (acc << 6) | (unsigned)v;
        bits += 6;
        if (bits >= 8) {
            bits -= 8;
            if (n >= cap) return -1;
            out[n++] = (unsigned char)((acc >> bits) & 0xFF);
        }
    }
    return (long)n;
}

static const char HEAD[] = "$ErrorActionPreference = 'Stop'\n$r = @{}\n";
static const char TAIL[] = "$r | ConvertTo-Json -Compress";
static const char LINE_START[] = "try { $v = [string](";
static const char SLOT_MARK[] = "); if ($v) { $r['";
static const char LINE_END[] = "'] = $v } } catch {}";

typedef struct { char name[SLOT_NAME_MAX]; const char *expr; size_t expr_len; } parsed_slot;

#define REJECT(text) do { snprintf(why, wcap, "%s", (text)); return 0; } while (0)

/* Fills slots[] from the script if it has exactly the launcher's scaffold. The
 * expressions are checked separately (see answer_hwid). On rejection `why` says
 * what didn't match. */
static int parse_query(char *script, parsed_slot *slots, int *count, char *why, size_t wcap) {
    char *w = script;
    size_t len;
    char *line;
    *count = 0;
    for (char *r = script; *r; r++) if (*r != '\r') *w++ = *r;   /* drop CRs */
    *w = 0;
    len = strlen(script);
    if (len < sizeof HEAD - 1 + sizeof TAIL - 1 ||
        strncmp(script, HEAD, sizeof HEAD - 1) != 0)
        REJECT("the script does not start with the expected header");
    if (strcmp(script + len - (sizeof TAIL - 1), TAIL) != 0)
        REJECT("the script does not end with the expected ConvertTo-Json line");
    script[len - (sizeof TAIL - 1)] = 0;

    line = script + sizeof HEAD - 1;
    while (*line) {
        char *end = strchr(line, '\n');
        if (end) *end = 0;
        if (*line) {
            char *mark, *slot_start, *slot_end;
            size_t slot_len, line_len = strlen(line), tail_len = sizeof LINE_END - 1;
            if (strncmp(line, LINE_START, sizeof LINE_START - 1) != 0)
                REJECT("a line does not start like a slot reader");
            mark = strstr(line, SLOT_MARK);
            if (!mark) REJECT("a line has no slot assignment");
            slot_start = mark + sizeof SLOT_MARK - 1;
            if (line_len < tail_len || strcmp(line + line_len - tail_len, LINE_END) != 0)
                REJECT("a line does not end like a slot reader");
            slot_end = line + line_len - tail_len;
            if (slot_end < slot_start) REJECT("a line is malformed");
            slot_len = (size_t)(slot_end - slot_start);
            if (slot_len == 0 || slot_len >= SLOT_NAME_MAX || *count >= SLOT_MAX)
                REJECT("a slot name is missing or too long, or there are too many slots");
            for (size_t i = 0; i < slot_len; i++) {
                char c = slot_start[i];
                if (!((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_'))
                    REJECT("a slot name has unexpected characters");
            }
            memcpy(slots[*count].name, slot_start, slot_len);
            slots[*count].name[slot_len] = 0;
            slots[*count].expr = line + sizeof LINE_START - 1;
            slots[*count].expr_len = (size_t)(mark - slots[*count].expr);
            (*count)++;
        }
        if (!end) break;
        line = end + 1;
    }
    return 1;
}

/* ------------------------------------------------------------------ output */

typedef struct { char *data; size_t len, cap; } buffer;

static void put(buffer *b, const char *text, size_t n) {
    if (b->len + n + 1 > b->cap) {
        b->cap = (b->len + n + 1) * 2;
        b->data = (char *)realloc(b->data, b->cap);
    }
    memcpy(b->data + b->len, text, n);
    b->len += n;
    b->data[b->len] = 0;
}

static void put_json_string(buffer *b, const char *s) {
    put(b, "\"", 1);
    for (; *s; s++) {
        unsigned char c = (unsigned char)*s;
        char esc[8];
        if (c == '"' || c == '\\') { esc[0] = '\\'; esc[1] = (char)c; put(b, esc, 2); }
        else if (c < 0x20) { snprintf(esc, sizeof esc, "\\u%04x", c); put(b, esc, 6); }
        else put(b, (const char *)&c, 1);
    }
    put(b, "\"", 1);
}

/* ---------------------------------------- classification, logging, alarms */

#define EXIT_DECLINED 1
#define EXIT_HWID_UNRECOGNIZED 190
#define ENV_LOG L"SUL_POWERSHELL_LOG"
#define ENV_NO_POPUP L"SUL_POWERSHELL_NO_POPUP"
#define POPUP_FLAG L"--sul-show-error"
#define PREPARE_FLAG L"--sul-tpm-prepare"
#define LOG_NAME L"sul-powershell-commands.log"
#define LOG_MAX_BYTES (2 * 1024 * 1024)
#define PATH_CAP (MAX_PATH * 2)

/* Hardware-identifier data: the WMI classes and registry value the fingerprint
 * reads. A command mentioning any of these is a hardware-ID command. Chosen so
 * the non-hardware commands the app legitimately sends (installer probes, TPM,
 * diagnostics: Win32_OperatingSystem, Win32_VideoController, Get-WinEvent, ...)
 * never match. */
static const char *const HWID_MARKERS[] = {
    "MachineGuid", "PNPDeviceID", "MACAddress",
    "Win32_ComputerSystem", "Win32_BaseBoard", "Win32_BIOS", "Win32_DiskDrive",
    "Win32_Processor", "Win32_PhysicalMemory", "Win32_SystemEnclosure",
    "Win32_NetworkAdapter", "Win32_LogicalDisk", "WmiMonitorID",
};

/* PowerShell is case-insensitive, so the checks are too. */
static const char *ci_strstr(const char *hay, const char *needle) {
    size_t n = strlen(needle);
    if (n == 0) return hay;
    for (; *hay; hay++)
        if (_strnicmp(hay, needle, n) == 0) return hay;
    return NULL;
}

static int is_hwid_related(const char *text) {
    /* The fingerprint scaffold itself, even if its readers were rewritten. */
    if (ci_strstr(text, "$r = @{}") && ci_strstr(text, "ConvertTo-Json") &&
        ci_strstr(text, "$r['"))
        return 1;
    for (size_t i = 0; i < sizeof HWID_MARKERS / sizeof HWID_MARKERS[0]; i++)
        if (ci_strstr(text, HWID_MARKERS[i])) return 1;
    return 0;
}

static void put_str(buffer *b, const char *s) { put(b, s, strlen(s)); }

static void utf8_from_wide(const wchar_t *w, char *out, int cap) {
    if (WideCharToMultiByte(CP_UTF8, 0, w, -1, out, cap, NULL, NULL) <= 0) out[0] = 0;
}

static void write_std(DWORD which, const char *text) {
    DWORD n = 0;
    WriteFile(GetStdHandle(which), text, (DWORD)strlen(text), &n, NULL);
}

static void log_path(wchar_t *out) {
    DWORD n = GetEnvironmentVariableW(ENV_LOG, out, PATH_CAP);
    if (n == 0 || n >= PATH_CAP) {
        wchar_t tmp[MAX_PATH];
        GetTempPathW(MAX_PATH, tmp);
        swprintf(out, PATH_CAP, L"%ls%ls", tmp, LOG_NAME);
    }
}

/* Where a person can open it: "Z:\home\x" is /home/x on the host. */
static void display_path(const wchar_t *win, char *out, int cap) {
    char narrow[PATH_CAP * 3];
    utf8_from_wide(win, narrow, sizeof narrow);
    if ((narrow[0] == 'Z' || narrow[0] == 'z') && narrow[1] == ':') {
        snprintf(out, (size_t)cap, "%s", narrow + 2);
        for (char *c = out; *c; c++) if (*c == '\\') *c = '/';
    } else {
        snprintf(out, (size_t)cap, "%s", narrow);
    }
}

static void log_command(const char *verdict, const char *cmdline, const char *text,
                        const char *note) {
    wchar_t path[PATH_CAP], old[PATH_CAP];
    WIN32_FILE_ATTRIBUTE_DATA info;
    SYSTEMTIME t;
    char head[256];
    buffer b = {0};
    HANDLE h;
    DWORD written = 0;

    log_path(path);
    /* Keep it bounded: one previous generation, then start over. */
    if (GetFileAttributesExW(path, GetFileExInfoStandard, &info) &&
        info.nFileSizeHigh == 0 && info.nFileSizeLow > LOG_MAX_BYTES) {
        swprintf(old, PATH_CAP, L"%ls.1", path);
        MoveFileExW(path, old, MOVEFILE_REPLACE_EXISTING);
    }
    GetSystemTime(&t);
    snprintf(head, sizeof head, "==== %04d-%02d-%02dT%02d:%02d:%02dZ pid=%lu verdict=%s ====\n",
             t.wYear, t.wMonth, t.wDay, t.wHour, t.wMinute, t.wSecond,
             (unsigned long)GetCurrentProcessId(), verdict);
    put_str(&b, head);
    put_str(&b, "command line: ");
    put_str(&b, cmdline);
    put_str(&b, "\n");
    if (note && *note) { put_str(&b, note); put_str(&b, "\n"); }
    put_str(&b, "command text:\n");
    put_str(&b, text ? text : "(none: not a -Command/-C/-EncodedCommand invocation)");
    put_str(&b, "\n\n");

    h = CreateFileW(path, FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE, NULL,
                    OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h != INVALID_HANDLE_VALUE) {
        WriteFile(h, b.data, (DWORD)b.len, &written, NULL);
        CloseHandle(h);
    }
    free(b.data);
}

/* The message box runs as a separate detached process so it outlives this one:
 * the app kills a PowerShell that takes longer than a few seconds. */
static int show_error_popup(const wchar_t *logfile) {
    char shown[PATH_CAP * 3], msg[PATH_CAP * 3 + 1024];
    wchar_t wide[PATH_CAP * 3 + 1024];
    display_path(logfile, shown, sizeof shown);
    snprintf(msg, sizeof msg,
        "The PowerShell stand-in from steam-utility-launcher received a hardware-ID "
        "command that it does not recognise, and refused it.\n\n"
        "This usually means ROTK changed how its launcher collects hardware "
        "information. The stand-in has to be updated to match; until then the game "
        "will most likely reject the launch (hwid_required).\n\n"
        "The command was logged to:\n%s", shown);
    if (MultiByteToWideChar(CP_UTF8, 0, msg, -1, wide, (int)(sizeof wide / sizeof wide[0])) <= 0)
        return 1;
    MessageBoxW(NULL, wide, L"ROTK PowerShell stand-in: unrecognized hardware-ID command",
                MB_OK | MB_ICONERROR | MB_TOPMOST | MB_SETFOREGROUND);
    return 0;
}

static void spawn_popup(const wchar_t *logfile) {
    wchar_t self[MAX_PATH], line[PATH_CAP + MAX_PATH + 64];
    STARTUPINFOW si;
    PROCESS_INFORMATION pi;
    DWORD n = GetModuleFileNameW(NULL, self, MAX_PATH);
    if (n == 0 || n >= MAX_PATH) return;
    swprintf(line, sizeof line / sizeof line[0], L"\"%ls\" %ls \"%ls\"", self, POPUP_FLAG, logfile);
    ZeroMemory(&si, sizeof si);
    si.cb = sizeof si;
    if (CreateProcessW(self, line, NULL, NULL, FALSE, DETACHED_PROCESS, NULL, NULL, &si, &pi)) {
        CloseHandle(pi.hProcess);
        CloseHandle(pi.hThread);
    }
}

static int fail_loudly(const char *cmdline, const char *text, const char *why) {
    wchar_t path[PATH_CAP];
    char shown[PATH_CAP * 3], msg[PATH_CAP * 3 + 512], note[8192];
    log_path(path);
    snprintf(note, sizeof note,
             "note: this command references hardware-ID data but does not match the exact "
             "query the stand-in implements, so it was refused.\nreason: %s",
             (why && *why) ? why : "it is not the fingerprint query (it uses hardware-ID data another way)");
    log_command("HWID-UNRECOGNIZED", cmdline, text, note);
    display_path(path, shown, sizeof shown);
    snprintf(msg, sizeof msg,
        "powershell.exe stand-in (steam-utility-launcher): REFUSED an unrecognized "
        "hardware-ID command.\nIt was logged to: %s\nROTK probably changed its hardware "
        "query; the stand-in must be updated to match.\n", shown);
    write_std(STD_ERROR_HANDLE, msg);
    if (GetEnvironmentVariableW(ENV_NO_POPUP, NULL, 0) == 0) spawn_popup(path);
    return EXIT_HWID_UNRECOGNIZED;
}

/* ------------------------------------------------------ command extraction */

/* UTF-8 script text out of an -EncodedCommand argument, or NULL. */
static char *decode_encoded(const wchar_t *encoded) {
    size_t wcap = wcslen(encoded) + 8;
    unsigned char *raw = (unsigned char *)calloc(wcap, 1);
    long raw_len;
    int narrow_cap, n;
    char *script;
    if (!raw) return NULL;
    raw_len = b64_decode(encoded, raw, wcap);
    if (raw_len <= 0 || raw_len % 2) { free(raw); return NULL; }
    narrow_cap = (int)raw_len * 3 + 8;
    script = (char *)calloc((size_t)narrow_cap, 1);
    if (!script) { free(raw); return NULL; }
    n = WideCharToMultiByte(CP_UTF8, 0, (const wchar_t *)raw, (int)(raw_len / 2),
                            script, narrow_cap - 1, NULL, NULL);
    free(raw);
    if (n <= 0) { free(script); return NULL; }
    script[n] = 0;
    return script;
}

/* The remaining arguments joined by spaces, as PowerShell does for -Command. */
static char *join_arguments(int argc, wchar_t **argv, int from) {
    size_t wide_len = 1;
    wchar_t *joined;
    char *out;
    int cap;
    for (int i = from; i < argc; i++) wide_len += wcslen(argv[i]) + 1;
    joined = (wchar_t *)calloc(wide_len, sizeof(wchar_t));
    if (!joined) return NULL;
    for (int i = from; i < argc; i++) {
        if (i > from) wcscat(joined, L" ");
        wcscat(joined, argv[i]);
    }
    cap = (int)wide_len * 4 + 8;
    out = (char *)calloc((size_t)cap, 1);
    if (out) utf8_from_wide(joined, out, cap);
    free(joined);
    return out;
}

/* The command this invocation asks PowerShell to run, or NULL if it has none
 * we can read. *encoded says it came from -EncodedCommand. */
static char *command_text(int argc, wchar_t **argv, int *encoded) {
    *encoded = 0;
    for (int i = 1; i < argc; i++) {
        if (_wcsicmp(argv[i], L"-EncodedCommand") == 0 && i + 1 < argc) {
            *encoded = 1;
            return decode_encoded(argv[i + 1]);
        }
        if (_wcsicmp(argv[i], L"-Command") == 0 || _wcsicmp(argv[i], L"-C") == 0)
            return join_arguments(argc, argv, i + 1);
    }
    return NULL;
}

/* ------------------------------------------------- the one query we answer */

static void append_name(char *list, size_t cap, const char *name) {
    size_t len = strlen(list);
    if (len + strlen(name) + 3 >= cap) return;
    if (len) strcat(list, ", ");
    strcat(list, name);
}

/* Answers the exact fingerprint query; returns 0 (writing nothing, and saying why
 * in `why`) if `script` is not exactly that: the scaffold, every slot name and
 * every slot's expression must match what this stand-in was written for.
 * Reports which slots were answered, could not be read here, or have no reader,
 * never their values. */
static int answer_hwid(char *script, char *answered, size_t acap, char *unreadable,
                       size_t ucap, char *no_reader, size_t ncap, char *why, size_t wcap) {
    static parsed_slot slots[SLOT_MAX];
    int slot_count = 0;
    buffer out = {0};
    int first = 1;
    DWORD written = 0;
    const size_t reader_count = sizeof READERS / sizeof READERS[0];
    why[0] = 0;
    if (!parse_query(script, slots, &slot_count, why, wcap)) return 0;

    /* Verify every line before answering anything. */
    for (int i = 0; i < slot_count; i++) {
        size_t r = 0;
        while (r < reader_count && strcmp(slots[i].name, READERS[r].slot) != 0) r++;
        if (r == reader_count) {
            snprintf(why, wcap, "slot '%.47s' is not one this stand-in knows (ROTK added a new slot?)",
                     slots[i].name);
            return 0;
        }
        if (slots[i].expr_len != strlen(READERS[r].expr) ||
            memcmp(slots[i].expr, READERS[r].expr, slots[i].expr_len) != 0) {
            snprintf(why, wcap,
                     "slot '%.47s': its expression differs from the one this stand-in was written for.\n"
                     "  expected: %.1500s\n  received: %.*s",
                     slots[i].name, READERS[r].expr,
                     (int)(slots[i].expr_len > 1500 ? 1500 : slots[i].expr_len), slots[i].expr);
            return 0;
        }
    }

    put(&out, "{", 1);
    for (int i = 0; i < slot_count; i++) {
        for (size_t r = 0; r < reader_count; r++) {
            char value[VALUE_MAX];
            if (strcmp(slots[i].name, READERS[r].slot) != 0) continue;
            if (!READERS[r].read) { append_name(no_reader, ncap, slots[i].name); break; }
            value[0] = 0;
            if (READERS[r].read(value) && value[0]) {
                if (!first) put(&out, ",", 1);
                first = 0;
                put_json_string(&out, slots[i].name);
                put(&out, ":", 1);
                put_json_string(&out, value);
                append_name(answered, acap, slots[i].name);
            } else {
                append_name(unreadable, ucap, slots[i].name);
            }
            break;
        }
    }
    put(&out, "}\n", 2);
    WriteFile(GetStdHandle(STD_OUTPUT_HANDLE), out.data, (DWORD)out.len, &written, NULL);
    free(out.data);
    return 1;
}

/* ------------------------- the installer's "is the app running?" one-liners */

/* ROTK Launcher's NSIS installer (electron-builder's template, which is also
 * what the in-app updater runs) decides whether the app is still running, and
 * closes it, with four fixed PowerShell one-liners; <P> is the install
 * directory:
 *
 *   if (Get-Command Get-CimInstance -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }
 *   if ((Get-ExecutionPolicy -Scope Process) -eq 'Restricted') { exit 1 } else { exit 0 }
 *   if ((Get-CimInstance -ClassName Win32_Process | ? {$_.Path -and $_.Path.StartsWith('<P>', 'CurrentCultureIgnoreCase')}).Count -gt 0) { exit 0 } else { exit 1 }
 *   Get-CimInstance -ClassName Win32_Process | ? {$_.Path -and $_.Path.StartsWith('<P>', 'CurrentCultureIgnoreCase')} | % { Stop-Process -Id $_.ProcessId [-Force] }
 *
 * Wine has no PowerShell, so a stand-in that just failed them would leave the
 * installer unable to tell whether the app is running (it then reports that the
 * app "cannot be closed"). They are answered here for real: the scan is done
 * with Win32 process enumeration, and Stop-Process terminates what it matched.
 * As with the fingerprint query, the whole command must match one of these
 * shapes (whitespace aside); the only free part is <P>. */

typedef enum {
    CHECK_NONE,
    CHECK_CIM_AVAILABLE,
    CHECK_POLICY_OPEN,
    CHECK_RUNNING,
    CHECK_STOP,
} check_kind;

#define SCAN_HEAD "Get-CimInstance -ClassName Win32_Process | ? {$_.Path -and $_.Path.StartsWith('"
#define SCAN_TAIL "', 'CurrentCultureIgnoreCase')}"

static void collapse_whitespace(char *s) {
    char *w = s;
    int pending = 0;
    for (char *r = s; *r; r++) {
        if (*r == ' ' || *r == '\t' || *r == '\r' || *r == '\n') { pending = 1; continue; }
        if (pending && w != s) *w++ = ' ';
        pending = 0;
        *w++ = *r;
    }
    *w = 0;
}

static int starts_with(const char *s, const char *prefix) {
    return strncmp(s, prefix, strlen(prefix)) == 0;
}

/* If `text` is `head <P> tail`, copies <P> into `out` (a path never contains a
 * single quote, which would end the PowerShell string early) and returns 1. */
static int match_template(const char *text, const char *head, const char *tail,
                          char *out, size_t cap) {
    size_t hl = strlen(head), tl = strlen(tail), n = strlen(text);
    size_t mid;
    if (n < hl + tl + 1 || !starts_with(text, head) || strcmp(text + n - tl, tail) != 0) return 0;
    mid = n - hl - tl;
    if (mid >= cap || memchr(text + hl, '\'', mid)) return 0;
    memcpy(out, text + hl, mid);
    out[mid] = 0;
    return 1;
}

static check_kind classify_check(const char *text, char *prefix, size_t cap) {
    if (strcmp(text, "if (Get-Command Get-CimInstance -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }") == 0)
        return CHECK_CIM_AVAILABLE;
    if (strcmp(text, "if ((Get-ExecutionPolicy -Scope Process) -eq 'Restricted') { exit 1 } else { exit 0 }") == 0)
        return CHECK_POLICY_OPEN;
    if (match_template(text, "if ((" SCAN_HEAD, SCAN_TAIL ").Count -gt 0) { exit 0 } else { exit 1 }",
                       prefix, cap))
        return CHECK_RUNNING;
    if (match_template(text, SCAN_HEAD, SCAN_TAIL " | % { Stop-Process -Id $_.ProcessId }", prefix, cap) ||
        match_template(text, SCAN_HEAD, SCAN_TAIL " | % { Stop-Process -Id $_.ProcessId -Force }",
                       prefix, cap))
        return CHECK_STOP;
    return CHECK_NONE;
}

/* How many other processes run from a path starting with `prefix` (compared
 * case-insensitively, as the one-liner does); terminates them if asked. */
static int scan_processes(const char *prefix, int terminate) {
    wchar_t wide[PATH_CAP];
    size_t len;
    int matches = 0;
    DWORD self = GetCurrentProcessId();
    PROCESSENTRY32W entry;
    HANDLE snapshot;
    if (MultiByteToWideChar(CP_UTF8, 0, prefix, -1, wide, PATH_CAP) <= 0) return 0;
    len = wcslen(wide);
    snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    if (snapshot == INVALID_HANDLE_VALUE) return 0;
    entry.dwSize = sizeof entry;
    for (BOOL more = Process32FirstW(snapshot, &entry); more; more = Process32NextW(snapshot, &entry)) {
        wchar_t path[PATH_CAP];
        DWORD size = PATH_CAP;
        HANDLE process;
        if (entry.th32ProcessID == 0 || entry.th32ProcessID == self) continue;
        process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | (terminate ? PROCESS_TERMINATE : 0),
                              FALSE, entry.th32ProcessID);
        if (!process) continue;
        if (QueryFullProcessImageNameW(process, 0, path, &size) && _wcsnicmp(path, wide, len) == 0) {
            matches++;
            if (terminate) TerminateProcess(process, 1);
        }
        CloseHandle(process);
    }
    CloseHandle(snapshot);
    return matches;
}

/* Answers one of the installer's one-liners. Returns its exit code, or -1 if
 * `command` is not one of them (nothing is logged or done then). */
static int answer_instance_check(const char *cmdline, const char *command) {
    char *text = _strdup(command);
    char prefix[PATH_CAP] = "";
    char note[PATH_CAP + 128];
    check_kind kind;
    int code = 0, matches = 0;
    if (!text) return -1;
    collapse_whitespace(text);
    kind = classify_check(text, prefix, sizeof prefix);
    free(text);
    switch (kind) {
    case CHECK_NONE: return -1;
    case CHECK_CIM_AVAILABLE:
        snprintf(note, sizeof note, "installer check: Get-CimInstance is available -> exit 0");
        break;
    case CHECK_POLICY_OPEN:
        snprintf(note, sizeof note, "installer check: execution policy is not Restricted -> exit 0");
        break;
    case CHECK_RUNNING:
        matches = scan_processes(prefix, 0);
        code = matches > 0 ? 0 : 1;
        snprintf(note, sizeof note, "installer check: %d process(es) running from: %s -> exit %d",
                 matches, prefix, code);
        break;
    case CHECK_STOP:
        matches = scan_processes(prefix, 1);
        snprintf(note, sizeof note, "installer check: stopped %d process(es) running from: %s",
                 matches, prefix);
        break;
    }
    log_command("answered", cmdline, command, note);
    return code;
}

/* ----------------------------- diagnostics the app collects while it runs */

/* Two encoded commands the app runs for its diagnostics report (neither affects
 * a launch; the app treats a failure as "unavailable"). Like everything else
 * here they are matched exactly, whitespace aside, and anything that differs is
 * declined. The texts below are the commands as logged by this stand-in; in the
 * event query the three digit runs (two Unix-ms times and a PID) are holes.
 *
 *  1. system info: caption/version/build/memory of Win32_OperatingSystem, the
 *     Win32_VideoController list and Win32_PageFileUsage, as one JSON object.
 *     Answered from Wine's own WMI, i.e. what Windows software sees under Wine.
 *     Wine has no Win32_PageFileUsage (and no Windows pagefile), which a real
 *     PowerShell reports as no instances, so `pageFiles` is [].
 *  2. crash events: Application-log records (IDs 1000/1001/1002) about H1Z1.exe
 *     in the launch window. Wine keeps no such log, so a real PowerShell prints
 *     the empty array; nothing is invented. */
#define HOLE "\001"

static const char SYSINFO_SCRIPT[] =
    "$ErrorActionPreference = 'Stop'\n"
    "[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)\n"
    "$osInfo = Get-CimInstance Win32_OperatingSystem\n"
    "$videoInfo = @(Get-CimInstance Win32_VideoController | Select-Object Name, DriverVersion, DriverDate, AdapterRAM, CurrentHorizontalResolution, CurrentVerticalResolution)\n"
    "$pageInfo = @(Get-CimInstance Win32_PageFileUsage | Select-Object AllocatedBaseSize, CurrentUsage, PeakUsage)\n"
    "[pscustomobject]@{ caption=$osInfo.Caption; version=$osInfo.Version; build=$osInfo.BuildNumber; freePhysicalMemoryKiB=$osInfo.FreePhysicalMemory; freeVirtualMemoryKiB=$osInfo.FreeVirtualMemory; totalVirtualMemoryKiB=$osInfo.TotalVirtualMemorySize; video=$videoInfo; pageFiles=$pageInfo } | ConvertTo-Json -Depth 6 -Compress";

static const char EVENTS_SCRIPT[] =
    "$ErrorActionPreference = 'Stop'\n"
    "[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)\n"
    "$from = [DateTimeOffset]::FromUnixTimeMilliseconds(" HOLE ").LocalDateTime\n"
    "$until = [DateTimeOffset]::FromUnixTimeMilliseconds(" HOLE ").LocalDateTime\n"
    "$gameProcessId = [uint32]" HOLE "\n"
    "$records = @(Get-WinEvent -FilterHashtable @{LogName='Application'; Id=1000,1001,1002; StartTime=$from; EndTime=$until} -MaxEvents 100 -ErrorAction SilentlyContinue)\n"
    "$result = @()\n"
    "foreach ($record in $records) {\n"
    "  $xml = [xml]$record.ToXml()\n"
    "  $fields = @{}\n"
    "  foreach ($entry in $xml.Event.EventData.Data) { if ($entry.Name) { $fields[[string]$entry.Name] = [string]$entry.'#text' } }\n"
    "  $xmlText = $xml.OuterXml\n"
    "  if ($xmlText -notmatch '(?i)H1Z1\\.exe') { continue }\n"
    "  $eventPid = $null\n"
    "  foreach ($name in @('ProcessId','ProcessID','FaultingProcessId')) {\n"
    "    if ($fields.ContainsKey($name)) {\n"
    "      try { $rawPid = $fields[$name]; $eventPid = if ($rawPid.StartsWith('0x')) { [Convert]::ToUInt32($rawPid.Substring(2),16) } else { [uint32]$rawPid } } catch {}\n"
    "      break\n"
    "    }\n"
    "  }\n"
    "  if ($eventPid -ne $null -and $eventPid -ne $gameProcessId) { continue }\n"
    "  # Keep structured fault information only, never rendered messages/command lines.\n"
    "  $selected = @{}\n"
    "  foreach ($name in @('AppName','AppVersion','AppTimeStamp','ModuleName','ModuleVersion','ModuleTimeStamp','ExceptionCode','FaultingOffset','ProcessId','ProcessCreationTime','ReportId','IntegratorReportId','EventName','Response','CabId','P1','P2','P3','P4','P5','P6','P7','P8','P9','P10','HangType')) {\n"
    "    if ($fields.ContainsKey($name)) { $selected[$name] = $fields[$name] }\n"
    "  }\n"
    "  $result += [pscustomobject]@{ at=$record.TimeCreated.ToUniversalTime().ToString('o'); eventId=$record.Id; provider=$record.ProviderName; correlation= $(if ($eventPid -ne $null) {'pid-and-time'} else {'executable-and-time-only'}); data=$selected }\n"
    "}\n"
    "ConvertTo-Json -InputObject @($result) -Depth 6 -Compress";

/* One whitespace-collapsed command matches a collapsed template exactly, except
 * that each HOLE stands for a run of 1-20 digits. */
static int matches_with_digit_holes(const char *text, const char *tmpl) {
    while (*tmpl) {
        if (*tmpl == HOLE[0]) {
            size_t n = 0;
            while (isdigit((unsigned char)text[n])) n++;
            if (n == 0 || n > 20) return 0;
            text += n;
            tmpl++;
        } else if (*text++ != *tmpl++) {
            return 0;
        }
    }
    return *text == 0;
}

static int command_is(const char *command, const char *tmpl) {
    char *text = _strdup(command), *want = _strdup(tmpl);
    int same = 0;
    if (text && want) {
        collapse_whitespace(text);
        collapse_whitespace(want);
        same = matches_with_digit_holes(text, want);
    }
    free(text);
    free(want);
    return same;
}

/* ---- Wine's WMI, through COM ---- */

static IWbemServices *wmi_connect(void) {
    IWbemLocator *locator = NULL;
    IWbemServices *services = NULL;
    BSTR resource;
    HRESULT hr = CoInitializeEx(NULL, COINIT_MULTITHREADED);
    if (FAILED(hr) && hr != RPC_E_CHANGED_MODE) return NULL;
    hr = CoCreateInstance(&CLSID_WbemLocator, NULL, CLSCTX_INPROC_SERVER, &IID_IWbemLocator,
                          (void **)&locator);
    if (FAILED(hr)) return NULL;
    resource = SysAllocString(L"ROOT\\CIMV2");
    hr = IWbemLocator_ConnectServer(locator, resource, NULL, NULL, NULL, 0, NULL, NULL, &services);
    SysFreeString(resource);
    IWbemLocator_Release(locator);
    return SUCCEEDED(hr) ? services : NULL;
}

static IEnumWbemClassObject *wmi_query(IWbemServices *services, const wchar_t *wql) {
    IEnumWbemClassObject *rows = NULL;
    BSTR language = SysAllocString(L"WQL"), query = SysAllocString(wql);
    HRESULT hr = IWbemServices_ExecQuery(services, language, query,
                                         WBEM_FLAG_FORWARD_ONLY | WBEM_FLAG_RETURN_IMMEDIATELY,
                                         NULL, &rows);
    SysFreeString(language);
    SysFreeString(query);
    return SUCCEEDED(hr) ? rows : NULL;
}

/* A CIM datetime ("20230627000000.000000+000": local time and the offset in
 * minutes east of UTC) as Unix milliseconds. */
static int cim_datetime_ms(const wchar_t *s, long long *ms) {
    int y, mo, d, h, mi, se, us, off;
    wchar_t sign;
    long long days, a, era, yoe, doy, doe;
    if (wcslen(s) < 25 ||
        swscanf(s, L"%4d%2d%2d%2d%2d%2d.%6d%lc%3d", &y, &mo, &d, &h, &mi, &se, &us, &sign, &off) != 9)
        return 0;
    if (sign == L'-') off = -off;
    a = mo <= 2 ? 1 : 0;                      /* days from civil (Hinnant) */
    era = ((y - a) >= 0 ? (y - a) : (y - a - 399)) / 400;
    yoe = (y - a) - era * 400;
    doy = (153 * (mo + (mo > 2 ? -3 : 9)) + 2) / 5 + d - 1;
    doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    days = era * 146097 + doe - 719468;
    *ms = ((days * 24 + h) * 60 + mi - off) * 60 * 1000 + se * 1000LL + us / 1000;
    return 1;
}

typedef enum { PROP_STRING, PROP_NUMBER, PROP_DATE } prop_kind;

/* Appends one WMI property as JSON the way PowerShell 5.1's ConvertTo-Json
 * writes it: strings quoted, numbers bare, a DateTime as "\/Date(ms)\/", and
 * null for a property the provider doesn't have. */
static void put_property(buffer *b, IWbemClassObject *row, const wchar_t *name, prop_kind kind) {
    VARIANT v;
    char text[VALUE_MAX], number[32];
    unsigned long long n = 0;
    int have = 0;
    VariantInit(&v);
    if (SUCCEEDED(IWbemClassObject_Get(row, name, 0, &v, NULL, NULL))) {
        switch (V_VT(&v)) {
        case VT_BSTR:
            if (kind == PROP_STRING) {
                utf8_from_wide(V_BSTR(&v), text, sizeof text);
                have = 1;
            } else if (kind == PROP_NUMBER) {
                n = _wcstoui64(V_BSTR(&v), NULL, 10);   /* WMI hands uint64 over as text */
                have = 1;
            } else {
                long long ms;
                if (cim_datetime_ms(V_BSTR(&v), &ms)) {
                    snprintf(text, sizeof text, "%lld", ms);
                    have = 1;
                }
            }
            break;
        case VT_I4:  n = (unsigned int)V_I4(&v);  have = kind == PROP_NUMBER; break;
        case VT_UI4: n = V_UI4(&v);               have = kind == PROP_NUMBER; break;
        case VT_I2:  n = (unsigned short)V_I2(&v); have = kind == PROP_NUMBER; break;
        case VT_UI2: n = V_UI2(&v);               have = kind == PROP_NUMBER; break;
        case VT_I8:  n = (unsigned long long)V_I8(&v); have = kind == PROP_NUMBER; break;
        default: break;
        }
    }
    if (!have) {
        put_str(b, "null");
    } else if (kind == PROP_STRING) {
        put_json_string(b, text);
    } else if (kind == PROP_NUMBER) {
        snprintf(number, sizeof number, "%llu", n);
        put_str(b, number);
    } else {
        put_str(b, "\"\\/Date(");
        put_str(b, text);
        put_str(b, ")\\/\"");
    }
    VariantClear(&v);
}

typedef struct { const char *json_key; const wchar_t *property; prop_kind kind; } column;

static void put_columns(buffer *b, IWbemClassObject *row, const column *columns, size_t count) {
    for (size_t i = 0; i < count; i++) {
        if (i) put_str(b, ",");
        put_json_string(b, columns[i].json_key);
        put_str(b, ":");
        put_property(b, row, columns[i].property, columns[i].kind);
    }
}

/* Writes the system-info object; returns 0 (writing nothing) if Wine's WMI
 * can't be queried or has no operating-system row. */
static int answer_sysinfo(buffer *out) {
    static const column os[] = {
        {"caption", L"Caption", PROP_STRING},
        {"version", L"Version", PROP_STRING},
        {"build", L"BuildNumber", PROP_STRING},
        {"freePhysicalMemoryKiB", L"FreePhysicalMemory", PROP_NUMBER},
        {"freeVirtualMemoryKiB", L"FreeVirtualMemory", PROP_NUMBER},
        {"totalVirtualMemoryKiB", L"TotalVirtualMemorySize", PROP_NUMBER},
    };
    static const column video[] = {
        {"Name", L"Name", PROP_STRING},
        {"DriverVersion", L"DriverVersion", PROP_STRING},
        {"DriverDate", L"DriverDate", PROP_DATE},
        {"AdapterRAM", L"AdapterRAM", PROP_NUMBER},
        {"CurrentHorizontalResolution", L"CurrentHorizontalResolution", PROP_NUMBER},
        {"CurrentVerticalResolution", L"CurrentVerticalResolution", PROP_NUMBER},
    };
    IWbemServices *services = wmi_connect();
    IEnumWbemClassObject *rows;
    IWbemClassObject *row = NULL;
    ULONG got = 0;
    int ok = 0, first = 1;
    if (!services) return 0;

    rows = wmi_query(services,
        L"SELECT Caption, Version, BuildNumber, FreePhysicalMemory, FreeVirtualMemory,"
        L" TotalVirtualMemorySize FROM Win32_OperatingSystem");
    if (rows && SUCCEEDED(IEnumWbemClassObject_Next(rows, WBEM_INFINITE, 1, &row, &got)) && got == 1) {
        put_str(out, "{");
        put_columns(out, row, os, sizeof os / sizeof os[0]);
        IWbemClassObject_Release(row);
        ok = 1;
    }
    if (rows) IEnumWbemClassObject_Release(rows);
    if (!ok) { IWbemServices_Release(services); return 0; }

    put_str(out, ",\"video\":[");
    rows = wmi_query(services,
        L"SELECT Name, DriverVersion, DriverDate, AdapterRAM, CurrentHorizontalResolution,"
        L" CurrentVerticalResolution FROM Win32_VideoController");
    while (rows && SUCCEEDED(IEnumWbemClassObject_Next(rows, WBEM_INFINITE, 1, &row, &got)) && got == 1) {
        if (!first) put_str(out, ",");
        first = 0;
        put_str(out, "{");
        put_columns(out, row, video, sizeof video / sizeof video[0]);
        put_str(out, "}");
        IWbemClassObject_Release(row);
    }
    if (rows) IEnumWbemClassObject_Release(rows);
    IWbemServices_Release(services);
    put_str(out, "],\"pageFiles\":[]}\n");
    return 1;
}

/* Answers one of the two diagnostics commands: returns 0 if it did, -1 if
 * `command` is neither (nothing is logged or done then). */
static int answer_diagnostic(const char *cmdline, const char *command) {
    buffer out = {0};
    DWORD written = 0;
    const char *note;
    if (command_is(command, SYSINFO_SCRIPT)) {
        if (!answer_sysinfo(&out)) {
            free(out.data);
            log_command("declined", cmdline, command, "diagnostic: system info, but Wine's WMI could not be queried");
            return EXIT_DECLINED;
        }
        note = "diagnostic: system info from Wine's WMI (pageFiles is empty: Wine has none)";
    } else if (command_is(command, EVENTS_SCRIPT)) {
        put_str(&out, "[]\n");
        note = "diagnostic: Application-log crash events -> [] (Wine keeps no such log)";
    } else {
        return -1;
    }
    WriteFile(GetStdHandle(STD_OUTPUT_HANDLE), out.data, (DWORD)out.len, &written, NULL);
    free(out.data);
    log_command("answered", cmdline, command, note);
    return 0;
}

/* ------------------------------------------- the app's two TPM commands */

/* Level 1 and level 2 ("anchor") TPM proofs, exactly as the app runs them
 * (-Command, with the binding message base64 in ROTK_TPM_MESSAGE_B64). They
 * are answered with this machine's real TPM (see tpm2.h); if it can't be used
 * they are declined, as on a PC without a TPM.
 * The third command, credential activation, is answered by the TPM as well
 * (as an elevated Windows user would see it). */

static const char TPM_SIGN_SCRIPT[] =
    "\n"
    "$ErrorActionPreference = \"Stop\"\n"
    "$encoded = $env:ROTK_TPM_MESSAGE_B64\n"
    "if ([string]::IsNullOrEmpty($encoded)) { throw \"no message\" }\n"
    "$data = [Convert]::FromBase64String($encoded)\n"
    "$provider = [System.Security.Cryptography.CngProvider]::new(\"Microsoft Platform Crypto Provider\")\n"
    "if ([System.Security.Cryptography.CngKey]::Exists(\"rotk-hwid-tpm-v1\", $provider)) {\n"
    "  $key = [System.Security.Cryptography.CngKey]::Open(\"rotk-hwid-tpm-v1\", $provider)\n"
    "} else {\n"
    "  $p = [System.Security.Cryptography.CngKeyCreationParameters]::new()\n"
    "  $p.Provider = $provider\n"
    "  $p.ExportPolicy = [System.Security.Cryptography.CngExportPolicies]::None\n"
    "  $p.KeyUsage = [System.Security.Cryptography.CngKeyUsages]::Signing\n"
    "  $key = [System.Security.Cryptography.CngKey]::Create([System.Security.Cryptography.CngAlgorithm]::ECDsaP256, \"rotk-hwid-tpm-v1\", $p)\n"
    "}\n"
    "$ecdsa = [System.Security.Cryptography.ECDsaCng]::new($key)\n"
    "$sig = $ecdsa.SignData($data, [System.Security.Cryptography.HashAlgorithmName]::SHA256)\n"
    "$pub = $key.Export([System.Security.Cryptography.CngKeyBlobFormat]::EccPublicBlob)\n"
    "Write-Output ([Convert]::ToBase64String($pub) + \"|\" + [Convert]::ToBase64String($sig))";

static const char TPM_ANCHOR_SCRIPT[] =
    "\n"
    "$ErrorActionPreference = \"Stop\"\n"
    "$encoded = $env:ROTK_TPM_MESSAGE_B64\n"
    "if ([string]::IsNullOrEmpty($encoded)) { throw \"no message\" }\n"
    "$data = [Convert]::FromBase64String($encoded)\n"
    "\n"
    "Add-Type -TypeDefinition @\"\n"
    "using System; using System.Runtime.InteropServices;\n"
    "public static class RotkNCrypt {\n"
    "  [DllImport(\"ncrypt.dll\")] public static extern int NCryptOpenStorageProvider(out IntPtr phProvider, [MarshalAs(UnmanagedType.LPWStr)] string pszProviderName, uint dwFlags);\n"
    "  [DllImport(\"ncrypt.dll\")] public static extern int NCryptGetProperty(IntPtr hObject, [MarshalAs(UnmanagedType.LPWStr)] string pszProperty, byte[] pbOutput, uint cbOutput, out uint pcbResult, uint dwFlags);\n"
    "  [DllImport(\"ncrypt.dll\")] public static extern int NCryptFreeObject(IntPtr hObject);\n"
    "  public static byte[] Get(IntPtr h, string name) {\n"
    "    uint cb; if (NCryptGetProperty(h, name, null, 0, out cb, 0) != 0) return null;\n"
    "    var buf = new byte[cb]; if (NCryptGetProperty(h, name, buf, cb, out cb, 0) != 0) return null;\n"
    "    Array.Resize(ref buf, (int)cb); return buf; }\n"
    "}\n"
    "\"@\n"
    "\n"
    "$provider = [System.Security.Cryptography.CngProvider]::new(\"Microsoft Platform Crypto Provider\")\n"
    "if ([System.Security.Cryptography.CngKey]::Exists(\"rotk-tpm-aik-v1\", $provider)) {\n"
    "  $key = [System.Security.Cryptography.CngKey]::Open(\"rotk-tpm-aik-v1\", $provider)\n"
    "} else {\n"
    "  $p = [System.Security.Cryptography.CngKeyCreationParameters]::new()\n"
    "  $p.Provider = $provider\n"
    "  $p.ExportPolicy = [System.Security.Cryptography.CngExportPolicies]::None\n"
    "  $p.KeyUsage = [System.Security.Cryptography.CngKeyUsages]::Signing\n"
    "  $p.Parameters.Add([System.Security.Cryptography.CngProperty]::new(\"PCP_KEY_USAGE_POLICY\", [BitConverter]::GetBytes([uint32]1), [System.Security.Cryptography.CngPropertyOptions]::None))\n"
    "  $key = [System.Security.Cryptography.CngKey]::Create([System.Security.Cryptography.CngAlgorithm]::ECDsaP256, \"rotk-tpm-aik-v1\", $p)\n"
    "}\n"
    "$ecdsa = [System.Security.Cryptography.ECDsaCng]::new($key)\n"
    "$sig = $ecdsa.SignData($data, [System.Security.Cryptography.HashAlgorithmName]::SHA256)\n"
    "$pub = $key.Export([System.Security.Cryptography.CngKeyBlobFormat]::EccPublicBlob)\n"
    "$opaque = $key.Export([System.Security.Cryptography.CngKeyBlobFormat]::new(\"OpaqueKeyBlob\"))\n"
    "$header = [BitConverter]::ToUInt32($opaque, 4)\n"
    "$cbPublic = [BitConverter]::ToUInt32($opaque, 16)\n"
    "$tpmPublic = New-Object byte[] $cbPublic\n"
    "[Array]::Copy($opaque, $header, $tpmPublic, 0, $cbPublic)\n"
    "$r = @{\n"
    "  publicKey = [Convert]::ToBase64String($pub)\n"
    "  signature = [Convert]::ToBase64String($sig)\n"
    "  tpmPublic = [Convert]::ToBase64String($tpmPublic)\n"
    "}\n"
    "$h = [IntPtr]::Zero\n"
    "if ([RotkNCrypt]::NCryptOpenStorageProvider([ref]$h, \"Microsoft Platform Crypto Provider\", 0) -eq 0) {\n"
    "  try {\n"
    "    $ekpub = [RotkNCrypt]::Get($h, \"PCP_EKPUB\")\n"
    "    if ($ekpub -ne $null) { $r.ekPublicKey = [Convert]::ToBase64String($ekpub) }\n"
    "    $man = [RotkNCrypt]::Get($h, \"PCP_TPM_MANUFACTURER_ID\")\n"
    "    if ($man -ne $null) { $r.manufacturer = [Text.Encoding]::Unicode.GetString($man).Trim([char]0).Trim() }\n"
    "    $ver = [RotkNCrypt]::Get($h, \"PCP_TPM_VERSION\")\n"
    "    if ($ver -ne $null -and $ver.Length -ge 4) { $v = [BitConverter]::ToUInt32($ver, 0); $r.version = \"$($v -shr 16).$($v -band 0xffff)\" }\n"
    "    $fw = [RotkNCrypt]::Get($h, \"PCP_TPM_FW_VERSION\")\n"
    "    if ($fw -ne $null) { $r.firmware = (($fw | ForEach-Object { $_.ToString(\"x2\") }) -join \"\") }\n"
    "    $certs = @()\n"
    "    $cert = [RotkNCrypt]::Get($h, \"PCP_EKCERT\")\n"
    "    if ($cert -ne $null -and $cert.Length -gt 0) { $certs += [Convert]::ToBase64String($cert) }\n"
    "    if ($certs.Count -eq 0) {\n"
    "      try {\n"
    "        $info = Get-TpmEndorsementKeyInfo -HashAlgorithm Sha256\n"
    "        foreach ($c in @($info.ManufacturerCertificates) + @($info.AdditionalCertificates)) {\n"
    "          if ($c -ne $null -and $c.RawData -ne $null) { $certs += [Convert]::ToBase64String($c.RawData) }\n"
    "        }\n"
    "      } catch {}\n"
    "    }\n"
    "    $r.ekCertificates = $certs\n"
    "  } finally { [void][RotkNCrypt]::NCryptFreeObject($h) }\n"
    "}\n"
    "Write-Output ($r | ConvertTo-Json -Compress)";

static const char TPM_ACTIVATE_SCRIPT[] =
    "\n"
    "$ErrorActionPreference = \"Stop\"\n"
    "$blob = [Convert]::FromBase64String($env:ROTK_TPM_ACTIVATION)\n"
    "$provider = [System.Security.Cryptography.CngProvider]::new(\"Microsoft Platform Crypto Provider\")\n"
    "$key = [System.Security.Cryptography.CngKey]::Open(\"rotk-tpm-aik-v1\", $provider)\n"
    "$key.SetProperty([System.Security.Cryptography.CngProperty]::new(\"PCP_TPM12_IDACTIVATION\", $blob, [System.Security.Cryptography.CngPropertyOptions]::None))\n"
    "$secret = $key.GetProperty(\"PCP_TPM12_IDACTIVATION\", [System.Security.Cryptography.CngPropertyOptions]::None).GetValue()\n"
    "Write-Output ([Convert]::ToBase64String($secret))\n";

#define TPM_ACTIVATION_ENV L"ROTK_TPM_ACTIVATION"

/* The server's credential, answered by the TPM (see tpm_activate). */
static int answer_activation(const char *cmdline, const char *command) {
    static uint8_t blob[2048];
    static wchar_t encoded[4096];
    static char answer[256];
    char why[512] = "", note[600];
    DWORD chars, written = 0;
    long length;
    chars = GetEnvironmentVariableW(TPM_ACTIVATION_ENV, encoded, (DWORD)(sizeof encoded / sizeof encoded[0]));
    length = (chars > 0 && chars < sizeof encoded / sizeof encoded[0]) ? b64_decode(encoded, blob, sizeof blob) : -1;
    if (length <= 0) {
        log_command("declined", cmdline, command, "tpm: no readable ROTK_TPM_ACTIVATION");
        return EXIT_DECLINED;
    }
    if (!tpm_activate(blob, (size_t)length, answer, sizeof answer, why, sizeof why)) {
        snprintf(note, sizeof note, "tpm: declined (credential activation): %s", why);
        log_command("declined", cmdline, command, note);
        return EXIT_DECLINED;
    }
    strcat(answer, "\n");
    WriteFile(GetStdHandle(STD_OUTPUT_HANDLE), answer, (DWORD)strlen(answer), &written, NULL);
    log_command("answered", cmdline, command, "tpm: credential activated by this machine's TPM");
    return 0;
}

#define TPM_MESSAGE_ENV L"ROTK_TPM_MESSAGE_B64"
#define TPM_MESSAGE_MAX 24576
#define TPM_ANSWER_MAX 40960

static int tpm_enabled(void) {
    wchar_t flag[4];
    return GetEnvironmentVariableW(L"SUL_TPM", flag, 4) > 0 && flag[0] == L'1';
}

/* Answers one of the two TPM commands: 0 if it did, EXIT_DECLINED if it
 * recognized one but the TPM couldn't, -1 if `command` is neither. */
static int answer_tpm(const char *cmdline, const char *command) {
    static uint8_t message[TPM_MESSAGE_MAX];
    static wchar_t encoded[TPM_MESSAGE_MAX * 2];
    static char answer[TPM_ANSWER_MAX];
    char why[512] = "", note[600];
    int level1 = command_is(command, TPM_SIGN_SCRIPT);
    int anchor = !level1 && command_is(command, TPM_ANCHOR_SCRIPT);
    DWORD chars;
    long length;
    DWORD written = 0;
    int ok;
    if ((level1 || anchor) && !tpm_enabled()) {
        log_command("declined", cmdline, command, "tpm: not enabled (rotk-launcher --tpm); behaving as a PC without a TPM");
        return EXIT_DECLINED;
    }
    if (!level1 && !anchor) {
        return command_is(command, TPM_ACTIVATE_SCRIPT) ? answer_activation(cmdline, command) : -1;
    }

    chars = GetEnvironmentVariableW(TPM_MESSAGE_ENV, encoded, (DWORD)(sizeof encoded / sizeof encoded[0]));
    length = (chars > 0 && chars < sizeof encoded / sizeof encoded[0]) ? b64_decode(encoded, message, sizeof message) : -1;
    if (length <= 0) {
        log_command("declined", cmdline, command, "tpm: no readable ROTK_TPM_MESSAGE_B64 (the app always sends one)");
        return EXIT_DECLINED;
    }
    ok = level1 ? tpm_level1_proof(message, (size_t)length, answer, sizeof answer, why, sizeof why)
                : tpm_anchor_json(message, (size_t)length, answer, sizeof answer, why, sizeof why);
    if (!ok) {
        snprintf(note, sizeof note, "tpm: declined (%s): %s", level1 ? "level 1 proof" : "anchor", why);
        log_command("declined", cmdline, command, note);
        return EXIT_DECLINED;
    }
    strcat(answer, "\n");
    WriteFile(GetStdHandle(STD_OUTPUT_HANDLE), answer, (DWORD)strlen(answer), &written, NULL);
    snprintf(note, sizeof note, "tpm: %s answered with this machine's TPM (%ld-byte message)",
             level1 ? "level 1 proof" : "anchor", length);
    log_command("answered", cmdline, command, note);
    return 0;
}

int wmain(int argc, wchar_t **argv) {
    const wchar_t *command_line;
    char *cmdline, *text;
    char why[4096] = "";
    int encoded;

    if (argc >= 3 && wcscmp(argv[1], POPUP_FLAG) == 0) return show_error_popup(argv[2]);
    if (argc == 2 && wcscmp(argv[1], PREPARE_FLAG) == 0) {
        if (tpm_prepare(why, sizeof why)) return 0;
        fprintf(stderr, "tpm prepare failed: %s\n", why);
        return EXIT_DECLINED;
    }

    command_line = GetCommandLineW();
    cmdline = (char *)calloc(wcslen(command_line) * 4 + 8, 1);
    if (!cmdline) return EXIT_DECLINED;
    utf8_from_wide(command_line, cmdline, (int)(wcslen(command_line) * 4 + 8));
    text = command_text(argc, argv, &encoded);

    if (text && !encoded) {
        int code = answer_instance_check(cmdline, text);
        if (code >= 0) {
            free(text);
            free(cmdline);
            return code;
        }
    }

    if (text && !encoded) {
        int code = answer_tpm(cmdline, text);
        if (code >= 0) {
            free(text);
            free(cmdline);
            return code;
        }
    }

    if (text && encoded) {
        char answered[2048] = "", unreadable[2048] = "", no_reader[2048] = "";
        char note[6300];
        char *copy = _strdup(text);   /* parse_query edits its input */
        int ok = copy && answer_hwid(copy, answered, sizeof answered, unreadable,
                                     sizeof unreadable, no_reader, sizeof no_reader,
                                     why, sizeof why);
        free(copy);
        if (ok) {
            snprintf(note, sizeof note,
                     "slots answered: %s\nslots unreadable on this machine (omitted): %s\n"
                     "slots this stand-in has no reader for (omitted): %s",
                     answered, unreadable, no_reader);
            log_command("answered", cmdline, text, note);
            free(text);
            free(cmdline);
            return 0;
        }
    }

    if (text && encoded) {
        int code = answer_diagnostic(cmdline, text);
        if (code >= 0) {
            free(text);
            free(cmdline);
            return code;
        }
    }

    if (text && is_hwid_related(text)) {
        int code = fail_loudly(cmdline, text, why);
        free(text);
        free(cmdline);
        return code;
    }

    log_command("declined", cmdline, text, NULL);
    free(text);
    free(cmdline);
    return EXIT_DECLINED;
}
