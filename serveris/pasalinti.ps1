# Pašalina video serverio automatinį paleidimą ir ugniasienės taisyklę. Paleisti administratoriaus PowerShell lange.
$taskName = 'Video analize serveris'
Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
Get-NetFirewallRule -DisplayName $taskName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
Write-Host 'Pašalinta. Įrašai sessions\ nepaliesti.'
