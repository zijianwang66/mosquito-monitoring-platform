[CmdletBinding()]
param(
    [string]$Python = 'python',
    [string]$Psql = 'psql',
    [string]$CsvPath
)

$ErrorActionPreference = 'Stop'
$database = 'mosquito_monitoring_test'
$root = Split-Path -Parent $PSScriptRoot

if (-not $CsvPath) {
    $fixtureDir = Join-Path $root 'outputs/01a1cf8a-45e7-7251-bbc4-2a43c03d864a'
    $fixtures = @(Get-ChildItem -LiteralPath $fixtureDir -Filter '*.csv' -File)
    if ($fixtures.Count -ne 1) {
        throw 'Specify the two-day, seven-device fixture with -CsvPath.'
    }
    $CsvPath = $fixtures[0].FullName
}
$CsvPath = (Resolve-Path -LiteralPath $CsvPath).Path

$seedSql = @'
INSERT INTO core.device (source_id, machine_no, point_no, is_demo)
SELECT 'demo', 'P' || lpad(n::text, 2, '0') || '-DEMO', n, true
FROM generate_series(1, 7) AS points(n)
ON CONFLICT (source_id, machine_no) DO NOTHING;
'@

$checkSql = @'
SELECT count(*)
FROM app.point_daily_stats
WHERE source_id = 'demo' AND is_demo AND metric = 'mosquito_total'
  AND observation_day IN (DATE '2026-11-01', DATE '2026-11-02')
  AND point_no BETWEEN 1 AND 7
  AND machine_no = 'P' || lpad(point_no::text, 2, '0') || '-DEMO'
  AND reported_count = 24 AND valid_count = 24;
'@

$previousDatabase = $env:PGDATABASE
$previousService = $env:PGSERVICE
$previousEncoding = $env:PGCLIENTENCODING
try {
    $env:PGDATABASE = $database
    $env:PGSERVICE = $null
    $env:PGCLIENTENCODING = 'UTF8'
    $psqlOptions = @('-X', '-w', '-v', 'ON_ERROR_STOP=1', '-d', $database)

    & $Python (Join-Path $PSScriptRoot 'scan.py') --file $CsvPath --source-id demo --check
    if ($LASTEXITCODE -ne 0) { throw 'Demo CSV validation failed.' }

    & $Psql @psqlOptions -f (Join-Path $PSScriptRoot 'schema.sql')
    if ($LASTEXITCODE -ne 0) { throw "Schema setup failed. Check the connection to $database." }

    & $Psql @psqlOptions -c $seedSql
    if ($LASTEXITCODE -ne 0) { throw 'Demo device registration failed.' }

    & $Python (Join-Path $PSScriptRoot 'scan.py') --file $CsvPath --source-id demo --psql $Psql
    if ($LASTEXITCODE -ne 0) { throw 'Demo CSV import failed or contains rows needing review.' }

    $verified = & $Psql @psqlOptions -qAt -c $checkSql
    if ($LASTEXITCODE -ne 0 -or $verified -ne '14') {
        throw 'Expected 14 device-day totals with 24 valid samples each.'
    }
    Write-Host 'Demo ready: 7 devices, 2 days, 14 daily totals with 24 valid samples each.'
}
finally {
    $env:PGDATABASE = $previousDatabase
    $env:PGSERVICE = $previousService
    $env:PGCLIENTENCODING = $previousEncoding
}
