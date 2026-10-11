# Hosting on Render (so it runs in a browser, nothing to install)

1. **Render account:** sign up at render.com with GitHub. Choose **New > Blueprint**, pick this private repo, and approve. Render reads `render.yaml`.
2. **Two secrets when asked:** `APP_PASSWORD` (what you and the team type to get in) and `ANTHROPIC_API_KEY`. They live on Render only, never in GitHub.
3. **Wait for the first deploy** (a few minutes). Open the `https://…onrender.com` address in Chrome or Edge. The browser asks for a password; leave the user name as anything.

Cost: the `starter` plan plus a 1 GB disk is roughly US$7-8 a month. The disk keeps your sessions, transcripts and scores. On the free plan the app sleeps when idle and loses saved sessions on each restart; Pitch Packs still work as a backup because you can export and re-import them.

## Slide changes when hosted
A hosted app can't see PowerPoint on your PC. Two ways:
- **Buttons / arrow keys** on the rehearsal page. Nothing to install. This is the default.
- **Optional helper:** `tools/slide_helper.ps1` runs on the PC that has the deck open and reports the slide to the hosted app. It is plain PowerShell (nothing to install) but needs scripts to be allowed. Run: `powershell -ExecutionPolicy Bypass -File tools\slide_helper.ps1 -Url https://your-app.onrender.com -Password "your password"`. The page shows "PowerPoint linked" when it works.

## Before you use real client material
The RFP, deck and transcripts are stored on Render's servers as well as sent to Anthropic for analysis. Check IT / the NDA first. Everything is behind the password, but it is still a third-party host.

## Updating
Push to the repo; Render redeploys. Local settings are unchanged: `run.bat` still works on any PC where you are allowed to install it.
