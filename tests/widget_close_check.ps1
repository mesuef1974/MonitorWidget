$ErrorActionPreference='Stop'
$tokens=$null;$errors=$null;$path=Join-Path (Split-Path $PSScriptRoot -Parent) 'widget.ps1'
$ast=[Management.Automation.Language.Parser]::ParseFile($path,[ref]$tokens,[ref]$errors)
if($errors.Count){throw ($errors.Message -join '; ')}
$code=$ast.Find({param($n)$n -is [Management.Automation.Language.StringConstantExpressionAst] -and $n.Value -match 'public static class WF\s*\{'},$true)
if(-not $code){throw 'Dashboard window selector missing'}
Add-Type -TypeDefinition $code.Value
$title='مرصد الجهاز'
if(-not [WF]::IsDashboardCandidate($title,$title,'msedge',$true)){throw 'Exact Edge dashboard was not matched'}
foreach($shellProc in @('pwsh','powershell')){if(-not [WF]::IsDashboardCandidate($title,$title,$shellProc,$true)){throw "Exact app_shell ($shellProc) dashboard was not matched"}}   # الغلاف app_shell.ps1 يستضيف الواجهة
foreach($row in @(@($title+' — الويدجت','pwsh',$true),@($title+' — الويدجت','msedge',$true),@($title+' — تقرير','msedge',$true),@('تقرير '+$title,'msedge',$true),@($title,'python',$true),@($title,'msedgewebview2',$true),@($title,'msedge',$false))){if([WF]::IsDashboardCandidate($title,$row[0],$row[1],$row[2])){throw 'Unrelated window matched'}}
if([WF]::IsDashboardCandidate('','','msedge',$true)){throw 'Empty title matched'}
if([WF]::PickOne([long[]]@(101,202,303),202) -ne 202){throw 'Foreground dashboard not preferred'}
if([WF]::PickOne([long[]]@(101,202,303),999) -ne 101){throw 'Fallback did not select exactly one candidate'}
if([WF]::PickOne([long[]]@(),202) -ne 0){throw 'Empty candidate set selected a target'}
$closeCode=([regex]::Match($code.Value,'public static int Close\(string title\)\s*\{([\s\S]*?)\n    \}')).Groups[1].Value
if(([regex]::Matches($closeCode,'PostMessage\(')).Count -ne 1 -or $closeCode -match 'foreach|EnumWindows|Process\.Kill'){throw 'Close can affect multiple windows/processes'}
$placeCode=([regex]::Match($code.Value,'public static bool Place\(string title,[^\n]*\)\s*\{([\s\S]*?)\n    \}')).Groups[1].Value
if(-not $placeCode -or ([regex]::Matches($placeCode,'MoveWindow\(')).Count -ne 1 -or $placeCode -match 'Contains|EnumWindows|foreach'){throw 'Placement can move unrelated or multiple windows'}
if($placeCode -notmatch 'PickOne\(Candidates\(title\)'){throw 'Placement bypasses exact Edge dashboard identification'}
$openFn=$ast.Find({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Open-Dash'},$true)
if($ast.Extent.Text -match 'public static class PL\{' -or $openFn.Extent.Text -notmatch '\[WF\]::Place\('){throw 'Unsafe legacy widget placement helper still used'}
if($openFn.Extent.Text -match 'Start-Sleep'){throw 'Dashboard placement blocks widget scrolling/input'}
if($ast.Extent.Text -notmatch '\$win.MinWidth = \$win.Width; \$win.MaxWidth = \$win.Width'){throw 'Fixed-width widget lost its explicit size guard'}
if($ast.Extent.Text -notmatch '\$UI.closeBtn.Add_Click\(\{[^\r\n]*\$win.Hide\(\)'){throw 'Widget X no longer only hides its window'}
Write-Output 'PASS exact title + Edge process + visible dashboard matching; one target only, foreground preferred, unrelated windows excluded; widget X still hides'
Write-Output 'PASS placement shares exact dashboard selector and moves one target only; widget title is excluded and retries do not block the input dispatcher'
Write-Output 'No real window enumeration, message, close or process termination was executed by this test.'
Add-Type -TypeDefinition 'public static class CompactWindowStub{public static bool Found;public static int Activations,Closes;public static bool Exists(string t){return Found;}public static bool Activate(string t){Activations++;return true;}public static int Close(string t){Closes++;return 1;}}'
$click=$ast.Find({param($n)$n -is [Management.Automation.Language.InvokeMemberExpressionAst] -and $n.Expression.Extent.Text -eq '$UI.open' -and $n.Member.Value -eq 'Add_Click'},$true)
if(-not $click){throw 'Application opener handler missing'}
$body=$click.Arguments[0].ScriptBlock.Extent.Text;$body=$body.Substring(1,$body.Length-2).Replace('[WF]::','[CompactWindowStub]::').Replace("(Join-Path `$PSScriptRoot 'web\index.html')","(Join-Path `$widgetRoot 'web\index.html')")
if($body -match '\[CompactWindowStub\]::Close'){throw 'Compact opener can close an application window'}
$widgetRoot=Split-Path $PSScriptRoot -Parent;$script:ticketCalls=0;$script:openedTicket=$null
function Show-Toast($message){$script:openError=$message}
function Open-Dash($ticket){$script:openedTicket=$ticket}
function Start-Bg($path,$payload,$ok,$err){if($path -ne '/api/ticket'){throw 'Unexpected opener endpoint'};$script:ticketCalls++;& $ok ([pscustomobject]@{ticket='unit-ticket'})}
[CompactWindowStub]::Found=$true;. ([scriptblock]::Create($body))
if([CompactWindowStub]::Activations -ne 1 -or [CompactWindowStub]::Closes -ne 0 -or $script:ticketCalls -ne 0){throw 'Existing application was closed or duplicate opened'}
[CompactWindowStub]::Found=$false;. ([scriptblock]::Create($body))
if([CompactWindowStub]::Closes -ne 0 -or $script:ticketCalls -ne 1 -or $script:openedTicket -ne 'unit-ticket'){throw 'Missing application was not opened through a ticket'}
Write-Output 'PASS actual compact opener handler with isolated stubs: Activate existing window or ticket-open missing window; Close never invoked'
