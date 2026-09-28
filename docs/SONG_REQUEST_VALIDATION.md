# Song request upgrade validation

Validated September 28, 2026.

## Automated checks

- 52 Python tests passed, including the five existing request-history tests.
- The frontend TypeScript/Vite build passed.
- Python compilation and `git diff --check` passed. The final empty-market-list edge case was verified with mocked market checks; the live reports precede that small availability adjustment.
- An actual OpenAI SDK test verified `text.format.type=json_schema`, `strict=true`, and rejection of extra schema fields using a mocked HTTP transport.
- Mocked request tests cover GPT-6 Luna/Sol/Astra and saved GPT-5 settings. Live model validation used GPT-6 Luna.

## Live comparison

The old baseline is commit `97939e2f6b00d63fb8e52b875eb75797f7f48bda` with the saved `gpt-5-mini` model. The new pipeline used `gpt-6-luna`. All inputs were synthetic. Spotify access was restricted to account/catalog reads; no queue insertion or playback occurred. Credentials and saved settings were not changed.

The corpus has 30 cases. New results below are the latest observation per case: a full run plus six targeted reruns after adding the artist eligibility check. These cases were used during development; this is not a held-out benchmark or a controlled latency experiment.

| Metric | Old pipeline | New pipeline |
| --- | ---: | ---: |
| Strict extraction matches | 25 | 29 |
| Strict catalog matches | 21 | 25 |
| Service failures | 0 | 0 |
| Unresolved song requests | 2 | 4 |
| Median processing seconds | 3.545 | 2.487 |
| Model calls | 39 | 37 |
| Input tokens | 6434 | 14623 |
| Output tokens (including reasoning) | 8880 | 2466 |

Counts exclude preflight calls and superseded development runs. Catalog matches include three negative cases that correctly produce no song; they are not all successful playback resolutions.

## Reviewed outcomes and remaining limits

- A single song with allowance for three requests stays a single song; the old pipeline returned three copies.
- Thank-you chatter and the instruction-only negative case produce no song rather than a whole-message catalog search.
- The lone strict extraction mismatch is `Beyonce` versus fixture spelling `Beyoncé`; the actual catalog track was correct. Raw scores were not adjusted.
- One catalog mismatch is `Mucka Blucka (Bonus Track)` versus the fixture title `Mucka Blucka`, with the requested artist Tally Hall. Raw scores retain that mismatch.
- Bare `U` and `Hi` remained unresolved in this market/candidate set. Earlier development runs selected `Up` and `Higher`; the final implementation rejects that expansion. Supplying an artist or track link remains more reliable for short ambiguous titles.
- Requested live Adele and acoustic Eagles recordings were not established by the available candidate metadata. Earlier runs selected a studio track or unrelated cover artist. The final targeted checks returned no match for both rather than substituting those recordings.
- Google hints and title-only matching remain dependent on search evidence and catalog coverage. The model chooses among eligible candidates using best effort; ambiguity is not eliminated.

## Reproduction and raw evidence

See [evaluation instructions](SONG_REQUEST_EVALUATION.md). The generated, git-ignored reports remain under `build/`: `song-request-evaluation.json` (original comparison), `song-request-evaluation-validated.json` (full rerun), `song-request-version-verification.json` (final targeted verification), and `song-request-evaluation-summary.json` (latest observations with source hash).

Existing saved model choices remain unchanged. New or blank settings use `gpt-6-luna`; an existing installation must select that model explicitly to switch from its saved choice.
