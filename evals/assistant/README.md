# Personal-assistant acceptance preparation

`manifest.json` fixes 24 functional case IDs, natural-language inputs and observable assertions. `scoring.json` fixes the scoring rules. These describe acceptance scenarios, not completed results. The execution layout contains 32 windows: 24 first rounds and two additional rounds each for A4, C1, C5 and D2. A4, B2, C5 and D2 are the four pilot windows within that total.

Prepare an isolated review bundle without loading configuration, credentials or a provider:

```sh
python -m evals.assistant --output .local/verification/assistant-review --scope synthetic_materials_disclosed --model deepseek-flash
```

The command writes a code/input/dependency freeze and a pending review form. Prepare the 32 isolated fixture directories before reviewing the final form:

```sh
python -m evals.assistant prepare-case --batch .local/verification/assistant-review --all
```

Preparation makes no model request and grants no approval. Each case has its own workspace and durable source/state receipt. Labels and preparation receipts stay outside the model's workspace. D fixtures use disclosed synthetic manual sources in live project mode. D2 to D5 run their baseline through the guarded ResearchReasoner inside the counted window. D6 loads a copied database before installing the assistant link and preserves the source fixture.

For browser and Windows cases, explicitly bind a local driver-only TOML before reviewing approval. Only the browser/hands enable flags and their deterministic backends are accepted. A full credential configuration is rejected here. Readiness checks installed package metadata and exact Chromium executable hashes without launching a browser, application, screenshot or input:

```sh
python -m evals.assistant configure-drivers --batch .local/verification/assistant-review --profile <driver-only-toml> --browser-runtime <installed-playwright-directory>
python -m evals.assistant driver-readiness --batch .local/verification/assistant-review
```

The settings and runtime executable hashes are part of the review freeze. Driver readiness does not prove the desktop is unlocked or that an application has focus; ordinary observations and approval cards enforce those conditions during execution. Replacing an unapproved fixture with `reprepare-case` preserves its old directory under the batch's `retired-fixtures` and creates a new pending review hash. Approved or started batches cannot replace fixtures or drivers.

A new explicit assistant approval must bind the final freeze, all annotations, the model, source scope and a locally reviewed policy. Old research approval documents are rejected. Monetary limits, vendor rates and review receipts stay under ignored `.local/` paths. If code changes before approval, `refresh-freeze --batch ...` can refreeze it while checking that prepared fixture receipts and labels remain unchanged.

`EvaluationGuard` supplies technical admission only. It enforces serial windows, 12 requests per execution, 384 per batch, an output cap of 4,096 tokens, request timeout and execution deadline. Main-chat and research clients retain their original execution owner, including delayed task continuations and reasoner creation. A late host cannot borrow a subsequent window. The input is text only and has a serialized payload byte cap. Reported usage also has an input token cap. Payload bytes are not a tokenizer guarantee. Each request reserves the approved full vendor context through the existing durable `BatchBudget`, with cache discounts ignored. Unknown usage, interruption, stale approval, changed code or exceeded limits block further spending. Completion retries, prompt-mode fallback and alternate clients are unavailable.

`wire_isolated_service` connects one guarded provider to a newly constructed service's main chat and the existing research reasoner factory. It requires the execution's own `cases/<execution-id>/runtime/workspace`, a local stopped service, no relay, embeddings, skills or MCP. The assistant model sees the restricted task tool view; the executor retains its drivers. Uncounted operator/media/coding clients are unavailable. Cached research reasoners retain their execution owner. FixtureReasoner results do not count as real-model research results.

`run-case` sends the prepared natural-language input through the real main chat, waits for domain work and collects inventories, file hashes, timeline events, model reservations and usage. It never answers approval or consent cards for the user. Live mode requires a new approved receipt, all prepared fixtures, an explicit local configuration and its credential-data directory. Credentials are loaded only after the approval preflight. The normal authenticated interface is served on the selected loopback port while the case runs.

```sh
python -m evals.assistant run-case --batch .local/verification/assistant-review --execution-id A4-r1 --live --config <local-config> --model-data <credential-data>
python -m evals.assistant record-verdict --batch .local/verification/assistant-review --execution-id A4-r1 --review <human-review-json>
```

Offline execution requires an explicit `--mock-script`; its results retain `offline_mock=true`. Partial automatic assertions are reported separately from pending semantic, dispatch, browser and native criteria. `record-verdict` requires explicit human review, evidence for every criterion, the result and scoring hashes, and critical-failure checks. It cannot erase failed observable assertions. Neither fixture preparation nor closing an execution window scores a case as passed.

Before cleanup, the runner seals model admission and atomically writes an immutable `execution-evidence.json` checkpoint containing authoritative state, unfinished tasks, requests and reservations. Uvicorn close waits and application lifespan shutdown have bounded waits, including a separate lifespan shutdown after a leaked Windows transport waiter. The final append-only `result.json` records cleanup and callback failures. A request cap or unfinished action cannot be reported with `error=null`. Intentionally pending study checkpoints and preference consent are recorded separately. A closing window interrupted before quiescence remains stopped on restart; cleanup never clears charges or reuses its execution ID.

After exactly four finished pilot windows, `amend-approval --batch ... --receipt <new-approved-receipt>` may apply a new full-phase approval. It retains the same ledger, prior execution IDs, requests and settled charges. Model, rates, context, scope and freeze cannot change. Outstanding usage or a stopped batch requires reconciliation. A crash during receipt publication can be repaired by retrying that exact approved amendment; no allowance resets occur.

C cases require the explicit frozen drivers and retain unverified native/browser criteria as pending. Controlled-page hosting and public download fixture delivery still need manual hand-off; private-address download protections are retained. D5 replays the actual completed change request and its durable outbox payload twice. It records no pass when the real event or eligible notice is absent. D6 contains a rendered historical report explicitly marked as synthetic and unevaluated; it does not count as a new model result. The fixture runner accepts only the disclosed synthetic-material scope; a public-repository scope needs separate fixtures and a new review. Semantic review, real model runs, GUI availability, new-architecture overnight and Windows lock-screen recovery remain distinct acceptance work. This harness does not provide production-wide spending control or a background daily allowance.
