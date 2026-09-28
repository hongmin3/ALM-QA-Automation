[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$TaskName = 'ALM_QA_Automation_Daily',
    [string]$PythonExe = '',
    [ValidatePattern('^(?:[01]\d|2[0-3]):[0-5]\d$')]
    [string]$At = '09:00',
    [switch]$PlanJson,
    [switch]$Install,
    [switch]$FinalizeTransition,
    [string]$Manifest = ''
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'automation_task_helpers.ps1')
$selectedModes = @($PlanJson.IsPresent, $Install.IsPresent, $FinalizeTransition.IsPresent) |
    Where-Object { $_ }
if ($selectedModes.Count -gt 1) {
    throw 'Choose only one of -PlanJson, -Install, or -FinalizeTransition.'
}

$scriptProjectRoot = (Split-Path -Parent $PSScriptRoot)
$scriptProjectItem = Get-Item -LiteralPath $scriptProjectRoot
if ($scriptProjectItem.Parent.Name -eq '.worktrees') {
    $ProjectRoot = $scriptProjectItem.Parent.Parent.FullName
}
else {
    $ProjectRoot = $scriptProjectItem.FullName
}

$days = @('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday')
$entrypoint = Join-Path $ProjectRoot 'automation.py'
$plan = [ordered]@{
    taskName = $TaskName
    mode = 'auto'
    days = $days
    at = $At
    workingDirectory = $ProjectRoot
    entrypoint = $entrypoint
    startWhenAvailable = $true
    networkRequired = $true
    multipleInstances = 'IgnoreNew'
    executionTimeLimitMinutes = 240
    priority = 7
}

if ($PlanJson) {
    $plan | ConvertTo-Json -Depth 4
    exit 0
}

$legacyTasks = @(
    'VXvue_SRS_Spec_Automation',
    'VXvue_SRS_Spec_Automation_CatchUp'
)

if ($FinalizeTransition) {
    if (-not $Manifest -or -not (Test-Path -LiteralPath $Manifest -PathType Leaf)) {
        throw 'A successful integrated manifest is required.'
    }
    $result = Get-Content -LiteralPath $Manifest -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($result.status -ne 'SUCCESS' -or $result.dataComplete -ne $true) {
        throw 'Legacy tasks remain enabled because the integrated manifest is not a complete success.'
    }
    foreach ($legacyTask in $legacyTasks) {
        if ($PSCmdlet.ShouldProcess($legacyTask, 'Disable-ScheduledTask')) {
            $existing = Get-ScheduledTask -TaskName $legacyTask -ErrorAction SilentlyContinue
            if ($null -ne $existing) {
                Disable-ScheduledTask -TaskName $legacyTask -ErrorAction Stop | Out-Null
                Write-Output "Disabled legacy task: $legacyTask"
            }
            else {
                Write-Output "Legacy task not present: $legacyTask"
            }
        }
    }
    exit 0
}

if (-not $Install) {
    $plan | Format-List
    exit 0
}

if (-not $PythonExe) {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($null -eq $pythonCommand) {
        throw 'Python is required and was not found on PATH.'
    }
    $PythonExe = $pythonCommand.Source
}
$PythonExe = (Get-Item -LiteralPath $PythonExe).FullName
if (-not (Test-Path -LiteralPath $entrypoint -PathType Leaf)) {
    throw 'The integrated automation entrypoint is missing.'
}

Push-Location $ProjectRoot
try {
    & $PythonExe $entrypoint --check-local
    if ($LASTEXITCODE -ne 0) {
        throw 'The integrated local check failed; no scheduled task was changed.'
    }
}
finally {
    Pop-Location
}

$action = New-ScheduledTaskAction -Execute $PythonExe -Argument 'automation.py' -WorkingDirectory $ProjectRoot
$trigger = New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek $days -At $At
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 4) `
    -Priority 7
$userId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType S4U -RunLevel Limited

if ($PSCmdlet.ShouldProcess($TaskName, 'Register-ScheduledTask')) {
    $backupRoot = Join-Path $ProjectRoot '.migration\scheduled-tasks'
    New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    foreach ($name in @($TaskName) + $legacyTasks) {
        $existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        if ($null -ne $existing) {
            $safeName = $name -replace '[^A-Za-z0-9_.-]', '_'
            $backupPath = Join-Path $backupRoot "$stamp-$safeName.xml"
            Export-ScheduledTask -TaskName $name | Set-Content -LiteralPath $backupPath -Encoding UTF8
        }
    }

    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Principal $principal `
        -Force | Out-Null

    $registered = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    $registeredAction = @($registered.Actions)[0]
    $registeredTrigger = @($registered.Triggers)[0]
    $registeredAt = ([datetime]$registeredTrigger.StartBoundary).ToString('HH:mm')
    $expectedPython = [IO.Path]::GetFullPath($PythonExe)
    $actualPython = [IO.Path]::GetFullPath([string]$registeredAction.Execute)
    $executionLimit = [Xml.XmlConvert]::ToTimeSpan([string]$registered.Settings.ExecutionTimeLimit)
    $mismatch = @(
        $actualPython -ne $expectedPython
        [string]$registeredAction.Arguments -ne 'automation.py'
        [IO.Path]::GetFullPath([string]$registeredAction.WorkingDirectory) -ne [IO.Path]::GetFullPath($ProjectRoot)
        $registeredAt -ne $At
        -not (Test-AutomationWeekdayTrigger $registeredTrigger)
        $registered.Settings.StartWhenAvailable -ne $true
        $registered.Settings.RunOnlyIfNetworkAvailable -ne $true
        [string]$registered.Settings.MultipleInstances -ne 'IgnoreNew'
        [int]$executionLimit.TotalMinutes -ne 240
        [int]$registered.Settings.Priority -ne 7
    ) | Where-Object { $_ }
    if (@($mismatch).Count -ne 0) {
        throw 'Scheduled task readback did not match the requested definition.'
    }
    Write-Output "Registered and verified scheduled task: $TaskName"
}
