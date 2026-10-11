# Pitch Pack format (version 1)

A Pitch Pack is one JSON file that carries a finished pitch analysis between tools. Suggested name: `<pitch title>.pitchpack.json`.

- The **pitch analysis tool** exports it. It costs nothing to rehearse from, because the analysis is already done.
- The **Pitch Rehearsal Coach** imports it (setup screen, third option: *Rehearse from a Pitch Pack*) and goes straight to the panel. It makes no AI call on import.
- The Coach can also export one (**Export Pitch Pack** on the pre-read screen), including its rehearsal scores, so the analysis tool can show progress.

## Minimum valid pack

```json
{
  "format": "pitch-pack",
  "version": 1,
  "slides": [{ "title": "Welcome" }, { "title": "Why us" }],
  "analysis": {
    "deck_summary": "Two or three sentences on the deck's argument.",
    "requirements": [
      { "id": "R1", "text": "Recruit and train 15 chefs", "category": "people", "priority": "must", "ignored": false }
    ],
    "slides": [
      { "n": 1, "purpose": "Opens and frames the pitch", "key_message": "We understand your program",
        "pacing": "quick", "pacing_reason": "Title slide", "covers": [],
        "evidence_strength": "ok", "evidence_note": "", "likely_questions": [] },
      { "n": 2, "purpose": "Proves capability", "key_message": "We have done this at scale",
        "pacing": "weighty", "pacing_reason": "Carries R1", "covers": ["R1"],
        "evidence_strength": "thin", "evidence_note": "No named client or number",
        "likely_questions": [{ "question": "Which client, and how many chefs?", "persona_hint": "skeptic" }] }
    ],
    "unanswered_requirements": [],
    "inconsistencies": []
  }
}
```

## All fields

| Field | Required | Notes |
|---|---|---|
| `format` | yes | Always `"pitch-pack"`. |
| `version` | yes | Integer. This tool reads version 1 and refuses higher numbers. |
| `slides[]` | yes | One entry per slide, in deck order. `title` is used on screen and in reports. `text`, `notes`, `pictures` are optional. Slide number is the position in this list (1-based). |
| `analysis` | yes | The analysis itself. See below. |
| `pitch` | no | `title`, `channel`, `ignore_topics`, `guidance`, `presenters` (`[{name, from, to}]`). Fills the setup form's defaults. |
| `deck_fingerprint` | no | Lets the Coach warn if the deck changed since the analysis (only when the user also attaches the deck). Algorithm below. |
| `rfp_text` | no | The brief as plain text. Only needed so the Coach can re-run its own pre-read. Leave it out to keep the file small or confidential. |
| `source` | no | `{ "tool": "...", "model": "..." }`. Informational. |
| `created` | no | Timestamp string. |
| `rehearsals[]` | no | Written by the Coach on export, one summary per scored rehearsal (coverage, gut feel, question handling, delivery, total, win % panel and calculated, per-slide verdicts, top actions, unmet requirement ids). Ignored on import. |

### `analysis`

| Field | Type | Meaning |
|---|---|---|
| `deck_summary` | string | The deck's overall argument. |
| `requirements[]` | list | `id` (unique, e.g. `R1`), `text` (one atomic thing the client asked for), `category`, `priority` (`must` / `should` / `nice`), `ignored` (true = out of scope, never asked about or counted). |
| `slides[]` | list | One per slide. `n`; `purpose` (the job the slide does in the argument); `key_message` (the one sentence to leave with); `pacing` (`quick` / `normal` / `weighty`); `pacing_reason`; `presenter_hint` (optional first name); `covers` (requirement ids); `evidence_strength` (`strong` / `ok` / `thin` / `none`); `evidence_note`; `likely_questions[]` (`question`, `persona_hint` of `finance` / `operations` / `skeptic` / `insights` / `channel_expert`). |
| `unanswered_requirements[]` | list | `req_id`, `note`. Requirements no slide really answers. |
| `inconsistencies[]` | list | `description`, `slides` (numbers). Contradictions or sums that don't add up. |

## How the Coach treats a pack

- Unknown enum values are replaced with safe defaults (`pacing` becomes `normal`, `persona_hint` becomes `skeptic`). Requirement ids in `covers` that don't exist are dropped. Slides missing from the analysis are treated as normal pace, and the user is told.
- Pacing the user overrides in the Coach is baked into the pack when they export.
- Panel personas, sliders, difficulty, slide range and presenters are **not** part of the pack. They are chosen per rehearsal on the setup screen, so one pack can be rehearsed with different panels.

## Deck fingerprint

First 16 hex characters of SHA-256 over, for each slide in order, the UTF-8 text `"{n}|{title}|{text}\n"`, where `title` and `text` are trimmed and `text` is the on-slide text (not speaker notes) with shapes read top to bottom, left to right. A mismatch is only a warning. If the analysis tool can't reproduce this exactly, leave the field out; the slide-count check still works.
