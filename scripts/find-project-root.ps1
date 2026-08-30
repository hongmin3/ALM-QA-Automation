<#
.SYNOPSIS
  Walk up from a starting directory to find the nearest Akela project context root.
.DESCRIPTION
  Looks for akela.json first; falls back to the nearest ancestor containing
  AGENTS.md, CLAUDE.md, .git, or README.md. No hardcoded paths.
#>
param(
    [string]$StartPath = (Get-Location).Path
)
$dir = Get-Item -LiteralPath $StartPath
$fallback = $null
while ($dir) {
    $akelaConfig = Join-Path $dir.FullName "akela.json"
    if (Test-Path -LiteralPath $akelaConfig) { Write-Output $dir.FullName; exit 0 }
    if (-not $fallback) {
        foreach ($marker in @("AGENTS.md", "CLAUDE.md", ".git", "README.md")) {
            if (Test-Path -LiteralPath (Join-Path $dir.FullName $marker)) { $fallback = $dir.FullName; break }
        }
    }
    $parent = $dir.Parent
    if (-not $parent -or $parent.FullName -eq $dir.FullName) { break }
    $dir = $parent
}
if ($fallback) { Write-Output $fallback; exit 0 }
Write-Output $StartPath
exit 1
