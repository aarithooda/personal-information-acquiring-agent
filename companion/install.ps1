<#
Sets up the PIA companion for the current Windows user. Run it yourself, once:

    .\install.ps1 -Url https://your-pia-address -Token pia_dev_xxxxxxxx
    .\install.ps1 -Uninstall

What it does (all of it per-user; nothing needs administrator rights):
  1. Saves the server address and the device token to %APPDATA%\PIA\companion.json. The token is encrypted with
     Windows DPAPI, so only your Windows account on this computer can read it.
  2. Registers "PIA" as a notification sender (one key under HKCU\Software\Classes\AppUserModelId), so toasts are
     labelled PIA and can be managed in Windows Settings > Notifications.
  3. Adds a shortcut to your Startup folder so the companion starts, hidden, when you sign in.

Get the token from PIA: Notifications > Devices > "Create a device token" (or `pia add-device`).
#>
param(
    [string]$Url,
    [string]$Token,
    [int]$Interval = 60,
    [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'
$folder = Join-Path $env:APPDATA 'PIA'
$settingsPath = Join-Path $folder 'companion.json'
$registryKey = 'HKCU:\Software\Classes\AppUserModelId\PIA.Companion'
$shortcutPath = Join-Path ([Environment]::GetFolderPath('Startup')) 'PIA companion.lnk'
$script = Join-Path $PSScriptRoot 'pia-companion.ps1'

if ($Uninstall) {
    Remove-Item $shortcutPath -ErrorAction SilentlyContinue
    Remove-Item $registryKey -Recurse -ErrorAction SilentlyContinue
    Remove-Item $settingsPath -ErrorAction SilentlyContinue
    Write-Output 'PIA companion removed. Revoke the device in PIA (Notifications > Devices) to invalidate its token.'
    exit 0
}

if (-not $Url -or -not $Token) { throw 'Both -Url and -Token are required. See the comment at the top of this file.' }
if ($Url -notmatch '^https?://') { throw 'The address must start with http:// or https://' }
if ($Url -match '^http://' -and $Url -notmatch '^http://(localhost|127\.0\.0\.1)(:|/|$)') {
    Write-Warning 'This address is not HTTPS. The device token would travel unencrypted; use HTTPS for a server on the internet.'
}

New-Item -ItemType Directory -Force -Path $folder | Out-Null
$protected = ConvertFrom-SecureString (ConvertTo-SecureString $Token -AsPlainText -Force)
@{ url = $Url.TrimEnd('/'); token = $protected; interval = $Interval } | ConvertTo-Json | Set-Content -Path $settingsPath -Encoding UTF8

New-Item -Path $registryKey -Force | Out-Null
Set-ItemProperty -Path $registryKey -Name 'DisplayName' -Value 'PIA'

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = (Get-Command powershell.exe).Source
$shortcut.Arguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$script`""
$shortcut.WorkingDirectory = $PSScriptRoot
$shortcut.Description = 'Shows PIA notifications on this computer'
$shortcut.Save()

Write-Output "Saved settings to $settingsPath"
Write-Output 'Checking the connection...'
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $script -Once
if ($LASTEXITCODE -ne 0) { Write-Warning 'The server could not be reached with this address and token. Check both, then run install.ps1 again.' }
else { Write-Output 'Connected. The companion will start when you sign in. To start it now, run the "PIA companion" shortcut in your Startup folder.' }
