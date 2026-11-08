<#
.SYNOPSIS
    Build Panelo for Windows: PyInstaller folder, portable zip, Inno Setup installer, checksums.

.DESCRIPTION
    Run from anywhere (Windows PowerShell 5.1 or PowerShell 7):
        powershell -ExecutionPolicy Bypass -File packaging\build.ps1 -Selftest

    Outputs (in dist\):
        Panelo\                       the one-folder app
        Panelo-<ver>-portable.zip     zip of that folder
        Panelo-Setup-<ver>.exe        per-user installer (unless -SkipInstaller)
        SHA256SUMS.txt                checksums of the .exe and .zip files

.PARAMETER Selftest
    Run the frozen app with --selftest after building and require every check to pass.

.PARAMETER SkipChecks
    Skip ruff and pytest.

.PARAMETER SkipInstaller
    Do not build the Inno Setup installer.
#>
[CmdletBinding()]
param(
    [switch]$Selftest,
    [switch]$SkipChecks,
    [switch]$SkipInstaller
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot

function Write-Step([string]$Message) {
    Write-Host ''
    Write-Host "==> $Message" -ForegroundColor Cyan
}

# Simple (non-advanced) functions on purpose: native flags such as -c or -a stay in $args
# instead of being parsed as PowerShell parameters.
#
# Function-local $ErrorActionPreference = 'Continue': in Windows PowerShell 5.1, stderr lines
# from native tools (uv, PyInstaller's INFO log, tar, ISCC) become NativeCommandError records
# when output is redirected, and under 'Stop' the first one aborts the script. Failure is
# decided by the exit code alone.
function Invoke-Native {
    $ErrorActionPreference = 'Continue'
    $exe = $args[0]
    $rest = @($args | Select-Object -Skip 1)
    # Merge stderr and print every line as plain text.
    & $exe @rest 2>&1 | ForEach-Object { "$_" }
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed (exit code $LASTEXITCODE): $exe $($rest -join ' ')"
    }
}

function Get-NativeOutput {
    $ErrorActionPreference = 'Continue'
    $exe = $args[0]
    $rest = @($args | Select-Object -Skip 1)
    # Capture stdout only; stderr goes to the host and never into the returned value.
    $output = & $exe @rest
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed (exit code $LASTEXITCODE): $exe $($rest -join ' ')"
    }
    return (($output | Out-String).Trim())
}

# Push/Pop so the caller's current directory is restored, even if the script is dot-sourced or fails.
Push-Location -LiteralPath $RepoRoot
try {

    # --- 0. Tools -----------------------------------------------------------------
    Write-Step 'Checking tools'
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        throw 'uv was not found on PATH. Install it with: winget install --id astral-sh.uv -e'
    }

    # --- 1. Version ---------------------------------------------------------------
    Write-Step 'Reading version'
    $Version = Get-NativeOutput uv run python -c "import tomllib;print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])"
    $PackageVersion = Get-NativeOutput uv run python -c "import panelo;print(panelo.__version__)"
    if ($Version -ne $PackageVersion) {
        throw "Version mismatch: pyproject.toml says '$Version' but panelo.__version__ is '$PackageVersion'."
    }
    if ($Version -notmatch '^\d+(\.\d+){0,3}$') {
        throw "Unexpected version format: '$Version'"
    }
    Write-Host "Version $Version"

    # --- 2. SQLite FTS5 -----------------------------------------------------------
    Write-Step 'Checking SQLite FTS5 support'
    Invoke-Native uv run python -c "import sqlite3;sqlite3.connect(':memory:').execute('create virtual table t using fts5(x)')"

    # --- 3. Lint and tests --------------------------------------------------------
    if ($SkipChecks) {
        Write-Step 'Skipping ruff and pytest (-SkipChecks)'
    } else {
        Write-Step 'Running ruff'
        Invoke-Native uv run ruff check .
        Write-Step 'Running pytest'
        Invoke-Native uv run pytest
    }

    # --- 4. PyInstaller -----------------------------------------------------------
    Write-Step 'Building with PyInstaller'
    foreach ($dir in @('build', 'dist')) {
        if (Test-Path -LiteralPath $dir) {
            Remove-Item -LiteralPath $dir -Recurse -Force
        }
    }
    Invoke-Native uv run pyinstaller 'packaging\panelo.spec' --noconfirm --clean --distpath dist --workpath build

    $AppDir = Join-Path $RepoRoot 'dist\Panelo'
    $Exe = Join-Path $AppDir 'Panelo.exe'
    if (-not (Test-Path -LiteralPath $Exe -PathType Leaf)) {
        throw "PyInstaller did not produce $Exe"
    }

    # --- 4a. Required files -------------------------------------------------------
    Write-Step 'Checking bundle contents'
    $Required = @(
        '_internal\panelo\ui\qml\Main.qml',
        '_internal\panelo\ui\qml\App\qmldir',
        '_internal\panelo\ui\fonts\Outfit[wght].ttf',
        '_internal\panelo\ui\icons\app-icon.svg',
        '_internal\panelo\db\migrations\env.py',
        '_internal\PySide6\plugins\platforms\qwindows.dll',
        '_internal\PySide6\plugins\imageformats\qsvg.dll',
        '_internal\PySide6\plugins\iconengines\qsvgicon.dll',
        '_internal\PySide6\Qt6Multimedia.dll',
        '_internal\PySide6\qml\QtQuick\Effects',
        '_internal\PySide6\qml\QtQuick\Shapes',
        '_internal\PySide6\qml\QtQuick\Dialogs',
        '_internal\PySide6\qml\QtQuick\Window',
        '_internal\tzdata\zoneinfo\UTC'
    )
    $Missing = @()
    foreach ($rel in $Required) {
        # -LiteralPath: the font file name contains [ and ], which -Path treats as wildcards.
        if (-not (Test-Path -LiteralPath (Join-Path $AppDir $rel))) {
            $Missing += $rel
        }
    }
    $VersionsDir = Join-Path $AppDir '_internal\panelo\db\migrations\versions'
    $MigrationCount = 0
    if (Test-Path -LiteralPath $VersionsDir) {
        $MigrationCount = @(Get-ChildItem -LiteralPath $VersionsDir -Filter '*.py' -File).Count
    }
    if ($MigrationCount -lt 3) {
        $Missing += "_internal\panelo\db\migrations\versions\*.py (found $MigrationCount, need at least 3)"
    }
    $MultimediaPluginDir = Join-Path $AppDir '_internal\PySide6\plugins\multimedia'
    $MultimediaPluginCount = 0
    if (Test-Path -LiteralPath $MultimediaPluginDir) {
        $MultimediaPluginCount = @(Get-ChildItem -LiteralPath $MultimediaPluginDir -Filter '*.dll' -File).Count
    }
    if ($MultimediaPluginCount -lt 1) {
        $Missing += '_internal\PySide6\plugins\multimedia\*.dll (found none)'
    }
    if ($Missing.Count -gt 0) {
        throw ("Bundle is missing required files:`n  " + ($Missing -join "`n  "))
    }
    Write-Host "All required files present ($MigrationCount migrations)."

    # --- 4b. Unwanted files -------------------------------------------------------
    $Forbidden = @('Qt6WebEngine*', 'QtWebEngineProcess.exe', 'Qt6Pdf*', 'Qt63D*', 'Qt6Quick3D*', 'Qt6Charts*', 'opengl32sw.dll')
    $AllFiles = @(Get-ChildItem -LiteralPath $AppDir -Recurse -File -Force)
    $Unwanted = @()
    foreach ($pattern in $Forbidden) {
        $Unwanted += @($AllFiles | Where-Object { $_.Name -like $pattern } | ForEach-Object { $_.FullName.Substring($AppDir.Length + 1) })
    }
    if ($Unwanted.Count -gt 0) {
        throw ("Bundle contains files that should have been excluded:`n  " + ($Unwanted -join "`n  "))
    }
    $BundleBytes = ($AllFiles | Measure-Object -Property Length -Sum).Sum
    Write-Host ('Bundle size: {0:N1} MB in {1} files' -f ($BundleBytes / 1MB), $AllFiles.Count)

    # --- 5. Frozen selftest -------------------------------------------------------
    if ($Selftest) {
        Write-Step 'Running frozen selftest'
        $SelftestLog = Join-Path $RepoRoot 'build\selftest-frozen.log'
        if (Test-Path -LiteralPath $SelftestLog) {
            Remove-Item -LiteralPath $SelftestLog -Force
        }
        $env:PANELO_SELFTEST_OUTPUT = $SelftestLog
        try {
            $Process = Start-Process -FilePath $Exe -ArgumentList '--selftest' -WorkingDirectory $AppDir -PassThru
        } finally {
            Remove-Item Env:\PANELO_SELFTEST_OUTPUT -ErrorAction SilentlyContinue
        }
        if (-not $Process.WaitForExit(900000)) {
            try { $Process.Kill() } catch { Write-Warning "Could not kill selftest process: $_" }
            throw 'Frozen selftest timed out'
        }
        # The untimed WaitForExit() after a successful timed wait makes sure the process has fully
        # exited so ExitCode is populated (a known .NET quirk).
        $Process.WaitForExit()
        $ExitCode = $Process.ExitCode
        if (-not (Test-Path -LiteralPath $SelftestLog -PathType Leaf)) {
            throw "Selftest wrote no log at $SelftestLog (exit code $ExitCode)"
        }
        $LogLines = @(Get-Content -LiteralPath $SelftestLog -Encoding UTF8)
        $LogLines | Where-Object { $_ -match '^(PASS|FAIL): ' } | ForEach-Object { Write-Host "  $_" }
        $QtLines = @($LogLines | Where-Object { $_ -match '^qt: ' })
        foreach ($line in $QtLines) {
            Write-Warning $line
        }
        $Summary = @($LogLines | Where-Object { $_ -match '^selftest: (\d+)/(\d+) passed' })
        if ($Summary.Count -eq 0) {
            throw "Selftest log has no summary line (exit code $ExitCode). See $SelftestLog"
        }
        $null = $Summary[-1] -match '^selftest: (\d+)/(\d+) passed'
        $Passed = [int]$Matches[1]
        $Total = [int]$Matches[2]
        Write-Host "Selftest: $Passed/$Total passed, exit code $ExitCode"
        if (($Passed -ne $Total) -or ($Total -eq 0) -or ($ExitCode -ne 0)) {
            throw "Frozen selftest failed: $Passed/$Total passed, exit code $ExitCode. See $SelftestLog"
        }
    }

    # --- 6. Portable zip ----------------------------------------------------------
    Write-Step 'Creating portable zip'
    $ZipName = "Panelo-$Version-portable.zip"
    $ZipPath = Join-Path $RepoRoot "dist\$ZipName"
    $Tar = Join-Path $env:SystemRoot 'System32\tar.exe'
    if (-not (Test-Path -LiteralPath $Tar -PathType Leaf)) {
        throw "tar.exe not found at $Tar (needs Windows 10 1803 or newer)"
    }
    Invoke-Native $Tar -a -c -f $ZipPath -C dist Panelo
    if (-not (Test-Path -LiteralPath $ZipPath -PathType Leaf)) {
        throw "Zip was not created: $ZipPath"
    }
    Write-Host ('{0} ({1:N1} MB)' -f $ZipName, ((Get-Item -LiteralPath $ZipPath).Length / 1MB))

    # --- 7. Installer -------------------------------------------------------------
    function Find-Iscc {
        $cmd = Get-Command 'ISCC.exe' -ErrorAction SilentlyContinue
        if ($cmd) { return $cmd.Source }

        $candidates = @()
        if ($env:LOCALAPPDATA) { $candidates += (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe') }
        $pf86 = ${env:ProgramFiles(x86)}
        if ($pf86) { $candidates += (Join-Path $pf86 'Inno Setup 6\ISCC.exe') }
        if ($env:ProgramFiles) { $candidates += (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe') }

        $uninstallKeys = @(
            'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1',
            'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1',
            'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1'
        )
        foreach ($key in $uninstallKeys) {
            if (Test-Path -LiteralPath $key) {
                $props = Get-ItemProperty -LiteralPath $key -ErrorAction SilentlyContinue
                if ($props -and ($props.PSObject.Properties.Name -contains 'InstallLocation') -and $props.InstallLocation) {
                    $candidates += (Join-Path $props.InstallLocation 'ISCC.exe')
                }
            }
        }

        foreach ($candidate in $candidates) {
            if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
        }
        return $null
    }

    if ($SkipInstaller) {
        Write-Step 'Skipping installer (-SkipInstaller)'
    } else {
        Write-Step 'Building installer with Inno Setup'
        $Iscc = Find-Iscc
        if (-not $Iscc) {
            throw 'Inno Setup 6 (ISCC.exe) was not found. Install it with: winget install --id JRSoftware.InnoSetup -e  (or pass -SkipInstaller)'
        }
        Write-Host "Using $Iscc"
        Invoke-Native $Iscc "/DAppVersion=$Version" '/Qp' 'packaging\panelo.iss'
        $SetupPath = Join-Path $RepoRoot "dist\Panelo-Setup-$Version.exe"
        if (-not (Test-Path -LiteralPath $SetupPath -PathType Leaf)) {
            throw "Inno Setup did not produce $SetupPath"
        }
        Write-Host ('Panelo-Setup-{0}.exe ({1:N1} MB)' -f $Version, ((Get-Item -LiteralPath $SetupPath).Length / 1MB))
    }

    # --- 8. Checksums -------------------------------------------------------------
    Write-Step 'Writing SHA256SUMS.txt'
    $DistDir = Join-Path $RepoRoot 'dist'
    $Artifacts = @(Get-ChildItem -LiteralPath $DistDir -File | Where-Object { $_.Extension -in @('.exe', '.zip') } | Sort-Object Name)
    $SumLines = @()
    foreach ($file in $Artifacts) {
        $hash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        $SumLines += "$hash  $($file.Name)"
    }
    $SumsPath = Join-Path $DistDir 'SHA256SUMS.txt'
    Set-Content -LiteralPath $SumsPath -Value $SumLines -Encoding Ascii
    $SumLines | ForEach-Object { Write-Host "  $_" }

    Write-Step "Done: Panelo $Version"
    Get-ChildItem -LiteralPath $DistDir | ForEach-Object { Write-Host "  dist\$($_.Name)" }
} finally {
    Pop-Location
}
