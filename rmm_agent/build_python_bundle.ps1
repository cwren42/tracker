#Requires -Version 5.1
<#
.SYNOPSIS
    Build the self-contained "embedded Python" bundle the RMM agent ships with.

    Produces  rmm_agent/deps/cirque-python-embed-3.12.4-win_amd64.zip  — a fully
    self-contained CPython 3.12.4 (python.org *embeddable* distribution) that carries
    the agent's own pip dependencies AND tkinter, so an endpoint needs NO system-wide
    Python installed. install_agent.ps1 extracts this to C:\CirqueRMM\python\ and points
    the NSSM service at C:\CirqueRMM\python\python.exe agent_launcher.py.

    WHY embeddable + manual assembly (not the .exe installer):
      * the embeddable distribution is a relocatable zip — no registry, no PATH, no
        admin MSI — perfect for a per-install private interpreter under C:\CirqueRMM\python\.
      * BUT the embeddable distro omits two things the agent needs, which this script
        adds back from a full CPython install:
          1. pip / site-packages  → enabled by rewriting python312._pth (uncomment
             "import site") and vendoring the agent's wheels into site-packages.
          2. tkinter / Tcl-Tk     → the tray dialogs (ticket form, install-software
             picker, request-fix, info, updates) all use tkinter, which the embeddable
             distro does NOT include. We copy _tkinter.pyd, tcl86t.dll, tk86t.dll,
             zlib1.dll, the Lib/tkinter package and the tcl/ runtime from a full 3.12.x.

    MUST run on Windows x64 (win_amd64 .pyd/.dll are platform-specific). Built in CI on
    windows-latest (.github/workflows/agent-python-bundle.yml) or by hand on a Win box.

.PARAMETER PyVersion
    CPython version to bundle. Default 3.12.4 (matches the pinned deps/ Python + the
    cp312 wheelhouse). Keep in lockstep with deps/python-3.12.4-amd64.exe.

.PARAMETER OutDir
    Where to write the bundle zip. Default: the deps/ mirror next to this script.

.PARAMETER FullPython
    python.exe of a FULL CPython 3.12.x install to source tkinter/Tcl-Tk + build pip from.
    Default: the python on PATH (CI's actions/setup-python provides 3.12).
#>
param(
    [string]$PyVersion  = "3.12.4",
    [string]$OutDir     = "$PSScriptRoot\deps",
    [string]$FullPython = "python"
)
$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Arch       = "amd64"
$BundleName = "cirque-python-embed-$PyVersion-win_$Arch.zip"
$Work       = Join-Path $env:TEMP "cirque_pybundle_$([guid]::NewGuid().ToString('N').Substring(0,8))"
$EmbedRoot  = Join-Path $Work "python"      # becomes C:\CirqueRMM\python\
New-Item -ItemType Directory -Force -Path $Work, $OutDir | Out-Null

Write-Host "=== Building $BundleName ===" -ForegroundColor Cyan
Write-Host "    work dir: $Work"

# ── 1. Fetch + extract the embeddable distribution ───────────────────────────
$EmbedZip = Join-Path $Work "python-embed.zip"
$EmbedUrl = "https://www.python.org/ftp/python/$PyVersion/python-$PyVersion-embed-$Arch.zip"
Write-Host "[1] Downloading embeddable distro: $EmbedUrl"
Invoke-WebRequest -Uri $EmbedUrl -OutFile $EmbedZip -UseBasicParsing
Expand-Archive -Path $EmbedZip -DestinationPath $EmbedRoot -Force
Write-Host "    extracted to $EmbedRoot"

# ── 2. Enable site-packages in the ._pth ──────────────────────────────────────
# The embeddable distro ships python312._pth with `import site` COMMENTED OUT, which
# disables site-packages (so pip-installed packages won't import). Uncomment it and
# add a Lib\site-packages line so our vendored deps are on sys.path.
$verNodot = ($PyVersion -split '\.')[0..1] -join ''      # 3.12.4 -> 312
$Pth = Join-Path $EmbedRoot "python$verNodot._pth"
if (-not (Test-Path $Pth)) { throw "._pth not found at $Pth (embeddable layout changed?)" }
$pthLines = Get-Content $Pth
$pthLines = $pthLines | ForEach-Object {
    if ($_ -match '^\s*#\s*import\s+site\s*$') { 'import site' } else { $_ }
}
if ($pthLines -notcontains 'import site') { $pthLines += 'import site' }
if ($pthLines -notcontains 'Lib\site-packages') { $pthLines += 'Lib\site-packages' }
Set-Content -Path $Pth -Value $pthLines -Encoding ASCII
Write-Host "[2] Patched $([System.IO.Path]::GetFileName($Pth)) (site enabled)"

# ── 3. Bootstrap pip into the embeddable interpreter ──────────────────────────
$EmbedPy = Join-Path $EmbedRoot "python.exe"
New-Item -ItemType Directory -Force -Path (Join-Path $EmbedRoot "Lib\site-packages") | Out-Null
$GetPip = Join-Path $Work "get-pip.py"
Write-Host "[3] Bootstrapping pip"
Invoke-WebRequest -Uri "https://bootstrap.pypa.io/get-pip.py" -OutFile $GetPip -UseBasicParsing
& $EmbedPy $GetPip --no-warn-script-location
if ($LASTEXITCODE -ne 0) { throw "get-pip bootstrap failed" }

# ── 4. Install the agent's deps into the embedded site-packages ───────────────
# Prefer the offline wheelhouse next to this script (deps/wheelhouse) so the build
# is reproducible/offline-capable; fall back to PyPI when it's absent.
$Wheelhouse = Join-Path $PSScriptRoot "deps\wheelhouse"
$Req        = Join-Path $PSScriptRoot "requirements.txt"
Write-Host "[4] Installing agent dependencies into embedded site-packages"
if (Test-Path $Wheelhouse) {
    & $EmbedPy -m pip install --no-warn-script-location --no-index --find-links $Wheelhouse -r $Req
    if ($LASTEXITCODE -ne 0) { throw "offline wheelhouse install failed" }
} else {
    & $EmbedPy -m pip install --no-warn-script-location -r $Req
    if ($LASTEXITCODE -ne 0) { throw "PyPI dep install failed" }
}
# pystray + pillow are tray-only (not in requirements.txt's hard set) but the tray menu
# needs them; install them too so the bundle is complete.
if (Test-Path $Wheelhouse) {
    & $EmbedPy -m pip install --no-warn-script-location --no-index --find-links $Wheelhouse pystray pillow
} else {
    & $EmbedPy -m pip install --no-warn-script-location pystray pillow
}
if ($LASTEXITCODE -ne 0) { throw "pystray/pillow install failed" }

# ── 5. Graft tkinter + Tcl-Tk from a full CPython install ─────────────────────
# The tray's dialogs import tkinter, which the embeddable distro omits. Copy the
# tkinter Lib package, _tkinter.pyd, the Tcl/Tk DLLs and the tcl runtime tree from
# a full 3.12.x. Without this, every tray dialog raises ModuleNotFoundError: _tkinter.
Write-Host "[5] Grafting tkinter + Tcl-Tk from full CPython ($FullPython)"
$fullBase = (& $FullPython -c "import sys,os;print(os.path.dirname(sys.executable))").Trim()
if (-not (Test-Path $fullBase)) { throw "could not locate full CPython base from '$FullPython'" }
$fullVer  = (& $FullPython -c "import sys;print('%d.%d'%sys.version_info[:2])").Trim()
$embedVer = ($PyVersion -split '\.')[0..1] -join '.'
if ($fullVer -ne $embedVer) { throw "FullPython is $fullVer but bundling $embedVer — minor versions must match for tkinter ABI" }

# pythonXY.zip in the embeddable distro holds the stdlib; tkinter needs to live in
# Lib\ (a real dir) so its package data + _tkinter.pyd resolve. Create Lib\ and copy.
$embedLib = Join-Path $EmbedRoot "Lib"
New-Item -ItemType Directory -Force -Path $embedLib | Out-Null
Copy-Item (Join-Path $fullBase "Lib\tkinter") (Join-Path $embedLib "tkinter") -Recurse -Force
# _tkinter.pyd + the loader DLLs live in DLLs\ (pyd) and the base dir (tcl/tk DLLs).
foreach ($pyd in @("_tkinter.pyd")) {
    $src = Join-Path $fullBase "DLLs\$pyd"
    if (Test-Path $src) { Copy-Item $src $EmbedRoot -Force } else { throw "missing $src" }
}
# Tcl/Tk runtime DLLs — names are version-suffixed (tcl86t.dll/tk86t.dll for 3.12).
$tkDlls = Get-ChildItem (Join-Path $fullBase "DLLs") -Filter "*.dll" |
          Where-Object { $_.Name -match '^(tcl|tk)\d|^zlib1\.dll$' }
if (-not $tkDlls) {
    # Some layouts keep the DLLs in the base dir, not DLLs\.
    $tkDlls = Get-ChildItem $fullBase -Filter "*.dll" | Where-Object { $_.Name -match '^(tcl|tk)\d' }
}
foreach ($d in $tkDlls) { Copy-Item $d.FullName $EmbedRoot -Force }
# The tcl/ runtime tree (init.tcl etc.). It sits in the install root's tcl\ dir.
$tclSrc = Join-Path $fullBase "tcl"
if (-not (Test-Path $tclSrc)) {
    # actions/setup-python layout: <base>\tcl may be one level up
    $tclSrc = Join-Path (Split-Path $fullBase) "tcl"
}
if (-not (Test-Path $tclSrc)) { throw "could not find tcl/ runtime tree near $fullBase" }
Copy-Item $tclSrc (Join-Path $EmbedRoot "tcl") -Recurse -Force
Write-Host "    grafted tkinter, _tkinter.pyd, $($tkDlls.Count) Tcl/Tk DLLs, tcl/ runtime"

# ── 6. Verify: the bundled interpreter imports EVERY agent dependency ─────────
Write-Host "[6] Verifying bundled interpreter imports all agent dependencies" -ForegroundColor Yellow
$verify = @'
import sys
mods = ["psutil", "websockets", "mss", "PIL", "pystray", "tkinter", "ssl",
        "ctypes", "urllib.request", "asyncio", "json", "hashlib", "platform", "socket"]
# pywinpty exposes the import name "winpty"
mods.append("winpty")
failed = []
for m in mods:
    try:
        __import__(m)
        print("  OK   ", m)
    except Exception as e:
        print("  FAIL ", m, "->", e)
        failed.append(m)
# tkinter must be able to construct a Tk (needs Tcl/Tk DLLs + tcl/ runtime).
try:
    import tkinter
    r = tkinter.Tk(); r.withdraw(); r.destroy()
    print("  OK    tkinter.Tk() constructed")
except Exception as e:
    print("  FAIL  tkinter.Tk() ->", e); failed.append("tkinter.Tk")
print("python:", sys.version)
sys.exit(1 if failed else 0)
'@
$verifyFile = Join-Path $Work "verify_bundle.py"
Set-Content -Path $verifyFile -Value $verify -Encoding UTF8
& $EmbedPy $verifyFile
if ($LASTEXITCODE -ne 0) { throw "Bundle verification FAILED — one or more agent deps did not import." }
Write-Host "    all agent dependencies import under the bundled interpreter." -ForegroundColor Green

# Confirm pythonw.exe is present (tray launches via pythonw to suppress the console).
if (-not (Test-Path (Join-Path $EmbedRoot "pythonw.exe"))) {
    throw "pythonw.exe missing from embeddable distro — tray launch needs it."
}

# ── 7. Zip the bundle ─────────────────────────────────────────────────────────
$OutZip = Join-Path $OutDir $BundleName
if (Test-Path $OutZip) { Remove-Item $OutZip -Force }
Write-Host "[7] Packing $OutZip"
# Zip the CONTENTS of $EmbedRoot so the archive's top level is python.exe/Lib/... ,
# extracting to C:\CirqueRMM\python\ yields C:\CirqueRMM\python\python.exe directly.
Compress-Archive -Path (Join-Path $EmbedRoot '*') -DestinationPath $OutZip -CompressionLevel Optimal -Force

$size = "{0:N1} MB" -f ((Get-Item $OutZip).Length / 1MB)
$sha  = (Get-FileHash $OutZip -Algorithm SHA256).Hash.ToLower()
Write-Host ""
Write-Host "=== Bundle built ===" -ForegroundColor Green
Write-Host "    $OutZip"
Write-Host "    size  : $size"
Write-Host "    sha256: $sha"
Write-Host ""
Write-Host "Place this in the Tracker deps mirror (rmm_agent/deps/) and it will be served"
Write-Host "token-gated by /download/deps/$BundleName to the installer."

# Cleanup work dir (keep the zip).
Remove-Item $Work -Recurse -Force -ErrorAction SilentlyContinue
