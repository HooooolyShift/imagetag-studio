<#
  部署 Premiere MCP Bridge 到本机 CEP 扩展目录，并打开未签名面板加载开关。
  可反复执行（幂等）。
#>
$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot          # ...\promo
$source      = Join-Path $PSScriptRoot "cep_extension"
$extensionsDir = Join-Path $env:APPDATA "Adobe\CEP\extensions"
$target      = Join-Path $extensionsDir "com.codex.premiere.bridge"

if (-not (Test-Path $source)) { throw "找不到扩展源码目录：$source" }

New-Item -ItemType Directory -Force $extensionsDir | Out-Null
New-Item -ItemType Directory -Force $target | Out-Null

Copy-Item (Join-Path $source "*") $target -Recurse -Force
Write-Host "已部署扩展到 $target"

# 允许 CEP 加载未签名扩展（Premiere 2024 使用 CSXS.11）
foreach ($v in 9, 10, 11, 12, 13) {
    $key = "HKCU:\Software\Adobe\CSXS.$v"
    if (Test-Path $key) {
        Set-ItemProperty -Path $key -Name PlayerDebugMode -Value "1" -Type String
        Write-Host "CSXS.$v PlayerDebugMode=1"
    }
}

Write-Host ""
Write-Host "部署完成。请在 Premiere 中确认：窗口 > 扩展 > Premiere MCP Bridge"
