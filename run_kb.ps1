param(
    [string]$DataDir,
    [ValidateSet("all", "convert", "extract", "tables", "graph", "viz")]
    [string]$Stage = "all",
    [string]$Model,
    [switch]$Force,
    [string]$Only
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $repoRoot

function Require-Command {
    param([string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command not found: $Name. 请先安装: https://github.com/astral-sh/uv"
    }
}

Require-Command "uv"

$args = @("run", "python", "run_pipeline.py")
if ($Stage -ne "all") {
    $args += @("--stage", $Stage)
}
if ($Force) {
    $args += "--force"
}
if ($Only) {
    $args += @("--only", $Only)
}
if ($DataDir) {
    $args += @("--data-dir", $DataDir)
}
if ($Model) {
    $args += @("--model", $Model)
}

Write-Host "== kb-builder =="
Write-Host "repo  : $repoRoot"
Write-Host "data  : $(if ($DataDir) { $DataDir } else { 'knowledge (default)' })"
Write-Host "stage : $Stage"
if ($Model) {
    Write-Host "model : $Model"
}
Write-Host ""
Write-Host "uv " + ($args -join " ")
Write-Host ""

& uv @args
