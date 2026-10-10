# Steam Utility Launcher

Downloads game utilities from GitHub releases and launches them. On Linux it
runs them within the same Proton prefix the game uses, so trainers and practice
tools function properly. On Windows only the download/update functionality is
useful; running tools inside a Proton prefix is Linux-only.

## Installation

```bash
pipx install steam-utility-launcher
```

## Usage

> **Note:** Always launch the game first, then run the utility. The tool runs
> inside the game's already-running Proton wineserver, so the game must be
> open before the launcher is invoked.

Global flags (`--log-file`, `-v`, `-q`, `--debug`) must come **before** the
subcommand name:

```bash
steam-utility-launcher --debug dsr-gadget   # correct
steam-utility-launcher dsr-gadget --debug   # ignored
```

### Preset utilities

After launching Dark Souls: Remastered, run DSR-Gadget:
```bash
steam-utility-launcher dsr-gadget
```

After launching Hitman WoA, run the Peacock private server:
```bash
steam-utility-launcher hitman-peacock
```

> **Linux note:** Peacock requires `node` to be installed and in your `PATH`
> (`node chunk0.js` is run directly, outside of Wine). Peacock user data
> (`userdata/`, `contracts/`, `contractSessions/`) is preserved across
> updates.

After launching Dark Souls: Remastered, run SilkySouls:
```bash
steam-utility-launcher silky-souls
```

Install and run ROTK Launcher for Z1 Battle Royale:
```bash
steam-utility-launcher rotk-launcher
```

> **Linux note:** the game doesn't need to already be running for this one —
> ROTK Launcher isn't a mod that attaches to a live game process, so it just
> needs the game's Proton prefix to exist (i.e. the game has been launched
> at least once before).

If ROTK Launcher isn't installed yet, this downloads the latest release,
verifies its checksum against the release's `SHA256SUMS.txt`, and runs the
installer silently inside the game's Proton prefix before launching the app.

If it's already installed, **this does not update it** — ROTK Launcher has
its own in-app auto-updater, so this tool defers to that rather than
fighting over version state. It checks GitHub for a newer release at most
once a day (not on every run) and logs a warning (visible even without
`-v`/`--debug`) when one's found, so you're occasionally reminded one is
available without hitting the network every time you launch:

```bash
steam-utility-launcher rotk-launcher --force
```

Pass `--force` to install the latest release immediately instead of waiting
on the in-app updater (also useful to repair an installation, since it
reinstalls even if already at the latest version).

Wine's `powershell.exe` is an unimplemented stub: it never runs the script
it's given and always exits `0`. ROTK Launcher's installer (and its own
in-app auto-updater, which shares the same NSIS installer machinery) uses a
PowerShell one-liner to check whether an old instance of itself is running
before installing, treating exit `0` as "yes, it's running" — since the
stub always exits `0`, it would otherwise conclude the app is permanently
running and get stuck forever on a "cannot be closed" dialog. To avoid
that, `rotk-launcher` disables `powershell.exe` (via `WINEDLLOVERRIDES`) for
just the installer and app processes it starts, making it fail to launch
instead of falsely succeeding. The installer's own template already handles
PowerShell being unavailable, falling back to `tasklist`/`findstr`/
`taskkill` to answer the same check. Nothing is written to the prefix, and
the override applies only to those processes; the shared Proton installation
and every other prefix and process are untouched.

**Hardware ID (`hwid_required`).** ROTK's account service now refuses launches
that carry no hardware fingerprint, and the app collects it by running
PowerShell/WMI queries, which Wine's stub can't answer. This tool therefore
replaces the prefix's `powershell.exe` (both `system32` and `syswow64`, each
with its own architecture's build) with a small native Windows program
(`resources/wine_powershell_hwid.c`, prebuilt by `build-powershell-hwid`).
It is left in place permanently: every `rotk-launcher` run checks that it is
present and current and reinstalls it if it's missing or differs (for
example after a prefix reset or a new build), and never takes it back out.
It can't be removed after the launch, because ROTK Launcher 2.0.29 and later
carries on in another process a few seconds after the one we start exits,
and its in-app updater runs an installer after the app has quit. Wine's own
stub is only a symlink into the Proton installation, which is not touched.
It must be a real `.exe`: Wine can't connect a Windows process's pipes to a
native Unix program, and spawning one that way crashes the launcher.

The stand-in never executes the script it is given, and it only answers the
**exact** fingerprint query ROTK Launcher 2.0.24 sends: the same scaffold, only
slot names it knows, and, for every slot, the byte-for-byte PowerShell
expression the launcher uses for it (copied from its source into a table in
`resources/wine_powershell_hwid.c`). It answers with this machine's **real**
values (the prefix's `MachineGuid`, the C: volume serial, the first disk's
serial/model/firmware, DMI/BIOS data, CPU name, physical MAC addresses).
Anything it can't read is omitted, never invented. The ROTK team does not
officially support Linux, so their server may still decide to refuse it.

It also answers, for real, the other fixed commands the app runs, each matched
exactly and each from a genuine source rather than an invented one:

- **The installer's "is the app running / close it" checks** (what ROTK's
  in-app updater runs), with real process enumeration.
- **Two diagnostics commands.** System info comes from Wine's own WMI (OS,
  memory, GPU; `pageFiles` is empty because Wine has no Windows pagefile). The
  crash-event query answers an empty list, because Wine keeps no Windows
  Application log.
- **The two TPM proofs.** ROTK Launcher signs a one-time challenge with a key
  held in a TPM so that a modified launcher can't make up a hardware identity.
  The stand-in does the same with **this machine's real TPM 2.0**, through
  `/dev/tpmrm0` (see `enable-hwid-access`): the signing and identity keys live
  in the TPM and can't be exported (the files under
  `~/.local/share/steam-utility-launcher/tpm/` are only TPM-wrapped blobs that
  load on this TPM alone), and it reports the endorsement key, TPM
  manufacturer, version and firmware. All of this is **opt-in**
  (`rotk-launcher --tpm`): by default the TPM commands are declined and the
  app sees exactly what it would on a PC without a TPM. Once you opt in it has
  to work: if there is no TPM, your user can't use it (run
  `enable-hwid-access`), or setting it up fails, `rotk-launcher` stops with an
  error before starting anything. Two further extras
  need `--tpm-endorsement` (which implies `--tpm`), and are meant only for the
  day the server starts requiring them.
  They make the TPM answer as an elevated Windows user would, using nothing
  but this machine's own TPM and certificates. On AMD TPMs, which keep no
  certificate inside the TPM, the launcher then fetches the endorsement key's
  certificate chain once from `ftpm.amd.com` (the URL is a hash of your
  endorsement public key, the only thing AMD sees; nothing else is ever
  requested) and keeps it as `ek-cert-N.der` next to the key blobs; it is sent
  with the anchor. The credential activation the server may then request
  is performed by the TPM too (with an endorsement-hierarchy policy session),
  so the launcher shows the enrolment as an elevated Windows user would see it. If the TPM can't be used, the
  commands are declined, exactly as on a PC without one: it never substitutes
  a software key.

Every PowerShell command that reaches the stand-in is appended to
`~/.local/share/steam-utility-launcher/powershell-commands.log`; `rotk-launcher`
prints that path at the start of every run. Each record has a verdict:

- `answered`: the exact fingerprint query (only the slot names are logged
  (answered, unreadable here, or without a reader), never the hardware values),
  or one of the other fixed commands above.
- `declined`: anything else, and a TPM command when the TPM can't be used (the
  record says why). It exits 1 with no output, as a missing PowerShell would,
  which is what those callers expect.
- `HWID-UNRECOGNIZED`: a command that references hardware-ID data (the WMI
  classes and registry value the fingerprint reads, or the fingerprint's
  scaffold) but is not exactly the query above, for example because ROTK
  reworded it, changed a slot's expression, or added a slot. The stand-in
  refuses it and makes sure you notice: the record says which part differed, a
  message box is shown (by a separate process, so it outlives the call), a
  message goes to stderr naming the log, it exits 190, and `rotk-launcher`
  prints an error with the log path when the app exits. It never guesses at a
  changed format, so a wrongly formatted value is never sent. The fix is to
  update the stand-in to match.

Two environment variables exist for testing: `SUL_POWERSHELL_LOG` (log file,
set by `rotk-launcher`; default `%TEMP%\sul-powershell-commands.log`) and
`SUL_POWERSHELL_NO_POPUP` (suppress the message box).

#### Visual C++ runtime

ROTK's anti-cheat module is built with a recent MSVC and needs a recent
`msvcp140.dll`. Its `std::mutex` needs no initialization call, but the 2016
`msvcp140.dll` that Steam's redistributable installer leaves in a game's prefix
dereferences a null pointer when locking one, so the game died about three
seconds after the module loaded. 14.40 is the first release that handles it.

Before every launch, `rotk-launcher` reads the version of the prefix's
`msvcp140.dll` (both `system32` and `syswow64`, two file reads, no network). If
either is older than 14.40 it installs Microsoft's Visual C++ runtime into the
prefix, the same thing `winetricks vcrun2022` does, so neither winetricks nor
protontricks is needed. The installers (`vc_redist.x64.exe` and `.x86.exe`) are
downloaded once from Microsoft, checked against pinned SHA-256 hashes, and kept
in `~/.local/share/steam-utility-launcher/VC-Redist/`; a download that doesn't
match is never run. They are run silently (`/install`, then `/repair` if a file
is still old, which happens when the bundle is registered but its files were
replaced). After installing, the version is checked again and the launch stops
with an error if it is still old. `aka.ms` serves whatever Microsoft currently
publishes, so when Microsoft updates it the pinned hashes stop matching; update
them in `vc_runtime.py`.

#### `enable-hwid-access`

Four values (`smbios_uuid`, `baseboard_serial`, `bios_serial`,
`enclosure_serial`) come from firmware files under `/sys/class/dmi/id` that
Linux makes root-only, and the TPM (`/dev/tpmrm0`) is normally only usable by
the `tss` group. To let your user read the files and use the TPM, run once, as
yourself:

```bash
steam-utility-launcher enable-hwid-access
```

It checks whether they're already readable and does nothing if so. Otherwise it
prints the small script it will run, asks for confirmation (`-y` skips that),
and runs it with `sudo python3 -I -B -` piped over stdin: nothing of this tool
runs as root and no root-owned `.pyc` files are created. The script writes
`/etc/tmpfiles.d/steam-utility-launcher-hwid.conf` (a `z ... 0444` line per
file) and applies it immediately with `systemd-tmpfiles`, so it takes effect at
once (no new shell or logout needed) and again at every boot. Without
systemd-tmpfiles it changes the permissions directly, which lasts until reboot.
Every local user can then read those serials, as WMI already allows on Windows.
If the machine has a TPM it also writes
`/etc/udev/rules.d/70-steam-utility-launcher-tpm.rules`, a `uaccess` rule that
gives the logged-in desktop user access to the TPM's resource manager (applied
at once with `udevadm`). Each open of that device is isolated by the kernel, so
this only lets your own programs use the TPM, the usual arrangement on a
desktop. Undo it all with:

```bash
steam-utility-launcher enable-hwid-access --disable
```

Access to those files is required. `rotk-launcher` checks them first, before
downloading, installing or starting anything, and if any of them exists but
can't be read by your user it stops with an error telling you to run
`enable-hwid-access`. It never carries on with an incomplete fingerprint. (A
file that doesn't exist on your firmware is fine: there is nothing to read.)
The TPM is optional, and only checked when you pass `--tpm`: then a missing
TPM or one your user can't use is an error with the same fix
(`enable-hwid-access`), and so is a TPM that fails to set up.

### ZEmu: King of the Kill

[ZEmu](https://zemu.uk) is a community server for the 2017 Pre-Season 3 H1Z1
client. Install and run its official launcher (on Linux, pointed at your
Proton):

```bash
steam-utility-launcher zemu
```

Unlike ROTK Launcher, ZEmu Launcher has a native Linux build with Wine/Proton
support of its own. It downloads the game client (it uses the same Steam app as
Z1 Battle Royale, 433850, at an older version, so you sign in to Steam in its
window) and starts it through Proton itself. On Linux this command therefore
just:

1. Downloads the latest ZEmu Launcher AppImage from ZEmu's own releases the
   first time, and **verifies its signature** (ZEmu signs releases with a
   minisign key, which is pinned in the tool) before installing or running
   anything. A file that fails verification is never written to disk.
2. Points ZEmu Launcher at the Proton Steam uses for Z1 Battle Royale (else
   the newest stable one, or pass `--proton PATH`), in its own settings file.
   ZEmu **follows** it: if its runtime is anything else it is changed to match
   (and the change is logged as a warning), so switching Z1 to another Proton
   switches ZEmu too. Only the runtime is touched: nothing else in the file is
   changed, Wine switched off there is respected, and ZEmu keeps its own
   dedicated prefix, separate from your Z1/ROTK one. A newer Proton upgrades
   that prefix, which Proton can't undo, so copy it first if that matters.
   `--no-configure` skips this step, for a runtime you want to pick in ZEmu's
   own Properties screen.
3. Runs it, with `WEBKIT_DISABLE_DMABUF_RENDERER=1` set (a blank-window
   workaround for NVIDIA) unless you've set it, and `APPIMAGE_EXTRACT_AND_RUN=1`
   if FUSE 2 isn't installed.

**On Windows,** the same command downloads ZEmu's Windows installer, verifies
its signature against the same pinned key, installs it silently into this
tool's own folder (`ZEmu-Launcher\app` under the tool's data directory) and
runs it. There's no Proton, FUSE or settings step, and ZEmu Launcher starts the
game itself. This path has only been tested with unit tests, not on a real
Windows machine.

**Requirements (Linux):** the AppImage needs FUSE 2 (`libfuse.so.2`) to run
directly, which many distributions no longer install by default: `fuse2` on Arch,
`libfuse2` (`libfuse2t64` on newer Ubuntu) on Debian/Ubuntu, `fuse-libs` on
Fedora. Without it the tool falls back to extracting the AppImage on every
launch (`APPIMAGE_EXTRACT_AND_RUN=1`), which works but starts slower, so
installing it is recommended. You also need Steam with a Proton install and a
Vulkan-capable graphics driver; nothing else outside this project is required.

Like `rotk-launcher`, an existing install isn't updated by this command, because
ZEmu Launcher updates itself once it's running. A newer release is only
mentioned, at most once a day; `--force` installs the latest right away.

None of ROTK's workarounds (the PowerShell stand-in, `enable-hwid-access`, the
Visual C++ runtime step) are applied, since ZEmu's launcher doesn't need them
for itself. Whether the game client needs anything extra under Proton is
untested.

This tool is not affiliated with ZEmu. ZEmu Launcher's licence lets you use and
study it but not redistribute or modify it, so it is downloaded from ZEmu's
releases when you run this and is never bundled here, and none of its code is
used. ZEmu Launcher — https://zemu.uk

### Manual usage

Run any arbitrary Windows executable inside a game's Proton prefix. The game's
Steam App ID is in its store page URL —
`store.steampowered.com/app/`**`570940`**`/Dark_Souls_Remastered/`.

```bash
# Specify the game by its Steam App ID
steam-utility-launcher manual -g 570940 /path/to/SomeTool.exe

# Auto-detect the currently running Proton game
steam-utility-launcher manual --auto /path/to/SomeTool.exe
```

> **`--auto` caveat:** detection scans running processes for an active
> wineserver and picks the first match. If multiple games are running in
> Proton simultaneously the result is unpredictable; use `-g` instead.

Add `-w`/`--wait` to block until the launched process exits, then exit with
its status code. Useful for installers or any tool whose result you need to
check:

```bash
steam-utility-launcher manual -g 433850 --wait /path/to/Installer.exe
echo "exit code: $?"
```

### Finding a game's Proton prefix

Print the `WINEPREFIX` path used by a game, without launching anything:

```bash
steam-utility-launcher prefix-path -g 570940
steam-utility-launcher prefix-path --auto
```

This correctly resolves the game's Steam library (primary or a secondary
library), so it works even for games not installed in the default Steam
library.

## Installed tool locations

Tools are downloaded to:

```
~/.local/share/steam-utility-launcher/<ToolName>/
```

For example, DSR-Gadget lives at
`~/.local/share/steam-utility-launcher/DSR-Gadget/`.

Each tool directory contains a `.github_release_tag` file recording the
installed version. The launcher checks GitHub on every run and updates
automatically when a newer release is available. To force a fresh download,
delete that file:

```bash
rm ~/.local/share/steam-utility-launcher/DSR-Gadget/.github_release_tag
```

## Logging

Add `--log-file FILE` to write logs to a rotating file. Use `-v` / `-q` /
`--debug` to control console verbosity.
