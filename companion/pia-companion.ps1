<#
PIA Windows companion: shows PIA's notifications as ordinary Windows notifications.

It does one thing. Every minute it asks your PIA server "anything new for this device?", shows each answer as a
native toast, and tells the server it was delivered. Clicking a toast opens the item in PIA in your browser.
There is no model, no database and no background computation here: all deciding happens on the server.

  Set up once:   .\install.ps1 -Url https://your-pia-address -Token pia_dev_...
  Try it:        .\pia-companion.ps1 -Test          (shows one sample notification and exits)
  Run by hand:   .\pia-companion.ps1                (install.ps1 makes this start when you sign in)

Works with Windows PowerShell 5.1 (built into Windows 10 and 11). No modules to install.
#>
param(
    [switch]$Test,
    [switch]$Once,
    [string]$SettingsPath = (Join-Path $env:APPDATA 'PIA\companion.json')
)

$ErrorActionPreference = 'Stop'
$AppId = 'PIA.Companion'
# Until install.ps1 has registered "PIA" as a notification sender, borrow PowerShell's own identity so toasts still show.
$FallbackAppId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'

[void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
[void][Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime]

function Get-SenderId {
    if (Test-Path "HKCU:\Software\Classes\AppUserModelId\$AppId") { return $AppId }
    return $FallbackAppId
}

function Protect-Text([string]$Text) {
    # Titles come from the web: escape them so they can never be read as toast markup.
    return [System.Security.SecurityElement]::Escape($Text)
}

function Show-Toast([string]$Title, [string]$Body, [string]$Attribution, [string]$Link, [string]$Level) {
    $duration = if ($Level -eq 'critical') { 'long' } else { 'short' }
    $xml = @"
<toast activationType="protocol" launch="$(Protect-Text $Link)" duration="$duration">
  <visual><binding template="ToastGeneric">
    <text>$(Protect-Text $Title)</text>
    <text>$(Protect-Text $Body)</text>
    <text placement="attribution">$(Protect-Text $Attribution)</text>
  </binding></visual>
</toast>
"@
    $document = New-Object Windows.Data.Xml.Dom.XmlDocument
    $document.LoadXml($xml)
    $toast = New-Object Windows.UI.Notifications.ToastNotification $document
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier((Get-SenderId)).Show($toast)
}

function Read-Settings {
    if (-not (Test-Path $SettingsPath)) {
        throw "No settings at $SettingsPath. Run install.ps1 first (see companion\README.md)."
    }
    $settings = Get-Content $SettingsPath -Raw | ConvertFrom-Json
    # The token is stored encrypted for this Windows account (DPAPI); decrypt it only in memory.
    $secure = ConvertTo-SecureString $settings.token
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
    return @{ Url = $settings.url.TrimEnd('/'); Token = $plain; Interval = [Math]::Max(20, [int]$settings.interval) }
}

function Invoke-Pia($Settings, [string]$Method, [string]$Path, $Body) {
    $arguments = @{
        Uri = "$($Settings.Url)$Path"; Method = $Method; TimeoutSec = 20; UseBasicParsing = $true
        Headers = @{ Authorization = "Bearer $($Settings.Token)" }
    }
    if ($null -ne $Body) { $arguments.Body = ($Body | ConvertTo-Json -Compress); $arguments.ContentType = 'application/json' }
    return Invoke-RestMethod @arguments
}

function Get-LevelName([string]$Level) {
    switch ($Level) { 'critical' { 'Critical' } 'important' { 'Important' } default { 'Interesting' } }
}

if ($Test) {
    Show-Toast 'PIA is connected to this computer' 'Notifications from your information desk will appear like this one.' 'Interesting, a test notification' 'https://github.com' 'interesting'
    Write-Output 'A test notification was sent. If nothing appeared, check Windows Settings > System > Notifications.'
    exit 0
}

$settings = Read-Settings
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$failures = 0
while ($true) {
    try {
        $pending = (Invoke-Pia $settings 'GET' '/api/device/pending' $null).notifications
        $delivered = @()
        foreach ($note in $pending) {
            $link = if ($note.item_id) { "$($settings.Url)/#/?item=$($note.item_id)" } else { "$($settings.Url)/#/notifications" }
            Show-Toast $note.title $note.body "$(Get-LevelName $note.level), $($note.reason)" $link $note.level
            $delivered += [int]$note.id
            Start-Sleep -Milliseconds 700   # let each toast land before the next
        }
        if ($delivered.Count -gt 0) { [void](Invoke-Pia $settings 'POST' '/api/device/ack' @{ ids = $delivered }) }
        $failures = 0
    }
    catch {
        # The server may be restarting or the laptop offline: keep quiet and try again, backing off up to ten minutes.
        $failures += 1
        if ($Once) { Write-Error $_; exit 1 }
    }
    if ($Once) { exit 0 }
    Start-Sleep -Seconds ([Math]::Min(600, $settings.Interval * [Math]::Pow(2, [Math]::Min($failures, 4))))
}
