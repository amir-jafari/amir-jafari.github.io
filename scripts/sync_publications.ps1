# Weekly Google Scholar sync, run by Windows Task Scheduler on a home/office PC.
#
# Google Scholar blocks GitHub's servers, so the scrape has to run from a
# residential IP. This pulls, runs update_publications.py, and pushes any
# changes; GitHub Pages then redeploys the site.
#
# Register the weekly task once (no admin needed):
#     powershell -ExecutionPolicy Bypass -File scripts\sync_publications.ps1 -Register
# Run a sync by hand:
#     powershell -ExecutionPolicy Bypass -File scripts\sync_publications.ps1
# Log: %LOCALAPPDATA%\publications-sync.log
param([switch]$Register)

$TaskName = "Website - Sync Google Scholar publications"

if ($Register) {
    $action  = New-ScheduledTaskAction -Execute "powershell.exe" -Argument (
        "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$PSCommandPath`"")
    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At 9am
    # StartWhenAvailable: if the PC was off/asleep at 9am Monday, run at next wake.
    # Battery flags: Windows otherwise skips the task on a laptop running on battery.
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 20)
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Settings $settings -Force | Out-Null
    "Registered scheduled task '$TaskName' (Mondays 9:00 AM)."
    exit 0
}

$repo = Split-Path -Parent $PSScriptRoot
$log  = Join-Path $env:LOCALAPPDATA "publications-sync.log"
Set-Location $repo

function Log($msg) { "$(Get-Date -Format s)  $msg" | Add-Content -Path $log -Encoding utf8 }

function Run($exe, [string[]]$argv) {
    # ForEach "$_" turns git's stderr warnings into plain text, not PS errors.
    $out = & $exe @argv 2>&1 | ForEach-Object { "$_" } | Out-String
    if ($out.Trim()) { Log $out.Trim() }
    if ($LASTEXITCODE -ne 0) { Log "FAILED: $exe $argv (exit $LASTEXITCODE)"; exit 1 }
}

Log "=== sync start ==="

$branch = (git rev-parse --abbrev-ref HEAD).Trim()
if ($branch -ne "main") { Log "On branch '$branch', not main - skipping."; exit 0 }

Run git @("pull", "--rebase", "--autostash")

$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Run python @("scripts/update_publications.py")

$files = @("data/publications.json", "data/publications.js", "index.html")
Run git (@("add", "--") + $files)
git diff --cached --quiet -- $files
if ($LASTEXITCODE -eq 0) {
    Log "No changes."
} else {
    Run git (@("commit", "-m", "chore: sync publications from Google Scholar", "--") + $files)
    Run git @("push")
}

Log "=== sync done ==="
