# Download self-hosted fonts from fontsource CDN.
# Run once: powershell -ExecutionPolicy Bypass -File download_fonts.ps1
# DM Sans Regular + Medium already present from Google Fonts.

$base = Split-Path -Parent $MyInvocation.MyCommand.Path

# Define fonts with fontsource CDN URLs
$fonts = @(
    @{name='InstrumentSerif-Regular.woff2'; url='https://cdn.jsdelivr.net/fontsource/fonts/instrument-serif@latest/latin-400-normal.woff2'},
    @{name='DMSans-SemiBold.woff2'; url='https://cdn.jsdelivr.net/fontsource/fonts/dm-sans@latest/latin-600-normal.woff2'},
    @{name='DMSans-Bold.woff2'; url='https://cdn.jsdelivr.net/fontsource/fonts/dm-sans@latest/latin-700-normal.woff2'},
    @{name='JetBrainsMono-Regular.woff2'; url='https://cdn.jsdelivr.net/fontsource/fonts/jetbrains-mono@latest/latin-400-normal.woff2'},
    @{name='JetBrainsMono-Medium.woff2'; url='https://cdn.jsdelivr.net/fontsource/fonts/jetbrains-mono@latest/latin-500-normal.woff2'}
)

Write-Host "Downloading fonts from fontsource CDN (OFL-1.1 licensed)..." -ForegroundColor Cyan
Write-Host ""

foreach ($font in $fonts) {
    $path = Join-Path $base $font.name

    if (Test-Path $path) {
        Write-Host "✓ $($font.name) (already exists)"
    } else {
        try {
            Write-Host "↓ Downloading $($font.name)..." -NoNewline
            Invoke-WebRequest -Uri $font.url -OutFile $path -ErrorAction Stop
            Write-Host " done" -ForegroundColor Green
        } catch {
            Write-Host " ERROR: $_" -ForegroundColor Red
        }
    }
}

Write-Host ""
Write-Host "Font files in directory:" -ForegroundColor Cyan
Get-ChildItem $base -Filter "*.woff2" | ForEach-Object {
    Write-Host "  $($_.Name) ($($_.Length) bytes)"
}
