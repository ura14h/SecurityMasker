param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Setup", "Remove")]
    [string]$Action
)

$ErrorActionPreference = "Stop"
$Root = [IO.Path]::GetFullPath((Join-Path $env:ProgramData "SecurityMaskerOwnerGate"))
$Fixture = Join-Path $Root "wrong-owner.txt"

function Assert-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator
    )) {
        throw "Windows owner gate must run from an elevated administrator shell"
    }
}

function Assert-SafeRoot {
    $expected = [IO.Path]::GetFullPath(
        (Join-Path $env:ProgramData "SecurityMaskerOwnerGate")
    )
    if (-not $Root.Equals($expected, [StringComparison]::OrdinalIgnoreCase)) {
        throw "refusing to operate on an unexpected owner gate path"
    }
    if ((Test-Path -LiteralPath $Root) -and
        ((Get-Item -LiteralPath $Root -Force).Attributes -band
            [IO.FileAttributes]::ReparsePoint)) {
        throw "refusing to operate on a reparse-point owner gate fixture"
    }
}

function Remove-Fixture {
    Assert-SafeRoot
    if (-not (Test-Path -LiteralPath $Root)) {
        return
    }
    $entries = @(Get-ChildItem -LiteralPath $Root -Force)
    if ($entries.Count -gt 1 -or
        ($entries.Count -eq 1 -and
            -not $entries[0].FullName.Equals(
                $Fixture, [StringComparison]::OrdinalIgnoreCase
            ))) {
        throw "refusing to remove an owner gate directory with unexpected entries"
    }
    if (Test-Path -LiteralPath $Fixture) {
        Remove-Item -LiteralPath $Fixture -Force
    }
    Remove-Item -LiteralPath $Root -Force
}

function Setup-Fixture {
    Assert-SafeRoot
    if (Test-Path -LiteralPath $Root) {
        throw "owner gate fixture already exists; run cleanup first"
    }
    New-Item -ItemType Directory -Path $Root | Out-Null
    try {
        [IO.File]::WriteAllText($Fixture, "synthetic-owner-fixture")
        $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
        $system = [Security.Principal.SecurityIdentifier]::new("S-1-5-18")
        $administrators = [Security.Principal.SecurityIdentifier]::new(
            "S-1-5-32-544"
        )
        $security = [Security.AccessControl.FileSecurity]::new()
        $security.SetAccessRuleProtection($true, $false)
        foreach ($sid in @($identity.User, $system, $administrators)) {
            $rule = [Security.AccessControl.FileSystemAccessRule]::new(
                $sid,
                [Security.AccessControl.FileSystemRights]::FullControl,
                [Security.AccessControl.AccessControlType]::Allow
            )
            [void]$security.AddAccessRule($rule)
        }
        $security.SetOwner($administrators)
        Set-Acl -LiteralPath $Fixture -AclObject $security
        $installed = Get-Acl -LiteralPath $Fixture
        $installedOwner = [Security.Principal.NTAccount]::new(
            $installed.Owner
        ).Translate([Security.Principal.SecurityIdentifier])
        if ($installedOwner.Value -ne $administrators.Value) {
            throw "owner gate fixture owner was not set to Administrators"
        }
    }
    catch {
        Remove-Fixture
        throw
    }
    Write-Output "created synthetic wrong-owner fixture"
}

Assert-IsAdministrator
switch ($Action) {
    "Setup" { Setup-Fixture }
    "Remove" {
        Remove-Fixture
        Write-Output "removed synthetic wrong-owner fixture"
    }
}
