#!/usr/bin/env bash
# OpenCut Studio installer for Ubuntu / Debian desktops.
#
#   ./install.sh              install for the current user (recommended)
#   ./install.sh --system     install into /opt with a menu entry for everyone
#   ./install.sh --no-deps    skip apt/pip, just install the launcher
#
# Everything the editor needs is free software and runs offline.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ID="opencut-studio"
APP_NAME="OpenCut Studio"
MODE="user"
SKIP_DEPS=0

for arg in "$@"; do
  case "$arg" in
    --system) MODE="system" ;;
    --no-deps) SKIP_DEPS=1 ;;
    -h|--help)
      sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m==>\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m==>\033[0m %s\n' "$*" >&2; exit 1; }

# --------------------------------------------------------------------------- #
# 1. System packages
# --------------------------------------------------------------------------- #
install_apt_packages() {
  local packages=(
    ffmpeg
    python3
    python3-venv
    python3-pip
    fonts-dejavu-core
    libgl1
    libegl1
    libxkbcommon0
    libdbus-1-3
    libfontconfig1
    libglib2.0-0
  )

  # ALSA library: libasound2t64 on Ubuntu 24.04+, libasound2 on older.
  local alsa_pkg=""
  if apt-cache show libasound2t64 >/dev/null 2>&1; then
    local alsa_section
    alsa_section=$(apt-cache showpkg libasound2t64 2>/dev/null | head -5)
    if echo "$alsa_section" | grep -q "Versions:"; then
      alsa_pkg="libasound2t64"
    fi
  fi
  if [ -z "$alsa_pkg" ] && apt-cache show libasound2 >/dev/null 2>&1; then
    local alsa_section
    alsa_section=$(apt-cache showpkg libasound2 2>/dev/null | head -5)
    if echo "$alsa_section" | grep -q "Versions:"; then
      alsa_pkg="libasound2"
    fi
  fi
  if [ -n "$alsa_pkg" ]; then
    packages+=("$alsa_pkg")
  else
    warn "Neither libasound2t64 nor libasound2 found — ALSA audio may not work"
  fi

  if command -v apt-get >/dev/null 2>&1; then
    say "Installing system packages"
    if command -v sudo >/dev/null 2>&1; then SUDO=sudo; else SUDO=""; fi
    $SUDO apt-get update -qq || warn "apt update failed; continuing"
    $SUDO apt-get install -y --no-install-recommends "${packages[@]}" || \
      warn "some packages could not be installed; the editor will report what is missing"
  else
    warn "apt-get not found — install ffmpeg and python3 yourself, then re-run with --no-deps"
  fi
}

if [ "$SKIP_DEPS" -eq 0 ]; then
  MISSING=0
  command -v ffmpeg  >/dev/null 2>&1 || MISSING=1
  command -v python3 >/dev/null 2>&1 || MISSING=1
  if [ "$MISSING" -eq 1 ]; then
    install_apt_packages
  else
    say "ffmpeg and python3 are already installed"
    if ! ldconfig -p 2>/dev/null | grep -q "libGL.so"; then
      install_apt_packages
    fi
  fi
fi

# --------------------------------------------------------------------------- #
# 2. Python environment
# --------------------------------------------------------------------------- #
if [ "$MODE" = "system" ]; then
  APP_DIR="/opt/$APP_ID"
  BIN_DIR="/usr/local/bin"
  DESKTOP_DIR="/usr/share/applications"
  ICON_DIR="/usr/share/icons/hicolor/scalable/apps"
  VENV="$APP_DIR/venv"
  [ -w /opt ] || SUDO_PREFIX="sudo"
else
  APP_DIR="$HOME/.local/share/$APP_ID"
  BIN_DIR="$HOME/.local/bin"
  DESKTOP_DIR="$HOME/.local/share/applications"
  ICON_DIR="$HOME/.local/share/icons/hicolor/scalable/apps"
  VENV="$APP_DIR/venv"
  SUDO_PREFIX=""
fi
: "${SUDO_PREFIX:=}"

say "Installing the application into $APP_DIR"
mkdir -p "$APP_DIR" "$BIN_DIR" "$DESKTOP_DIR" "$ICON_DIR"

$SUDO_PREFIX cp -r "$HERE/opencut-studio" "$APP_DIR/"
if [ -d "$HERE/vn_editor" ]; then
  $SUDO_PREFIX cp -r "$HERE/vn_editor" "$APP_DIR/"
fi
$SUDO_PREFIX cp "$HERE/README.md" "$APP_DIR/" 2>/dev/null || true

if [ "$SKIP_DEPS" -eq 0 ]; then
  if [ ! -x "$VENV/bin/python" ]; then
    say "Creating a private Python environment"
    python3 -m venv "$VENV"
  fi

  say "Installing Python packages — this takes a minute"
  PIP_OK=0
  if "$VENV/bin/pip" install --quiet --upgrade pip 2>/dev/null; then
    if "$VENV/bin/pip" install --quiet -r "$APP_DIR/opencut-studio/requirements.txt" 2>/dev/null; then
      PIP_OK=1
    fi
  fi

  if [ "$PIP_OK" -eq 0 ]; then
    warn "Python package installation failed or was interrupted."
    warn "Re-run ./install.sh to retry — cached downloads will be reused."
    die "Installation incomplete. Fix the error above and try again."
  fi
else
  warn "skipping pip install; the launcher will try the system python"
fi

PY="$VENV/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"

# --------------------------------------------------------------------------- #
# 3. Launcher, icon, menu entry
# --------------------------------------------------------------------------- #
say "Writing the launcher"
cat > "$APP_DIR/launch.sh" <<LAUNCH
#!/usr/bin/env bash
# Generated by install.sh — runs $APP_NAME
export PYTHONPATH="$APP_DIR/opencut-studio:$APP_DIR\${PYTHONPATH:+:\$PYTHONPATH}"
exec "$PY" "$APP_DIR/opencut-studio/main.py" "\$@"
LAUNCH
chmod +x "$APP_DIR/launch.sh"

cat > "$ICON_DIR/$APP_ID.svg" <<'SVG'
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128">
  <rect width="128" height="128" rx="28" fill="#0b0d11"/>
  <rect x="10" y="10" width="108" height="108" rx="22" fill="#ff3b6b"/>
  <path d="M40 38h16l-8 22h12l8-22h16l-14 30 16 28h-16l-10-20h-12l10 20H40l16-28z" fill="#fff"/>
</svg>
SVG

cat > "$DESKTOP_DIR/$APP_ID.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=$APP_NAME
GenericName=Video Editor
Comment=Edit and export video: timeline, text, effects (no account needed)
Exec=$APP_DIR/launch.sh %f
Icon=$APP_ID
Terminal=false
Categories=AudioVideo;Video;AudioVideoEditing;
MimeType=application/x-opencut-studio-project;
Keywords=video;editor;timeline;export;caption;
StartupNotify=true
DESKTOP
chmod +x "$DESKTOP_DIR/$APP_ID.desktop" 2>/dev/null || true

if [ "$MODE" = "system" ]; then
  cat > "$BIN_DIR/$APP_ID" <<WRAP
#!/usr/bin/env bash
exec "$APP_DIR/launch.sh" "\$@"
WRAP
  chmod +x "$BIN_DIR/$APP_ID"
else
  ln -sf "$APP_DIR/launch.sh" "$BIN_DIR/$APP_ID"
fi

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -q "$ICON_DIR/.." >/dev/null 2>&1 || true
fi

# --------------------------------------------------------------------------- #
# 4. Verify
# --------------------------------------------------------------------------- #
say "Checking the installation"
if "$APP_DIR/launch.sh" --self-test; then
  echo
  say "Installed! Start it from your applications menu ($APP_NAME) or run:"
  say "  $BIN_DIR/$APP_ID"
  case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) warn "$BIN_DIR is not on your PATH. To launch, run: $BIN_DIR/$APP_ID" ;;
  esac
else
  warn "The self-test failed — see the output above."
  warn "Try re-running ./install.sh to retry incomplete steps."
  die "Installation could not be verified."
fi