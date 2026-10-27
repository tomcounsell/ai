#!/bin/zsh
# Snapshots of the popover in light and dark, drawn from this Mac's real log.
# Writes /tmp/BrewStatus-shots/popover-light.png and popover-dark.png.
set -u
shots=/tmp/BrewStatus-shots
rm -rf $shots
APP_SNAPSHOT=$shots ~/Applications/BrewStatus.app/Contents/MacOS/BrewStatus
