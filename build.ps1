param([switch]$Test)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not (Test-Path '.venv/Scripts/python.exe')) {
    py -3.12 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 x64 is required.' }
}
$python = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
& $python -m pip install -r requirements-lock.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
if ($Test) {
    & $python -m pytest tests -q --basetemp work/test_tmp -o cache_dir=work/test_cache
    if ($LASTEXITCODE -ne 0) { throw 'Tests failed.' }
}
& $python -m PyInstaller --noconfirm --onefile --windowed --name MoltenDaily --distpath dist --workpath work/build app.py
if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }
Write-Host 'Built dist/MoltenDaily.exe'
