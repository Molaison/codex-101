# Isolated Windows bootstrap integration: no user config, network or CPR calls.
$ErrorActionPreference = 'Stop'
$Original = @{}
foreach ($Name in @('LOCALAPPDATA', 'USERPROFILE', 'HOME', 'CODEX_HOME', 'CODEX_CONNECT_REF', 'CODEX_CONNECT_BASE', 'CODEX_CONNECT_PYTHON')) {
    $Original[$Name] = [Environment]::GetEnvironmentVariable($Name, 'Process')
}
$Root = Join-Path ([IO.Path]::GetTempPath()) ('codex101-test-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $Root | Out-Null
try {
    $env:LOCALAPPDATA = Join-Path $Root 'localappdata'
    $env:USERPROFILE = Join-Path $Root 'profile'
    $env:HOME = $env:USERPROFILE
    $env:CODEX_HOME = Join-Path $Root 'codex-home'
    $env:CODEX_CONNECT_REF = 'v0.2.0'
    $env:CODEX_CONNECT_BASE = 'https://fixture.invalid/download'
    $env:CODEX_CONNECT_PYTHON = (Get-Command python).Source
    $Source = Join-Path $Root 'source'
    New-Item -ItemType Directory -Force -Path $Source, $env:LOCALAPPDATA, $env:USERPROFILE, $env:CODEX_HOME | Out-Null
    $Provider = @'
#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
assert sys.argv[1] == '_native-setup'
assert '--lab' in sys.argv
assert '--sqlite-home' in sys.argv
assert sys.argv[sys.argv.index('--sqlite-home') + 1] == os.environ['CODEX_HOME']
state=Path(os.environ['CODEX_HOME'])/'provider-connect'
state.mkdir(parents=True,exist_ok=True)
(state/'native-installation.json').write_text(json.dumps({'status':'passed','code':'READY'}))
launcher=Path(os.environ['LOCALAPPDATA'])/'CodexLab'/'bin'/'codex.cmd'
launcher.parent.mkdir(parents=True,exist_ok=True)
launcher.write_text('@rem codex-provider-connect: lab official launcher\n@echo stub\n')
sys.exit(0)
'@
    [IO.File]::WriteAllText((Join-Path $Source 'codex_provider.py'), $Provider)
    [IO.File]::WriteAllText((Join-Path $Source 'model_catalog.json'), '{"models":[{"slug":"fixture"}]}')
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'install_lifecycle.py') -Destination $Source
    $Files = @{}
    foreach ($Name in @('codex_provider.py', 'model_catalog.json', 'install_lifecycle.py')) {
        $Files[$Name] = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $Source $Name)).Hash.ToLowerInvariant()
    }
    $Manifest = @{schema=1;release='v0.2.0';codex_channel='stable';files=$Files} | ConvertTo-Json -Depth 5
    [IO.File]::WriteAllText((Join-Path $Source 'release_manifest.json'), $Manifest)

    function Invoke-WebRequest {
        param([string]$Uri, [string]$OutFile, [switch]$UseBasicParsing, [int]$TimeoutSec)
        $Name = ($Uri -split '/')[-1]
        $Candidate = Join-Path $Source $Name
        if (-not (Test-Path -LiteralPath $Candidate)) { throw "Unexpected download: $Uri" }
        Copy-Item -LiteralPath $Candidate -Destination $OutFile -Force
    }

    & ([scriptblock]::Create((Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'install.ps1')))) -Lab -SqliteHome $env:CODEX_HOME
    $Installed = Join-Path $env:LOCALAPPDATA 'codex-provider-connect\active.json'
    if (-not (Test-Path -LiteralPath $Installed)) { throw 'PowerShell bootstrap did not install active.json' }
    $Record = Get-Content -Raw -LiteralPath $Installed | ConvertFrom-Json
    if ($Record.active -ne 'v0.2.0' -or $Record.status -ne 'READY') { throw "Unexpected installer result: $($Record | ConvertTo-Json)" }
    Write-Host 'Isolated Windows PowerShell bootstrap: READY fixture passed.'
}
finally {
    foreach ($Name in $Original.Keys) { [Environment]::SetEnvironmentVariable($Name, $Original[$Name], 'Process') }
    Remove-Item -LiteralPath $Root -Recurse -Force -ErrorAction SilentlyContinue
}
