#!/bin/bash
# Build SDE-Vault for macOS
cd "$(dirname "$0")"

echo "Installing PyInstaller..."
pip install pyinstaller --quiet

echo ""
echo "Building SDE-Vault (macOS)..."
pyinstaller sde_vault.spec --noconfirm

if [ -f "dist/SDE-Vault" ]; then
    echo ""
    echo "BUILD SUCCESS: dist/SDE-Vault"
    echo "Tip: zip it before sharing — right-click → Compress"
else
    echo "BUILD FAILED - check output above"
    exit 1
fi
