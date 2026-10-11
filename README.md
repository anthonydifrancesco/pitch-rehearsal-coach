# Pitch Rehearsal Coach

Runs on your own computer. Nothing is hosted online. Outside calls: the Anthropic API (the panel's thinking), and your browser's own speech-to-text service.

**Hosted instead of local?** See `DEPLOY.md` (Render, password-protected, opens in any browser).

## Run it (Windows)
1. Install Python 3.8 or later from python.org (tick "Add python.exe to PATH").
2. Double-click `run.bat`. First run installs packages (a minute or two).
3. Paste your key into `.env` after `ANTHROPIC_API_KEY=`, close the black window, run `run.bat` again.
4. Use **Chrome or Edge**. The app opens at http://127.0.0.1:8000.

Updating from an older copy: extract the new zip over the old folder and choose Replace. Your `.env`, sessions and packages are kept. `run.bat` installs the one new package (pywin32) automatically.

## The flow
0. **Already have an analysis?** Choose *Rehearse from a Pitch Pack* on the setup screen and load the `.pitchpack.json` made by the pitch analysis tool. No pre-read is run, so nothing is spent on analysis; you go straight to the review screen and then the rehearsal. **Export Pitch Pack** on the review screen produces the same file (with your rehearsal scores) for the other tool. Format: `PITCH_PACK.md`.
1. **Setup + pre-read.** Upload RFP and deck, pick personas and sliders, run the pre-read.
2. **Review and share.** Slide map, requirements, likely questions, gaps and inconsistencies. **Export report (.docx)** makes a landscape Word file for the team. **Assessment guidance** tells the AI how to assess; save and re-run to see the difference.
3. **Rehearse.** Press *Start rehearsal*. Check the mic, panel voices and slide detection, then present. Open your deck in PowerPoint and start the slide show; keep the rehearsal tab open behind it.
   - The panel listens, follows your slides, and interjects according to the slider (0 holds everything for the end).
   - *Finish pitch* starts the end Q&A. Answer out loud, press *Done answering* (or pause for a few seconds).
   - Scoring: brief coverage, gut feel, total, question handling, delivery per slide judged against each slide's intended pace, win likelihood (the panel's read next to a calculated figure), top actions. Download it as a Word report.
4. Everything is saved under `data/sessions/<id>/` (pre-read, rehearsal transcripts and scores).

## How it works
- Speech to text and voices are free and come from the browser (Web Speech API). Voices are your computer's built-in ones; Edge also offers high-quality online voices (tick the box in the pre-flight screen; the panel's spoken text then goes to Microsoft).
- Slide detection reads PowerPoint through Windows automation. If PowerPoint isn't open or detection fails, use the buttons or arrow keys. The page always shows which mode it is in.
- While a panel member speaks, the mic is ignored so the panel doesn't hear itself.
- Live decisions use Claude Haiku 5.5 with thinking off (fast, cheap). Q&A and scoring use Sonnet 5.5.

## Cost (estimates)
Pre-read about 20-40 cents. A 45 minute rehearsal about 30-60 cents (live decisions a few cents, Q&A a few cents, scoring 10-25 cents). Check usage in the Anthropic console.

## Known limits
- One presenter per session for now. Phones as extra microphones, session history across rehearsals and the reporting suite come next.
- The audio itself isn't recorded yet; the transcript is.
- Text only: charts and pictures on slides aren't read.
- Browser speech recognition can mishear names and numbers; the typed box under the transcript is the fallback.
- Real client material: check IT / NDA first.

## Tests
`python tests/test_end_to_end.py deck.pptx rfp.pdf` and `python tests/test_rehearsal.py deck.pptx rfp.pdf` use a fake AI client (no key, no network).
`python tests/test_pack.py deck.pptx rfp.pdf` checks Pitch Pack import and export with no AI client at all.
`tests/serve_fake.py` + `tests/browser_test.py` drive the rehearsal screen in headless Chrome with simulated speech.
