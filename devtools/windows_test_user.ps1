param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Setup", "Remove", "VerifyAbsent")]
    [string]$Action
)

$ErrorActionPreference = "Stop"
$User = "SecurityMaskerTester"
$Description = "SecurityMasker isolated CLI test user"
$RuleGroup = "SecurityMasker Windows CLI Egress Gate"
$RuleNames = @("SecurityMaskerCliEgressGate-v4", "SecurityMaskerCliEgressGate-v6")

function Assert-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator
    )) {
        throw "test user lifecycle must run from an elevated administrator shell"
    }
}

function Test-ProfileLeaf([string]$Path, [string]$AccountName) {
    $leaf = [IO.Path]::GetFileName([IO.Path]::GetFullPath($Path))
    return $leaf.Equals($AccountName, [StringComparison]::OrdinalIgnoreCase) -or
        $leaf.StartsWith("$AccountName.", [StringComparison]::OrdinalIgnoreCase)
}

function Assert-SafeProfile([object]$Profile, [string]$AccountName) {
    $path = [IO.Path]::GetFullPath([string]$Profile.LocalPath)
    $users = [IO.Path]::GetFullPath((Join-Path $env:SystemDrive "Users"))
    if (-not [IO.Path]::GetDirectoryName($path).Equals(
        $users.TrimEnd("\"), [StringComparison]::OrdinalIgnoreCase
    )) {
        throw "refusing to remove a test profile outside the local Users directory"
    }
    if (-not (Test-ProfileLeaf -Path $path -AccountName $AccountName)) {
        throw "refusing to remove a profile whose directory does not match the test user"
    }
    if ((Test-Path -LiteralPath $path) -and
        ((Get-Item -LiteralPath $path -Force).Attributes -band
            [IO.FileAttributes]::ReparsePoint)) {
        throw "refusing to remove a reparse-point test profile"
    }
}

function Get-MatchingProfiles([string]$AccountName, [string]$Sid) {
    $profiles = @(Get-CimInstance Win32_UserProfile | Where-Object {
        ($Sid -and $_.SID -eq $Sid) -or
        ($_.LocalPath -and
            (Test-ProfileLeaf -Path ([string]$_.LocalPath) -AccountName $AccountName))
    })
    if ($profiles.Count -gt 1) {
        throw "expected at most one Windows profile for the test user"
    }
    if ($profiles.Count -eq 1 -and $Sid -and $profiles[0].SID -ne $Sid) {
        throw "the test profile SID does not match the local user SID"
    }
    return $profiles
}

function Assert-NoOwnedRuntime([string]$AccountName, [string]$Sid) {
    $qualified = "$env:COMPUTERNAME\$AccountName"
    $processes = @(Get-Process -IncludeUserName | Where-Object {
        $_.UserName -and $_.UserName.Equals(
            $qualified, [StringComparison]::OrdinalIgnoreCase
        )
    })
    if ($processes.Count -ne 0) {
        throw "sign out the test user before removal; owned processes are still running"
    }
    $services = @(Get-CimInstance Win32_Service | Where-Object {
        $_.StartName -and (
            $_.StartName.Equals($qualified, [StringComparison]::OrdinalIgnoreCase) -or
            $_.StartName.Equals(".\$AccountName", [StringComparison]::OrdinalIgnoreCase)
        )
    })
    if ($services.Count -ne 0) {
        throw "refusing to remove a test user that owns Windows services"
    }
    $tasks = @(Get-ScheduledTask | Where-Object {
        $principal = [string]$_.Principal.UserId
        $principal -and (
            $principal.Equals($qualified, [StringComparison]::OrdinalIgnoreCase) -or
            $principal.Equals($AccountName, [StringComparison]::OrdinalIgnoreCase) -or
            ($Sid -and $principal.Equals($Sid, [StringComparison]::OrdinalIgnoreCase))
        )
    })
    if ($tasks.Count -ne 0) {
        throw "refusing to remove a test user that owns scheduled tasks"
    }
    if ($Sid -and (Test-Path -LiteralPath "Registry::HKEY_USERS\$Sid")) {
        throw "sign out the test user before removal; its registry hive is still loaded"
    }
}

function Assert-StandardUser([object]$LocalUser) {
    $administrators = @(Get-LocalGroup -SID "S-1-5-32-544" | Get-LocalGroupMember)
    if ($administrators.SID.Value -contains $LocalUser.SID.Value) {
        throw "the SecurityMasker test user must not be an administrator"
    }
}

function Get-GateRules([string]$Store) {
    return @($RuleNames | ForEach-Object {
        Get-NetFirewallRule -PolicyStore $Store -Name $_ -ErrorAction SilentlyContinue
    })
}

function Remove-GateRules {
    foreach ($name in $RuleNames) {
        $rules = @(Get-NetFirewallRule -PolicyStore PersistentStore -Name $name `
            -ErrorAction SilentlyContinue)
        foreach ($rule in $rules) {
            if ($rule.Group -ne $RuleGroup) {
                throw "refusing to remove a firewall rule outside the SecurityMasker group"
            }
            Remove-NetFirewallRule -PolicyStore PersistentStore -Name $name
        }
    }
}

function Assert-Absent([string]$AccountName) {
    if (Get-LocalUser -Name $AccountName -ErrorAction SilentlyContinue) {
        throw "the SecurityMasker test user still exists"
    }
    $profiles = @(Get-MatchingProfiles -AccountName $AccountName -Sid "")
    if ($profiles.Count -ne 0) {
        throw "the SecurityMasker test user profile still exists"
    }
    if ((Get-GateRules -Store PersistentStore).Count -ne 0 -or
        (Get-GateRules -Store ActiveStore).Count -ne 0) {
        throw "SecurityMasker test user firewall rules still exist"
    }
}

function Setup-Tester {
    if (Get-LocalUser -Name $User -ErrorAction SilentlyContinue) {
        throw "SecurityMaskerTester already exists"
    }
    if ((Get-MatchingProfiles -AccountName $User -Sid "").Count -ne 0) {
        throw "a SecurityMaskerTester profile already exists"
    }
    if ((Get-GateRules -Store PersistentStore).Count -ne 0 -or
        (Get-GateRules -Store ActiveStore).Count -ne 0) {
        throw "remove the existing SecurityMasker firewall gate before setup"
    }
    Write-Output "Enter the password for $User twice at the net.exe prompts."
    & "$env:SystemRoot\System32\net.exe" user $User "*" /add /expires:never
    if ($LASTEXITCODE -ne 0) {
        throw "net.exe failed to create SecurityMaskerTester"
    }
    try {
        Set-LocalUser -Name $User -Description $Description
        $created = Get-LocalUser -Name $User
        Assert-StandardUser -LocalUser $created
    }
    catch {
        Remove-LocalUser -Name $User -ErrorAction SilentlyContinue
        throw
    }
    Write-Output "created local standard test user $User"
}

function Remove-Tester {
    $localUser = Get-LocalUser -Name $User -ErrorAction SilentlyContinue
    $sid = if ($localUser) { $localUser.SID.Value } else { "" }
    $profiles = @(Get-MatchingProfiles -AccountName $User -Sid $sid)
    if (-not $sid -and $profiles.Count -eq 1) {
        $sid = [string]$profiles[0].SID
    }
    if (-not $localUser -and $profiles.Count -eq 0) {
        Remove-GateRules
        Assert-Absent -AccountName $User
        Write-Output "test user $User is already absent"
        return
    }
    Assert-NoOwnedRuntime -AccountName $User -Sid $sid
    foreach ($profile in $profiles) {
        if ($profile.Loaded) {
            throw "sign out the test user before removal; its profile is loaded"
        }
        Assert-SafeProfile -Profile $profile -AccountName $User
    }
    Remove-GateRules
    if ($localUser) {
        Disable-LocalUser -Name $User
    }
    foreach ($profile in $profiles) {
        $profilePath = [string]$profile.LocalPath
        Remove-CimInstance -InputObject $profile
        if (Test-Path -LiteralPath $profilePath) {
            throw "Windows profile removal left the test profile directory behind"
        }
    }
    if ($localUser) {
        Remove-LocalUser -Name $User
    }
    Assert-Absent -AccountName $User
    Write-Output "removed local test user $User and its Windows profile"
}

Assert-IsAdministrator
switch ($Action) {
    "Setup" { Setup-Tester }
    "Remove" { Remove-Tester }
    "VerifyAbsent" {
        Assert-Absent -AccountName $User
        Write-Output "test user $User, its profile, and firewall rules are absent"
    }
}
