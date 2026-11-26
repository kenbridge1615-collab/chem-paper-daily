# Chem Paper Daily 初回セットアップ（setup.bat から呼ばれます）
#
# 1. uv（Python とライブラリの管理ツール）が無ければ公式インストーラで入れる
# 2. uv sync で Python 本体と必要なライブラリを入れる
# 3. .env が無ければ .env.example をコピーする
# 4. Claude Code（claude コマンド）があるか確認する
# 5. Windows タスクスケジューラに毎朝のジョブを登録する
#    （ログオン中のみ実行・時刻に起動していなければ後ですぐ実行）
#
# このファイルは Windows PowerShell 5.1 で日本語を正しく読めるよう
# UTF-8（BOM 付き）で保存しています。

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root
$TaskName = "ChemPaperDaily"
$UserBin = Join-Path $env:USERPROFILE ".local\bin"
$Total = 5

function Step([int]$n, [string]$msg) {
    Write-Host ""
    Write-Host "[$n/$Total] $msg" -ForegroundColor Cyan
}
function Ok([string]$msg)   { Write-Host "  OK: $msg" -ForegroundColor Green }
function Warn([string]$msg) { Write-Host "  注意: $msg" -ForegroundColor Yellow }
function Fail([string]$msg) {
    Write-Host ""
    Write-Host "  エラー: $msg" -ForegroundColor Red
    Write-Host "  docs\SETUP_GUIDE.md の「困ったときは」を見てください。" -ForegroundColor Red
    exit 1
}
function Add-UserBinToPath {
    if ((Test-Path -LiteralPath $UserBin) -and -not (($env:Path -split ";") -contains $UserBin)) {
        $env:Path = "$UserBin;$env:Path"
    }
}

Write-Host "==============================================" -ForegroundColor Cyan
Write-Host "  Chem Paper Daily のセットアップを始めます"   -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host "  フォルダ: $Root"

# --- 1. uv ------------------------------------------------------------------
Step 1 "uv（Python の管理ツール）を確認しています"
Add-UserBinToPath
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "  uv が無いので、公式のインストーラで入れます（1〜2分かかります）..."
    powershell -NoProfile -ExecutionPolicy ByPass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    Add-UserBinToPath
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Fail "uv のインストールに失敗しました。インターネットにつながっているか確認して、もう一度 setup.bat を実行してください。"
    }
}
Ok ("uv " + ((uv --version) -join " "))

# --- 2. uv sync ---------------------------------------------------------------
Step 2 "Python と必要なライブラリを入れています（初回は数分かかります）"
$ConfigText = Get-Content -LiteralPath (Join-Path $Root "config.toml") -Raw -Encoding UTF8
if ($ConfigText -match '(?m)^\s*backend\s*=\s*"api"') {
    # API 方式のときは anthropic ライブラリも入れる
    uv sync --extra api
} else {
    uv sync
}
if ($LASTEXITCODE -ne 0) {
    Fail "ライブラリのインストール（uv sync）に失敗しました。インターネット接続を確認して、もう一度 setup.bat を実行してください。"
}
Ok "ライブラリの準備ができました"

# --- 3. .env と data フォルダ -------------------------------------------------
Step 3 "設定ファイル（.env）とデータ用フォルダを準備しています"
foreach ($d in @("data", "data\logs", "data\pdfs")) {
    New-Item -ItemType Directory -Force -Path (Join-Path $Root $d) | Out-Null
}
$EnvFile = Join-Path $Root ".env"
if (-not (Test-Path -LiteralPath $EnvFile)) {
    Copy-Item -LiteralPath (Join-Path $Root ".env.example") -Destination $EnvFile
    Ok ".env を作りました。あとで Slack の Webhook URL を書き込みます（手順書の 4 番）"
} else {
    Ok ".env はすでにあります（中身はそのままにしました）"
}
if ($ConfigText -match 'contact_email\s*=\s*"you@example\.com"') {
    Warn "config.toml の contact_email がまだ例のままです。手順書の 5 番で自分のメールアドレスに変えてください。"
}

# --- 4. Claude Code -------------------------------------------------------------
Step 4 "Claude Code（claude コマンド）を確認しています"
$ClaudeFound = $false
if (Get-Command claude -ErrorAction SilentlyContinue) {
    $ClaudeFound = $true
    Ok ("Claude Code が見つかりました: " + ((claude --version) -join " "))
    Write-Host "  ログインできているかは、手順書 6 番の run_daily.bat のテストで確認します。"
} else {
    Warn "Claude Code が見つかりません。紹介文を書くのに必要です。"
    Write-Host "    1) 手順書 docs\SETUP_GUIDE.md の 3 番を見て、Claude Code をインストールします"
    Write-Host "       （公式ページ: https://code.claude.com/docs/ja/quickstart ）"
    Write-Host "    2) PowerShell で claude と入力し、契約中の Claude アカウントでログインします"
    Write-Host "    3) そのあと、この setup.bat をもう一度ダブルクリックしてください"
}

# --- 5. タスクスケジューラ ------------------------------------------------------
$NotifyTime = "07:00"
if ($ConfigText -match '(?m)^\s*notify_time\s*=\s*"(\d{1,2}:\d{2})"') { $NotifyTime = $Matches[1] }
Step 5 "毎朝 $NotifyTime に動くよう、タスクスケジューラに登録しています"
try {
    $Bat = Join-Path $Root "run_daily.bat"
    $Action = New-ScheduledTaskAction -Execute "cmd.exe" `
        -Argument "/c `"`"$Bat`" --scheduled`"" -WorkingDirectory $Root
    $Trigger = New-ScheduledTaskTrigger -Daily -At $NotifyTime
    # -StartWhenAvailable: 時刻に PC が起動していなかったら、次に使えるようになったときすぐ実行
    $Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew
    # LogonType Interactive: ユーザーがログオンしているときだけ実行
    # （Claude Code のログイン情報はユーザーごとに保存されるため）
    $UserId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $Principal = New-ScheduledTaskPrincipal -UserId $UserId -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger `
        -Settings $Settings -Principal $Principal -Force -ErrorAction Stop `
        -Description "Chem Paper Daily: 毎朝の論文紹介と Slack 通知" | Out-Null
    Ok "タスク「$TaskName」を登録しました（毎日 $NotifyTime、ログオン中のみ、逃したら後で実行）"
} catch {
    Fail ("タスクスケジューラへの登録に失敗しました: " + $_.Exception.Message)
}

# --- 完了 --------------------------------------------------------------------------
Write-Host ""
Write-Host "==============================================" -ForegroundColor Green
Write-Host "  セットアップが終わりました！"                  -ForegroundColor Green
Write-Host "==============================================" -ForegroundColor Green
Write-Host ""
Write-Host "  次にやること（くわしくは docs\SETUP_GUIDE.md）:"
$n = 1
if (-not $ClaudeFound) {
    Write-Host "    $n. Claude Code をインストールしてログインし、setup.bat をもう一度実行"; $n++
}
Write-Host "    $n. .env をメモ帳で開き、Slack の Webhook URL を貼り付けて保存"; $n++
Write-Host "    $n. config.toml の contact_email を自分のメールアドレスに変更"; $n++
Write-Host "    $n. run_daily.bat をダブルクリックして、Slack に届くかテスト"; $n++
Write-Host "    $n. start.bat をダブルクリックすると、ブラウザでアプリが開きます"
Write-Host ""
Write-Host "  毎朝 $NotifyTime（PC が起動していなければ次に起動したとき）に Slack へ届きます。"
exit 0
