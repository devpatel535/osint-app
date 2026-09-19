#!/bin/sh
# ---------------------------------------------------------------------------
#  Set up OSINT Lookup on iSH (Alpine Linux on iOS), or any other Alpine box.
#
#      sh setup_ish.sh
#
#  iSH has no display server, so the desktop GUI cannot run there. This sets up
#  the command line version instead:
#
#      python3 osint.py alfredredbird
# ---------------------------------------------------------------------------
set -e

echo "Installing Python..."
if command -v apk >/dev/null 2>&1; then
    apk add --no-cache python3
else
    echo "This script expects Alpine's apk. On another distro install python3 yourself."
    exit 1
fi

echo
echo "Installing optional extras (the app works without them)..."
# Alpine packages are prebuilt, so they install far faster than pip does under
# iSH's x86 emulation. Any that are unavailable are simply skipped.
apk add --no-cache py3-requests    2>/dev/null || echo "  py3-requests unavailable - urllib fallback will be used"
apk add --no-cache py3-dnspython   2>/dev/null || echo "  py3-dnspython unavailable - email MX checks will be skipped"
apk add --no-cache py3-phonenumbers 2>/dev/null || echo "  py3-phonenumbers unavailable - phone analysis will be reduced"

echo
echo "Checking..."
python3 - <<'PY'
import sys
print("  python  :", sys.version.split()[0])
for name, why in (("requests", "faster, more tolerant HTTP"),
                  ("phonenumbers", "carrier / region / line type"),
                  ("dns.resolver", "email MX records")):
    try:
        __import__(name)
        print(f"  {name.split('.')[0]:13}: yes")
    except ImportError:
        print(f"  {name.split('.')[0]:13}: no  ({why} unavailable)")
PY

cat <<'EOF2'

Done. Try:

    python3 osint.py alfredredbird
    python3 osint.py "jane.doe@example.com" --save
    python3 osint.py --history

iSH runs x86 under emulation, so it is slow. A full 243-site sweep will take a
while; --threads 4 is gentler, and email or phone lookups return almost at once.
Keep iSH in the foreground while a search runs - iOS suspends backgrounded apps.
EOF2
