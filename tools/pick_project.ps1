# .uproject 파일 선택 창 → 프로젝트 폴더 경로를 stdout 으로 (취소하면 아무것도 출력하지 않음)
Add-Type -AssemblyName System.Windows.Forms
$owner = New-Object System.Windows.Forms.Form -Property @{ TopMost = $true; ShowInTaskbar = $false }
$d = New-Object System.Windows.Forms.OpenFileDialog
$d.Title = 'UE 프로젝트(.uproject) 선택'
$d.Filter = 'Unreal Project (*.uproject)|*.uproject'
$start = Join-Path $env:USERPROFILE 'Projects'
if (Test-Path $start) { $d.InitialDirectory = $start }
if ($d.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    Split-Path -Parent $d.FileName
}
$owner.Dispose()
