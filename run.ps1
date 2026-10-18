# Menjalankan node terus-menerus; dipanggil otomatis saat login oleh install.ps1.
$App = $PSScriptRoot
$Logs = Join-Path $App "logs"
New-Item -ItemType Directory -Force $Logs | Out-Null
$Log = Join-Path $Logs "node.log"
$env:PYTHONUTF8 = "1"
$env:PYTHONUNBUFFERED = "1"

while ($true) {
    $py = (Get-Content (Join-Path $App "python.txt") -Raw).Trim()
    if ((Test-Path $Log) -and (Get-Item $Log).Length -gt 10MB) { Move-Item $Log "$Log.old" -Force }
    Push-Location $App
    & $py -u -m inode run *>> $Log
    $code = $LASTEXITCODE
    Pop-Location
    if ($code -eq 5) { continue }          # pembaruan terpasang, jalankan ulang segera
    Add-Content $Log "[run] node berhenti (kode $code), menyala lagi dalam 15 detik"
    Start-Sleep 15
}
