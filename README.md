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

> **Note:** Always launch the game first, then run the utility.

### Preset utilities

After launching Dark Souls: Remastered, run DSR-Gadget:
```bash
steam-utility-launcher dsr-gadget
```

After launching Hitman WoA, run the Peacock private server:
```bash
steam-utility-launcher hitman-peacock
```

After launching Dark Souls: Remastered, run SilkySouls:
```bash
steam-utility-launcher silky-souls
```

### Manual usage

Run any arbitrary Windows executable inside a game's Proton prefix:
```bash
# Specify the game by its Steam App ID
steam-utility-launcher manual -g 570940 /path/to/SomeTool.exe

# Auto-detect the currently running Proton game
steam-utility-launcher manual --auto /path/to/SomeTool.exe
```

## Logging

Add `--log-file FILE` to write logs to a rotating file. Use `-v` / `-q` /
`--debug` to control console verbosity.
