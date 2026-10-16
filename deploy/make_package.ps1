# Builds trad_deploy.zip (code only: no venv, databases, sessions or secrets) to upload to the server.
#   powershell -ExecutionPolicy Bypass -File deploy\make_package.ps1
$root = Split-Path -Parent $PSScriptRoot
$out = Join-Path $root "trad_deploy.zip"
$stage = Join-Path $env:TEMP "trad_deploy_stage"
$skipDirs = @('.venv', '__pycache__', 'data', '.git')
$skipNames = @('.kite_session.json', 'engine_settings.json', 'secrets.toml', 'trading_signals_app (1).py',
               'trading_signals_app.backup.py', 'trad_deploy.zip')
$skipExt = @('.db', '.zip', '.pyc')

if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
New-Item -ItemType Directory -Path $stage | Out-Null
foreach ($f in Get-ChildItem -Path $root -Recurse -File -Force) {
    $rel = $f.FullName.Substring($root.Length + 1)
    $parts = $rel.Split('\')
    if ($parts | Where-Object { $skipDirs -contains $_ }) { continue }
    if ($skipNames -contains $f.Name -or $skipExt -contains $f.Extension) { continue }
    $dest = Join-Path $stage $rel
    New-Item -ItemType Directory -Force -Path (Split-Path $dest) | Out-Null
    Copy-Item $f.FullName $dest
}
if (Test-Path $out) { Remove-Item $out }
# .NET zip with forward-slash entry names (Compress-Archive in PowerShell 5.1 writes backslashes, which break on Linux)
Add-Type -AssemblyName System.IO.Compression, System.IO.Compression.FileSystem
$zip = [System.IO.Compression.ZipFile]::Open($out, 'Create')
foreach ($f in Get-ChildItem -Path $stage -Recurse -File -Force) {
    $name = $f.FullName.Substring($stage.Length + 1).Replace('\', '/')
    [void][System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, $f.FullName, $name)
}
$zip.Dispose()
Remove-Item -Recurse -Force $stage
Write-Host "Created $out"
