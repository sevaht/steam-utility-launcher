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
