# Song request evaluation

The parser and Spotify resolver can be evaluated without starting TipTune, queueing songs, or controlling playback. The synthetic corpus contains 30 cases covering artist/title order, typos, punctuation, short titles, multiple requests, count limits, chatter, embedded instructions, and recording versions. Mocked tests additionally cover direct/mixed links and service failures.

Run the offline checks from the repository root:

```powershell
env\Scripts\python.exe -m unittest discover -s tests -v
npm run webui:build
```

List the evaluation size without making network calls:

```powershell
env\Scripts\python.exe scripts\evaluate_song_requests.py
```

Explicitly opt in to API calls using an existing TipTune configuration and Spotify login:

```powershell
env\Scripts\python.exe scripts\evaluate_song_requests.py --live --config "$env:APPDATA\TipTune\config.ini"
```

The harness reads the configured OpenAI key, falling back to `OPENAI_API_KEY`. It reads the Spotify token cache beside the configuration, refreshing tokens only in memory. `--spotify-cache` can select another existing cache. It exposes only account and catalog reads to the old and new implementations; it does not initialize the playback controller or launch a login browser.

The default baseline is commit `97939e2f6b00d63fb8e52b875eb75797f7f48bda`, including its Google-first extraction, five-stage catalog search, short-message wrapper, and whole-message fallback. Its model is the saved model or historical `gpt-5-mini` default. The new path defaults to `gpt-6-luna`. Use `--baseline-model` and `--model` to select explicit models; the report records both. Comparisons therefore measure the combined model/pipeline upgrade, not an isolated prompt experiment.

Results default to `build/song-request-evaluation.json`. The JSON includes each interpretation, resolved metadata, correctness flags, failures, latency, and API token usage. Exceptions are recorded without request URLs, headers, or secret values. A failed preflight records a blocker rather than reporting fabricated accuracy. Use `--output` to retain multiple reports and `--implementation new` to rerun only the changed implementation. `--limit` supports smaller smoke checks.

Scoring is deliberately explicit: the number and order of extracted requests must match the fixture; specified artists must match; catalog results must match a listed acceptable title/artist pair. An empty expected list requires no request. Metadata scoring does not automatically treat remasters, bonus-track suffixes, alternate recordings, or every interpretation of an ambiguous title as equivalent. Inspect mismatches alongside the raw results before treating them as wrong-song selections. The finite acceptable lists and one run per case are diagnostic checks, not a statistically representative accuracy benchmark.

Live results depend on account access, market, catalog availability, and model behavior. Mocked tests verify the software contract; they cannot establish the model's semantic accuracy.
