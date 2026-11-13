# Reviewed bot setup

Updated: 2026-11-13.
Status: Store, authenticated API, driver, runtime registration and review UI are connected. Deterministic tests pass. Fresh real-VM provisioning and the UI in bare remain unverified.

## Flow

Describe the job on the overview. Deck proposes a draft from the job and the configured defaults. The suggested name uses the first words of the job. The model comes from the configured priced profile. These recommendations do not call a model. Job-specific model selection and conversational follow-up generation remain future work.

Review the name, instructions, model, authority, resources, budget, credential labels and optional first task. Save the draft or approve its current revision. A draft allocates a chat and metadata, but no computer. Setup approval records the active instructions and spending ceiling in the same transaction.

The driver allocates a private overlay, starts or restores the computer and installs its executor. It saves each step before and after its side effect. Safe failures get three total attempts by default. Waiting for the owner consumes no attempt. An unknown outcome stops retries.

When setup becomes ready, one transaction saves its start claim, checklist message and first task. Restart cannot lose the first task between a claim and queue insertion. A general role produces a ready message and no invented task.

The server registers each ready bot with its own runtime, workspace, model accounting and computer path. Its chat uses that runtime's queue and event stream. Controls and ceiling responses use the selected bot's route. A held ready bot still registers, so the owner can answer its ceiling request without waking the computer.

## Storage and ownership

Schema v7 marks lifecycle rows in the existing `bot_setup_drafts` table. The body holds the serialized setup. The database revision is the snapshot version. `draft.revision` is the revision that the owner reviews. These numbers have different roles.

Every snapshot write checks the database version. A stale writer fails. Approved drafts cannot change through snapshot writes. Approval creates the definition and accounting record atomically. Completion creates its task atomically. The driver does not use a separate database or task journal.

The existing generic draft helpers remain available for their original storage tests. Lifecycle queries read only rows marked for the setup driver. Do not parse older free-text draft bodies as setup snapshots.

A host profile digest binds approved setup to its image path, runtime root, VM template, resource profile, model and prices. A changed binding stops provisioning for review. Capacity limits and defaults for future drafts do not change that binding. The configured image's sidecar must describe the reviewed disk size. Existing disks and executor identities must match the approved image and bot. Mismatches retain their files and stop setup.

## Configuration

Provisioning is opt-in. Add a `[provisioning]` section to the private config. Do not put private paths, model endpoints or credentials in Git.

| Section | Required fields |
| --- | --- |
| `provisioning` | `base`, `runtime_root`, `max_bots` |
| `provisioning.template` | The existing VM configuration fields. `bot_id` and `runtime_dir` are template placeholders. The server replaces them per bot. |
| `provisioning.resources` | `cpus`, `memory_mib`, `disk_gib` |
| `provisioning.limits` | `max_cpus`, `max_memory_mib`, `max_disk_gib` |
| `provisioning.accounting` | `currency`, `exponent`, `period`, `policy_version`, `ceiling`, `allowance`, `increase_increment`, `price_version`, `input_per_million`, `output_per_million`, `extra_per_call`, `max_input_tokens`, `max_input_bytes`, `max_output_tokens`, `max_response_bytes`, `completion_token_field`, `request_usage` |

Supply a frozen base produced by the existing `vmproto.py freeze-base` workflow. Keep its sidecar beside it. Set `disk_gib` from the sidecar's `virtual_size`, in GiB. The current harness uses 2 CPUs and 2048 MiB. It cannot apply other CPU or memory recommendations. The model must match the configured agent model and price profile. Unsupported changes fail before provisioning.

Amounts use integer units at the configured decimal exponent. Supply real prices and request bounds for the provider. No monetary default is selected by this document. The owner reviews the proposed allowance, ceiling and period per bot. Period rollover is not automatic.

`max_bots` limits registered managed computers. It does not count sessions or implement awake-computer scheduling. A separately configured legacy bot is outside this count. Capacity expansion and concurrent VM behavior need separate validation. The driver runs provisioning steps serially.

## Authenticated API

All routes below use the existing trusted-proxy owner authentication. Machine event intake exposes none of them.

| Route | Operation |
| --- | --- |
| `GET /api/setup-options` | Reports availability and configured recommendation defaults. |
| `GET /api/setups` | Returns snapshots, checklist, phase and runtime availability. |
| `POST /api/setups` | Accepts `{"job":"Read and summarize supplied documents"}`. Returns a draft and its chat. |
| `POST /api/setups/{id}` | Accepts the current snapshot `version` and edited `draft`. |
| `POST /api/setups/{id}/approve` | Accepts snapshot `version` and reviewed draft `revision`. A repeat changes nothing. |
| `POST /api/setups/{id}/control` | Accepts snapshot `version` and a `command`. Supports pause, retry, attempt limits and explicit unknown-outcome resolution. |
| `GET /api/bots/{id}` | Returns a registered bot's status. |
| `/api/bots/{id}/...` | Uses the existing bot control routes for that bot. |

For example, a pause command is `{"version":3,"command":{"action":"pause","value":true}}`. The command preserves other holds. Requests for an unready bot cannot fall through to the shared chat agent and start work before approval.

## Credential and challenge boundary

Protected credential storage and guest transfer are not implemented. The server advertises `protected_input: false`. The UI accepts labels only. Do not put passwords or codes in job text or labels.

After the system steps, a required credential becomes a durable wait. Its value is never accepted by the ordinary wait-completion route. That route returns an unavailable response for credential waits. This preserves progress without pretending sign-in succeeded.

Phone and CAPTCHA wait states persist. Their real service interaction is not integrated or verified. Completing supported non-credential waits requires the current request identity. Expired answers create a replacement request. Before implementing protected input, follow D29 and inspect the referenced T3 Code mechanism.

## Verification and limits

Tests use real SQLite storage, a scripted model and fake harness operations. They cover restart, stale writes, approval idempotency, an atomic first task, a paused owner wait, held-runtime registration, authentication and image/executor mismatches. These tests do not prove fresh VM provisioning, desktop operation, model recommendation quality or real sign-in.

The web passes type checking, tests and the production build. The existing bundle warning remains. No browser acceptance or accessibility interaction test ran for this screen. See [unattended-progress.md](unattended-progress.md) for exact checks and remaining work.

## Protected input and readiness integration

Protected setup input now uses `POST /api/setups/{id}/secret`. Active-task input uses `POST /api/bots/{id}/secret`, with metadata listed by `GET /api/bots/{id}/secret-requests`. Values bypass ordinary chat. The host requires matching guest receipts before completing delivery or recovering a task response. The task resumes only if that exact task still waits.

Phone and CAPTCHA-style owner responses use `POST /api/setups/{id}/challenge/respond`. An owner answer is not service verification. The task checks status through `owner_help_status` and reports its observed result through `report_help_verification`. Cancellation retains its task binding for recovery. Pause, spending and uncertain-outcome holds still gate work.

Runtime registration now prepares private host directories. The executor setup step also waits for a bounded bare protocol-readiness probe. The synthetic guest fixture models this command explicitly. This is implementation, not a full live acceptance pass. See [the completion plan](setup-e2e-plan.md), [runtime evidence](setup-runtime-acceptance.md) and [owner UI evidence](setup-owner-ui-results.md).

The owner UI checks used seeded waits and a fake guest. Real guest protected delivery and the full synthetic sign-in still require a fresh serial run. [Bare issue 464](https://github.com/mrwick1/bare/issues/464) tracks missing CSS color support observed in the phone view. Deck adds no browser or color fallback.
