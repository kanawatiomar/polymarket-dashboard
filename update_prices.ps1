$dir = "C:\Users\kanaw\.openclaw\workspace\polymarket-dashboard"
Set-Location $dir

# Fetch live prices and update data.json
node "$dir\patch_prices.mjs"

# Git push if data changed
$status = git status --porcelain data.json
if ($status) {
    git add data.json
    git commit -m "auto: live prices $(Get-Date -Format 'HH:mm')"
    git push
    Write-Host "Pushed updated prices"
} else {
    Write-Host "No price changes"
}
