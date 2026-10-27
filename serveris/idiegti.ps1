# Vienkartinis video serverio įdiegimas. Paleisti ADMINISTRATORIAUS PowerShell lange:
#   powershell -ExecutionPolicy Bypass -File .\serveris\idiegti.ps1
#
# Ką daro:
#   1. Ugniasienės taisyklė: prievadas 8766 atidaromas TIK "Private" tinkle ir TIK iš vietinio potinklio
#      (LocalSubnet). Viešame Wi-Fi (kavinėje ir pan.) serveris nepasiekiamas.
#   2. Suplanuota užduotis (Task Scheduler): serveris paleidžiamas kompiuterio įjungimo metu,
#      fone (be lango), ir perleidžiamas, jei nukristų.

$ErrorActionPreference = 'Stop'

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { throw 'Paleisk šį skriptą administratoriaus PowerShell lange.' }

$port = 8766
$taskName = 'Video analize serveris'
$serverJs = Join-Path $PSScriptRoot 'server.js'
$node = (Get-Command node).Source

# 1. Ugniasienė
Get-NetFirewallRule -DisplayName $taskName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName $taskName -Direction Inbound -Action Allow -Protocol TCP -LocalPort $port `
    -Profile Private -RemoteAddress LocalSubnet | Out-Null
Write-Host "Ugniasienė: prievadas $port atidarytas tik Private tinkle ir tik vietiniam potinkliui."

# 2. Automatinis paleidimas.
# LogonType S4U = veikia tavo vardu, bet be lango ir nereikia saugoti slaptažodžio.
$action    = New-ScheduledTaskAction -Execute $node -Argument "`"$serverJs`"" -WorkingDirectory $PSScriptRoot
$trigger   = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Limited
$settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $taskName
Write-Host "Užduotis '$taskName' užregistruota ir paleista."

Start-Sleep -Seconds 2
$token = (Get-Content (Join-Path $PSScriptRoot 'token.txt') -Raw).Trim()
$ip = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.PrefixOrigin -in 'Dhcp','Manual' -and $_.IPAddress -notlike '169.254*' } | Select-Object -First 1).IPAddress
Write-Host ''
Write-Host "Atidaryk telefone ir išsisaugok (Add to Home screen):" -ForegroundColor Green
Write-Host "  http://${ip}:$port/?t=$token" -ForegroundColor Green
