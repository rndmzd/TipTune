# Error reporting

TipTune uses one consent-controlled Sentry sender for Python, React, and desktop errors. Reporting is off unless the user explicitly saves `General.send_error_reports=true`. The History debug-display setting does not grant upload consent. Enabling reporting does not upload old logs or request history.

## Receiving dashboard

1. Create a Sentry project for TipTune. Python is a suitable starting platform; the same project accepts the UI and desktop events.
2. Copy the **public DSN** from **Project Settings → Client Keys (DSN)** into the `dsn` field in the repository's `sentry.json`. This public ingestion address is bundled in the app; a Sentry password or private API token is not needed. Rebuild the app after changing it. `TIPTUNE_SENTRY_DSN` can override the bundled address for development or testing, but cannot grant reporting consent.
3. Set issue alerts, retention, and project access in Sentry. Reports use release `tiptune@<app version>` and the configured environment (`production` by default). Isolated development checks can set `TIPTUNE_SENTRY_ENVIRONMENT=development`.
4. In an isolated TipTune instance, enable reporting, save, and select **Send test report**. Search Sentry for the displayed event ID or `event_type:tiptune.reporting.test`. The button confirms queuing; the `/api/error-reporting` status exposes successful ingestion time and a bounded delivery error when available. A successful ingestion response confirms Sentry accepted the event, not that a developer opened it.

No read-access credentials are shipped. UI reports include bundled filename, line, column, a canonical asset reference, and its build debug ID. Sentry uses privately uploaded source maps to recover the original TypeScript/React code location. Rust panic reports contain the panic's code location when available, not a native memory dump or full native backtrace.

## Private JavaScript source maps

The receiving organization is `rys-circus` and the project is `python`. In the GitHub repository's **Settings → Secrets and variables → Actions**, configure:

- Secret `SENTRY_AUTH_TOKEN`: a Sentry organization token with the [`org:ci` permission](https://docs.sentry.io/api/permissions/) for CI uploads. Keep it out of chat, source control, and application configuration.
- Variable `SENTRY_ORG`: `rys-circus`.
- Variable `SENTRY_PROJECT`: `python`.

`npm run webui:build` generates hidden source maps and injects matching debug IDs with Sentry's Vite plugin. It archives the exact compiled JavaScript and maps under the ignored `.cache/sentry-source-maps/<build hash>/` directory, then removes maps from `webui/dist` before Python or Tauri packaging. The build makes no Sentry requests. PyInstaller also excludes `.map` files as a packaging safeguard.

`npm run sentry:verify` checks the archive, matching debug IDs, app version, and hashes of the distributed JavaScript. `npm run sentry:upload` repeats these checks, then uploads to Sentry using credentials supplied only in the build environment. It waits for processing and fails if credentials are missing or upload processing fails. Never inject debug IDs again after building: that can break the match between the app and its maps.

The tagged-release and manual desktop-build workflows upload after the final Tauri build, including its bundle hooks. A failed upload fails the workflow; the tagged release remains a draft. PR CI verifies the private archive without using the upload secret. To verify credentials or upload a development build without producing installers, run the **build** workflow with **source_maps_only** enabled on the intended branch. These workflows do not publish source maps as GitHub artifacts or release assets.

Debug IDs match the content of each JavaScript chunk, including the OBS overlay, even across builds with the same app version. The frontend records its compiled app version, and offline reports retain that version and their debug IDs. Only canonical `app:///assets/<bundle>.js` references that match stack frames enter reports; browser origins, URL queries, machine paths, raw stacks, and exception messages are discarded. Private maps contain application source code, so limit access to the Sentry project and the local build archive. Old reports received before matching maps are uploaded may remain unsymbolicated; verify with a new error after uploading.

## Report contents

Reports allowlist the app release, OS, component, exception types, code filenames/functions/line numbers, exception causes, failure stage, selected music source, provider HTTP status/request ID, elapsed time, and desktop exit code. Up to 50 preceding structured application event names can accompany a failure.

TipTune keeps OBS integration errors (`obs.*`) in local logs and drops their standalone reports before upload.

Frameless Web UI reports can include a `webui` context with the capture source and the screen label for a recognized app route. Capture sources distinguish global window errors, unhandled promise rejections, and React error boundaries. The context can also identify the most recent tracked action on that screen within 45 seconds. The tracked actions are Resume Queue, reloading or retrying the Spotify player, and Apply + Save for a Spotify device. These values use the existing reporting opt-in and consist of fixed labels.

Free-form log/exception text, usernames, tip/request messages, authentication headers, cookies, configured secrets, runtime source text, frame locals, request bodies, provider response bodies, device/host names, and absolute machine paths are excluded from reports. The separate build upload provides application source maps to Sentry. No automatic session reports, profiling, metrics, continuous remote logs, screenshots, or attachments are sent. Automatic SDK integrations and propagation to external services are disabled.

## Functional failures and support IDs

Opt-in reports also cover `song.lookup.failed`, `song.availability.failed`, `song.queue.failed`, and Spotify playback that is unconfirmed, unavailable, invalid, or raises an exception. These use fixed `error_code` tags and technical metadata. An availability-check failure means Spotify could not be checked; it does not establish a country restriction. Blank requests, no identified song, normal catalog misses, and confirmed Spotify restrictions remain normal request outcomes. They do not create new automatic error reports.

Unconfirmed Spotify starts keep the original counts for the first failed check on each poll. When Spotify reports playing on the selected device, `playback_identity` splits the existing `track_mismatch` count into missing item, unavailable recording metadata, and different recording under TipTune's comparison rules. `playback_device_was_active` records Spotify's active flag from the device list before the accepted command when available. `playback_progress` is optional: it compares observed positions for the same item on the selected device, and reports a reset (a drop of at least two seconds with a newer Spotify timestamp), advancement, or an unchanged playing position for at least one second. It does not identify the item or prove why the position changed. Older reports can omit all three fields.

Each song request gets an opaque `TT-<32 hex digits>` support ID. It appears on failed History entries and requests with a playback problem, with a **Copy support ID** action and a selectable fallback when clipboard access is unavailable. The same ID accompanies local structured logs, the queued track, and any Sentry errors or transactions. In Sentry, search `support_id:TT-...`; use `error_code:song.lookup.failed` (or another listed code) to group functional failures. A provider's `request_id` remains a separate provider reference.

Support IDs are saved locally even when automatic reporting is off. History distinguishes a report that was queued from one that was disabled, unavailable, or limited. Queuing does not confirm ingestion. Repeated identical errors within one operation are suppressed for one minute; separate requests keep separate IDs, subject to the existing queue and rate bounds. Background playback starts a fresh trace when necessary and retains the request's support ID. Playback failures update the matching History entry, including after queue restoration; a successful retry clears the playback error without changing the fact that the request was added to the queue.

## Safe environment details

New backend-collected reports and transactions snapshot a `diagnostics` context containing only allowlisted values: backend app/Python versions, OS build, CPU architecture, installed versions of selected Python dependencies, selected music source, OBS enablement, overlay mode, and a fixed playback-backend category when known. UI errors retain their compiled release and include the receiving backend's environment. On Windows, the installed Evergreen WebView2 version is included if its registry entry is available; this is not a claim about a separately supplied fixed runtime. Missing metadata is omitted. Native startup/crash records keep their existing technical fields; replay does not invent missing environment details.

Packaging embeds `build-info.json` with the Git commit, tracked-dirty flag, source/build content hash, and dependency versions. The hash covers application code and distributed frontend assets, excluding configuration, logs, and caches. Source runs identify themselves as `source` and do not invent packaged build identifiers. Backend reports also include a random reporting-session ID, created only after opt-in and reset on process restart or opt-out. No persistent installation or hardware identifier is collected.

Environment details are sanitized before storage and again before upload. Offline replay retains the original snapshot and does not attach the environment of a later app version to old reports. The existing public reporting DSN still grants no read access, and no additional credentials are needed by an installation.

## Tracing

The reporting opt-in also enables [Sentry tracing](https://docs.sentry.io/platforms/python/tracing/). Named app actions (POST API routes), music searches, queued song requests, and Spotify playback attempts create transactions. Song extraction, provider lookup, and queue insertion create child spans. Routine status polling, event streams, static assets, and audio streaming are excluded. UI errors link to the backend transaction receiving their local error report; browser rendering and native crash durations are not measured.

`sentry.json` sets `traces_sample_rate` to `1.0`, so all these selected operations are sampled while reporting is enabled. Rebuild after changing this setting; development can override it with `TIPTUNE_SENTRY_TRACES_SAMPLE_RATE` between `0` and `1`. A value of `0` disables tracing while retaining error reporting. Invalid values disable tracing. With sampling below `1`, an error can retain a trace ID even when its transaction was not sampled.

Transactions contain fixed operation names, random trace/span IDs, support IDs, start/end timestamps, status, release, and the safe environment details described above, with at most 50 child spans. Request URLs, headers, bodies, provider attributes, and arbitrary span data are discarded before storage and again before upload. Error events retain their original trace/span context when queued and replayed, so the background sender does not assign them to an unrelated trace. Tracing only enqueues locally on the operation thread; the existing consent-controlled worker uploads both event types.

Each attempted Google Custom Search fallback can produce one separate transaction with a fixed name: `song.lookup.google.recovered` (the second Spotify lookup returned a track), `song.lookup.google.empty` (Google returned no results), `song.lookup.google.no_match` (no usable hint or Spotify match), or `song.lookup.google.failed` (a provider or processing error during the fallback). Its `google_fallback.search_phrase` context can include up to 200 characters of the song and artist phrase sent to Google, after redaction. The phrase is omitted if it looks like it contains a URL, credential, or identifier. This transaction has no support ID, reporting-session context, original tip text, or Google results. Disabled and bypassed lookups do not create a fallback transaction.

In Sentry Explore → Traces, filter to the release that includes this change and to `is_transaction:true`. Group `count()` by `span.description` for the four names above. Add all four counts for observed attempts; divide the recovered count by that total for the observed recovery rate. Open a transaction to inspect its `google_fallback` context when present. These counts cover reports that opted in, were sampled, and reached Sentry. A recovered lookup does not confirm that the song was queued or played.

Transactions share the bounded offline queue and stop entering it at half capacity, reserving space for errors. Turning reporting off deletes both unsent errors and traces; an operation still running when consent is revoked cannot queue its completed trace. **Send test report** creates a linked error and transaction when tracing is sampled. Use its event ID in Sentry and open the associated trace.

## Delivery and consent

The Python worker uses HTTPS with certificate verification and bounded timeouts. Only sanitized JSON enters `<cache>/error-reports/pending`. Each report is at most 64 KiB; the queue holds at most 100 reports. Identical failures are suppressed for one minute, and retries back off with service rate limits. Reports expire after seven days and keep their original release when replayed after an update.

The saved consent is checked before collecting and before every upload. Disabling reporting clears unsent reports and stops future uploads; it cannot retract a request already in flight or a report previously accepted by Sentry. A file-removal failure is visible in reporting status and does not allow uploads while consent is off.

The React app and OBS overlay send only technical errors to the local backend. Global JavaScript errors, unhandled promise rejections, and React render errors are covered. The Tauri launcher writes sanitized startup failures, Rust panic locations, and unexpected Python-service exits into the same queue. These desktop records send on the next successful backend launch while consent remains enabled. Normal shutdowns do not create crash reports. Abrupt termination before an error is recorded can still leave no traceback.

## Development verification

Run the Python regression suite, Web UI build, and native error-reporting tests. Use isolated configuration/cache paths and mocked ingestion for local/browser checks; never enable reporting in the user's configuration as a test. Synthetic live receiver checks should use a separate `development` environment and contain no application data.
