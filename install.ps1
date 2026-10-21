# It's Me Node - pemasang satu baris, tanpa pertanyaan.
#   Komputer pertama:
#     irm https://raw.githubusercontent.com/OBKENTUNG01/itsme-node/main/install.ps1 | iex
#   Komputer berikutnya: kirim /pasang ke bot It's Me di Telegram, lalu tempel
#   satu baris yang dibalas bot. Isinya kode undangan sekali pakai (ITSME_INVITE);
#   kode gabung, token bot, dan pengaturan It's Me diambil sendiri dari komputer
#   yang mengundang, dan It's Me ikut dipasang.
# Kode gabung manual tetap bisa lewat variabel lingkungan ITSME_JOIN.

$ErrorActionPreference = "Stop"
$Repo = "OBKENTUNG01/itsme-node"
$Url = "https://raw.githubusercontent.com/$Repo/main/install.ps1"

function Say($m) { Write-Host "==> $m" -ForegroundColor Cyan }

function Install-ItsMeNode {
    $admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $admin) {
        Say "Meminta izin Administrator (untuk memasang program dan membuka port)..."
        $cmd = "`$env:ITSME_JOIN='$env:ITSME_JOIN'; `$env:ITSME_INVITE='$env:ITSME_INVITE'; irm $Url | iex; Read-Host 'Tekan Enter untuk menutup'"
        Start-Process powershell -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -Command $cmd"
        return
    }

    # Undangan dari /pasang: "KODE" atau "KODE~tskey-auth-..." (izin Tailscale sekali pakai).
    $invite, $tsKey = "$env:ITSME_INVITE".Trim() -split "~", 2

    $Root = Join-Path $env:LOCALAPPDATA "ItsMeNode"
    $App = Join-Path $Root "app"
    $Data = Join-Path $Root "data"
    New-Item -ItemType Directory -Force $App, $Data | Out-Null

    function Refresh-Path {
        $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                    [Environment]::GetEnvironmentVariable("Path", "User")
    }
    function Has($exe) { [bool](Get-Command $exe -ErrorAction SilentlyContinue) }
    # Nama fungsi sengaja bukan "Winget" supaya tidak bentrok dengan winget bawaan.
    function Install-WingetPackage($id) {
        Say "Memasang $id ..."
        winget.exe install --id $id -e --silent --accept-package-agreements --accept-source-agreements | Out-Host
        Refresh-Path
    }
    if (-not (Has winget.exe)) { throw "winget tidak ditemukan. Perbarui 'App Installer' dari Microsoft Store dulu." }

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
    if (-not $py) { Install-WingetPackage "Python.Python.3.12"; $py = Find-Python }
    if (-not $py) { throw "Python gagal dipasang." }
    Say "Python: $py"

    # 2. Ollama (model lokal) - dilewati kalau RAM < 6 GB
    $ram = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB)
    $ollama = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"
    if ($ram -ge 6 -and -not (Test-Path $ollama) -and -not (Has ollama)) { Install-WingetPackage "Ollama.Ollama" }
    if ($ram -lt 6) { Say "RAM $ram GB: komputer ini menjadi penjelajah tanpa model." }

    # 3. Tailscale (menyambungkan semua komputer)
    $ts = "$env:ProgramFiles\Tailscale\tailscale.exe"
    if (-not (Test-Path $ts)) { Install-WingetPackage "Tailscale.Tailscale" }
    & $ts status *> $null
    if ($LASTEXITCODE -ne 0) {
        if ($tsKey) {
            Say "Menyambungkan Tailscale otomatis ..."
            & $ts up --authkey=$tsKey --unattended | Out-Host
        } else {
            Say "Login Tailscale dengan akun yang SAMA seperti komputer lain (browser akan terbuka, cukup pilih akunnya)."
            & $ts up --unattended | Out-Host
        }
    }

    # 3b. Tukar kode undangan dengan komputer yang mengundang (lewat Tailscale)
    $joined = $null
    if ($invite) {
        Say "Mencari komputer yang mengundang di jaringan Tailscale ..."
        $body = @{ code = $invite; name = $env:COMPUTERNAME } | ConvertTo-Json
        $deadline = (Get-Date).AddMinutes(3)
        while (-not $joined -and (Get-Date) -lt $deadline) {
            $st = & $ts status --json 2>$null | Out-String | ConvertFrom-Json -ErrorAction SilentlyContinue
            $ips = @($st.Peer.PSObject.Properties.Value | Where-Object Online |
                     ForEach-Object { $_.TailscaleIPs | Where-Object { $_ -notmatch ":" } | Select-Object -First 1 })
            foreach ($ip in $ips) {
                try {
                    $r = Invoke-RestMethod "http://${ip}:7171/invite" -Method Post -Body $body `
                        -ContentType "application/json" -TimeoutSec 5
                    if ($r.join_code) { $joined = $r; break }
                } catch { }
            }
            if (-not $joined) { Start-Sleep 5 }
        }
        if (-not $joined) {
            throw ("Kode undangan tidak diterima. Kemungkinan: kodenya kedaluwarsa atau sudah dipakai, " +
                   "komputer yang mengundang sedang mati, atau Tailscale login dengan akun lain. " +
                   "Kirim /pasang lagi ke bot lalu tempel perintah barunya.")
        }
        Say "Diterima oleh $($joined.from)."
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

    # 4b. Gabung-ulang otomatis (install ulang): tanpa kode undangan & belum punya
    #     kode gabung, komputer ini mungkin DULU terdaftar lalu OS-nya diinstal ulang.
    #     Sidik jari perangkat kerasnya tetap sama, jadi jaringan mengenalinya kembali.
    $cfgPath = Join-Path $Data "config.json"
    $hadCode = (Test-Path $cfgPath) -and ((Get-Content $cfgPath -Raw | ConvertFrom-Json).join_code)
    if (-not $joined -and -not $env:ITSME_JOIN -and -not $hadCode) {
        Push-Location $App
        $fp = & $py -m inode fingerprint 2>$null | Select-Object -Last 1
        Pop-Location
        if ($fp) { $fp = "$fp".Trim() } else { $fp = "" }
        $st = & $ts status --json 2>$null | Out-String | ConvertFrom-Json -ErrorAction SilentlyContinue
        $peers = @($st.Peer.PSObject.Properties.Value | Where-Object Online)
        if ($fp -and $peers.Count -gt 0) {
            Say "Mencoba gabung otomatis (komputer mungkin dikenal dari sebelumnya) ..."
            $body = @{ fingerprint = $fp; name = $env:COMPUTERNAME } | ConvertTo-Json
            $deadline = (Get-Date).AddSeconds(45)
            while (-not $joined -and (Get-Date) -lt $deadline) {
                foreach ($peer in $peers) {
                    $ip = $peer.TailscaleIPs | Where-Object { $_ -notmatch ":" } | Select-Object -First 1
                    if (-not $ip) { continue }
                    try {
                        $r = Invoke-RestMethod "http://${ip}:7171/enroll" -Method Post -Body $body `
                            -ContentType "application/json" -TimeoutSec 5
                        if ($r.join_code) { $joined = $r; break }
                    } catch { }
                }
                if (-not $joined) {
                    Start-Sleep 4
                    $st = & $ts status --json 2>$null | Out-String | ConvertFrom-Json -ErrorAction SilentlyContinue
                    $peers = @($st.Peer.PSObject.Properties.Value | Where-Object Online)
                }
            }
            if ($joined) { Say "Berhasil gabung otomatis dari $($joined.from) - tanpa kode undangan baru." }
            else { Say "Belum dikenali jaringan; lanjut sebagai komputer baru (pakai /pasang kalau perlu)." }
        }
    }

    # 5. Kode gabung
    $cfg = if (Test-Path $cfgPath) { Get-Content $cfgPath -Raw | ConvertFrom-Json } else { [pscustomobject]@{} }
    if ($joined -or -not $cfg.join_code) {
        $code = if ($joined) { $joined.join_code } else { $env:ITSME_JOIN }
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
    $itsmeDir = Join-Path $HOME "ItsMe"
    $itsmeEnv = Join-Path $itsmeDir ".env"
    $envItems = @(if ($joined.env) { $joined.env.PSObject.Properties })
    if ($envItems.Count -and -not (Test-Path $itsmeEnv)) {
        # Pengaturan bot dari komputer yang mengundang (token bot, pemilik, kunci otak).
        New-Item -ItemType Directory -Force $itsmeDir | Out-Null
        $lines = $envItems | ForEach-Object { "$($_.Name)=$($_.Value)" }
        [IO.File]::WriteAllLines($itsmeEnv, [string[]]$lines, (New-Object Text.UTF8Encoding($false)))
    }
    if ($envItems.Count) {
        Write-Host ""
        Say "Memasang It's Me (bot Telegram) dengan pengaturan yang sama ..."
        $env:ITSME_NO_SLEEP = "1"
        $env:ITSME_UNATTENDED = "1"
        irm "https://raw.githubusercontent.com/OBKENTUNG01/website/main/jarvis/setup.ps1" | iex
        Write-Host ""
        Say "Selesai! Komputer ini sudah bergabung. Bot Telegram aktif di komputer pemimpin, komputer lain siaga."
    } elseif (Test-Path $itsmeEnv) {
        Say "It's Me terdeteksi: bot Telegram otomatis aktif di komputer yang sedang menjadi pemimpin."
    }
    if ($first) {
        Write-Host ""
        Write-Host "Ini komputer PERTAMA. Untuk menambah komputer lain, kirim /pasang ke bot It's Me" -ForegroundColor Yellow
        Write-Host "di Telegram, lalu tempel satu baris yang dibalas bot di PowerShell komputer itu." -ForegroundColor Yellow
    }
}

Install-ItsMeNode
