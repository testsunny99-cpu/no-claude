#Requires -Version 5.1
<#
    no-claude — Windows 薄封装

    复用 tools/config.py（应用层）和 tools/block.py（网络层），
    这里只负责找 Python、转发参数、装/卸计划任务。

    用法:
      .\no-claude.ps1 status
      .\no-claude.ps1 apply [-Guard]
      .\no-claude.ps1 revert
      .\no-claude.ps1 block install|status|uninstall
      .\no-claude.ps1 guard install|uninstall|status
      .\no-claude.ps1 uninstall

    注意：未在 Windows 实机验证。见同目录 README.md。
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)][string]$Command = "status",
    [Parameter(Position = 1)][string]$Action = "",
    [switch]$DryRun,
    [switch]$Guard
)

$ErrorActionPreference = "Stop"

$Root  = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$PyExe = Join-Path $Root "tools\config.py"
$Block = Join-Path $Root "tools\block.py"
$State = Join-Path $env:LOCALAPPDATA "no-claude"
$TaskName = "no-claude-guard"

function Get-PythonLauncher {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        return @{ Exe = "py"; Pre = @("-3") }
    }
    if (Get-Command python -ErrorAction SilentlyContinue) {
        return @{ Exe = "python"; Pre = @() }
    }
    throw "找不到 Python 3。请安装 https://www.python.org/downloads/windows/ 并勾选 Add python.exe to PATH。"
}

$Py = Get-PythonLauncher

function Invoke-Py {
    param([string[]]$Args)
    & $Py.Exe @($Py.Pre + $Args)
}

function Test-Ready {
    if (-not (Test-Path $PyExe)) {
        throw "找不到 $PyExe，请在仓库根目录运行，或检查是否完整 clone。"
    }
}

function Invoke-GuardInstall {
    New-Item -ItemType Directory -Force -Path $State | Out-Null
    $guard = Join-Path $State "guard.ps1"
    $quoted = $PyExe.Replace("'", "''")
    $pre = ($Py.Pre -join " ")
    @"
# no-claude 守护：CC Switch 切换供应商、Claude 自动更新会重写配置，这里定期补回。
if (Test-Path (Join-Path `$env:LOCALAPPDATA 'no-claude\disabled')) { exit 0 }
& '$($Py.Exe)' $pre '$quoted' apply --quiet | Out-Null
"@ | Set-Content -Path $guard -Encoding UTF8

    $action = "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$guard`""
    schtasks /Create /F /SC MINUTE /MO 1 /TN $TaskName /TR $action | Out-Null
    Remove-Item (Join-Path $State "disabled") -ErrorAction SilentlyContinue
    Write-Host "✓ 守护已安装：计划任务 $TaskName（每 60 秒）"
    Write-Host "  暂停: ni '$State\disabled'"
}

function Invoke-GuardUninstall {
    schtasks /Delete /F /TN $TaskName 2>$null | Out-Null
    Remove-Item (Join-Path $State "guard.ps1") -ErrorAction SilentlyContinue
    Write-Host "✓ 守护已卸载"
}

function Invoke-GuardStatus {
    $out = schtasks /Query /TN $TaskName 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "✓ 守护已安装（计划任务 $TaskName）"
        if (Test-Path (Join-Path $State "disabled")) {
            Write-Host "  ! 但已被 $State\disabled 暂停"
        }
    } else {
        Write-Host "- 守护未安装"
    }
}

function Invoke-Clean {
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $n = 0
    $bases = @($env:APPDATA, (Join-Path $env:USERPROFILE "AppData\Roaming")) |
        Where-Object { $_ -and (Test-Path $_) } | Select-Object -Unique
    foreach ($base in $bases) {
        Get-ChildItem -Path $base -Directory -Filter "Claude*" -ErrorAction SilentlyContinue | ForEach-Object {
            $sentry = Join-Path $_.FullName "sentry"
            if (-not (Test-Path $sentry)) { return }
            Get-ChildItem -Path $sentry -Filter "scope_v*.json" -ErrorAction SilentlyContinue | ForEach-Object {
                if ($DryRun) { Write-Host "  将归档 $($_.Name)" }
                else { Rename-Item $_.FullName "$($_.FullName).archived-$stamp" }
                $n++
            }
            $queue = Join-Path $sentry "queue"
            if (Test-Path $queue) {
                Get-ChildItem -Path $queue -File -ErrorAction SilentlyContinue |
                    Where-Object { $_.Name -ne "queue-v2.json" } | ForEach-Object {
                        if ($DryRun) { Write-Host "  将归档 $($_.Name)"; $n++ }
                        else {
                            $dest = Join-Path $sentry "queue-archived-$stamp"
                            New-Item -ItemType Directory -Force -Path $dest | Out-Null
                            Move-Item $_.FullName (Join-Path $dest $_.Name)
                            $n++
                        }
                    }
        }
    }
    if ($n -eq 0) { Write-Host "✓ 没有待回传的遥测数据" }
    else { Write-Host "✓ 已处理 $n 个文件（归档不删除）" }
}

Test-Ready

switch ($Command.ToLower()) {
    "status" {
        Invoke-Py @($PyExe, "status")
        Write-Host ""
        Invoke-Py @($Block, "status")
    }
    "apply" {
        if ($DryRun) { Invoke-Py @($PyExe, "apply", "--dry-run"); break }
        Invoke-Py @($PyExe, "apply")
        Write-Host ""
        if ($Guard) { Invoke-GuardInstall }
        else {
            Write-Host "提示: CC Switch 切换供应商可能覆盖开关，需要自动补回就加 -Guard。"
            Write-Host "网络层（可选）: .\no-claude.ps1 block install"
        }
    }
    "revert" {
        if ($DryRun) { Invoke-Py @($PyExe, "revert", "--dry-run") }
        else { Invoke-Py @($PyExe, "revert") }
    }
    "clean" { Invoke-Clean }
    "block" {
        $sub = if ($Action) { $Action } else { "status" }
        if ($DryRun) { Invoke-Py @($Block, $sub, "--dry-run") }
        else { Invoke-Py @($Block, $sub) }
    }
    "guard" {
        switch ($Action.ToLower()) {
            "install"   { Invoke-GuardInstall }
            "uninstall" { Invoke-GuardUninstall }
            "status"    { Invoke-GuardStatus }
            default     { Write-Host "用法: .\no-claude.ps1 guard install|uninstall|status"; exit 2 }
        }
    }
    "uninstall" {
        if ($DryRun) { Invoke-Py @($PyExe, "revert", "--dry-run") }
        else {
            Invoke-Py @($PyExe, "revert")
            Invoke-Py @($Block, "uninstall")
            Invoke-GuardUninstall
        }
    }
    default {
        Write-Host @"
no-claude (Windows)

用法: .\no-claude.ps1 <命令> [-DryRun] [-Guard]

命令:
  status                        查看应用层 + 网络层状态
  apply [-Guard]                写入关闭开关
  revert                        移除开关
  clean                         归档已排队的遥测数据（不删除）
  block install|status|uninstall  Clash Verge REJECT 规则
  guard install|uninstall|status  计划任务守护
  uninstall                     全部回滚
"@
        exit 2
    }
}
