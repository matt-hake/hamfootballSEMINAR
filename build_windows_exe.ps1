# Build the Windows client app.
#
# Run this file from PowerShell while inside the project root:
#
#   .\build_windows_exe.ps1
#
# Output:
#
#   dist\FootballParserApp\FootballParserApp.exe
#CITE: CHATGPT 5.2

$ErrorActionPreference = "Stop"

Write-Host "Cleaning old build files..." -ForegroundColor Cyan

if (Test-Path "build") {
    Remove-Item "build" -Recurse -Force
}

if (Test-Path "dist") {
    Remove-Item "dist" -Recurse -Force
}

if (Test-Path "FootballParserApp.spec") {
    Remove-Item "FootballParserApp.spec" -Force
}

Write-Host "Installing PyInstaller..." -ForegroundColor Cyan

python -m pip install --upgrade pip
python -m pip install pyinstaller

Write-Host "Building FootballParserApp.exe..." -ForegroundColor Cyan

pyinstaller `
    --onedir `
    --name FootballParserApp `
    --paths "src" `
    --hidden-import "app.api" `
    --add-data "src;src" `
    --add-data "config;config" `
    --add-data "README_START_HERE.txt;." `
    --add-data ".env.example;." `
    run_client_app.py

Write-Host "Creating runtime data folders..." -ForegroundColor Cyan

New-Item -ItemType Directory -Force -Path "dist\FootballParserApp\data" | Out-Null
New-Item -ItemType Directory -Force -Path "dist\FootballParserApp\data\temp" | Out-Null
New-Item -ItemType Directory -Force -Path "dist\FootballParserApp\data\exports" | Out-Null

Write-Host ""
Write-Host "Build complete!" -ForegroundColor Green
Write-Host "Client app folder:"
Write-Host "  dist\FootballParserApp"
Write-Host ""
Write-Host "Zip the whole FootballParserApp folder, not just the .exe."