# Code signing policy

TipTune supports Windows Authenticode signing through SignPath in the tag release
workflow. This integration requires an approved provider account and the settings
below before it can produce a release. Existing releases, including v1.8.1, were
not Windows Authenticode signed. Their `.sig` files are Tauri updater signatures.

When the SignPath Foundation integration is active: **Free code signing provided
by [SignPath.io](https://signpath.io/), certificate by
[SignPath Foundation](https://signpath.org/).**

Authors, reviewers, and signing approver: [rndmzd](https://github.com/rndmzd).
Production signing requests require the maintainer's approval in SignPath.
Contributors' changes are reviewed before release. SignPath and GitHub accounts
with these responsibilities must use multi-factor authentication.

## Free signing eligibility and account setup

[Apply to SignPath Foundation](https://signpath.org/apply) for
`https://github.com/rndmzd/TipTune`. TipTune is public, MIT licensed, maintained,
and already released. These facts support an application; they do not guarantee
acceptance. SignPath also evaluates project reputation and its other
[conditions](https://signpath.org/terms). Each free production signing request
requires manual approval; signing is not unattended.

After acceptance, configure a GitHub trusted build system for the TipTune project
and install the [SignPath GitHub app](https://github.com/apps/signpath) with access
to this repository. Import
[signpath-artifact.xml](../.github/signing/signpath-artifact.xml) as the artifact
configuration. Select the production Foundation certificate, require approval,
and restrict the policy to this repository's tag release workflow on GitHub-hosted
Windows runners. Do not select a test/self-signed certificate. Create an API token
limited to submitting requests for this project/policy.

In the repository's **Settings > Secrets and variables > Actions**, add:

| Kind | Name | Value |
| --- | --- | --- |
| Secret | `SIGNPATH_API_TOKEN` | The project's submitter token |
| Variable | `SIGNPATH_ORGANIZATION_ID` | Organization ID from SignPath |
| Variable | `SIGNPATH_PROJECT_SLUG` | Actual project slug from SignPath |
| Variable | `SIGNPATH_SIGNING_POLICY_SLUG` | Actual production signing policy slug |
| Variable | `SIGNPATH_ARTIFACT_CONFIGURATION_SLUG` | Slug of the imported artifact configuration |
| Variable | `WINDOWS_SIGNING_CERT_SUBJECT` | Exact certificate Subject as Windows displays it, including all fields |

Keep the existing `TAURI_SIGNING_PRIVATE_KEY` and
`TAURI_SIGNING_PRIVATE_KEY_PASSWORD` secrets and updater public key. Replacing that
key pair would break updates for existing installations. Do not paste tokens or
private keys into issues, pull requests, this document, or chat.

The free certificate identifies SignPath Foundation as the publisher. Use its
actual Subject for the variable; do not guess it or substitute the app name.
Certificates may rotate; pin the provider's verified identity rather than an
individual certificate thumbprint.

## What happens for every future release

1. Synchronize the six version fields and push a new `vX.Y.Z` tag.
2. The workflow checks the provider configuration and refuses to replace an
   already-published release. Missing signing settings stop it before building.
3. The Python backend receives TipTune product/version metadata and is signed
   before it becomes an installer input.
4. Tauri invokes the signing hook at the right point for each patched desktop
   executable, NSIS uninstaller, and finished MSI/NSIS installer. This retains
   the correct bundle type for existing MSI and NSIS updater installations.
5. Each callback uploads one file using the pinned official GitHub artifact
   client and submits it through the pinned official SignPath client. These run
   within `tauri-action`'s inherited Actions runtime so SignPath can verify the
   artifact's workflow origin. An ordinary shell step does not supply that runtime.
6. Keep the SignPath approval portal open and approve the requests belonging to
   this release. There can be several requests, not just one. Each request waits
   up to 30 minutes. Tauri buffers some signing output while waiting, so the
   portal is the reliable place to see pending approvals.
7. The hook requires Windows to validate the returned signature, the expected
   publisher, product/version metadata, and a timestamp. A failed request or an
   invalid result stops the release; there is no unsigned fallback.
8. Tauri generates its separate updater signatures from the final signed
   installers. `tauri-action` uploads them and `latest.json` to a **draft** release.
9. The verification step extracts both installers, checks the signatures on the
   desktop and backend inside them, and checks their updater bundle types.
   Review this successful job and its `windows-signing-report` artifact before
   publishing the draft. A failed verification leaves the draft unpublished.

Do not skip Tauri's binary patching or sign the desktop once before bundling:
the CLI patches it separately for MSI and NSIS, invalidating any earlier signature.
The generated release overlay also avoids rerunning the sidecar build after it
has been signed. Normal CI and local builds remain available without SignPath.

## Signing coverage and third-party components

SignPath Foundation only permits signing this project's artifacts. Bundled
FFmpeg, FFprobe, yt-dlp, Deno, and the standard NSIS plugins retain their upstream
bytes and signatures. The hook explicitly excludes those known upstream paths
and records their hashes and signature status; it refuses other unexpected files.
They must not be relabeled or re-signed as TipTune. Microsoft/WebView2 components
also keep their original publisher identities.

Signing the TipTune app does not establish trust for every dependency it may
execute or load. Check playback and uninstall on a separate Windows 11 computer
with Smart App Control enabled before describing those paths as verified.
New SmartScreen reputation warnings may still appear even on a correctly signed
release. Windows code signing and reputation are related but separate checks.

## Privacy and network use

TipTune uses the music, streaming, and AI services configured by its operator to
process music requests. Credentials and request content can therefore be sent to
the selected services for the requested functionality. Optional Sentry error
reporting is **off by default** and requires a separate user opt-in; see
[Error reporting](ERROR_REPORTING.md) for the collected fields, redaction, and
configuration. Refer to the selected providers' privacy policies when enabling
their services. No signing credentials are distributed with the app.

## Alternatives

If the Foundation does not accept the project, Microsoft's
[Artifact Signing](https://learn.microsoft.com/en-us/azure/artifact-signing/overview)
is a paid option with identity verification, or the Microsoft Store can sign a
Store-distributed MSIX package. A self-signed certificate is useful for isolated
tests, but does not solve public Smart App Control trust. Changing providers
requires adapting the signing hook, not changing the existing Tauri updater key.

References: [SignPath GitHub integration](https://docs.signpath.io/trusted-build-systems/github),
[Tauri Windows signing](https://v2.tauri.app/distribute/sign/windows/),
[Microsoft Smart App Control signing](https://learn.microsoft.com/en-us/windows/apps/develop/smart-app-control/code-signing-for-smart-app-control).
