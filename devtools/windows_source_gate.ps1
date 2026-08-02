param(
    [Parameter(Mandatory = $true)]
    [string]$Root
)

$ErrorActionPreference = "Stop"
$ExpectedUser = "SecurityMaskerTester"

function Assert-StandardTestUser {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $localUser = Get-LocalUser -Name $ExpectedUser -ErrorAction SilentlyContinue
    if (-not $localUser -or $localUser.SID.Value -ne $identity.User.Value) {
        throw "source archive gate must run as the fixed SecurityMaskerTester user"
    }
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if ($principal.IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator
    )) {
        throw "source archive gate must run as a standard user"
    }
}

function Assert-LocalFixedNtfs([string]$Path) {
    $fullPath = [IO.Path]::GetFullPath($Path)
    if ($fullPath -notmatch "^[A-Za-z]:\\") {
        throw "source archive must be on a local drive-letter path"
    }
    $volume = Get-Volume -DriveLetter $fullPath.Substring(0, 1)
    if ($volume.DriveType -ne "Fixed" -or $volume.FileSystem -ne "NTFS") {
        throw "source archive must be on a local fixed NTFS volume"
    }
    $candidate = Get-Item -LiteralPath $fullPath -Force
    while ($candidate) {
        if ($candidate.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "source archive path must not contain a reparse point"
        }
        $candidate = $candidate.Parent
    }
}

function Assert-CleanArchive([string]$Path) {
    foreach ($name in @(".git", ".venv")) {
        if (Test-Path -LiteralPath (Join-Path $Path $name)) {
            throw "source gate requires a fresh archive without $name"
        }
    }
    foreach ($name in @(
        "securitymasker.config",
        "securitymasker.dict",
        "securitymasker.state",
        "securitymasker.state.lock",
        "securitymasker-claude"
    )) {
        if (Test-Path -LiteralPath (Join-Path $Path $name)) {
            throw "source gate requires an archive without existing product data: $name"
        }
    }
    foreach ($name in @("PYTHONPATH", "VIRTUAL_ENV", "SECURITYMASKER_CONFIG")) {
        if (Test-Path -LiteralPath "Env:$name") {
            throw "source gate refuses inherited environment variable $name"
        }
    }
}

Assert-StandardTestUser
$resolvedRoot = (Resolve-Path -LiteralPath $Root).Path
Assert-LocalFixedNtfs -Path $resolvedRoot
Assert-CleanArchive -Path $resolvedRoot
[ordered]@{
    clean_archive = $true
    fixed_standard_user = $true
    local_fixed_ntfs = $true
} | ConvertTo-Json -Compress
