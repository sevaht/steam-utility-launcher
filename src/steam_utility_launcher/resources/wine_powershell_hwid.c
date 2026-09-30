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
 *   answered           the exact fingerprint query above; only slot names are
 *                      logged, never the hardware values.
 *   declined           anything else: exit 1 with no output, exactly as a
 *                      missing PowerShell would (the app's installer/updater,
 *                      TPM and diagnostics code all rely on that).
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
#include <windows.h>
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

int wmain(int argc, wchar_t **argv) {
    const wchar_t *command_line;
    char *cmdline, *text;
    char why[4096] = "";
    int encoded;

    if (argc >= 3 && wcscmp(argv[1], POPUP_FLAG) == 0) return show_error_popup(argv[2]);

    command_line = GetCommandLineW();
    cmdline = (char *)calloc(wcslen(command_line) * 4 + 8, 1);
    if (!cmdline) return EXIT_DECLINED;
    utf8_from_wide(command_line, cmdline, (int)(wcslen(command_line) * 4 + 8));
    text = command_text(argc, argv, &encoded);

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
