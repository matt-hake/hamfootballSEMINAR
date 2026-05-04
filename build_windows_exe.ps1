# Build the Windows client app locally.
#
# Run from the project root:
#
#   .\build_windows_exe.ps1
#
# This creates:
#
#   dist\FootballParserApp\

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

Write-Host "Building FootballParserApp..." -ForegroundColor Cyan

pyinstaller `
    --onedir `
    --name FootballParserApp `
    --paths "src" `
    --hidden-import "app.api" `
    --hidden-import "uuid" `
    --add-data "src;src" `
    --add-data "config;config" `
    run_client_app.py

Write-Host "Creating runtime data folders..." -ForegroundColor Cyan

New-Item -ItemType Directory -Force -Path "dist\FootballParserApp\data" | Out-Null
New-Item -ItemType Directory -Force -Path "dist\FootballParserApp\data\temp" | Out-Null
New-Item -ItemType Directory -Force -Path "dist\FootballParserApp\data\exports" | Out-Null

New-Item -ItemType Directory -Force -Path "dist\FootballParserApp\_internal\data" | Out-Null
New-Item -ItemType Directory -Force -Path "dist\FootballParserApp\_internal\data\temp" | Out-Null
New-Item -ItemType Directory -Force -Path "dist\FootballParserApp\_internal\data\exports" | Out-Null

Write-Host "Copying client-facing instruction files..." -ForegroundColor Cyan

if (Test-Path "README_START_HERE.txt") {
    Copy-Item "README_START_HERE.txt" "dist\FootballParserApp\README_START_HERE.txt" -Force
}

if (Test-Path ".env.example") {
    Copy-Item ".env.example" "dist\FootballParserApp\.env.example" -Force
}

# If a real .env file exists locally, copy it into the packaged app's
# internal folder, where the packaged Python files will look for it.
#
# IMPORTANT:
# Do not commit .env to GitHub.
if (Test-Path ".env") {
    Copy-Item ".env" "dist\FootballParserApp\_internal\.env" -Force
    Write-Host "Copied local .env into dist\FootballParserApp\_internal\.env" -ForegroundColor Yellow
} else {
    Write-Host "No local .env found. You must add .env before giving the app to the client." -ForegroundColor Yellow
    Write-Host "Expected location: dist\FootballParserApp\_internal\.env" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Build complete!" -ForegroundColor Green
Write-Host "Client app folder:"
Write-Host "  dist\FootballParserApp"
Write-Host ""
Write-Host "Before sending to the client:"
Write-Host "  1. Make sure dist\FootballParserApp\_internal\.env exists"
Write-Host "  2. Test FootballParserApp by running the application"
Write-Host "  3. Zip the entire FootballParserApp folder"