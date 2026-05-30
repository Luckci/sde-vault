@echo off
title SDE Vault - Build
cd /d "%~dp0"

echo Installing PyInstaller...
pip install pyinstaller --quiet

echo.
echo Building SDE-Vault.exe...
pyinstaller sde_vault.spec --noconfirm

echo.
if exist "dist\SDE-Vault.exe" (
    echo BUILD SUCCESS: dist\SDE-Vault.exe
) else (
    echo BUILD FAILED - check output above
)
pause
