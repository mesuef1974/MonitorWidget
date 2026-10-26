param([string]$Op, [string]$Path, [string]$Name)
# Whitelist is enforced by server.py; this script only executes one task operation.
$ErrorActionPreference = 'Stop'
try {
    switch ($Op) {
        'enable'  { Enable-ScheduledTask  -TaskPath $Path -TaskName $Name | Out-Null }
        'disable' { Disable-ScheduledTask -TaskPath $Path -TaskName $Name | Out-Null }
        'run'     { Start-ScheduledTask   -TaskPath $Path -TaskName $Name }
        default   { throw "unknown op $Op" }
    }
    Write-Output 'ok'
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
