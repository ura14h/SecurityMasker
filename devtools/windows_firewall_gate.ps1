param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Install", "Verify", "Remove")]
    [string]$Action,
    [string]$User
)

$ErrorActionPreference = "Stop"
$RuleGroup = "SecurityMasker Windows CLI Egress Gate"
$RuleV4 = "SecurityMaskerCliEgressGate-v4"
$RuleV6 = "SecurityMaskerCliEgressGate-v6"
$RemoteV4 = @(
    "0.0.0.0-126.255.255.255",
    "128.0.0.0-255.255.255.255"
)
$RemoteV6 = @(
    "::",
    "::2-ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff"
)

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-CurrentSid {
    return [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
}

function Resolve-LocalStandardUserSid([string]$AccountName) {
    if ([string]::IsNullOrWhiteSpace($AccountName)) {
        throw "Install requires -User with a dedicated local standard user account"
    }
    $account = [Security.Principal.NTAccount]::new($AccountName)
    $sid = $account.Translate([Security.Principal.SecurityIdentifier]).Value
    $localUser = Get-LocalUser | Where-Object { $_.SID.Value -eq $sid }
    if ($null -eq $localUser) {
        throw "the firewall gate account must be a local Windows user"
    }
    if ($sid -eq (Get-CurrentSid)) {
        throw "install the gate for a different user so the operator remains online"
    }
    $administrators = Get-LocalGroup -SID "S-1-5-32-544" | Get-LocalGroupMember
    if ($administrators.SID.Value -contains $sid) {
        throw "the firewall gate account must not be an administrator"
    }
    return $sid
}

function Get-ExactRule([string]$Name, [string]$PolicyStore = "ActiveStore") {
    $rules = @(Get-NetFirewallRule -PolicyStore $PolicyStore -Name $Name -ErrorAction SilentlyContinue)
    if ($rules.Count -ne 1) {
        throw "expected exactly one active firewall rule named $Name"
    }
    $rule = $rules[0]
    if ($rule.Group -ne $RuleGroup) {
        throw "refusing a firewall rule whose group does not match the SecurityMasker gate"
    }
    return $rule
}

function Assert-Rule(
    [string]$Name,
    [string]$Sid,
    [string[]]$ExpectedAddresses
) {
    $rule = Get-ExactRule -Name $Name
    if ($rule.Enabled -ne "True" -or $rule.Direction -ne "Outbound" -or
        $rule.Action -ne "Block" -or $rule.Profile -ne "Any") {
        throw "$Name is not an enabled outbound block rule for every profile"
    }
    $security = $rule | Get-NetFirewallSecurityFilter
    if ($security.LocalUser -notmatch [regex]::Escape($Sid)) {
        throw "$Name is not scoped to the current gate user"
    }
    $addresses = @(($rule | Get-NetFirewallAddressFilter).RemoteAddress)
    if ($addresses.Count -ne $ExpectedAddresses.Count) {
        throw "$Name has an unexpected remote address count"
    }
    foreach ($expected in $ExpectedAddresses) {
        if ($addresses -notcontains $expected) {
            throw "$Name does not block the required address range $expected"
        }
    }
    $port = $rule | Get-NetFirewallPortFilter
    if ($port.Protocol -ne "Any") {
        throw "$Name does not block every IP protocol"
    }
}

function Remove-GateRules {
    foreach ($name in @($RuleV4, $RuleV6)) {
        $rules = @(Get-NetFirewallRule -PolicyStore PersistentStore -Name $name -ErrorAction SilentlyContinue)
        foreach ($rule in $rules) {
            if ($rule.Group -ne $RuleGroup) {
                throw "refusing to remove $name because its group is not the SecurityMasker gate"
            }
            Remove-NetFirewallRule -PolicyStore PersistentStore -Name $name
        }
    }
}

switch ($Action) {
    "Install" {
        if (-not (Test-IsAdministrator)) {
            throw "Install must run from an elevated administrator shell"
        }
        $sid = Resolve-LocalStandardUserSid -AccountName $User
        foreach ($name in @($RuleV4, $RuleV6)) {
            if (Get-NetFirewallRule -PolicyStore PersistentStore -Name $name -ErrorAction SilentlyContinue) {
                throw "$name already exists; verify or remove the existing gate first"
            }
        }
        $sddl = "D:(A;;CC;;;$sid)"
        try {
            New-NetFirewallRule -PolicyStore PersistentStore -Name $RuleV4 `
                -DisplayName $RuleV4 -Group $RuleGroup -Description "Target SID: $sid" `
                -Enabled True -Profile Any -Direction Outbound -Action Block `
                -Protocol Any -RemoteAddress $RemoteV4 -LocalUser $sddl | Out-Null
            New-NetFirewallRule -PolicyStore PersistentStore -Name $RuleV6 `
                -DisplayName $RuleV6 -Group $RuleGroup -Description "Target SID: $sid" `
                -Enabled True -Profile Any -Direction Outbound -Action Block `
                -Protocol Any -RemoteAddress $RemoteV6 -LocalUser $sddl | Out-Null
            Assert-Rule -Name $RuleV4 -Sid $sid -ExpectedAddresses $RemoteV4
            Assert-Rule -Name $RuleV6 -Sid $sid -ExpectedAddresses $RemoteV6
        }
        catch {
            Remove-GateRules
            throw
        }
        Write-Output "installed firewall gate for SID $sid"
    }
    "Verify" {
        if (Test-IsAdministrator) {
            throw "the CLI E2E firewall gate must run as a standard user"
        }
        $sid = Get-CurrentSid
        Assert-Rule -Name $RuleV4 -Sid $sid -ExpectedAddresses $RemoteV4
        Assert-Rule -Name $RuleV6 -Sid $sid -ExpectedAddresses $RemoteV6
        [ordered]@{
            isolated = $true
            current_user_sid = $sid
            rule_names = @($RuleV4, $RuleV6)
        } | ConvertTo-Json -Compress
    }
    "Remove" {
        if (-not (Test-IsAdministrator)) {
            throw "Remove must run from an elevated administrator shell"
        }
        Remove-GateRules
        Write-Output "removed SecurityMasker firewall gate rules"
    }
}
