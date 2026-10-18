# It's Me Node - pemasang satu baris.
#   irm https://raw.githubusercontent.com/OBKENTUNG01/itsme-node/main/install.ps1 | iex
# Kode gabung bisa diisi lewat variabel lingkungan ITSME_JOIN supaya tidak ditanya.

$ErrorActionPreference = "Stop"
$Repo = "OBKENTUNG01/itsme-node"
$Url = "https://raw.githubusercontent.com/$Repo/main/install.ps1"

function Say($m) { Write-Host "==> $m" -ForegroundColor Cyan }

function Install-ItsMeNode {
    $admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $admin) {
        Say "Meminta izin Administrator (untuk memasang program dan membuka port)..."
        $cmd = "`$env:ITSME_JOIN='$env:ITSME_JOIN'; irm $Url | iex; Read-Host 'Tekan Enter untuk menutup'"
        Start-Process powershell -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -Command $cmd"
        return
    }

    $Root = Join-Path $env:LOCALAPPDATA "ItsMeNode"
    $App = Join-Path $Root "app"
    $Data = Join-Path $Root "data"
    New-Item -ItemType Directory -Force $App, $Data | Out-Null

    function Refresh-Path {
        $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                    [Environment]::GetEnvironmentVariable("Path", "User")
    }
    function Has($exe) { [bool](Get-Command $exe -ErrorAction SilentlyContinue) }
    function Winget($id) {
        Say "Memasang $id ..."
        winget install --id $id -e --silent --accept-package-agreements --accept-source-agreements | Out-Host
        Refresh-Path
    }
    if (-not (Has winget)) { throw "winget tidak ditemukan. Perbarui 'App Installer' dari Microsoft Store dulu." }

    # 1. Python
    function Find-Python {
        $cands = @(Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe",
                                 "$env:ProgramFiles\Python3*\python.exe" -ErrorAction SilentlyContinue |
                   Sort-Object FullName -Descending | ForEach-Object FullName)
        foreach ($p in $cands) {
            $v = & $p -c "import sys;print(sys.version_info>=(3,10))" 2>$null
            if ($v -eq "True") { return $p }
        }
        return $null
    }
    $py = Find-Python
    if (-not $py) { Winget "Python.Python.3.12"; $py = Find-Python }
    if (-not $py) { throw "Python gagal dipasang." }
    Say "Python: $py"

    # 2. Ollama (model lokal) - dilewati kalau RAM < 6 GB
    $ram = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB)
    $ollama = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"
    if ($ram -ge 6 -and -not (Test-Path $ollama) -and -not (Has ollama)) { Winget "Ollama.Ollama" }
    if ($ram -lt 6) { Say "RAM $ram GB: komputer ini menjadi penjelajah tanpa model." }

    # 3. Tailscale (menyambungkan semua komputer)
    $ts = "$env:ProgramFiles\Tailscale\tailscale.exe"
    if (-not (Test-Path $ts)) { Winget "Tailscale.Tailscale" }
    & $ts status *> $null
    if ($LASTEXITCODE -ne 0) {
        Say "Login Tailscale dengan akun yang SAMA seperti komputer lain (browser akan terbuka)."
        & $ts up | Out-Host
    }

    # 4. Kode program
    Say "Mengunduh It's Me Node ..."
    $sha = (Invoke-RestMethod "https://api.github.com/repos/$Repo/commits/main" -Headers @{ "User-Agent" = "itsme-node" }).sha
    $zip = Join-Path $env:TEMP "itsme-node.zip"
    Invoke-WebRequest "https://codeload.github.com/$Repo/zip/$sha" -OutFile $zip -UseBasicParsing
    $tmp = Join-Path $env:TEMP "itsme-node-src"
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    Expand-Archive $zip $tmp -Force
    $src = (Get-ChildItem $tmp -Directory | Select-Object -First 1).FullName
    Get-ChildItem $App -Exclude "logs" | Remove-Item -Recurse -Force
    Copy-Item "$src\*" $App -Recurse -Force
    Set-Content (Join-Path $App "COMMIT") $sha -NoNewline
    Set-Content (Join-Path $App "python.txt") $py -NoNewline
    Remove-Item $zip, $tmp -Recurse -Force -ErrorAction SilentlyContinue

    # 5. Kode gabung
    $cfgPath = Join-Path $Data "config.json"
    $cfg = if (Test-Path $cfgPath) { Get-Content $cfgPath -Raw | ConvertFrom-Json } else { [pscustomobject]@{} }
    if (-not $cfg.join_code) {
        $code = $env:ITSME_JOIN
        if (-not $code) { $code = Read-Host "Kode gabung (kosongkan kalau ini komputer PERTAMA)" }
        if (-not $code) {
            $code = & $py -c "import secrets;print(secrets.token_urlsafe(18))"
            $first = $true
        }
        $cfg | Add-Member -NotePropertyName join_code -NotePropertyValue $code.Trim() -Force
        [IO.File]::WriteAllText($cfgPath, ($cfg | ConvertTo-Json -Depth 5))
    }

    # 6. Buka port 7171 hanya untuk jaringan Tailscale
    if (-not (Get-NetFirewallRule -DisplayName "ItsMe Node" -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -DisplayName "ItsMe Node" -Direction Inbound -Protocol TCP -LocalPort 7171 `
            -RemoteAddress 100.64.0.0/10 -Action Allow | Out-Null
    }

    # 7. Jalan otomatis saat login (tersembunyi)
    $startup = [Environment]::GetFolderPath("Startup")
    $vbs = Join-Path $startup "ItsMeNode.vbs"
    $run = Join-Path $App "run.ps1"
    Set-Content $vbs ("CreateObject(""WScript.Shell"").Run ""powershell -NoProfile -WindowStyle Hidden " +
        "-ExecutionPolicy Bypass -File """"$run"""""", 0, False") -Encoding ASCII
    Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='powershell.exe'" |
        Where-Object { $_.CommandLine -like "*ItsMeNode*" -and $_.ProcessId -ne $PID } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Process wscript.exe "`"$vbs`""

    Write-Host ""
    Say "Selesai! Node berjalan di latar belakang dan otomatis menyala setiap login."
    Say "Tes kemampuan & unduh model berjalan di belakang (bisa beberapa menit)."
    Write-Host "Log      : $App\logs\node.log"
    Write-Host "Status   : & '$py' -m inode status   (jalankan di folder $App)"

    # 8. Bot Telegram It's Me ikut pemimpin
    if (Test-Path (Join-Path $HOME "ItsMe\.env")) {
        Say "It's Me terdeteksi: bot Telegram otomatis aktif di komputer yang sedang menjadi pemimpin."
    } else {
        Write-Host ""
        Write-Host "Supaya komputer ini bisa menggantikan bot Telegram It's Me saat pemimpin mati,"
        Write-Host "pasang juga It's Me dengan token bot yang SAMA seperti komputer lain:"
        Write-Host "  irm https://raw.githubusercontent.com/OBKENTUNG01/website/main/jarvis/setup.ps1 | iex"
    }
    if ($first) {
        Write-Host ""
        Write-Host "KODE GABUNG (simpan, pakai di komputer lain): $code" -ForegroundColor Yellow
    }
}

Install-ItsMeNode
