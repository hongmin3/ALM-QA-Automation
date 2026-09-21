param(
    [ValidateSet('menu', 'srs', 'issues')]
    [string]$Mode = 'menu',
    [Parameter(ValueFromRemainingArguments = $true)]
    [AllowEmptyString()]
    [string[]]$ApplicationArgs
)
$ErrorActionPreference = 'Stop'
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command py -ErrorAction SilentlyContinue }
if (-not $python) { throw 'Python is required. Install Python and add it to PATH.' }

# JSON avoids Windows PowerShell 5.1 native argument quoting loss.
$forward = @($Mode)
if ($Mode -eq 'menu') { $forward = @('--menu') }
elseif ($null -ne $ApplicationArgs) { $forward += $ApplicationArgs }
$previous = [Environment]::GetEnvironmentVariable('ALM_QA_LAUNCH_ARGS', 'Process')
try {
    $env:ALM_QA_LAUNCH_ARGS = ConvertTo-Json -InputObject @($forward) -Compress
    & $python.Source (Join-Path $PSScriptRoot 'run.py') --launcher-env
    $result = $LASTEXITCODE
}
finally {
    [Environment]::SetEnvironmentVariable('ALM_QA_LAUNCH_ARGS', $previous, 'Process')
}
exit $result
