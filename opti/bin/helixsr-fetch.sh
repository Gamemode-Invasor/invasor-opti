#!/usr/bin/env bash
# helixsr-fetch.sh - downloads the latest stable HelixSR, builds it on this device and leaves HelixSR Scaler
# with HelixSR's two upscalers, laid out as a game's umu folder.
#
#   ./helixsr-fetch.sh --out DIR [--yes]
#
#   --out DIR       where to leave the result (made if needed)
#   --yes, -y       don't ask (accepts the downloads of NVIDIA's DLSS DLL and AMD's driver package the build needs)
#
# Leaves in DIR (HelixSR 3.x):
#   OptiScaler.dll, OptiScaler.ini, OptiScaler/, Licenses/
#                   HelixSR Scaler (OptiScaler with HelixSR) as the release ships it; OptiScaler/ holds HLX FSR 4
#                   (amd_fidelityfx_upscaler_dx12.dll, with its shaders) and AMD's FSR next to it (.amd.dll)
#   HelixSR/helixsr.dll, HelixSR/helixsr.ini
#                   HLX DLSS with its network embedded, and its settings
#   VERSION         the release's tag
#   amdxcffx64.dll  AMD's FSR 4 driver file the build used (2.4.0.4370, checked by HelixSR's setup), when the
#                   setup got it: it's AMD's own DLL, kept for OptiScaler's FSR 4 (for this device only)
# Everything is built in a temporary folder, removed always (zip, portable Python, shader compilers, AMD's
# package, Wine prefix): under $TMPDIR, so point it at a disk when /tmp is in RAM. helixsr-setup.sh runs
# unchanged, but its games step is disabled in the temporary copy: nothing is installed in any game.
# Needs: bash, curl, unzip, Python 3 with numpy (or the setup downloads a portable one) and Proton/Wine.
#
# NOTICE: HLX DLSS's network comes from NVIDIA's DLSS DLL (its license) and HLX FSR 4's shaders from AMD's FSR 4
# driver file. The DLLs built are for your own device: don't share or upload them.
set -euo pipefail

REPO="lonewolf0622/HelixSR"
API="https://api.github.com/repos/$REPO/releases/latest"  # the latest stable one
OUT=""
ASSUME_YES=0
DLSS_LICENSE="https://github.com/NVIDIA/DLSS/blob/v310.7.0/LICENSE.txt"
DLL_NAME="helixsr.dll"

say() { printf '[helixsr-fetch] %s\n' "$*"; }
die() { printf '[helixsr-fetch] error: %s\n' "$*" >&2; exit 1; }
usage() { sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; }

# --- arguments ---
while [ $# -gt 0 ]; do
    case "$1" in
        --out) [ $# -ge 2 ] || die "--out needs a folder"; OUT="$2"; shift ;;
        --out=*) OUT="${1#--out=}" ;;
        --yes|-y) ASSUME_YES=1 ;;
        -h|--help) usage; exit 0 ;;
        *) die "unknown option: $1 (try --help)" ;;
    esac
    shift
done
[ -n "$OUT" ] || die "--out is needed"
[ "$(uname -m)" = x86_64 ] || die "x86-64 Linux only"
for t in curl unzip; do
    command -v "$t" >/dev/null || die "missing tool: $t"
done

# --- which version ---
say "looking for the latest stable version..."
JSON=$(curl -fsSL -H 'Accept: application/vnd.github+json' "$API") || die "can't query $API"
TAG=$(printf '%s' "$JSON" | grep -o '"tag_name": *"[^"]*"' | head -n1 | sed 's/.*: *"\(.*\)"/\1/')
# The release also has other zips (HelixSR Scaler): HelixSR-<version>.zip, else the first one.
ZIPS=$(printf '%s' "$JSON" | grep -o '"browser_download_url": *"[^"]*\.zip"' | sed 's/.*: *"\(.*\)"/\1/' || true)
URL=$(printf '%s\n' "$ZIPS" | grep -E '/HelixSR-[0-9][^/]*\.zip$' | head -n1 || true)
[ -n "$URL" ] || URL=$(printf '%s\n' "$ZIPS" | head -n1)
[ -n "$TAG" ] && [ -n "$URL" ] || die "no release with a zip in $API"
say "version: $TAG"

# --- confirmation (NVIDIA's DLL is downloaded and used once) ---
if [ "$ASSUME_YES" != 1 ]; then
    [ -t 0 ] || die "no interactive terminal: use --yes to accept the downloads of NVIDIA's DLL and AMD's package"
    cat <<EOF
HelixSR $TAG will be downloaded and, to build it, NVIDIA's DLSS 310.7.0 DLL and AMD's driver package (about
420 MB, only its FSR 4 file is read; NVIDIA's DLL is used once and removed, AMD's file is left in the folder too).
NVIDIA's license: $DLSS_LICENSE
The DLLs built carry data made from NVIDIA's and AMD's files: they're for this device only, don't share them.
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

# --- keep AMD's FSR 4 file: the setup only uses it in memory ---
# setup/hlxfsr4/hlx/amd.py's get() returns (the DLL's bytes, where they came from), checked against its SHA-256;
# in the temporary copy it also writes them to $HELIXSR_FETCH_AMD. Without that file, nothing is kept.
AMD_PY="$PKG/setup/hlxfsr4/hlx/amd.py"
AMD_OUT="$T/amd/amdxcffx64.dll"
if [ -f "$AMD_PY" ]; then
    mkdir -p "$T/amd"
    cat >> "$AMD_PY" <<'PY'


_helixsr_fetch_get = get


def get(*args, **kwargs):  # helixsr-fetch: keep the file too
    got = _helixsr_fetch_get(*args, **kwargs)
    out = os.environ.get("HELIXSR_FETCH_AMD")
    if out:
        with open(out, "wb") as f:
            f.write(got[0])
    return got
PY
else
    say "warning: this release has no setup/hlxfsr4/hlx/amd.py (did HelixSR change?): AMD's file won't be kept"
fi

# --- build with the official setup ---
# --yes accepts its downloads (agreed above); since HelixSR v2.0.0 it also forwards --yes to the games
# step (hence the no-op and the temporary HOME above). XDG_* send Python/DXC/prefix and caches to the
# temporary folder.
say "building HelixSR (it can take several minutes)..."
LOG="$T/build.log"
set +e
( cd "$PKG" && HOME="$SETUP_HOME" XDG_DATA_HOME="$T/data" XDG_CACHE_HOME="$T/cache" XDG_CONFIG_HOME="$T/config" \
    HELIXSR_FETCH_AMD="$AMD_OUT" bash ./helixsr-setup.sh --yes </dev/null ) 2>&1 \
    | tee "$LOG"
set -e

# --- check the result: HelixSR 3.x's layout ---
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

# --- leave the result: HelixSR Scaler as a game's umu folder, with HelixSR/ next to OptiScaler.dll ---
mkdir -p "$OUT" || die "can't make $OUT"
cp -a "$SCALER/." "$OUT/" || die "can't copy HelixSR Scaler to $OUT"
rm -f "$OUT"/README*.txt "$OUT/HelixSR-Scaler-package.txt" "$OUT/setup_linux.sh" "$OUT/setup_windows.bat"
cp -a "$PKG/HelixSR" "$OUT/" || die "can't copy HelixSR to $OUT"
printf '%s\n' "$TAG" > "$OUT/VERSION"
if [ -s "$AMD_OUT" ]; then
    cp -f "$AMD_OUT" "$OUT/amdxcffx64.dll" || die "can't copy amdxcffx64.dll to $OUT"
    say "AMD's FSR 4 file kept: amdxcffx64.dll"
fi
say "done: HelixSR $TAG (HelixSR Scaler, HLX DLSS and HLX FSR 4) in $OUT"
