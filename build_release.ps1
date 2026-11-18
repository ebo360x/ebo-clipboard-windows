<#
.SYNOPSIS
    Build ebo clipboard and publish it as a GitHub release, in one command.

.EXAMPLE
    .\build_release.ps1 1.4
    Builds, zips, commits version.py, tags 1.4, pushes and publishes the release.
    Release notes default to the commit subjects since the previous release.

.EXAMPLE
    .\build_release.ps1 1.4 -Notes "Fixed sync"
    Same, with your own release notes.

.EXAMPLE
    .\build_release.ps1 1.4 -DryRun
    Builds and zips only. No commit, tag, push or release.
#>
param(
    [Parameter(Mandatory = $true, Position = 0)][string]$Version,
    [string]$Notes = "",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
Set-Location $root

$python = Join-Path $root ".venv\Scripts\python.exe"
$appName = "ebo clipboard"
$distDir = Join-Path $root "build\release\$Version"
$workDir = Join-Path $root "build\release-work"
$exePath = Join-Path $distDir "$appName.exe"
$zipPath = Join-Path $distDir "ebo-clipboard.zip"
$versionFile = Join-Path $root "version.py"
$utf8NoBom = New-Object System.Text.UTF8Encoding $false

function Step($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }
function Assert-Ok($what) { if ($LASTEXITCODE -ne 0) { throw "$what failed (exit code $LASTEXITCODE)" } }

# ---------------------------------------------------------------- checks
Step "Checking"
if ($Version -notmatch '^\d+\.\d+(\.\d+)?$') { throw "Version must look like 1.4 or 1.4.1 (got '$Version')" }
if (-not (Test-Path $python)) { throw ".venv not found. Run: python -m venv .venv" }
$running = Get-Process -Name $appName -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $exePath }
if ($running) { throw "$exePath is running. Close the app from the tray first." }

$previousTag = $null
if (-not $DryRun) {
    if (-not (Get-Command gh -ErrorAction SilentlyContinue)) { throw "GitHub CLI (gh) is not installed" }
    $branch = (git rev-parse --abbrev-ref HEAD).Trim(); Assert-Ok "git rev-parse"
    if ($branch -ne "master") { throw "Releases are made from master (you are on '$branch')" }
    $dirty = git status --porcelain; Assert-Ok "git status"
    if ($dirty) { throw "Uncommitted changes. Commit or stash them first:`n$($dirty -join "`n")" }
    git fetch --quiet --tags origin; Assert-Ok "git fetch"
    if (git tag --list $Version) { throw "Tag $Version already exists" }
    try { $previousTag = git describe --tags --abbrev=0 2>$null } catch { $previousTag = $null }
    if ($LASTEXITCODE -ne 0) { $previousTag = $null }
}

# ---------------------------------------------------------------- build
Step "Installing build tools"
& $python -m pip install --quiet --disable-pip-version-check -r requirements-build.txt; Assert-Ok "pip install"

$originalVersionFile = [System.IO.File]::ReadAllText($versionFile)
$newVersionFile = $originalVersionFile -replace '__version__\s*=\s*"[^"]*"', "__version__ = `"$Version`""
[System.IO.File]::WriteAllText($versionFile, $newVersionFile, $utf8NoBom)

try {
    Step "Building $appName $Version"
    & $python -m PyInstaller --noconfirm --clean --log-level WARN `
        --onefile --noconsole --name $appName `
        --icon (Join-Path $root "icon.png") `
        --add-data "$(Join-Path $root 'fonts');fonts" `
        --add-data "$(Join-Path $root 'icon.png');." `
        --distpath $distDir --workpath $workDir --specpath $workDir `
        (Join-Path $root "main.py")
    Assert-Ok "PyInstaller"

    Step "Checking the bundle"
    & $python (Join-Path $root "tools\check_bundle.py") $exePath $Version; Assert-Ok "Bundle check"

    Step "Zipping"
    if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
    Compress-Archive -Path $exePath, (Join-Path $root "SYNC_SETUP.md") -DestinationPath $zipPath -CompressionLevel Optimal
    $sizeMb = [math]::Round((Get-Item $zipPath).Length / 1MB, 1)
    Write-Host "Created $zipPath ($sizeMb MB)"
}
catch {
    [System.IO.File]::WriteAllText($versionFile, $originalVersionFile, $utf8NoBom)
    throw
}

if ($DryRun) {
    [System.IO.File]::WriteAllText($versionFile, $originalVersionFile, $utf8NoBom)
    Step "Dry run done. Nothing was committed or published."
    Write-Host "Exe: $exePath"
    exit 0
}

# ---------------------------------------------------------------- publish
if (-not $Notes) {
    $range = if ($previousTag) { "$previousTag..HEAD" } else { "HEAD" }
    $subjects = git log $range --no-merges --pretty=format:"- %s"; Assert-Ok "git log"
    $Notes = if ($subjects) { ($subjects -join "`n") } else { "Release $Version" }
}
$notesFile = Join-Path $workDir "release-notes.md"
[System.IO.File]::WriteAllText($notesFile, $Notes, $utf8NoBom)

Step "Committing version $Version"
if ($newVersionFile -ne $originalVersionFile) {
    git add version.py; Assert-Ok "git add"
    git commit --quiet -m "Release $Version"; Assert-Ok "git commit"
}
git tag $Version; Assert-Ok "git tag"

Step "Pushing"
git push --quiet origin master $Version; Assert-Ok "git push"

Step "Publishing GitHub release $Version"
gh release create $Version $zipPath --title $Version --notes-file $notesFile --verify-tag; Assert-Ok "gh release create"

Step "Done"
gh release view $Version --json url --jq .url
