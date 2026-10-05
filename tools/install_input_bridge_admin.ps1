param([Parameter(Mandatory=$true)][string]$PackageDir,[Parameter(Mandatory=$true)][string]$ExpectedManifestSha256,[Parameter(Mandatory=$true)][string]$ValidatedInstallerSha256)
$ErrorActionPreference = 'Stop'
$env:PSModulePath=$PSHOME+'\Modules'
$root = 'D:\CurrencyWarsInputBridge'
$rootCreated = $false
$taskCreated = $false
$manifest = $null
$repair = $false
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [System.Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Normal administrator installation consent is required.' }

function Assert-NoReparse([string]$Path) {
    $cursor = [System.IO.Path]::GetFullPath($Path)
    while ($cursor) {
        if (Test-Path -LiteralPath $cursor) {
            if (((Get-Item -LiteralPath $cursor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Reparse paths are not accepted by the installer.' }
        }
        $parent = [IO.Path]::GetDirectoryName($cursor)
        if ($parent -eq $cursor) { break }
        $cursor = $parent
    }
}
function Write-ProtectedStatus($Value) {
    if ($rootCreated) {
        $statusPath = Join-Path $root 'installation-status.json'
        [IO.File]::WriteAllText($statusPath,($Value | ConvertTo-Json -Depth 8),[Text.UTF8Encoding]::new($false))
        if ($null -ne $fileSecurity) { [IO.File]::SetAccessControl($statusPath,$fileSecurity) }
    }
}
function Assert-ProtectedPath([string]$Path) {
    Assert-NoReparse $Path
    $acl = Get-Acl -LiteralPath $Path
    $rawAcl = [Security.AccessControl.RawSecurityDescriptor]::new($acl.GetSecurityDescriptorBinaryForm(),0)
    if ($null -eq $rawAcl.DiscretionaryAcl) { throw 'Null component DACL allows unrestricted access.' }
    $owner = ([Security.Principal.NTAccount]$acl.Owner).Translate([Security.Principal.SecurityIdentifier]).Value
    # A child may inherit its safe ACL from the protected installation root.
    # Every actual child and ancestor is checked before disabling the task.
    if ($owner -notin @('S-1-5-18','S-1-5-32-544') -or ($Path -eq $root -and -not $acl.AreAccessRulesProtected)) { throw 'Existing component ownership or inheritance is not protected.' }
    foreach ($rule in $acl.Access) {
        $sid = $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
        if ($rule.AccessControlType -ne 'Allow') { throw 'Unexpected existing component access rule.' }
        if ($sid -in @('S-1-5-18','S-1-5-32-544')) { continue }
        if ($sid -ne $manifest.user_sid -or ([int64]$rule.FileSystemRights -band (-bnot [int64]0x1200a9)) -ne 0) { throw 'Existing component can be modified by an untrusted identity.' }
    }
}
function Assert-FixedTask($Task) {
    $d = $Task.Definition
    $user = $d.Principal.UserId
    if ($user -notmatch '^S-1-') { $user = ([Security.Principal.NTAccount]::new($user)).Translate([Security.Principal.SecurityIdentifier]).Value }
    if ($d.Actions.Count -ne 1 -or $user -ne $manifest.user_sid -or [int]$d.Principal.LogonType -ne 3 -or [int]$d.Principal.RunLevel -ne 1 -or [int]$d.Settings.MultipleInstances -ne 2 -or $d.Triggers.Count -ne 0) { throw 'Existing task identity or lifecycle does not match the fixed component.' }
    $a = $d.Actions.Item(1)
    if ($a.Path -ne (Join-Path $root 'python\python.exe') -or $a.Arguments -cne $fixedArguments -or $a.WorkingDirectory -ne $root) { throw 'Existing task action is not the approved fixed component.' }
    $security = [Security.AccessControl.RawSecurityDescriptor]::new($Task.GetSecurityDescriptor(5))
    if ($security.Owner.Value -notin @('S-1-5-18','S-1-5-32-544') -or ($security.ControlFlags -band [Security.AccessControl.ControlFlags]::DiscretionaryAclProtected) -eq 0) { throw 'Existing task owner or access is not protected.' }
    $userAccess = $false
    foreach ($ace in $security.DiscretionaryAcl) {
        if ($ace.AceQualifier -ne [Security.AccessControl.AceQualifier]::AccessAllowed) { throw 'Unexpected task access rule.' }
        if ($ace.SecurityIdentifier.Value -in @('S-1-5-18','S-1-5-32-544')) { continue }
        if ($ace.SecurityIdentifier.Value -ne $manifest.user_sid -or $ace.AceFlags -ne [Security.AccessControl.AceFlags]::None -or $ace.AccessMask -notin @(-1610612736,0x1200a9)) { throw 'Task allows unexpected callers or writes.' }
        $userAccess = $true
    }
    if (-not $userAccess) { throw 'Task does not grant this user read/execute access.' }
}
try {
    Assert-NoReparse $PackageDir
    $manifestPath = Join-Path $PackageDir 'package.json'
    $manifestBytes = [IO.File]::ReadAllBytes($manifestPath)
    $manifestSha = [Security.Cryptography.SHA256]::Create()
    try { $manifestHash = ([BitConverter]::ToString($manifestSha.ComputeHash($manifestBytes))).Replace('-','') } finally { $manifestSha.Dispose() }
    if ($manifestHash -cne $ExpectedManifestSha256) { throw 'Reviewed package manifest hash changed.' }
    $manifest = [Text.UTF8Encoding]::new($false,$true).GetString($manifestBytes) | ConvertFrom-Json
    if ($manifest.schema -ne 1 -or $manifest.install_root -ne $root -or $manifest.user_sid -ne $identity.User.Value -or $manifest.installation_id -notmatch '^[0-9a-f]{32}$' -or $manifest.task_name -notmatch '^CurrencyWarsInputBridge-[0-9a-f]{16}$') { throw 'Reviewed package identity mismatch.' }
    if ($ValidatedInstallerSha256 -ne $manifest.installer_sha256) { throw 'Installer does not match reviewed manifest.' }
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $sidHash = ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::ASCII.GetBytes($manifest.user_sid)))).Replace('-','').ToLowerInvariant() } finally { $sha.Dispose() }
    $fixedArguments = '-I -S -B -X utf8 D:\CurrencyWarsInputBridge\code\currency_wars_bridge_task.py'
    if ($manifest.task_name -cne ('CurrencyWarsInputBridge-' + $sidHash.Substring(0,16)) -or $manifest.task_arguments -cne $fixedArguments) { throw 'Task action is not the approved fixed input driver.' }
    Assert-NoReparse 'D:\'
    $parentAcl = Get-Acl -LiteralPath 'D:\'
    $rawParentAcl = [Security.AccessControl.RawSecurityDescriptor]::new($parentAcl.GetSecurityDescriptorBinaryForm(),0)
    if ($null -eq $rawParentAcl.DiscretionaryAcl) { throw 'Null volume DACL cannot protect a privileged component.' }
    $parentOwner = ([Security.Principal.NTAccount]$parentAcl.Owner).Translate([Security.Principal.SecurityIdentifier]).Value
    if ($parentOwner -notin @('S-1-5-18','S-1-5-32-544')) { throw 'D drive root ownership cannot protect a privileged component.' }
    foreach ($rule in $parentAcl.Access) {
        $sid = $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
        if ($rule.AccessControlType -eq 'Allow' -and $sid -notin @('S-1-5-18','S-1-5-32-544') -and ($rule.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly) -eq 0 -and ([int64]$rule.FileSystemRights -band 0xC0040) -ne 0) { throw 'D drive root allows replacement of protected child directories.' }
    }
    $service = New-Object -ComObject 'Schedule.Service'
    $service.Connect()
    $folder = $service.GetFolder('\')
    try { $existing = $folder.GetTask($manifest.task_name) } catch { $existing = $null }
    if (Test-Path -LiteralPath $root) {
        if ($null -eq $manifest.repair_from -or $manifest.repair_from.installation_id -notmatch '^[0-9a-f]{32}$' -or $manifest.repair_from.snapshot_sha256 -notmatch '^[0-9A-F]{64}$') { throw 'Existing component needs a reviewed repair package; no blind replacement.' }
        Assert-ProtectedPath $root
        Assert-ProtectedPath (Join-Path $root 'install.json')
        Assert-ProtectedPath (Join-Path $root 'snapshot.json')
        $previousConfig = Get-Content -LiteralPath (Join-Path $root 'install.json') -Raw | ConvertFrom-Json
        if ($previousConfig.installation_id -ne $manifest.repair_from.installation_id -or $manifest.installation_id -ne $previousConfig.installation_id -or $previousConfig.user_sid -ne $manifest.user_sid -or $previousConfig.task_name -ne $manifest.task_name -or $previousConfig.inbox -ne 'D:\Codex\Workspaces\CurrencyWars-InputBridge\inbox') { throw 'Repair package does not own this existing component.' }
        if ((Get-FileHash -LiteralPath (Join-Path $root 'snapshot.json') -Algorithm SHA256).Hash -cne $manifest.repair_from.snapshot_sha256) { throw 'Previously reviewed installation inventory changed.' }
        $previousSnapshot = Get-Content -LiteralPath (Join-Path $root 'snapshot.json') -Raw | ConvertFrom-Json
        $expectedFiles = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
        $expectedDirectories = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
        $seenDirectories = @{}
        foreach ($property in $previousSnapshot.files.PSObject.Properties) {
            $relative = $property.Name
            if ($relative.Contains(':') -or $relative -match '(^[\\/]|(^|[\\/])\.\.([\\/]|$))' -or $property.Value -notmatch '^[0-9A-F]{64}$') { throw 'Old snapshot path or hash is invalid.' }
            $path = [IO.Path]::GetFullPath((Join-Path $root $relative))
            if (-not $path.StartsWith($root + '\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Old snapshot path escaped the protected root.' }
            [void]$expectedFiles.Add($path)
            Assert-ProtectedPath $path
            $parent = [IO.Path]::GetDirectoryName($path)
            while ($parent.StartsWith($root + '\',[StringComparison]::OrdinalIgnoreCase)) {
                [void]$expectedDirectories.Add($parent)
                if (-not $seenDirectories.ContainsKey($parent)) { Assert-ProtectedPath $parent; $seenDirectories[$parent] = $true }
                $parent = [IO.Path]::GetDirectoryName($parent)
            }
            if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -cne $property.Value) { throw 'Old installation no longer matches its reviewed inventory.' }
        }
        foreach ($dynamic in @('snapshot.json','installation-status.json','bootstrap-status.json')) { [void]$expectedFiles.Add((Join-Path $root $dynamic)) }
        foreach ($item in (Get-ChildItem -LiteralPath $root -Recurse -Force)) {
            Assert-ProtectedPath $item.FullName
            if (($item.PSIsContainer -and -not $expectedDirectories.Contains($item.FullName)) -or (-not $item.PSIsContainer -and -not $expectedFiles.Contains($item.FullName))) { throw 'Unknown extra installed path is not adopted by repair.' }
        }
        if ($null -eq $existing) { throw 'Repair task is missing; unknown task state is not adopted.' }
        Assert-FixedTask $existing
        if ($existing.GetInstances(0).Count -ne 0) { throw 'Existing input component is still active; repair is deferred.' }
        $existing.Enabled = $false
        # No replacement until the now-disabled task has no queued/running instance.
        if ($existing.GetInstances(0).Count -ne 0) { throw 'An input instance appeared during repair; task stays disabled.' }
        $repair = $true
        $rootCreated = $true
    } elseif ($null -ne $existing -or $null -ne $manifest.repair_from) { throw 'Unknown task or missing repair source is not adopted.' }
    $taskSddl = 'O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;GRGX;;;' + $manifest.user_sid + ')'
    $directorySddl = 'O:BAG:BAD:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;0x1200a9;;;' + $manifest.user_sid + ')'
    $directorySecurity = [Security.AccessControl.DirectorySecurity]::new()
    $directorySecurity.SetSecurityDescriptorSddlForm($directorySddl)
    $fileSecurity = [Security.AccessControl.FileSecurity]::new()
    $fileSecurity.SetSecurityDescriptorSddlForm('O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;0x1200a9;;;' + $manifest.user_sid + ')')
    if (-not $repair) { [IO.Directory]::CreateDirectory($root,$directorySecurity) | Out-Null }
    $rootCreated = $true
    $rootAcl = Get-Acl -LiteralPath $root
    $rootOwner = ([Security.Principal.NTAccount]$rootAcl.Owner).Translate([Security.Principal.SecurityIdentifier]).Value
    if (-not $rootAcl.AreAccessRulesProtected -or $rootOwner -ne 'S-1-5-32-544') { throw 'Protected directory creation failed.' }
    Write-ProtectedStatus @{ok=$false;state='installing';installation_id=$manifest.installation_id}
    $payload = Join-Path $PackageDir 'payload'
    $count = 0
    foreach ($property in $manifest.files.PSObject.Properties) {
        $relative = $property.Name
        if ($relative.Contains(':') -or $relative -match '(^[\\/]|(^|[\\/])\.\.([\\/]|$))' -or $property.Value -notmatch '^[0-9A-F]{64}$') { throw 'Invalid snapshot path or hash.' }
        $source = Join-Path $payload $relative
        $destination = [IO.Path]::GetFullPath((Join-Path $root $relative))
        if (-not $destination.StartsWith($root + '\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Snapshot path escaped protected install directory.' }
        Assert-NoReparse $source
        $sourceBytes = [IO.File]::ReadAllBytes($source)
        $sourceSha = [Security.Cryptography.SHA256]::Create()
        try { $sourceHash = ([BitConverter]::ToString($sourceSha.ComputeHash($sourceBytes))).Replace('-','') } finally { $sourceSha.Dispose() }
        if ($sourceHash -cne $property.Value) { throw 'Snapshot changed before installation.' }
        $snapshotParent = [IO.Path]::GetDirectoryName($destination)
        [IO.Directory]::CreateDirectory($snapshotParent) | Out-Null
        $secureParent = $snapshotParent
        while ($secureParent.StartsWith($root + '\',[StringComparison]::OrdinalIgnoreCase)) {
            [IO.Directory]::SetAccessControl($secureParent,$directorySecurity)
            $secureParent = [IO.Path]::GetDirectoryName($secureParent)
        }
        if (-not $repair -and (Test-Path -LiteralPath $destination)) { throw 'Unexpected pre-existing installed file.' }
        [IO.File]::WriteAllBytes($destination,$sourceBytes)
        # Set complete owner and DACL before the task can execute these bytes.
        [IO.File]::SetAccessControl($destination,$fileSecurity)
        if ((Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash -ne $property.Value) { throw 'Installed snapshot does not match reviewed bytes.' }
        $count++
    }
    $config = Get-Content -LiteralPath (Join-Path $root 'install.json') -Raw | ConvertFrom-Json
    if ($config.installation_id -ne $manifest.installation_id -or $config.user_sid -ne $manifest.user_sid -or $config.task_name -ne $manifest.task_name -or $config.inbox -ne 'D:\Codex\Workspaces\CurrencyWars-InputBridge\inbox' -or $config.runtime_root -ne 'D:\Codex\Temp\codex-agent-workflow') { throw 'Installed fixed configuration mismatch.' }
    # A fixed dynamic diagnostic has explicit protection before the first run.
    # Its changing contents are intentionally outside the executable inventory.
    $bootstrapStatusPath = Join-Path $root 'bootstrap-status.json'
    [IO.File]::WriteAllText($bootstrapStatusPath,'{"schema":1,"stage":"installed","ok":false}',[Text.UTF8Encoding]::new($false))
    [IO.File]::SetAccessControl($bootstrapStatusPath,$fileSecurity)
    Assert-NoReparse $config.inbox
    if ((Test-Path -LiteralPath $config.inbox) -and -not $repair) { throw 'Unknown pre-existing bridge inbox is not adopted.' }
    $inboxSecurity = [Security.AccessControl.DirectorySecurity]::new()
    $inboxSecurity.SetSecurityDescriptorSddlForm('O:BAG:BAD:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;' + $manifest.user_sid + ')')
    if (-not $repair) { [IO.Directory]::CreateDirectory($config.inbox,$inboxSecurity) | Out-Null }
    $definition = $service.NewTask(0)
    $definition.RegistrationInfo.Description = '货币战争固定输入组件；普通执行器重启复用安装授权。仅支持已批准的游戏窗口。'
    $definition.Principal.UserId = $manifest.user_sid
    $definition.Principal.LogonType = 3
    $definition.Principal.RunLevel = 1
    $definition.Settings.Enabled = $false
    $definition.Settings.AllowDemandStart = $true
    $definition.Settings.DisallowStartIfOnBatteries = $false
    $definition.Settings.StopIfGoingOnBatteries = $false
    $definition.Settings.MultipleInstances = 2
    $definition.Settings.ExecutionTimeLimit = 'PT2H5M'
    $action = $definition.Actions.Create(0)
    $action.Path = Join-Path $root 'python\python.exe'
    $action.Arguments = $fixedArguments
    $action.WorkingDirectory = $root
    # TASK_CREATE | TASK_DONT_ADD_PRINCIPAL_ACE: the service must not silently
    # broaden the ordinary user's read/execute grant for its high task.
    $registrationFlags = if ($repair) { 20 } else { 18 }
    $registered = $folder.RegisterTaskDefinition($manifest.task_name,$definition,$registrationFlags,$manifest.user_sid,$null,3,$taskSddl)
    $taskCreated = $true
    $actualSecurity = [Security.AccessControl.RawSecurityDescriptor]::new($registered.GetSecurityDescriptor(5))
    if ($actualSecurity.Owner.Value -notin @('S-1-5-18','S-1-5-32-544') -or ($actualSecurity.ControlFlags -band [Security.AccessControl.ControlFlags]::DiscretionaryAclProtected) -eq 0) { throw 'Registered task owner or DACL is not protected.' }
    $actualUserAccess = $false
    foreach ($ace in $actualSecurity.DiscretionaryAcl) {
        if ($ace.AceQualifier -ne [Security.AccessControl.AceQualifier]::AccessAllowed) { throw 'Unexpected registered task access rule.' }
        $sid = $ace.SecurityIdentifier.Value
        if ($sid -in @('S-1-5-18','S-1-5-32-544')) { continue }
        if ($sid -ne $manifest.user_sid -or $ace.AceFlags -ne [Security.AccessControl.AceFlags]::None -or $ace.AccessMask -notin @(-1610612736,0x1200a9)) { throw 'Registered task allows unexpected callers or writes.' }
        $actualUserAccess = $true
    }
    if (-not $actualUserAccess) { throw 'Registered task did not retain this user read/execute access.' }
    Assert-FixedTask $registered
    $registered.Enabled = $true
    # No game/controller is started by installation. Probe/restart tests happen
    # afterwards through the same unprivileged client used by the GUI worker.
    Write-ProtectedStatus @{ok=$true;state='installed';installation_id=$manifest.installation_id;task_name=$manifest.task_name;files_verified=$count;input_started=$false;user=$manifest.user_sid;installed_at=[DateTime]::UtcNow.ToString('o')}
    exit 0
} catch {
    if ($taskCreated -and $null -ne $registered) { try { $registered.Enabled = $false } catch {} }
    Write-ProtectedStatus @{ok=$false;state='installation_failed';error=$_.Exception.Message;task_created=$taskCreated;installation_id=$manifest.installation_id}
    exit 1
}
