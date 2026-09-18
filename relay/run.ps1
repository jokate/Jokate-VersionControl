# 릴레이 실행 도우미: 프롬프트 파일을 읽어 Agent 카태 릴레이로 보낸다.
#   relay\run.bat 17-layout            (prompts\17-layout.txt)
#   relay\run.bat 19a-baseline -Model fable -Relay quick-fable
# 프롬프트를 명령줄에 직접 쓰면 큰따옴표·백슬래시가 인자 파싱을 깨므로 반드시 파일에서 읽는다.
param(
    [Parameter(Mandatory = $true)][string]$Prompt,
    [string]$Model = "opus",            # opus | fable | sonnet
    [string]$Relay = "quick-fable",     # 단계 예산 $2. 작은 수정은 quick(Sonnet, $0.8)
    [string]$Repo = "jokate",
    [string]$RelayDir = "$env:USERPROFILE\Projects\relay-agent"
)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$file = $Prompt
if (-not (Test-Path $file)) { $file = Join-Path $here "prompts\$Prompt" }
if (-not (Test-Path $file)) { $file = Join-Path $here "prompts\$Prompt.txt" }
if (-not (Test-Path $file)) { Write-Host "[X] 프롬프트 파일 없음: $Prompt"; exit 1 }

$repoRoot = Split-Path -Parent $here
$dirty = git -C $repoRoot status --porcelain
if ($dirty) {
    Write-Host "[!] 커밋 안 된 변경이 있습니다. 릴레이 결과와 섞이니 먼저 커밋하세요:"
    $dirty | Select-Object -First 10 | ForEach-Object { Write-Host "    $_" }
    exit 1
}

$goal = (Get-Content $file -Raw -Encoding UTF8).Trim()
Write-Host "프롬프트 : $file ($($goal.Length)자)"
Write-Host "릴레이   : $Relay  모델: $Model  저장소: $Repo"
Write-Host "대시보드 : http://127.0.0.1:8020"
Write-Host ""
Push-Location $RelayDir
try {
    uv run relay run $Relay --repo $Repo --stage-model "build=claude:$Model" $goal
} finally {
    Pop-Location
}
Write-Host ""
Write-Host "다음: relay\verify.bat 로 검증 → 확인되면 git commit"
