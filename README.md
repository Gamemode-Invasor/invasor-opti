# OptiHLX

OptiHLX is an [Invasor](https://github.com/Gamemode-Invasor/invasor) module that installs [OptiScaler](https://github.com/optiscaler/OptiScaler) in your games from the panel. It installs either the latest OptiScaler nightly or **HelixSR Scaler**: OptiScaler with [HelixSR](https://github.com/lonewolf0622/HelixSR)'s two upscalers, HLX DLSS and HLX FSR 4, which the module compiles on your device. With either, it can add your FSR 4 driver DLLs, and it edits the game's `OptiScaler.ini`.

It lives in the library's Invasor panel only, not in Quick Access: there's nothing to do with it during a game. Three sub-tabs (L2/R2):

- **Game**: OptiScaler in the selected game, and its file.
- **Global**: what the games get, in three groups.

  The **OptiScaler** group shows the OptiScaler build the module has downloaded, and **Download the latest nightly** gets the newest build of [OptiScaler-nightly](https://github.com/optiscaler/OptiScaler-nightly/releases) (it shows in the Tasks panel). The `.7z` is checked against GitHub's checksum and unpacked as it is into `~/.local/share/invasor-opti/optiscaler/`, with `.invasor-opti.version` saying which build it is; the old build is replaced only once the new one is complete, and a build with a symbolic link in it is refused. Unpacking needs `bsdtar` (part of SteamOS).

  Under it, the **HelixSR** group shows the HelixSR Scaler the module has compiled. **Download and compile**, after asking, builds the latest stable release of HelixSR (3.x) on this device:
  - `opti/bin/helixsr-fetch.sh` downloads it and runs HelixSR's own setup in a temporary folder, with its games step turned off: nothing is installed in any game.
  - The setup downloads NVIDIA's DLSS DLL (under [NVIDIA's license](https://github.com/NVIDIA/DLSS/blob/v310.7.0/LICENSE.txt)) and AMD's FSR 4 driver file `amdxcffx64.dll` (read out of AMD's driver package, about 420 MB), and builds HLX DLSS's network and HLX FSR 4's shaders from them. The DLLs it makes carry data made from those files: they're for your own device, don't share them.
  - What's kept, in `~/.local/share/invasor-opti/helixsr/` (replaced only once a new build is complete and checked), is HelixSR Scaler as the release ships it (`OptiScaler.dll`, `OptiScaler.ini`, the `OptiScaler` folder with HLX FSR 4 and AMD's FSR next to it, `Licenses`), `HelixSR/helixsr.dll` (HLX DLSS) with `HelixSR/helixsr.ini`, and `.invasor-opti.version`.
  - The setup only uses AMD's `amdxcffx64.dll` in memory; the script keeps the file, and it goes to Custom DLLs when that has none (one you chose stays).
  - It runs as a task in the Tasks panel (several minutes, not while a game is running) and needs `curl`, `unzip` and a Proton in Steam (in `steamapps/common` or `compatibilitytools.d`), which HelixSR's setup uses for its shader compilers, plus Python 3 with numpy (or the setup downloads a portable one).
  - The module asks GitHub for the latest stable tag (at most once an hour once it has an answer; a failed check is retried after 2 minutes) and compares it with the compiled one: the button then reads **Update to v…** when there's a newer one, or **Recompile** when it's up to date.

  Last, **Custom DLLs** keeps the DLLs the module can't ship, with the version each file says it is: **FSR 4 (AMD driver DLLs)**, `amdxcffx64.dll` (DirectX 12) and `amdvkffx64.dll` (Vulkan; INT8 RDNA2 and INT8 Lite there need it) from AMD's driver. **Choose amdxcffx64.dll (DirectX 12)…** and **Choose amdvkffx64.dll (Vulkan)…** are always there and open Invasor's file picker (a big window that lists folders and `.dll` files; a `.dll` is picked by pressing it): choosing one keeps the other, and takes the other too when it's next to the one chosen. The module keeps a copy in `~/.local/share/invasor-opti/custom/`, so the original can move or be on a card that isn't there. **Remove FSR 4 (AMD driver DLLs)** forgets both; games that have them keep theirs.
- **Credits**: author, thanks to OptiScaler and HelixSR, and that OptiHLX isn't affiliated with either.

The game's file is read from its Proton prefix, `compatdata/<appid>/pfx/drive_c/windows/system32/umu/OptiScaler.ini`, the folder Wine's OptiScaler hack in GE-Proton and proton-cachyos loads OptiScaler from (with `WINE_OPTISCALER_NAME=dxgi.dll %command%` in the game's launch options).

Under the file's path, the Game tab installs OptiScaler in that folder:

- When OptiScaler or HelixSR Scaler is installed (there is an `OptiScaler.dll`, whoever put it there), the launch line, `WINE_OPTISCALER_NAME=dxgi.dll %command%`, with a **Copy** button, after 🟢 when the game's launch options have it (whatever else is around it) or 🔴 when they don't. The options are asked to Steam; when Steam can't answer they're read from its files, and a Steam game's (`localconfig.vdf`, which Steam writes only from time to time) may lag. When Steam can change them, **Apply to the game** writes the line into the game's launch options: it adds `WINE_OPTISCALER_NAME=<dll>.dll` (or replaces the one there), adds `%command%` if it's missing (what was there before are the game's arguments, so they go after it), and keeps the rest; Steam shows a notification. Copy stays for a Steam without that call. The game's launch options need at least that line, and the game must run with GE-Proton or Proton-CachyOS (the two with Wine's OptiScaler hack).
- A folded **Installation** group:
  - **Install OptiScaler** copies the nightly downloaded in Global: `OptiScaler.dll`, the `OptiScaler` folder and `.invasor-opti.version`, plus, if the game has no `OptiScaler.ini`, the nightly's own with `[Libraries] OptiDllPath=c:\windows\system32\umu\OptiScaler` set (an existing one is kept as it is). Set its other options in the game's file below. Then it links the chosen DLL name to `OptiScaler.dll`. Its upscalers are OptiScaler's own (FSR 2/3/4, XeSS), plus the FSR 4 driver DLLs if you turn them on.
  - **Install HelixSR Scaler** copies the HelixSR Scaler compiled in Global the same way, with its `HelixSR` and `Licenses` folders, and its own `OptiScaler.ini` (a game's that has a `[HelixSR]` section is kept; any other is kept aside as `OptiScaler.ini.bak`). If a step fails, what it installed is removed again. In that file it sets `[Libraries] OptiDllPath=c:\windows\system32\umu\OptiScaler` (without it, HelixSR Scaler loads its DLLs from the game's `.exe` folder, where a game's own FSR or XeSS may be) and `FfxDx12SRPath=C:\windows\system32\umu\OptiScaler\amd_fidelityfx_upscaler_dx12.dll` (HLX FSR 4), and it copies the FSR 4 driver DLLs if they're kept in Global. Its menu then has every upscaler: HLX DLSS (as DLSS), HLX FSR 4 with AMD's FSR versions after it, AMD's FSR 4 from the driver, and XeSS.
  - Only one of the two is installed in a game: to change, uninstall the one there first.
  - **Update to …** shows when Global has another build of the game's kind than the game's (or the game's was put there by hand): it copies the build's files over the game's (`OptiScaler.dll`, every file of its folders, `.invasor-opti.version`). Everything else stays: the game's `OptiScaler.ini` and `HelixSR/helixsr.ini`, its DLL name, its custom DLLs and anything you added to the folders. A DLL name that was a copy of `OptiScaler.dll` becomes a link to it.
  - **Uninstall** (after asking) removes `OptiScaler.dll`, `OptiScaler.ini`, the `OptiScaler` folder (and, for HelixSR Scaler, the `HelixSR` and `Licenses` folders), `.invasor-opti.version`, and every `dxgi.dll`, `d3d12.dll`, `dbghelp.dll` or `winmm.dll` that is a link to `OptiScaler.dll` or a copy of it. The folders go whole, with anything you added there (plugins, logs…); nothing else in `umu` is touched.
  - **DLL to load** (`dxgi`, `d3d12`, `dbghelp`, `winmm`): the name the game loads OptiScaler as. Changing it while OptiScaler is installed renames the link and changes the launch line. A DLL of that name that isn't OptiScaler is never replaced.
  - With either installed, a switch per custom DLL (enabled once it's chosen in Global): **FSR 4 driver DLLs** copies them into the game's `OptiScaler` folder; turning it off removes them.

The game's file is shown from the options in it, so it follows whatever OptiScaler version is installed:

- Everything sits in one **All Configuration** group, folded, and inside it one group per `[Section]`, folded too. **X** folds or unfolds the group the cursor is in.
- One option per key, with its current value. When the comments above the key name its values (`true or false`, `0 = FSR 4.0.2 | 1 = FSR 3.1.5`, `0.0 - 1.0`, `xess, fsr21, ffx, dlss`) it is a list, with `auto` (OptiScaler's own default) first; otherwise it is a text field, where empty means `auto`.
- **Hide default values** (just above it) hides the options whose value is still OptiScaler's own (from the build in Global of the game's kind, else the file in `opti/assets/`), and the groups left empty: what's left is what was changed.
- **Y** shows the key's description, the comments OptiScaler wrote above it.
- Every change is written at once (a list once its value stays put for a moment, so going through it with ←→ doesn't write each value on the way). Only the line of that key changes: comments, order and line ends stay as they are. If another program changed the file meanwhile, nothing is written and the page reloads.

## Screenshots

<p align="center">
  <img src="docs/game.png" alt="Game page: the launch line with Copy and Apply to the game, the Installation group with HelixSR Scaler installed, and the game's OptiScaler.ini" width="48%">
  <img src="docs/global.png" alt="Global page: the downloaded nightly, HelixSR and Custom DLLs" width="48%">
</p>

## Requirements
Invasor 0.3.0 or newer (`min_core`): it uses `ctx.http` to ask GitHub and download the nightly, `ctx.archive` to unpack it and swap folders, `ctx.paths` and `ctx.vdf` to find a game's prefix and read Steam's files, `ctx.steam` for the launch options, and `ctx.copy` and `ctx.pickPath` in the panel. `bsdtar` (part of SteamOS) unpacks the `.7z`.

## Build, test and install
With the core checked out next to this repository (`../invasor`):

```sh
python3 ../invasor/tools/pack_module.py opti       # build, check, tests -> opti-<version>.zip
python3 ../invasor/tools/install_module.py opti    # or install that zip from ⚙ Settings › Install module
```

## Changes

### 0.3.0
- HelixSR 3.x: **Download and compile** (now its own **HelixSR** group in Global) keeps HelixSR Scaler with HLX DLSS and HLX FSR 4, and the Game tab can install **HelixSR Scaler** instead of OptiScaler, with every upscaler in its menu (HLX DLSS, HLX FSR 4, AMD's FSR versions, the FSR 4 driver DLLs, XeSS). The build now also downloads AMD's driver package (about 420 MB) for HLX FSR 4's shaders.
- Compiling HelixSR keeps AMD's `amdxcffx64.dll` (the one its setup downloads for HLX FSR 4) in Custom DLLs when there's none. Custom DLLs: one Choose button per FSR 4 DLL (DirectX 12 and Vulkan); choosing one keeps the other.
- The Global `OptiScaler.ini` and its resets are gone: a game gets its build's own file (the nightly's or HelixSR Scaler's, so it always has that version's options) with only `OptiDllPath` set (and HLX FSR 4's `FfxDx12SRPath` for HelixSR Scaler); set its other options in Game. The old Global file stays in `~/.local/share/invasor-opti/` unused (it goes when the module is uninstalled with its data).
- The **HelixSR** custom DLL and its switch are gone (HelixSR 3.x has no DLL for the official OptiScaler's FidelityFX slot): a HelixSR 2.0 DLL already in a game stays there (with its `FfxDx12SRPath` in the game's `OptiScaler.ini`): remove it by hand, or uninstall OptiScaler in that game and install it again.
- The module is now called **OptiHLX** (it isn't affiliated with OptiScaler); its id (`opti`), tab and folders stay.
- README: new screenshots.

### 0.2.0
- Needs Invasor 0.3.0: the nightly is downloaded, checked and unpacked by the core (`ctx.http`, `ctx.archive`), Steam's folders and files come from `ctx.paths` and `ctx.vdf`, and the panel uses the core's file picker and clipboard. 7-Zip is no longer used: `bsdtar` (part of SteamOS) unpacks the `.7z`.
- Game: **Apply to the game** writes the launch line into the game's launch options (when Steam can), keeping the rest of the line; the 🟢/🔴 mark asks Steam first, so it no longer lags for Steam games.
- README: screenshots.

### 0.1.1
- Global → Custom DLLs: **Download and compile** builds the latest stable HelixSR on this device and keeps its `helixsr.dll` and `helixsr.ini` (it replaces HelixSR in the Companion module). The button tells when a newer stable HelixSR is out (**Update to v…**) or yours is the latest (**Recompile**).

### 0.1.0
- First release.

## License
[GNU General Public License v3.0 or later](LICENSE) (GPL-3.0-or-later).

### Third-party
- `opti/assets/OptiScaler.optiscaler.ini` is OptiScaler's own file (v10.0.0-pre1, nightly 2026-10-23), used when a build has none: [OptiScaler](https://github.com/optiscaler/OptiScaler) is GPL-3.0.
- No DLL is shipped with the module. OptiScaler is downloaded from its own nightly releases, which carry the licenses of what they include (FidelityFX, XeSS, DirectX) in their `Licenses` folder. AMD's driver DLLs are files you bring (or the one HelixSR's setup downloads, kept), and HelixSR is built on your machine with **Download and compile**: they're copied only on your machine. HelixSR Scaler comes with the HelixSR release (GPL-3.0, based on OptiScaler, [source](https://github.com/lonewolf0622/HelixSR-Scaler)); HelixSR's own DLLs have HelixSR's license. To build them, HelixSR's setup downloads NVIDIA's DLSS DLL ([NVIDIA's license](https://github.com/NVIDIA/DLSS/blob/v310.7.0/LICENSE.txt)) and AMD's driver package and uses them once; the DLLs it makes carry data made from them and are for your own device only.
- OptiHLX isn't affiliated with OptiScaler, HelixSR, NVIDIA or AMD: the name only says what it installs (OptiScaler, and HelixSR's HLX upscalers).
