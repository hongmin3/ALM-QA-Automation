<#
.SYNOPSIS
    등록된 VXvue SRS 사양서 자동화 Task Scheduler 작업을 제거한다.

.DESCRIPTION
    install_task.ps1이 등록하는 작업 두 개(<TaskName>, <TaskName>_CatchUp)를 모두 제거한다.
#>
param(
    [string]$TaskName = "VXvue_SRS_Spec_Automation"
)

$ErrorActionPreference = "Stop"

foreach ($name in @($TaskName, "${TaskName}_CatchUp")) {
    if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
        Write-Output "작업 제거 완료: $name"
    } else {
        Write-Output "작업 '$name'이(가) 존재하지 않습니다."
    }
}
