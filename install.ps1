# It's Me Node - pemasang satu baris, tanpa pertanyaan, TANPA Tailscale.
#   Komputer pertama:
#     irm https://raw.githubusercontent.com/OBKENTUNG01/itsme-node/main/install.ps1 | iex
#   Komputer berikutnya: kirim /pasang ke bot It's Me di Telegram, lalu tempel
#   satu baris yang dibalas bot. Isinya kode undangan sekali pakai (ITSME_INVITE)
#   dan alamat Hub (ITSME_HUB). Komputer baru menukar kode itu di dashboard Hub,
#   mengambil kode gabung + token jembatan + pengaturan bot, lalu menyambung
#   KELUAR ke Hub (tanpa VPN/Tailscale, tetap jalan di balik NAT).
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
        $cmd = "`$env:ITSME_JOIN='$env:ITSME_JOIN'; `$env:ITSME_INVITE='$env:ITSME_INVITE'; `$env:ITSME_HUB='$env:ITSME_HUB'; irm $Url | iex; Read-Host 'Tekan Enter untuk menutup'"
        Start-Process powershell -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -Command $cmd"
        return
    }

    # Undangan dari /pasang: "KODE" (format lama "KODE~..." tetap diterima; sisanya diabaikan).
    $invite = ("$env:ITSME_INVITE".Trim() -split "~", 2)[0]
    $hub = "$env:ITSME_HUB".Trim().TrimEnd("/")

    $Root = Join-Path $env:LOCALAPPDATA "ItsMeNode"
    $App = Join-Path $Root "app"
    $Data = Join-Path $Root "data"
    New-Item -ItemType Directory -Force $App, $Data | Out-Null
    $cfgPath = Join-Path $Data "config.json"
    # Pemasangan ulang: alamat Hub bisa diambil dari config lama kalau tidak diberikan.
    if (-not $hub -and (Test-Path $cfgPath)) {
        $hub = "" + (Get-Content $cfgPath -Raw | ConvertFrom-Json).bridge_url
        $hub = $hub.TrimEnd("/")
    }

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

    # 3. Copot Tailscale kalau ada - It's Me tidak memakainya lagi (pakai jembatan ke Hub).
    $tsExe = "$env:ProgramFiles\Tailscale\tailscale.exe"
    if ((Test-Path $tsExe) -or (Has tailscale)) {
        Say "Mencopot Tailscale (tidak dipakai lagi) ..."
        try { winget.exe uninstall --id Tailscale.Tailscale -e --silent --accept-source-agreements | Out-Host } catch { }
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

    # 4b. Tukar kode undangan di dashboard Hub (tanpa Tailscale)
    $joined = $null
    if ($invite) {
        if (-not $hub) {
            throw "Alamat Hub (ITSME_HUB) kosong. Kirim /pasang lagi ke bot It's Me untuk baris baru."
        }
        Say "Menukar kode undangan di Hub ..."
        $body = @{ code = $invite; name = $env:COMPUTERNAME } | ConvertTo-Json
        try {
            $joined = Invoke-RestMethod "$hub/api/node/invite" -Method Post -Body $body `
                -ContentType "application/json" -TimeoutSec 30
        } catch {
            throw ("Kode undangan tidak diterima (" + $_.Exception.Message + "). Kemungkinan: kodenya " +
                   "kedaluwarsa atau sudah dipakai, atau Hub (dashboard) sedang mati / alamatnya salah. " +
                   "Kirim /pasang lagi ke bot lalu tempel perintah barunya.")
        }
        if (-not $joined.join_code) { throw "Hub tidak mengembalikan kode gabung. Kirim /pasang lagi." }
        Say "Diterima oleh $($joined.from)."
    }

    # 4c. Gabung-ulang otomatis (install ulang aplikasi, OS masih sama): tanpa kode
    #     undangan & belum punya kode gabung, tapi Hub diketahui dari config lama.
    #     Sidik jari perangkat kerasnya tetap sama, jadi Hub mengenalinya kembali.
    $hadCode = (Test-Path $cfgPath) -and ((Get-Content $cfgPath -Raw | ConvertFrom-Json).join_code)
    if (-not $joined -and -not $env:ITSME_JOIN -and -not $hadCode -and $hub) {
        Push-Location $App
        $fp = & $py -m inode fingerprint 2>$null | Select-Object -Last 1
        Pop-Location
        if ($fp) { $fp = "$fp".Trim() } else { $fp = "" }
        if ($fp) {
            Say "Mencoba gabung otomatis lewat Hub (komputer mungkin dikenal dari sebelumnya) ..."
            $body = @{ fingerprint = $fp; name = $env:COMPUTERNAME } | ConvertTo-Json
            try {
                $joined = Invoke-RestMethod "$hub/api/node/enroll" -Method Post -Body $body `
                    -ContentType "application/json" -TimeoutSec 20
            } catch { $joined = $null }
            if ($joined.join_code) { Say "Berhasil gabung otomatis dari $($joined.from) - tanpa kode undangan baru." }
            else { $joined = $null; Say "Belum dikenali Hub; lanjut sebagai komputer baru (pakai /pasang kalau perlu)." }
        }
    }

    # 5. Kode gabung + jembatan ke Hub
    $cfg = if (Test-Path $cfgPath) { Get-Content $cfgPath -Raw | ConvertFrom-Json } else { [pscustomobject]@{} }
    if ($joined -or -not $cfg.join_code) {
        $code = if ($joined) { $joined.join_code } else { $env:ITSME_JOIN }
        if (-not $code) {
            $code = & $py -c "import secrets;print(secrets.token_urlsafe(18))"
            $first = $true
        }
        $cfg | Add-Member -NotePropertyName join_code -NotePropertyValue $code.Trim() -Force
    }
    if ($hub) { $cfg | Add-Member -NotePropertyName bridge_url -NotePropertyValue $hub -Force }
    if ($joined.bridge_token) {
        $cfg | Add-Member -NotePropertyName bridge_token -NotePropertyValue "$($joined.bridge_token)" -Force
    }
    [IO.File]::WriteAllText($cfgPath, ($cfg | ConvertTo-Json -Depth 5))

    # 6. Buka port 7171 hanya untuk jaringan lokal (LAN); jembatan ke Hub menyambung keluar.
    if (-not (Get-NetFirewallRule -DisplayName "ItsMe Node" -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -DisplayName "ItsMe Node" -Direction Inbound -Protocol TCP -LocalPort 7171 `
            -RemoteAddress LocalSubnet -Action Allow | Out-Null
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
