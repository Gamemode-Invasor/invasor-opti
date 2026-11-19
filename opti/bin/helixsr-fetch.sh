#!/usr/bin/env bash
# helixsr-fetch.sh - downloads HelixSR (the latest stable one, or the newest of all), builds it on this device and leaves HelixSR Scaler
# with HelixSR's two upscalers, laid out as a game's umu folder.
#
#   ./helixsr-fetch.sh --out DIR [--channel stable|prerelease] [--preset-k] [--yes]
#
#   --out DIR       where to leave the result (made if needed)
#   --channel C     stable (default): the latest stable release; prerelease: the newest release of all
#   --preset-k      also build Preset K for HLX DLSS (HelixSR 3.2 and later): it takes much longer (about 25 minutes more)
#   --yes, -y       don't ask (accepts the download of NVIDIA's DLSS DLL the build needs)
#
# Leaves in DIR (HelixSR 3.2):
#   OptiScaler.dll, OptiScaler.ini, OptiScaler/, Licenses/
#                   HelixSR Scaler (OptiScaler with HelixSR) as the release ships it; OptiScaler/ holds HLX FSR 4
#                   (amd_fidelityfx_upscaler_dx12.dll, shipped built with its shaders), AMD's FSR next to it
#                   (.amd.dll) and custom/ (HLX FSR 4 Full)
#   HelixSR/helixsr.dll, HelixSR/helixsr.ini
#                   HLX DLSS with its network embedded, and its settings
#   HelixSR/k/      Preset K for HLX DLSS, only with --preset-k (HelixSR 3.2 and later); if it can't be built the
#                   rest is still left, without this folder
#   VERSION         the release's tag
#   nvngx_dlss.dll  NVIDIA's DLSS DLL the build used (the version and SHA-256 HelixSR's setup pins), downloaded
#                   here and given to the setup: kept for games whose DLSS needs it next to OptiScaler
# Everything is built in a temporary folder, removed always (zip, portable Python, shader compilers, Wine prefix):
# under $TMPDIR, so point it at a disk when /tmp is in RAM. helixsr-setup.sh runs unchanged, but its games step is disabled in the temporary copy: nothing is installed in any game.
# Needs: bash, curl, unzip, Python 3 with numpy (or the setup downloads a portable one) and Proton/Wine.
#
# NOTICE: HLX DLSS's network comes from NVIDIA's DLSS DLL (its license). The DLLs built are for your own device:
# don't share or upload them.
set -euo pipefail

REPO="lonewolf0622/HelixSR"
OUT=""
CHANNEL="stable"
PRESET_K=0
ASSUME_YES=0
DLSS_LICENSE="https://github.com/NVIDIA/DLSS/blob/v310.7.0/LICENSE.txt"
DLL_NAME="helixsr.dll"

say() { printf '[helixsr-fetch] %s\n' "$*"; }
die() { printf '[helixsr-fetch] error: %s\n' "$*" >&2; exit 1; }
usage() { sed -n '2,29p' "$0" | sed 's/^# \{0,1\}//'; }

# --- arguments ---
while [ $# -gt 0 ]; do
    case "$1" in
        --out) [ $# -ge 2 ] || die "--out needs a folder"; OUT="$2"; shift ;;
        --out=*) OUT="${1#--out=}" ;;
        --channel) [ $# -ge 2 ] || die "--channel needs stable or prerelease"; CHANNEL="$2"; shift ;;
        --channel=*) CHANNEL="${1#--channel=}" ;;
        --preset-k) PRESET_K=1 ;;
        --yes|-y) ASSUME_YES=1 ;;
        -h|--help) usage; exit 0 ;;
        *) die "unknown option: $1 (try --help)" ;;
    esac
    shift
done
[ -n "$OUT" ] || die "--out is needed"
case "$CHANNEL" in
    stable) API="https://api.github.com/repos/$REPO/releases/latest" ;;  # the latest stable one
    prerelease) API="https://api.github.com/repos/$REPO/releases?per_page=10" ;;  # newest first
    *) die "--channel must be stable or prerelease" ;;
esac
[ "$(uname -m)" = x86_64 ] || die "x86-64 Linux only"
for t in curl unzip; do
    command -v "$t" >/dev/null || die "missing tool: $t"
done

# --- which version ---
say "looking for the latest ${CHANNEL} version..."
JSON=$(curl -fsSL -H 'Accept: application/vnd.github+json' "$API") || die "can't query $API"
# The prerelease channel answers a list, newest first: keep its first release. GitHub pretty-prints its answers, so
# each release's object starts at two spaces of indentation; if that changes there is no release below, and it dies.
if [ "$CHANNEL" = prerelease ]; then
    JSON=$(printf '%s\n' "$JSON" | awk '/^  \{/ { n++ } n == 1') || true
fi
TAG=$(printf '%s' "$JSON" | grep -o '"tag_name": *"[^"]*"' | head -n1 | sed 's/.*: *"\(.*\)"/\1/' || true)
# The release also has other zips (HelixSR Scaler): HelixSR-<version>.zip, else the first one.
ZIPS=$(printf '%s' "$JSON" | grep -o '"browser_download_url": *"[^"]*\.zip"' | sed 's/.*: *"\(.*\)"/\1/' || true)
URL=$(printf '%s\n' "$ZIPS" | grep -E '/HelixSR-[0-9][^/]*\.zip$' | head -n1 || true)
[ -n "$URL" ] || URL=$(printf '%s\n' "$ZIPS" | head -n1)
[ -n "$TAG" ] && [ -n "$URL" ] || die "no release with a zip in $API"
SUMS_URL=$(printf '%s' "$JSON" | grep -o '"browser_download_url": *"[^"]*/SHA256SUMS\.txt"' | head -n1 | sed 's/.*: *"\(.*\)"/\1/' || true)
[ -n "$SUMS_URL" ] || die "$TAG has no SHA256SUMS.txt: the zip can't be checked"
say "version: $TAG"

# --- confirmation (NVIDIA's DLL is downloaded and used once) ---
if [ "$ASSUME_YES" != 1 ]; then
    [ -t 0 ] || die "no interactive terminal: use --yes to accept the download of NVIDIA's DLL"
    cat <<EOF
HelixSR $TAG will be downloaded and, to build it, NVIDIA's DLSS 310.7.0 DLL; the file is left in the folder
too, for this device only.
NVIDIA's license: $DLSS_LICENSE
The DLLs built carry data made from NVIDIA's file: they're for this device only, don't share them.
Into: $OUT
EOF
    read -r -p "Continue? [Y/n] " ans || ans=n
    case "$ans" in ''|y|Y|yes|YES) ;; *) die "cancelled" ;; esac
fi

# --- temporary folder (removed always) ---
T=$(mktemp -d) || die "can't make a temporary folder"
cleanup() { rm -rf "$T"; }
trap cleanup EXIT
trap 'exit 130' INT TERM

say "downloading..."
curl -fL --retry 3 -sS -o "$T/helixsr.zip" "$URL" || die "download failed: $URL"
# The release's own SHA256SUMS.txt says what the zip is: a damaged or cut download stops here.
curl -fL --retry 3 -sS -o "$T/SHA256SUMS.txt" "$SUMS_URL" || die "download failed: $SUMS_URL"
ZIP_NAME=${URL##*/}
WANT=$(awk -v n="$ZIP_NAME" '{ f = $2; sub(/^[*.\/]+/, "", f) } f == n { print $1; exit }' "$T/SHA256SUMS.txt")
[ -n "$WANT" ] || die "SHA256SUMS.txt doesn't list $ZIP_NAME"
echo "$WANT  $T/helixsr.zip" | sha256sum -c --status || die "$ZIP_NAME doesn't match the SHA-256 of SHA256SUMS.txt"
mkdir -p "$T/pkg" "$T/data" "$T/cache" "$T/config"
unzip -q "$T/helixsr.zip" -d "$T/pkg" || die "can't unpack the zip"
SETUP=$(find "$T/pkg" -name helixsr-setup.sh | head -n1)
[ -n "$SETUP" ] || die "this release has no helixsr-setup.sh"
PKG=$(dirname "$SETUP")

# --- keep the games step away: nothing is installed in any game here ---
# HelixSR v2.0.0 (2026-10-19) makes helixsr-setup.sh forward --yes to its games step
# (setup/helixsr_install.py), which then installs HelixSR in EVERY game it finds.
# Two barriers; if neither can be set up, the script dies before the setup runs:
#   A) setup/helixsr_install.py of the temporary copy -> no-op (the games step doesn't run);
#   B) a temporary HOME without Steam libraries (the scan uses Path.home()): the scan finds no game;
#      only the real Proton/Wine tool is linked there, which the build needs for the shader compiler
#      (compatibilitytools.d isn't a library).
A_OK=0
B_OK=0
SETUP_HOME="$HOME"
INSTALLER="$PKG/setup/helixsr_install.py"
if [ -f "$INSTALLER" ]; then
    cat > "$INSTALLER" <<'PY'
#!/usr/bin/env python3
import sys
print("[helixsr-setup] games step skipped (helixsr-fetch: nothing goes into games)", flush=True)
sys.exit(0)
PY
    A_OK=1
    say "games step disabled (nothing goes into games)"
else
    say "warning: this release has no setup/helixsr_install.py (did HelixSR change?): the temporary HOME is needed"
fi
for p in "$HOME"/.local/share/Steam/steamapps/common/Proton*/files/bin/wine \
         "$HOME"/.steam/steam/steamapps/common/Proton*/files/bin/wine \
         "$HOME"/.local/share/Steam/compatibilitytools.d/*/files/bin/wine \
         "$HOME"/.steam/root/compatibilitytools.d/*/files/bin/wine; do
    if [ -x "$p" ]; then
        TOOL=${p%/files/bin/wine}
        mkdir -p "$T/home/.local/share/Steam/compatibilitytools.d"
        ln -sfn "$TOOL" "$T/home/.local/share/Steam/compatibilitytools.d/$(basename "$TOOL")"
        SETUP_HOME="$T/home"
        B_OK=1
        say "temporary HOME for the setup (games hidden; Proton/Wine linked: $(basename "$TOOL"))"
        break
    fi
done
if [ "$B_OK" = 0 ]; then
    say "warning: no Proton/Wine found for the temporary HOME: the setup runs with the real HOME"
fi
if [ "$A_OK" = 0 ] && [ "$B_OK" = 0 ]; then
    die "can't disable or isolate the games step: check helixsr-fetch.sh against this release"
fi

# --- NVIDIA's DLSS DLL: downloaded here, as the setup pins it, and given to it with --dlss (so it's kept) ---
SETUP_PY="$PKG/setup/helixsr_setup.py"
DLSS_URL=$(sed -n "s/^DLSS_URL = '\(https:[^']*\)'.*/\1/p" "$SETUP_PY" 2>/dev/null | head -n1) || true
DLSS_SHA=$(sed -n "s/^DLSS_SHA256 = '\([0-9a-f]\{64\}\)'.*/\1/p" "$SETUP_PY" 2>/dev/null | head -n1) || true
DLSS_OUT="$T/nvidia/nvngx_dlss.dll"
DLSS_ARGS=()
if [ -n "$DLSS_URL" ] && [ -n "$DLSS_SHA" ]; then
    say "downloading nvngx_dlss.dll from NVIDIA..."
    mkdir -p "$T/nvidia"
    curl -fL --retry 3 -sS -o "$DLSS_OUT" "$DLSS_URL" || die "download failed: $DLSS_URL"
    echo "$DLSS_SHA  $DLSS_OUT" | sha256sum -c --status || die "nvngx_dlss.dll isn't the one HelixSR's setup expects"
    DLSS_ARGS=(--dlss "$DLSS_OUT")
else
    say "warning: no DLSS_URL/DLSS_SHA256 in setup/helixsr_setup.py (did HelixSR change?): the setup downloads it, not kept"
fi

# --- Preset K: only when asked, and when the release has it (HelixSR 3.2 and later) ---
K_ARGS=()
if [ "$PRESET_K" = 1 ]; then
    if [ -f "$PKG/setup/preset-k/helixsr.dll" ]; then
        K_ARGS=(--preset-k)
        say "Preset K will be built too (it takes much longer)"
    else
        say "warning: this release has no Preset K: it's built without it"
    fi
fi

# --- build with the official setup ---
# --yes accepts its downloads (agreed above); since HelixSR v2.0.0 it also forwards --yes to the games
# step (hence the no-op and the temporary HOME above). XDG_* send Python/DXC/prefix and caches to the
# temporary folder.
say "building HelixSR (it can take several minutes)..."
LOG="$T/build.log"
set +e
( cd "$PKG" && HOME="$SETUP_HOME" XDG_DATA_HOME="$T/data" XDG_CACHE_HOME="$T/cache" XDG_CONFIG_HOME="$T/config" \
    bash ./helixsr-setup.sh --yes "${DLSS_ARGS[@]}" "${K_ARGS[@]}" </dev/null ) 2>&1 \
    | tee "$LOG"
set -e

# --- check the result: HelixSR 3.2's layout ---
#   HelixSR/helixsr.dll                                   HLX DLSS, its network at the end (HXSRNET1 marker)
#   helixsr-scaler/OptiScaler.dll                         HelixSR Scaler
#   helixsr-scaler/OptiScaler/amd_fidelityfx_upscaler_dx12.dll
#                                                         HLX FSR 4, its shaders at the end (HLXFSR4D marker)
tail_is() { [ -f "$1" ] && [ "$(wc -c <"$1")" -gt 1048576 ] && [ "$(tail -c "$2" "$1" | head -c 8 | tr -d '\0')" = "$3" ]; }
fail() {
    printf '[helixsr-fetch] ---- last lines of the build ----\n' >&2
    tail -n 30 "$LOG" >&2 || true
    die "$1"
}

SCALER="$PKG/helixsr-scaler"
FSR4="$SCALER/OptiScaler/amd_fidelityfx_upscaler_dx12.dll"
tail_is "$PKG/HelixSR/$DLL_NAME" 32 HXSRNET1 || fail "the build made no HLX DLSS with its network (HelixSR/$DLL_NAME)"
[ -f "$SCALER/OptiScaler.dll" ] || fail "this release has no HelixSR Scaler (helixsr-scaler/OptiScaler.dll)"
tail_is "$FSR4" 24 HLXFSR4D || fail "the build made no HLX FSR 4 with its shaders (helixsr-scaler/OptiScaler/)"

if [ "${#K_ARGS[@]}" -gt 0 ] && [ ! -f "$PKG/HelixSR/k/helixsr.dll" ]; then
    say "warning: Preset K wasn't built (the reason is in the setup's lines above): the rest is left without it"
fi

# --- leave the result: HelixSR Scaler as a game's umu folder, with HelixSR/ next to OptiScaler.dll ---
mkdir -p "$OUT" || die "can't make $OUT"
cp -a "$SCALER/." "$OUT/" || die "can't copy HelixSR Scaler to $OUT"
rm -f "$OUT"/README*.txt "$OUT/HelixSR-Scaler-package.txt" "$OUT/setup_linux.sh" "$OUT/setup_windows.bat"
cp -a "$PKG/HelixSR" "$OUT/" || die "can't copy HelixSR to $OUT"
printf '%s\n' "$TAG" > "$OUT/VERSION"
if [ -s "$DLSS_OUT" ]; then
    cp -f "$DLSS_OUT" "$OUT/nvngx_dlss.dll" || die "can't copy nvngx_dlss.dll to $OUT"
    say "NVIDIA's DLSS DLL kept: nvngx_dlss.dll"
fi
say "done: HelixSR $TAG (HelixSR Scaler, HLX DLSS and HLX FSR 4) in $OUT"
