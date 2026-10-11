# Slide helper: tells the hosted Pitch Rehearsal Coach which slide PowerPoint is showing.
# Needs nothing installed: Windows PowerShell and PowerPoint only. Run it on the PC that has the deck open.
#
#   powershell -ExecutionPolicy Bypass -File slide_helper.ps1 -Url https://your-app.onrender.com -Password "your password"
#
# If your IT blocks scripts, skip it: the rehearsal page works with the on-screen buttons and arrow keys.
param(
    [Parameter(Mandatory = $true)][string]$Url,
    [Parameter(Mandatory = $true)][string]$Password
)
$ErrorActionPreference = "Stop"
$endpoint = $Url.TrimEnd("/") + "/api/slide"
$cred = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("user:" + $Password))
$headers = @{ Authorization = "Basic $cred" }
Write-Host "Reporting PowerPoint's current slide to $endpoint. Press Ctrl+C to stop."
$last = ""
while ($true) {
    $state = $null
    try {
        $app = [Runtime.InteropServices.Marshal]::GetActiveObject("PowerPoint.Application")
        $total = $null; $name = ""
        try { $total = [int]$app.ActivePresentation.Slides.Count; $name = [string]$app.ActivePresentation.Name } catch {}
        if ($app.SlideShowWindows.Count -gt 0) {
            $state = @{ slide = [int]$app.SlideShowWindows.Item(1).View.Slide.SlideIndex; total = $total; name = $name; mode = "slideshow" }
        } else {
            $state = @{ slide = [int]$app.ActiveWindow.View.Slide.SlideIndex; total = $total; name = $name; mode = "editing" }
        }
    } catch { $state = $null }
    if ($state) {
        try {
            Invoke-RestMethod -Uri $endpoint -Method Post -Headers $headers -ContentType "application/json" -Body ($state | ConvertTo-Json) | Out-Null
            $msg = "slide $($state.slide) of $($state.total) ($($state.mode))"
        } catch { $msg = "could not reach the app: $($_.Exception.Message)" }
    } else { $msg = "PowerPoint is not open or has no presentation showing" }
    if ($msg -ne $last) { Write-Host (Get-Date -Format "HH:mm:ss") $msg; $last = $msg }
    Start-Sleep -Milliseconds 1000
}
