#!/bin/zsh
# Builds BrewStatus, installs it to ~/Applications/BrewStatus.app, and links
# the CLI as ~/.local/bin/brew-status (into the app bundle).
# Build products live in ~/Library/Developer/BrewStatus, outside the repo.
#
# Usage: ./build.sh            release build, install
#        ./build.sh --debug    debug build, install
#        ./build.sh --open     install, then quit the running copy and open the new one
set -euo pipefail
here=${0:A:h}
name="BrewStatus"
cli="brew-status"
scratch="$HOME/Library/Developer/$name/build"
config=release
[[ " $* " == *" --debug "* ]] && config=debug

# Sign with an Apple Development certificate when the keychain has one; otherwise ad hoc.
identity="${SIGN_IDENTITY:-}"
if [[ -z "$identity" ]]; then
  identity=$(security find-identity -v -p codesigning 2>/dev/null | grep -m1 -o '"Apple Development: [^"]*"' | tr -d '"' || true)
fi
[[ -n "$identity" ]] || identity=-

# --disable-sandbox: SwiftPM's manifest sandbox cannot start inside sandbox-exec (Valor's turns).
swift build --disable-sandbox -c $config --package-path "$here" --scratch-path "$scratch" 2>&1 | grep -E "error|warning|Build complete" | grep -v "^\[" || true
bin="$scratch/$config/$name"
[[ -x "$bin" && -x "$scratch/$config/$cli" ]] || { echo "build failed"; exit 1; }
# swift build can report success while an edited file failed to compile. Catch that.
stale=$(find "$here/Sources" -name '*.swift' -newer "$bin" | head -1)
if [[ -n "$stale" ]]; then
  echo "build failed: binary is older than $stale"
  swift build --disable-sandbox -c $config --package-path "$here" --scratch-path "$scratch" 2>&1 | grep -E "error" | head -20
  exit 1
fi

app="$HOME/Applications/$name.app"
mkdir -p "$HOME/Applications"
rm -rf "$app"
mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources"
cp "$bin" "$app/Contents/MacOS/$name"
cp "$scratch/$config/$cli" "$app/Contents/MacOS/$cli"
sed -e "s/__BUILD__/$(date +%Y%m%d%H%M)/" "$here/Info.plist.in" > "$app/Contents/Info.plist"
# The icon: Icon/AppIcon.icon -> Resources/Assets.car + AppIcon.icns, rebuilt when it changed.
if [[ -d "$here/Icon/AppIcon.icon" ]]; then
  car="$here/Resources/Assets.car"
  if [[ ! -f "$car" || -n "$(find "$here/Icon/AppIcon.icon" -newer "$car" | head -1)" ]]; then
    "$here/tools/make_icon.sh"
  fi
  cp -X "$car" "$here/Resources/AppIcon.icns" "$app/Contents/Resources/"
fi
xattr -cr "$app"
# The nested CLI first, then the bundle that seals it.
codesign --force --sign "$identity" "$app/Contents/MacOS/$cli" 2>&1 | grep -v "replacing existing signature" || true
codesign --force --sign "$identity" "$app" 2>&1 | grep -v "replacing existing signature" || true
echo "installed $app (signed: ${identity})"

mkdir -p "$HOME/.local/bin"
ln -sf "$app/Contents/MacOS/$cli" "$HOME/.local/bin/$cli"
echo "linked $HOME/.local/bin/$cli"

if [[ " $* " == *" --open "* ]]; then
  pkill -x "$name" 2>/dev/null || true
  sleep 0.5
  open "$app"
fi
