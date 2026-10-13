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

Free-form log/exception text, usernames, tip/request messages, authentication headers, cookies, configured secrets, runtime source text, frame locals, request bodies, provider response bodies, device/host names, and absolute machine paths are excluded from reports. The separate build upload provides application source maps to Sentry. No sessions, tracing, profiling, metrics, continuous remote logs, screenshots, or attachments are sent. The SDK has automatic integrations disabled and runs only inside the delivery worker.

## Delivery and consent

The Python worker uses HTTPS with certificate verification and bounded timeouts. Only sanitized JSON enters `<cache>/error-reports/pending`. Each report is at most 64 KiB; the queue holds at most 100 reports. Identical failures are suppressed for one minute, and retries back off with service rate limits. Reports expire after seven days and keep their original release when replayed after an update.

The saved consent is checked before collecting and before every upload. Disabling reporting clears unsent reports and stops future uploads; it cannot retract a request already in flight or a report previously accepted by Sentry. A file-removal failure is visible in reporting status and does not allow uploads while consent is off.

The React app and OBS overlay send only technical errors to the local backend. Global JavaScript errors, unhandled promise rejections, and React render errors are covered. The Tauri launcher writes sanitized startup failures, Rust panic locations, and unexpected Python-service exits into the same queue. These desktop records send on the next successful backend launch while consent remains enabled. Normal shutdowns do not create crash reports. Abrupt termination before an error is recorded can still leave no traceback.

## Development verification

Run the Python regression suite, Web UI build, and native error-reporting tests. Use isolated configuration/cache paths and mocked ingestion for local/browser checks; never enable reporting in the user's configuration as a test. Synthetic live receiver checks should use a separate `development` environment and contain no application data.
