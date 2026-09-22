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
that, `rotk-launcher` temporarily swaps in a small native
`powershell.exe` replacement (see `resources/powershell_shim.c`) that
answers that specific check using real Win32 process enumeration, only for
the duration of the installer subprocess. Whatever was at `powershell.exe`
before (Wine's stub, in practice) is put back exactly as it was immediately
afterward, so nothing about the prefix is left changed. The shim binaries
are prebuilt and shipped with the package, so this needs nothing extra
installed. (Maintainers rebuilding them from `resources/powershell_shim.c`
after a change need a mingw-w64 cross-compiler — see
`build-powershell-shim` at the repo root.)

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
