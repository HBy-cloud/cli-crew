<#
.SYNOPSIS
  Install (or uninstall) the cli-crew skill for Claude Code, codex and agy from this repository.

.DESCRIPTION
  The repository's skill folder stays the single source; nothing is copied.
    claude : junction  ~/.claude/skills/cli-crew  -> <repo>/skill/cli-crew
    codex  : junction  ~/.codex/skills/cli-crew   -> <repo>/skill/cli-crew
    agy    : entry in  ~/.gemini/config/skills.json pointing at <repo>/skill
             (agy does not follow junctions in ~/.gemini/config/skills; see docs/research/2026-11-21-e03b-*)
  Existing files that this script did not create are never overwritten. An existing agy skills.json is
  backed up before an entry is added or removed (skills.json.bak, then numbered suffixes).
  Windows only. Start a new CLI session afterwards; running sessions do not pick up new skills.

.EXAMPLE
  pwsh -File scripts/install-skill.ps1
  pwsh -File scripts/install-skill.ps1 -Agents codex,agy
  pwsh -File scripts/install-skill.ps1 -Uninstall
#>
param(
  [string[]]$Agents = @('claude', 'codex', 'agy'),
  [switch]$Uninstall,
  [string]$HomeDir = $HOME   # override for tests
)
$ErrorActionPreference = 'Stop'

# pwsh -File passes comma-separated names as one string; PowerShell callers can pass an array.
$Agents = @($Agents -split ',' | ForEach-Object { $_.Trim() })
foreach ($agent in $Agents) {
  if ($agent -notin @('claude', 'codex', 'agy')) {
    throw "Unknown agent '$agent'. Choose from claude, codex, agy."
  }
}

$skillParent = (Resolve-Path (Join-Path $PSScriptRoot '..\skill')).Path
$skillSrc = Join-Path $skillParent 'cli-crew'
if (-not (Test-Path (Join-Path $skillSrc 'SKILL.md'))) { throw "skill not found: $skillSrc" }
$agyEntry = $skillParent -replace '\\', '/'

function Get-LinkTarget($item) {
  $t = $item.Target
  if ($t -is [array]) { $t = $t[0] }
  if ($t) { return ([string]$t).TrimEnd('\') -replace '^\\\\\?\\', '' }
  return $null
}

function Install-Junction($name, $dir) {
  $link = Join-Path $dir 'cli-crew'
  if (Test-Path $link) {
    $i = Get-Item $link -Force
    if ($i.LinkType -eq 'Junction' -and (Get-LinkTarget $i) -ieq $skillSrc) { "${name}: already installed ($link)"; return }
    Write-Warning "${name}: $link already exists and is not this repository's junction; skipped"
    return
  }
  New-Item -ItemType Directory -Force $dir | Out-Null
  New-Item -ItemType Junction -Path $link -Target $skillSrc | Out-Null
  "${name}: installed junction $link -> $skillSrc"
}

function Uninstall-Junction($name, $dir) {
  $link = Join-Path $dir 'cli-crew'
  if (-not (Test-Path $link)) { "${name}: not installed"; return }
  $i = Get-Item $link -Force
  if ($i.LinkType -eq 'Junction' -and (Get-LinkTarget $i) -ieq $skillSrc) {
    $i.Delete()   # removes the link only; the repository folder is untouched
    "${name}: removed junction $link"
  } else {
    Write-Warning "${name}: $link is not this repository's junction; left as is"
  }
}

function Read-AgyConfig($path) {
  if (-not (Test-Path $path)) { return $null }
  $cfg = Get-Content $path -Raw | ConvertFrom-Json
  if ($null -eq $cfg) { $cfg = [pscustomobject]@{} }
  if (-not ($cfg.PSObject.Properties.Name -contains 'entries')) {
    $cfg | Add-Member -NotePropertyName entries -NotePropertyValue @()
  }
  if ($null -eq $cfg.entries) { $cfg.entries = @() }
  return $cfg
}

function Test-AgyEntry($e) { $e.path -and (($e.path -replace '\\', '/').TrimEnd('/') -ieq $agyEntry) }

function Save-AgyConfig($path, $cfg) {
  $json = $cfg | ConvertTo-Json -Depth 100 -WarningAction Stop
  $backup = "$path.bak"
  for ($suffix = 1; Test-Path -LiteralPath $backup; $suffix++) {
    $backup = "$path.bak.$suffix"
  }
  [System.IO.File]::Copy($path, $backup)
  Set-Content -LiteralPath $path -Encoding utf8 -Value $json
  return (Split-Path -Leaf $backup)
}

function Install-Agy($dir) {
  $path = Join-Path $dir 'skills.json'
  $cfg = Read-AgyConfig $path
  if ($null -eq $cfg) {
    New-Item -ItemType Directory -Force $dir | Out-Null
    [pscustomobject]@{ entries = @([pscustomobject]@{ path = $agyEntry }) } |
      ConvertTo-Json -Depth 10 | Set-Content -Encoding utf8 $path
    "agy: created $path with entry $agyEntry"
    return
  }
  if (@($cfg.entries | Where-Object { Test-AgyEntry $_ }).Count -gt 0) { "agy: already installed ($path)"; return }
  $cfg.entries = @($cfg.entries) + [pscustomobject]@{ path = $agyEntry }
  $backup = Save-AgyConfig $path $cfg
  "agy: added entry $agyEntry to $path (backup: $backup)"
}

function Uninstall-Agy($dir) {
  $path = Join-Path $dir 'skills.json'
  $cfg = Read-AgyConfig $path
  if ($null -eq $cfg -or @($cfg.entries | Where-Object { Test-AgyEntry $_ }).Count -eq 0) { "agy: not installed"; return }
  $cfg.entries = @($cfg.entries | Where-Object { -not (Test-AgyEntry $_) })
  $backup = Save-AgyConfig $path $cfg
  "agy: removed entry $agyEntry from $path (backup: $backup)"
}

$dirs = @{
  claude = Join-Path $HomeDir '.claude\skills'
  codex  = Join-Path $HomeDir '.codex\skills'
  agy    = Join-Path $HomeDir '.gemini\config'
}
foreach ($a in $Agents) {
  if ($Uninstall) {
    if ($a -eq 'agy') { Uninstall-Agy $dirs.agy } else { Uninstall-Junction $a $dirs[$a] }
  } else {
    if ($a -eq 'agy') { Install-Agy $dirs.agy } else { Install-Junction $a $dirs[$a] }
  }
}

if (-not $Uninstall -and -not (Get-Command crew -ErrorAction SilentlyContinue)) {
  Write-Warning "the 'crew' command is not on PATH; install it with: uv tool install `"$((Resolve-Path (Join-Path $PSScriptRoot '..\crew')).Path)`""
}
