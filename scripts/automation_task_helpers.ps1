function Test-AutomationWeekdayTrigger {
    param([Parameter(Mandatory = $true)]$Trigger)

    # MSFT_TaskWeeklyTrigger exposes DaysOfWeek as a UInt16 bitmask:
    # Monday=2, Tuesday=4, Wednesday=8, Thursday=16, Friday=32.
    return ([int]$Trigger.DaysOfWeek -eq 62 -and [int]$Trigger.WeeksInterval -eq 1)
}
