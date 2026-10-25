# Code signing policy

TipTune's tag release workflow supports Windows Authenticode signing with
**Microsoft Azure Artifact Signing**. Activation requires a paid Azure account,
completed identity validation, a Public Trust certificate profile, and the GitHub
configuration below. Existing releases, including v1.8.1, are not Authenticode
signed. Their `.sig` files are separate Tauri updater signatures.

Maintainer and release reviewer: [rndmzd](https://github.com/rndmzd).
Signing uses the certificate's actual validated publisher identity. Certificates
can rotate; the verifier checks the trusted signature, exact certificate Subject,
and timestamp instead of pinning a single short-lived certificate thumbprint.

## Set up Azure from scratch

For an individual developer, Public Trust signing is currently available in the
United States and Canada. Use the same legal name and address on your personal
Azure billing account and identity documents. Review **Certificate subject
preview** before submitting identity validation: the public certificate contains
your validated name, city, state/province, and country. Microsoft's individual
workflow excludes your email and street address from the certificate. It cannot
use an arbitrary TipTune publisher name.

1. Open [Azure account signup](https://azure.microsoft.com/en-us/pricing/purchase-options/azure-account?icid=payg)
   and choose **Pay as you go**. Artifact Signing does not support free, trial,
   or sponsored subscriptions. You do not need an Entra P1/P2 license for this
   workflow. Use the directory associated with your own Azure subscription;
   a separate additional tenant is unnecessary if you already control that one.
2. In [Azure portal](https://portal.azure.com/), confirm the active subscription
   and directory. If a form asks for billing account type, select **Individual**.
   Complete billing and identity steps directly with Microsoft.
3. Open **Subscriptions > your subscription > Resource providers**, find
   `Microsoft.CodeSigning`, and register it if it is not registered already.
4. Create **Artifact Signing account** with the **Basic** plan. A suggested
   resource group is `tiptune-signing`, region **East US**, and account name
   `rndmzdtiptune` if available. The account name must be globally unique.
   Basic is advertised at US$9.99/month including 5,000 signatures; additional
   signatures and taxes can cost extra. Billing starts when the signing account
   is created, including while identity validation is pending. No virtual machine,
   Key Vault, or other paid compute is required for GitHub-hosted builds.
5. On the signing account's **Access control (IAM)**, assign your own user the
   **Artifact Signing Identity Verifier** role. Subscription Owner alone does not
   automatically supply this identity-validation role.
6. Open **Identity validations > Individual > New identity > Public**. Follow
   Microsoft's identity verification instructions and wait for **Completed**.
   Keep ID photos, selfies, billing information, and verification links out of
   GitHub and chat. If approval fails, review or delete the unused signing account
   to avoid continuing charges.
7. Create a **Public Trust** certificate profile linked to the completed identity.
   A suggested profile name is `windows-public`. Do not choose Public Trust Test
   or Private Trust: those do not provide public trust for downloaded releases.
8. Record the actual account name, certificate profile name, regional endpoint,
   and exact certificate Subject. East US uses `https://eus.codesigning.azure.net`.
   Other regions have different endpoints; copy the endpoint from the account.

See Microsoft's [setup quickstart](https://learn.microsoft.com/en-us/azure/artifact-signing/quickstart),
[pricing](https://azure.microsoft.com/en-us/products/artifact-signing), and
[subscription FAQ](https://learn.microsoft.com/en-us/azure/artifact-signing/faq).

## Connect GitHub without a client secret

1. In this repository, open **Settings > Environments**, create `windows-signing`,
   and set **Deployment branches and tags** to **Selected branches and tags**.
   Add a **Tag** rule named `v*`; do not add a branch rule. Complete this restriction
   before granting Azure signing access. Maintainers may add required reviewers
   if they want an additional approval before each release.
2. In **Microsoft Entra ID > App registrations > New registration**, create
   `TipTune GitHub signing` for **Accounts in this organizational directory only**.
   Leave redirect URI empty. Record its **Application (client) ID** and
   **Directory (tenant) ID**. Do not create a client secret or add API permissions.
   If the portal adds the default Microsoft Graph `User.Read` permission, remove
   that configured permission; this signing app does not need it or admin consent.
3. On that app, open **Certificates & secrets > Federated credentials > Add
   credential**. For TipTune's current GitHub subject configuration, select
   **Other issuer** and **Explicit subject identifier**, then enter:

   | Field | Value |
   | --- | --- |
   | Issuer | `https://token.actions.githubusercontent.com` |
   | Value (subject) | `repo:rndmzd/TipTune:environment:windows-signing` |
   | Name | `tiptune-windows-releases` |
   | Audience | `api://AzureADTokenExchange` |

   The subject must match the repository's actual OIDC configuration. Check it
   with `gh api repos/rndmzd/TipTune/actions/oidc/customization/sub`: TipTune currently
   returns `use_default: true`, `use_immutable_subject: false`, and
   `sub_claim_prefix: repo:rndmzd/TipTune`. The portal's GitHub scenario may propose
   a newer immutable-ID subject that does not match this repository's setting.
   If that setting changes, update Azure's exact subject to match. The same
   federation configuration is
   available as [azure-federation.json](../.github/signing/azure-federation.json)
   for an administrator using the Azure CLI.
4. Assign **Artifact Signing Certificate Profile Signer** to the new app's
   service principal, scoped to the certificate profile. Microsoft's documented
   method for profile-level assignments is the Azure CLI. Replace these
   placeholders using the actual resource IDs before running:

   ```powershell
   az role assignment create --assignee-object-id "<service-principal-object-id>" --assignee-principal-type ServicePrincipal --role "Artifact Signing Certificate Profile Signer" --scope "/subscriptions/<subscription-id>/resourceGroups/<resource-group>/providers/Microsoft.CodeSigning/codeSigningAccounts/<account-name>/certificateProfiles/<profile-name>"
   ```

   The service principal's Object ID is under **Enterprise applications >
   TipTune GitHub signing > Overview**; it differs from both the client ID and
   the app registration's Object ID. Some older portal screens may still use
   the former **Trusted Signing** role name. The app needs signing permission
   only; it does not need Contributor, Owner, identity-verifier, or Microsoft
   Graph permissions.
5. In GitHub's `windows-signing` environment, add these **environment variables**:

   | Name | Value |
   | --- | --- |
   | `AZURE_TENANT_ID` | Directory ID from the app registration |
   | `AZURE_CLIENT_ID` | Application ID from the app registration |
   | `AZURE_ARTIFACT_SIGNING_ENDPOINT` | Regional HTTPS signing endpoint |
   | `AZURE_ARTIFACT_SIGNING_ACCOUNT_NAME` | Actual signing account name |
   | `AZURE_ARTIFACT_SIGNING_CERTIFICATE_PROFILE_NAME` | Actual Public Trust profile name |
   | `WINDOWS_SIGNING_CERT_SUBJECT` | Exact full certificate Subject as Windows displays it |

   These are identifiers and settings, not Azure passwords. The workflow needs no
   client secret, certificate private key, Azure CLI login, or subscription ID.
   Never paste tokens or private keys into issues, pull requests, or chat.
6. Keep the existing repository secrets `TAURI_SIGNING_PRIVATE_KEY` and
   `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`, and the app's updater public key.
   Replacing that key pair would break updates for existing installations.

References: [GitHub OIDC with Azure](https://docs.github.com/en/actions/security-for-github-actions/security-hardening-your-deployments/configuring-openid-connect-in-azure),
[Azure role assignments](https://learn.microsoft.com/en-us/azure/artifact-signing/tutorial-assign-roles).

## What happens for future releases

1. Synchronize all six version fields and push a new `vX.Y.Z` tag. Missing settings,
   an incorrect tag, or an attempt to overwrite a published release stops the job.
2. The Windows job enters the restricted `windows-signing` environment. It installs
   pinned Microsoft SDK/Artifact Signing packages, verifies their archive hashes
   and Microsoft signatures, and uses .NET 8 for the native signing client.
3. The Python backend receives TipTune product/version metadata and is signed
   before it becomes an installer input. Tauri invokes the native hook after
   patching each desktop executable for its MSI/NSIS bundle type. The NSIS
   uninstaller and both completed installers are also signed.
4. Each signing callback obtains a fresh GitHub OIDC assertion for Azure's token
   exchange audience. The Microsoft client exchanges it through
   `WorkloadIdentityCredential` using the app registration's federation. Other
   credential types are excluded. The temporary assertion file is removed on
   success or failure; it is never logged, uploaded, or exported through GitHub
   workflow environment/output files. Signing requires no per-file provider
   approval after setup.
5. Signing operates on a staged copy. The hook requires a valid Windows signature,
   the expected publisher, matching product/version metadata, and a timestamp
   before replacing the original input. Failures stop the build with no unsigned
   fallback. The timestamp is essential because Azure's certificates are short-lived.
6. Tauri generates its separate updater signatures from the finished signed
   installers. The workflow uploads the installers and updater metadata to a
   **draft** release, then extracts both installers to verify their actual TipTune
   executables and updater bundle markers. Review the successful job and
   `windows-signing-report` artifact before publishing the draft.

Keep Azure billing active and renew identity validation before the expiration
shown in the portal. Microsoft sends reminders starting 60 days before expiry;
expired validation stops new signing and certificate renewal. See
[identity validation renewal](https://learn.microsoft.com/en-us/azure/artifact-signing/faq#what-if-my-identity-validation-expires).

Do not sign the desktop once before bundling: Tauri patches it separately for
MSI and NSIS. The generated overlay also prevents a second sidecar build from
replacing signed bytes. Ordinary CI and local builds work without Azure settings.
Do not merge this workflow until Azure setup is ready if tags must keep producing
releases; missing production settings intentionally prevent release creation.

## Coverage and privacy

Bundled FFmpeg, FFprobe, yt-dlp, Deno, and standard NSIS/WiX plugins retain their
upstream bytes and publisher identities. The hook excludes only their known
paths and records their hashes/signature status. Microsoft/WebView2 components
also keep their original identities. Signing TipTune does not establish trust for
every dependency it executes or loads. Validate playback and uninstall on a
separate Windows 11 machine with Smart App Control enabled before describing
those paths as verified. SmartScreen reputation warnings may still occur.

TipTune sends music requests and credentials to services selected by its operator
for the requested functionality. Optional Sentry error reporting is **off by
default**, requires a separate opt-in, and is documented in
[Error reporting](ERROR_REPORTING.md). Refer to selected providers' privacy
policies when enabling their services. No signing credentials are distributed
with the app.

A configured production run must verify Azure authentication and signing. Also
test execution on a separate Smart App Control machine. Offline tests and
package checks do not establish those outcomes.
