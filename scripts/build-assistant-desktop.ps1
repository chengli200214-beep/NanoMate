param(
    [string]$PythonPath = '.local/assistant-desktop-build/Scripts/python.exe',
    [ValidateSet('dir', 'installer')][string]$Target = 'dir',
    [ValidateRange(0, 9)][int]$CompressionLevel = 1,
    [switch]$SkipDependencies,
    [switch]$SkipBrowserDownload
)
$ErrorActionPreference = 'Stop'
$env:ELECTRON_BUILDER_COMPRESSION_LEVEL = [string]$CompressionLevel
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$desktopRoot = Join-Path $projectRoot 'desktop/assistant'
$pythonCandidate = if ([IO.Path]::IsPathRooted($PythonPath)) { $PythonPath } else { Join-Path $projectRoot $PythonPath }
$resolvedPython = (Resolve-Path $pythonCandidate).Path
function Assert-NativeExit([string]$Step) {
    if ($LASTEXITCODE -ne 0) { throw "$Step failed with exit code $LASTEXITCODE" }
}
Push-Location $projectRoot
try {
    Push-Location (Join-Path $projectRoot 'web')
    try {
        if (-not $SkipDependencies) { npm ci --registry=https://registry.npmjs.org/; Assert-NativeExit 'web dependencies' }
        npm run check; Assert-NativeExit 'web checks'
        npm run build; Assert-NativeExit 'web build'
    } finally { Pop-Location }
    Push-Location $desktopRoot
    try {
        if (-not $SkipDependencies) { npm ci --registry=https://registry.npmjs.org/; Assert-NativeExit 'desktop dependencies' }
        if (-not (Test-Path -LiteralPath 'node_modules/electron/dist/electron.exe')) {
            Remove-Item Env:ELECTRON_SKIP_BINARY_DOWNLOAD -ErrorAction SilentlyContinue
            node node_modules/electron/install.js; Assert-NativeExit 'Electron binary'
        }
        npm run check; Assert-NativeExit 'desktop checks'
        node -e 'const p=require("./package-lock.json");if(Object.values(p.packages).some(x=>x.resolved&&!x.resolved.startsWith("https://registry.npmjs.org/")))process.exit(1)'
        Assert-NativeExit 'public dependency registry'
    } finally { Pop-Location }
    if (-not $SkipBrowserDownload) {
        $env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $desktopRoot 'browsers'
        & $resolvedPython -m playwright install chromium; Assert-NativeExit 'browser runtime'
    } elseif (-not (Test-Path -LiteralPath (Join-Path $desktopRoot 'browsers'))) {
        throw 'Stage the browser directory or omit -SkipBrowserDownload.'
    }
    & $resolvedPython -m PyInstaller --noconfirm --distpath desktop/assistant/runtime --workpath build/assistant-runtime-pyi scripts/desktop-app/assistant_runtime.spec
    Assert-NativeExit 'assistant runtime'
    & $resolvedPython scripts/desktop-app/stage-assistant-resources.py
    Assert-NativeExit 'assistant package data'
    Push-Location $desktopRoot
    try {
        if ($Target -eq 'installer') { npm run dist:win } else { npm run dist:dir }
        Assert-NativeExit 'desktop packaging'
    } finally { Pop-Location }
} finally { Pop-Location }
