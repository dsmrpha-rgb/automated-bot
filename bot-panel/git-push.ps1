<#
  git-push.ps1 — push the constructor changes.

  Part 1: panel changes  -> the bot-panel repo (this folder's repo).
  Part 2: engine files   -> the automated-bot repo (fresh clone to %TEMP%).

  Run from the bot-panel folder:
      powershell -ExecutionPolicy Bypass -File .\git-push.ps1

  Uses your existing git credentials. Never force-pushes.
#>

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$AUTOMATED_REPO = "https://github.com/dsmrpha-rgb/automated-bot.git"
$TRAILER = "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`nClaude-Session: https://claude.ai/code/session_01VCnFEeruC2Zn7ganYLzgqH"

function Say($m)  { Write-Host "==> $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "!!  $m" -ForegroundColor Yellow }

# ───────────────────────── Part 1: panel ─────────────────────────
Say "Pushing panel changes..."
Set-Location $root

git rev-parse --is-inside-work-tree 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Warn "This folder is not inside a git repo. Skipping panel push."
} else {
    $panelFiles = @(
        "app.py",
        "constructor.py",
        "templates/constructor.html",
        "templates/dashboard.html",
        "templates/add_bot.html"
    )
    foreach ($f in $panelFiles) {
        if (Test-Path (Join-Path $root $f)) { git add -- $f }
    }

    git diff --cached --quiet
    if ($LASTEXITCODE -eq 0) {
        Warn "No panel changes staged (already committed?)."
    } else {
        git commit `
            -m "Add bot-shop constructor: config-driven menu engine" `
            -m "Visual page/button builder in the panel, multi-token + wallet editor, per-bot Constructor entry. Engine bots run bot_engine.py." `
            -m $TRAILER
        git push
        Say "Panel pushed."
    }
}

# ─────────────────────── Part 2: engine files ───────────────────────
Say "Pushing engine files to automated-bot..."
$work = Join-Path $env:TEMP "automated-bot-push"
if (Test-Path $work) { Remove-Item -Recurse -Force $work }

git clone --depth 1 $AUTOMATED_REPO $work
if ($LASTEXITCODE -ne 0) { throw "clone of automated-bot failed" }

Copy-Item (Join-Path $root "_bot_engine\menu_engine.py")  (Join-Path $work "menu_engine.py")  -Force
Copy-Item (Join-Path $root "_bot_engine\bot_engine.py")   (Join-Path $work "bot_engine.py")   -Force
# menu_config.json in the repo is the TEMPLATE for new bots (live per-bot menus
# live in /opt/<name>/), so always keep it current.
Copy-Item (Join-Path $root "_bot_engine\menu_config.json") (Join-Path $work "menu_config.json") -Force

Set-Location $work
git add -- menu_engine.py bot_engine.py menu_config.json

git diff --cached --quiet
if ($LASTEXITCODE -eq 0) {
    Warn "No engine changes to push (files already up to date)."
} else {
    git commit `
        -m "Add config-driven menu engine (bot_engine.py + menu_engine.py)" `
        -m "Constructor-built bots run bot_engine.py, driving the whole user menu from menu_config.json. Reuses the existing admin panel + crypto/deposit backbone. Legacy bot.py bots are unaffected." `
        -m $TRAILER
    git push
    Say "Engine files pushed to automated-bot."
}

Set-Location $root
Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
Say "Done. On the VPS: Git-Pull the 'panel' bot in the dashboard, then Restart it."
