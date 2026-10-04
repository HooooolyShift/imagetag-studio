# 环境安装脚本：创建 conda 环境、安装依赖、下载 torch(CUDA) 与本地模型
# 用法：
#   powershell -ExecutionPolicy Bypass -File tools\setup_env.ps1              # 全部
#   powershell -ExecutionPolicy Bypass -File tools\setup_env.ps1 -Step deps   # 只装依赖
param(
    [ValidateSet("all", "env", "deps", "torch", "models")][string]$Step = "all",
    [string]$EnvName = "imtag",
    [string]$CondaRoot = "D:\Anaconda"
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Net.Http
$root = Split-Path -Parent $PSScriptRoot
$py = "$env:USERPROFILE\.conda\envs\$EnvName\python.exe"
$envPip = "https://pypi.tuna.tsinghua.edu.cn/simple"
$wheelDir = Join-Path $root ".wheels"

function Get-Resumable {
    param([string[]]$Urls, [string]$Dest)
    $tmp = "$Dest.part"
    $client = [System.Net.Http.HttpClient]::new()
    $client.Timeout = [TimeSpan]::FromMinutes(30)
    foreach ($Url in $Urls) {
        Write-Host "  source: $Url"
    for ($attempt = 1; $attempt -le 40; $attempt++) {
        $Url = $Urls[0]
        $have = if (Test-Path $tmp) { (Get-Item $tmp).Length } else { 0 }
        try {
            $req = [System.Net.Http.HttpRequestMessage]::new("Get", $Url)
            if ($have -gt 0) { $req.Headers.Range = [System.Net.Http.Headers.RangeHeaderValue]::new($have, $null) }
            $resp = $client.SendAsync($req, [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult()
            $total = if ($resp.Content.Headers.ContentLength) { $resp.Content.Headers.ContentLength + $have } else { 0 }
            $mode = if ((Test-Path $tmp) -and $resp.StatusCode -eq "PartialContent") { "Append" } else { "Create" }
            $fs = [System.IO.File]::Open($tmp, $mode, "Write", "Read")
            $fs.Seek(0, "End") | Out-Null
            $stream = $resp.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
            $buf = New-Object byte[] (1MB)
            $last = Get-Date
            while (($n = $stream.Read($buf, 0, $buf.Length)) -gt 0) {
                $fs.Write($buf, 0, $n)
                if (((Get-Date) - $last).TotalSeconds -gt 5) {
                    $last = Get-Date
                    $mb = [math]::Round($fs.Length / 1MB, 1)
                    $tot = if ($total -gt 0) { [math]::Round($total / 1MB) } else { "?" }
            Write-Host ("  {0}: {1} MB / {2} MB" -f (Split-Path $Dest -Leaf), $mb, $tot)
                }
            }
            $fs.Close()
            if ($total -gt 0 -and (Get-Item $tmp).Length -lt $total) { throw "下载不完整" }
            Move-Item -Force $tmp $Dest
            Write-Host ("  done {0} ({1} MB)" -f (Split-Path $Dest -Leaf), [math]::Round((Get-Item $Dest).Length / 1MB))
            return
        }
        catch {
            Write-Host ("  retry #{0}: {1}" -f $attempt, $_.Exception.Message)
            Start-Sleep -Seconds 3
            if ($attempt % 10 -eq 0 -and $Urls.Count -gt 1) { $Urls = $Urls[1..($Urls.Count - 1)]; break }
        }
        finally {
            if ($fs) { $fs.Dispose() }
        }
    }
    }
    throw "download failed: $Url"
}

if ($Step -in @("all", "env")) {
    if (-not (Test-Path $py)) {
        Write-Host "create conda env $EnvName ..."
        & "$CondaRoot\Scripts\conda.exe" create -y -n $EnvName python=3.10 `
            -c https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/main --override-channels
    }
}

if ($Step -in @("all", "deps")) {
    Write-Host "install base deps (tsinghua mirror) ..."
    & $py -m pip install -i $envPip numpy pillow opencv-python onnxruntime huggingface_hub PySide6 scikit-learn tqdm regex ftfy
    Write-Host "install CLIP deps without torch ..."
    & $py -m pip install -i $envPip --no-deps open_clip_torch timm
}

if ($Step -in @("all", "torch")) {
    New-Item -ItemType Directory -Force -Path $wheelDir | Out-Null
    $bases = @("https://mirror.sjtu.edu.cn/pytorch-wheels/cu121",
               "https://download-r2.pytorch.org/whl/cu121")
    $torch = "torch-2.5.1%2Bcu121-cp310-cp310-win_amd64.whl"
    $vision = "torchvision-0.20.1%2Bcu121-cp310-cp310-win_amd64.whl"
    Get-Resumable -Urls ($bases | ForEach-Object { "$_/$torch" }) -Dest (Join-Path $wheelDir "torch-2.5.1+cu121-cp310-cp310-win_amd64.whl")
    Get-Resumable -Urls ($bases | ForEach-Object { "$_/$vision" }) -Dest (Join-Path $wheelDir "torchvision-0.20.1+cu121-cp310-cp310-win_amd64.whl")
    Write-Host "install torch ..."
    & $py -m pip install (Join-Path $wheelDir "torch-2.5.1+cu121-cp310-cp310-win_amd64.whl") `
        (Join-Path $wheelDir "torchvision-0.20.1+cu121-cp310-cp310-win_amd64.whl") -i $envPip
}

if ($Step -in @("all", "models")) {
    Write-Host "download models (hf-mirror) ..."
    & $py -m app.cli models
}

Write-Host "done."
