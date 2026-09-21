<#
.SYNOPSIS
    ALM(Polarion) 이슈를 PDF/HTML/Markdown으로 내보냅니다.

.DESCRIPTION
    polarion_query_backup.py 를 대신 실행해 주는 도우미입니다.

    - 인자 없이 실행하면 무엇을 내보낼지 차례대로 물어봅니다.
    - -id / -query 등을 주면 묻지 않고 바로 실행합니다.
    - 실행 전에 Python·설정 파일·PAT가 준비됐는지 먼저 확인합니다.
    - 끝나면 결과 문서를 자동으로 엽니다 (-NoOpen 으로 끌 수 있음).

.PARAMETER Id
    내보낼 이슈 ID. 쉼표나 공백으로 여러 개를 넣을 수 있습니다.

.PARAMETER Query
    Polarion 검색 Query. 검색 화면의 Query Pane > Convert to Text 결과를
    그대로 넣는 것이 가장 안전합니다.

.PARAMETER Out
    결과를 저장할 폴더 (기본값은 config.yaml 의 output.directory).

.PARAMETER Title
    결과 문서 맨 위에 찍히는 제목 (기본값은 config.yaml 의 output.document_title).

.PARAMETER Limit
    이번 실행에만 적용할 최대 건수. 필드 확인용으로 -Limit 1 이 편합니다.

.PARAMETER Timestamp
    결과를 실행 시각 하위 폴더에 저장해 이전 결과를 남깁니다.

.PARAMETER NoOpen
    끝난 뒤 결과 문서를 자동으로 열지 않습니다.

.PARAMETER Yes
    이전 버전 호환 옵션입니다. 현재 기존 결과는 백업으로 보존합니다.

.PARAMETER Check
    Polarion에 접속하지 않고 준비 상태만 점검합니다.

.PARAMETER Config
    설정 파일 경로 (기본값: config.yaml).

.EXAMPLE
    .\run.ps1
    무엇을 내보낼지 물어봅니다.

.EXAMPLE
    .\run.ps1 -id VP-6955

.EXAMPLE
    .\run.ps1 -id VP-6955,VP-7001 -Out 결과_모음

.EXAMPLE
    .\run.ps1 -query "status:(in_progress) AND author.id:(hong)"

.EXAMPLE
    .\run.ps1 -Check
#>

[CmdletBinding()]
param(
    [Alias('i')]
    [string[]]$Id,

    [Alias('q')]
    [string]$Query,

    [Alias('o')]
    [string]$Out,

    [string]$Title,

    [int]$Limit,

    [switch]$Timestamp,

    [switch]$NoOpen,

    [switch]$Yes,

    [switch]$Check,

    [string]$Config
)

$ErrorActionPreference = 'Stop'

$scriptDirectory = $PSScriptRoot
Set-Location $scriptDirectory

$pythonScript = Join-Path $scriptDirectory 'polarion_query_backup.py'
$configName = if ($Config) { $Config } else { 'config.yaml' }
$configPath = Join-Path $scriptDirectory $configName
$examplePath = Join-Path $scriptDirectory 'config.example.yaml'
$tokenName = 'POLARION_TOKEN'

function Write-Banner {
    param([string]$Text)

    Write-Host ''
    Write-Host ('=' * 54) -ForegroundColor DarkCyan
    Write-Host ('  ' + $Text) -ForegroundColor Cyan
    Write-Host ('=' * 54) -ForegroundColor DarkCyan
}

function Write-Info {
    param([string]$Text)
    Write-Host ('  ' + $Text)
}

function Write-Problem {
    param([string]$Text)
    Write-Host ('  ' + $Text) -ForegroundColor Yellow
}

function Write-Failure {
    param([string]$Text)
    Write-Host ('  ' + $Text) -ForegroundColor Red
}

function Resolve-PythonCommand {
    foreach ($candidate in @('python', 'py')) {
        $found = Get-Command $candidate -ErrorAction SilentlyContinue

        if ($found) {
            return $candidate
        }
    }

    return $null
}

function Confirm-Yes {
    param([string]$Question)

    $answer = Read-Host ('  ' + $Question + ' [y/N]')
    return ($answer -match '^\s*(y|yes|ㅛ)\s*$')
}

Write-Banner 'ALM 이슈 Export'

# ---------------------------------------------------------------- 준비 확인

if (-not (Test-Path $pythonScript)) {
    Write-Failure "polarion_query_backup.py 를 찾을 수 없습니다: $pythonScript"
    Write-Info 'run.ps1 은 스크립트와 같은 폴더에 있어야 합니다.'
    exit 1
}

$python = Resolve-PythonCommand

if (-not $python) {
    Write-Failure 'Python을 찾을 수 없습니다.'
    Write-Info 'https://www.python.org 에서 설치할 때'
    Write-Info '"Add Python to PATH" 를 반드시 체크하세요.'
    exit 1
}

# 필요한 패키지가 모두 있는지 확인한다. 하나라도 없으면 설치 명령을 알려 준다.
& $python -c "import requests, yaml, bs4" 2>$null | Out-Null

if ($LASTEXITCODE -ne 0) {
    Write-Problem '필요한 패키지가 설치되어 있지 않습니다.'
    Write-Info ''

    if (Confirm-Yes '지금 설치할까요?') {
        & $python -m pip install -r (Join-Path $scriptDirectory 'requirements.txt')

        if ($LASTEXITCODE -ne 0) {
            Write-Failure '패키지 설치에 실패했습니다. 위 메시지를 확인하세요.'
            exit 1
        }

        Write-Info 'PDF 생성을 위한 Chromium도 내려받습니다 (최초 1회, 몇 분 걸릴 수 있음).'
        & $python -m playwright install chromium
    }
    else {
        Write-Info '다음 명령을 직접 실행한 뒤 다시 시도하세요.'
        Write-Info '  python -m pip install -r requirements.txt'
        Write-Info '  python -m playwright install chromium'
        exit 1
    }
}

# 설정 파일이 없으면 예시 파일에서 만들어 준다.
if (-not (Test-Path $configPath)) {
    Write-Problem "설정 파일이 없습니다: $configName"

    if (-not (Test-Path $examplePath)) {
        Write-Failure 'config.example.yaml 도 없어 자동으로 만들 수 없습니다.'
        exit 1
    }

    Write-Info ''

    if (Confirm-Yes 'config.example.yaml 을 복사해서 만들까요?') {
        Copy-Item $examplePath $configPath
        Write-Info ''
        Write-Info "만들었습니다: $configPath"
        Write-Info '메모장이 열리면 아래 두 값을 사내 환경에 맞게 채우고 저장하세요.'
        Write-Info '  polarion.host        사내 Polarion 주소'
        Write-Info '  polarion.project_id  프로젝트 ID'
        Write-Info ''
        Write-Info '저장한 뒤 run.ps1 을 다시 실행하면 됩니다.'

        Start-Process notepad.exe $configPath
    }
    else {
        Write-Info '다음 명령으로 직접 만들 수 있습니다.'
        Write-Info "  copy config.example.yaml $configName"
    }

    exit 1
}

# PAT(Personal Access Token) 확인.
if (-not [Environment]::GetEnvironmentVariable($tokenName)) {
    Write-Problem "환경변수 $tokenName 가 없어 Polarion에 로그인할 수 없습니다."
    Write-Info ''
    Write-Info '계속 쓰려면 새 PowerShell 창에서 아래를 한 번 실행해 두세요.'
    Write-Info ("  setx $tokenName " + '"발급받은 PAT"')
    Write-Info ''

    if (Confirm-Yes '지금 입력해서 이번 실행에만 사용할까요?') {
        $secureToken = Read-Host '  PAT 입력(화면에 표시되지 않습니다)' -AsSecureString
        $pointer = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)

        try {
            $plainToken = [System.Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
        }
        finally {
            [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
        }

        if (-not $plainToken) {
            Write-Failure '입력된 값이 없습니다.'
            exit 1
        }

        Set-Item -Path "Env:$tokenName" -Value $plainToken
        Write-Info 'PAT를 이번 실행에만 적용했습니다.'
    }
    else {
        exit 1
    }
}

# ---------------------------------------------------------- 무엇을 내보낼지

$hasTarget = $PSBoundParameters.ContainsKey('Id') `
    -or $PSBoundParameters.ContainsKey('Query') `
    -or $Check

if (-not $hasTarget) {
    Write-Host ''
    Write-Host '  무엇을 내보낼까요?'
    Write-Host ''
    Write-Host '    1) 이슈 ID 입력       예) VP-6955  또는  VP-6955,VP-7001'
    Write-Host '    2) 검색 쿼리 입력     Polarion 화면의 Convert to Text 결과'
    Write-Host "    3) $configName 에 저장된 조건 그대로"
    Write-Host '    4) 실행 환경만 점검 (Polarion에 접속하지 않음)'
    Write-Host ''

    $choice = Read-Host '  번호 선택 [1]'

    if (-not $choice) {
        $choice = '1'
    }

    switch ($choice.Trim()) {
        '1' {
            $enteredIds = Read-Host '  이슈 ID'

            if (-not $enteredIds) {
                Write-Failure '입력된 ID가 없습니다.'
                exit 1
            }

            $Id = $enteredIds -split '[,\s]+' | Where-Object { $_ }
        }

        '2' {
            Write-Host ''
            Write-Info '따옴표 없이 그대로 붙여넣으세요.'
            $enteredQuery = Read-Host '  검색 쿼리'

            if (-not $enteredQuery) {
                Write-Failure '입력된 쿼리가 없습니다.'
                exit 1
            }

            $Query = $enteredQuery
        }

        '3' {
            Write-Info "$configName 의 search.query 를 사용합니다."
        }

        '4' {
            $Check = $true
        }

        default {
            Write-Failure "1~4 중에서 선택하세요. (입력값: $choice)"
            exit 1
        }
    }
}

# ------------------------------------------------------------------ 실행

$arguments = @($pythonScript)

if ($Check) {
    $arguments += '--check'
}
else {
    if ($Id -and $Id.Count -gt 0) {
        $arguments += '-id'
        $arguments += ($Id -join ',')
    }
    elseif ($Query) {
        $arguments += '-query'
        $arguments += $Query
    }

    if ($Out) {
        $arguments += @('-o', $Out)
    }

    if ($Title) {
        $arguments += @('--title', $Title)
    }

    if ($PSBoundParameters.ContainsKey('Limit')) {
        $arguments += @('--limit', "$Limit")
    }

    if ($Timestamp) {
        $arguments += '--timestamp'
    }

    if ($Yes) {
        $arguments += '-y'
    }

    if (-not $NoOpen) {
        $arguments += '--open'
    }
}

if ($Config) {
    $arguments += @('--config', $Config)
}

# 다음부터는 직접 칠 수 있도록, 실제로 실행하는 명령을 보여 준다.
$displayParts = @('python', 'polarion_query_backup.py')

if ($arguments.Count -gt 1) {
    $displayParts += $arguments[1..($arguments.Count - 1)]
}

$displayCommand = (
    $displayParts | ForEach-Object {
        if ($_ -match '\s') { '"' + $_ + '"' } else { $_ }
    }
) -join ' '

Write-Host ''
Write-Host ('  실행: ' + $displayCommand) -ForegroundColor DarkGray
Write-Host ('-' * 54) -ForegroundColor DarkGray

& $python @arguments

$exitCode = $LASTEXITCODE

Write-Host ('-' * 54) -ForegroundColor DarkGray

if ($exitCode -eq 0) {
    Write-Host '  완료되었습니다.' -ForegroundColor Green
}
else {
    Write-Failure "실패했습니다 (종료 코드 $exitCode). 위 메시지를 확인하세요."
    Write-Info '준비 상태만 다시 확인하려면:  .\run.ps1 -Check'
}

exit $exitCode
