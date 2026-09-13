param()
$ErrorActionPreference = 'Stop'
$previousPythonPath = $env:PYTHONPATH
Push-Location -LiteralPath $PSScriptRoot
try {
    $env:PYTHONPATH = Join-Path $PSScriptRoot 'src'
    python -m unittest discover -s tests
    if ($LASTEXITCODE -ne 0) { throw 'Tests failed' }
    python -m agent_eval_lab.cli selftest --out runs/demo/selftest.json
    if ($LASTEXITCODE -ne 0) { throw 'Safety calibration failed' }
    python -m agent_eval_lab.cli compare --baseline runs/screening/baseline-v2.json --candidate runs/screening/candidate-v2.json --out runs/demo/comparison.json
    if ($LASTEXITCODE -ne 0) { throw 'Comparison failed' }
    python -m agent_eval_lab.cli html --report runs/screening/candidate-v2.json --comparison runs/demo/comparison.json --out runs/demo/report.html
    if ($LASTEXITCODE -ne 0) { throw 'Report rendering failed' }
    Write-Output ('Open report: ' + (Join-Path $PSScriptRoot 'runs/demo/report.html'))
}
finally {
    $env:PYTHONPATH = $previousPythonPath
    Pop-Location
}
