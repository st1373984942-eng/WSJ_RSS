<#
.SYNOPSIS
    把项目同步到服务器（只传代码和配置，不传产物）。

.EXAMPLE
    powershell -File sync-to-server.ps1 -Server 1.2.3.4 -User root
    powershell -File sync-to-server.ps1 -Server 1.2.3.4 -User ubuntu -RemoteRoot /opt/wsj-digest
#>
param(
    [Parameter(Mandatory = $true)][string]$Server,
    [string]$User = "root",
    [string]$RemoteRoot = "/opt/wsj-digest",
    [string]$KeyFile = ""
)

$ErrorActionPreference = "Stop"
$ws = Split-Path -Parent $PSScriptRoot          # 工作区根目录（wsj-kindle 的上一级）
$staging = Join-Path $env:TEMP ("wsj-sync-" + (Get-Date -Format "yyyyMMddHHmmss"))

# 产物目录不用传：服务器上会自己生成
$excludeDirs = @("articles", "public", "out", "data", "logs", "images", ".browser-profile", ".browser-profile-edge")

Write-Host "==> 准备暂存目录 $staging"
New-Item -ItemType Directory -Force -Path $staging | Out-Null

foreach ($proj in @("wsj-kindle")) {
    $src = Join-Path $ws $proj
    if (-not (Test-Path $src)) { Write-Warning "跳过不存在的 $src"; continue }
    $dst = Join-Path $staging $proj
    New-Item -ItemType Directory -Force -Path $dst | Out-Null
    $xd = $excludeDirs | ForEach-Object { Join-Path $src $_ }
    # robocopy 的退出码 0/1 都算成功
    $args = @($src, $dst, "/E", "/NFL", "/NDL", "/NJH", "/NJS", "/NP") + ($xd | ForEach-Object { "/XD"; $_ })
    & robocopy @args | Out-Null
    if ($LASTEXITCODE -gt 7) { throw "robocopy 失败，退出码 $LASTEXITCODE" }
}

# deploy 目录也一起传（预检脚本、systemd 单元、安装脚本）
$depDst = Join-Path $staging "deploy"
& robocopy (Join-Path $ws "deploy") $depDst /E /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -gt 7) { throw "robocopy deploy 失败，退出码 $LASTEXITCODE" }

Write-Host "==> 确保服务器目录存在"
$sshArgs = @()
$scpArgs = @()
if ($KeyFile) { $sshArgs += @("-i", $KeyFile); $scpArgs += @("-i", $KeyFile) }
& ssh @sshArgs "$User@$Server" "mkdir -p '$RemoteRoot'"
if ($LASTEXITCODE -ne 0) { throw "ssh 连接失败：检查 $User@$Server 和密钥" }

Write-Host "==> 上传到 $User@$Server`:$RemoteRoot"
& scp @scpArgs -r "$staging\wsj-kindle" "$staging\deploy" "$User@${Server}:$RemoteRoot/"
if ($LASTEXITCODE -ne 0) { throw "scp 失败" }

Remove-Item $staging -Recurse -Force

Write-Host @"

==> 上传完成。接下来在服务器上执行：

  sudo APP_ROOT=$RemoteRoot bash $RemoteRoot/deploy/install-server.sh
  # 编辑两个 config.json 的 kindle 段（收件地址 + SMTP 授权码，enabled 改 true）
  $RemoteRoot/venv/bin/python $RemoteRoot/preflight.py
  sudo systemctl enable --now wsj-kindle.timer

"@
