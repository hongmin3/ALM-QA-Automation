<#
.SYNOPSIS
    VXvue/License Manager SRS 사양서 자동화를 Windows Task Scheduler에 등록한다.

.DESCRIPTION
    작업 두 개를 등록한다.

    1) <TaskName>        - 매주 월요일 07:00 정기 실행
    2) <TaskName>_CatchUp - PC 시작 5분 후 실행. `--catch-up`이라서 이번 주에 이미
                            수행한 기록이 있으면 아무것도 하지 않고 즉시 종료한다.
                            예정 시각에 PC가 꺼져 있어 놓친 주만 여기서 만회된다.

    실패한 주는 자동으로 다시 돌리지 않는다(사용자 결정). 성공/실패 여부는 매 실행
    결과 메일로 알린다.

    공통 설정
    - StartWhenAvailable: 예정된 시작을 놓친 경우 다음 가능 시점에 실행
    - MultipleInstances = IgnoreNew: 정기 실행과 부팅 실행이 겹쳐도 중복 실행 안 함
    - RunOnlyIfNetworkAvailable: Polarion 접근 불가 상태에서 헛돌지 않게
    - S4U 로그온: 로그온 여부와 무관하게 실행되며 비밀번호를 저장하지 않음
    - Priority 4(보통): Task Scheduler 기본값 7(낮음)에서는 Chromium 인쇄가 크게
      느려져 정상 그룹도 시간제한을 초과하는 것을 실제로 확인했다.
    - 실패 시 자동 재시도는 하지 않는다(RestartCount 0). 실패는 메일로 알린다.

.PARAMETER TaskName
    등록할 작업 이름 (기본값: VXvue_SRS_Spec_Automation)

.PARAMETER PythonExe
    사용할 python.exe 전체 경로 (기본값: PATH에서 자동 탐색)

.PARAMETER At
    정기 실행 시각 (기본값: 07:00). 오전 8시 전에 끝나도록 여유를 둔 값이다.
#>
param(
    [string]$TaskName = "VXvue_SRS_Spec_Automation",
    [string]$PythonExe = "",
    [string]$At = "07:00"
)

$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $PSScriptRoot
Write-Output "프로젝트 경로: $ProjectDir"

if (-not $PythonExe) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $cmd) {
        throw "python.exe를 PATH에서 찾을 수 없습니다. -PythonExe 파라미터로 전체 경로를 지정하세요."
    }
    $PythonExe = $cmd.Source
}
Write-Output "Python 경로: $PythonExe"

# 로그온 여부와 무관하게 실행되며, 별도로 비밀번호를 저장하지 않는다.
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Limited

function New-CommonSettings {
    New-ScheduledTaskSettingsSet `
        -StartWhenAvailable `
        -MultipleInstances IgnoreNew `
        -RunOnlyIfNetworkAvailable `
        -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
        -Priority 4
}

function Register-One {
    param($Name, $Arguments, $Trigger, $Description)

    if (Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue) {
        Write-Output "기존 작업 '$Name' 발견 - 갱신을 위해 먼저 제거합니다."
        Unregister-ScheduledTask -TaskName $Name -Confirm:$false
    }
    $action = New-ScheduledTaskAction -Execute $PythonExe -Argument $Arguments -WorkingDirectory $ProjectDir
    Register-ScheduledTask -TaskName $Name -Action $action -Trigger $Trigger `
        -Settings (New-CommonSettings) -Principal $principal -Description $Description | Out-Null
    Write-Output "작업 등록 완료: $Name"
}

# 1) 주간 정기 실행
$weekly = @{
    Name        = $TaskName
    Arguments   = "main.py"
    Trigger     = (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At $At)
    Description = "VXvue/License Manager Polarion SRS 사양서 자동 최신화 및 변경 리포트 생성 (매주 월요일 $At)"
}
Register-One @weekly

# 2) PC 시작 시 만회 실행 (이번 주에 이미 수행했으면 즉시 종료)
$catchUpName = "${TaskName}_CatchUp"
$startupTrigger = New-ScheduledTaskTrigger -AtStartup
$startupTrigger.Delay = "PT5M"   # 부팅 직후 부하를 피해 5분 지연
$catchUp = @{
    Name        = $catchUpName
    Arguments   = "main.py --catch-up"
    Trigger     = $startupTrigger
    Description = "놓친 주간 실행 만회. 이번 주에 이미 수행한 기록이 있으면 아무것도 하지 않는다."
}
Register-One @catchUp

Write-Output ""
foreach ($n in @($TaskName, $catchUpName)) {
    Get-ScheduledTask -TaskName $n | Format-List TaskName, State
    Get-ScheduledTaskInfo -TaskName $n | Format-List NextRunTime, LastRunTime, LastTaskResult
}
