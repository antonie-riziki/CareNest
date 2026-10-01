# CareNest 

CareNest is a Django marketplace that connects domestic workers and employers. This branch adds **CareNest WorkOS**: a persistent agent that manages the engagement lifecycle, with Stellar/Soroban as the settlement and audit layer.

The marketplace, worker/employer flows, USSD app and existing UI are preserved. The agent is a workflow manager, not a chatbot.

## What was added

- Soroban `WorkContract` with an explicit state machine (`CREATED` → `FUNDED` → `WORKING` → `SUBMITTED` → `APPROVAL_PENDING` → `RELEASED` / `DISPUTED` / `CANCELLED`). Escrow cannot be funded or released twice.
- Persistent Omega-compatible agent (`apps/agentic_core`) with memory, tools, audit trail and human approval gates.
- Stellar testnet integration (wallet connect, contract invoke, tx status, RPC event ingestion) plus a clearly labelled **DEMO DATA** fallback.
- Work Passport: verifiable proof of completed work (hash anchored on-chain). Workers are not tokenized.
- Agent Command Center for employers.

## Local run

```bash
cd Care_Nest
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# set DJANGO_DEBUG=true and, optionally, ASI_API_KEY
python3 manage.py migrate
python3 manage.py seed_workos
python3 manage.py runserver 0.0.0.0:8000
```

Demo accounts (password `CareNestDemo1!`):

| Role | Email |
| --- | --- |
| Employer | `sarah@carenest.demo` |
| Worker (Mary) | `mary@carenest.demo` |
| Worker | `jane@carenest.demo` |
| Worker | `john@carenest.demo` |

Open http://127.0.0.1:8000

## Tests

```bash
cd Care_Nest
python3 manage.py test apps.agentic_core
```

Soroban contract tests (requires the Stellar/Soroban 22+ toolchain):

```bash
cd Care_Nest/apps/contracts
cargo test
```

## Demo flow

1. Sign in as Sarah (`sarah@carenest.demo`).
2. Connect a Stellar wallet at `/wallet/connect/` (Freighter, or paste a testnet `G…` public key). Without a live contract id the UI shows **DEMO DATA**.
3. Open **WorkOS Agent** (`/agent/`). Enter: `I need a full-time nanny in Westlands, KES 45,000/month.`
4. The agent parses the requirement, searches CareNest worker profiles, and ranks Mary Wanjiku.
5. Choose Mary → **Prepare engagement**. Terms are hashed off-chain; nothing financial happens.
6. In the Command Center, approve **create agreement**, then **fund escrow**. Each step needs an explicit human approval. The agent never releases funds on its own.
7. Sign in as Mary (`mary@carenest.demo`) → **Engagements** → start work → submit completion.
8. Back as Sarah, approve payment once. The payment transaction hash is shown. A Work Credential is issued.
9. As Mary, open **Work Passport**.

Session persistence: after step 6, reload the app and ask `What is happening with Mary's contract?` The agent answers from `AgentMemory` + ingested events, not from invented state.

USSD: `POST /ussd/callback/` — menu option **5. WorkOS Agent** looks up a real engagement; option **3** plus an id returns live contract status.

## Environment variables

See `Care_Nest/.env.example`. Important:

| Variable | Purpose |
| --- | --- |
| `ASI_API_KEY` | Omega/ASI Alliance inference (same convention as `singnet/Omega` ASICloud). Parsing/explaining only. |
| `OMEGA_LLM_BASE_URL` | Default `https://llm.c.singularitynet.io/v1` |
| `OMEGA_LLM_MODEL` | Default `asi1-mini` |
| `CARENEST_CONTRACT_ID` | Deployed Soroban contract (`C…`). Empty → DEMO DATA. |
| `CARENEST_FORCE_DEMO_MODE` | Force recorded fixtures even if a contract id is set. |
| `STELLAR_DEMO_EMPLOYER_SECRET` / `STELLAR_DEMO_WORKER_SECRET` | Optional **testnet** keys so the guided demo can sign without Freighter. Never mainnet. |
| `DJANGO_SECRET_KEY` | Required in production. |

The agent never logs secrets. Stellar secret seeds are stripped from audit payloads.

## Stellar deployment (testnet)

Requires the current Stellar CLI and a funded testnet account.

```bash
cd Care_Nest/apps/contracts
stellar contract build
stellar contract deploy \
  --wasm target/wasm32v1-none/release/carenest_work_contract.wasm \
  --network testnet \
  --source-account <YOUR_TESTNET_SECRET>
stellar contract invoke \
  --id <CONTRACT_ID> \
  --network testnet \
  --source-account <ARBITER_SECRET> \
  -- initialize --arbiter <ARBITER_G_ADDRESS>
```

Then set `CARENEST_CONTRACT_ID` and restart Django. Native XLM SAC on testnet is `CDLZFC3SYJYDZT7K67VZ75HPJVIEUVNIXF47ZG2FB2RMQQVU2HHGCYSC`.

Pull events:

```bash
python3 manage.py ingest_stellar_events
```

If RPC is down, the UI falls back to **DEMO DATA**. Live activity is never labelled as demo, and demo activity is never labelled live.

## Architecture

- **Django** is the application index (jobs, profiles, agent memory, credentials).
- **Stellar/Soroban** is the source of truth for escrow state and settlement.
- **Omega/ASI** is used only to parse employer language and phrase known facts. Decisions, transitions and financial gates are deterministic (`policies.py`).
- Personal data (names, phones, IDs) stays off-chain. On-chain: wallet addresses, engagement ids, hashes, statuses, timestamps.

Contract events: `agreement_created`, `agreement_funded`, `work_submitted`, `approval_requested`, `agreement_disputed`, `payment_released`, `agreement_cancelled`, `credential_issued`.

## Remaining limitations

- Live testnet requires a deployed contract id, funded wallets and (for browser signing) Freighter. Without those, the flow is fully usable as labelled DEMO DATA.
- The Omega MeTTa/Hyperon runtime is not bundled; CareNest talks to the ASI Alliance OpenAI-compatible endpoint the same way Omega's ASICloud provider does.
- Dispute resolution still needs a CareNest arbiter key (`STELLAR_ARBITER_SECRET`) for the on-chain `resolve_dispute` call.
- Testnet escrow uses a small XLM amount as a stand-in for a KES stablecoin.

## Original product

Worker job map, profiles, courses, USSD and the existing Django templates remain. New screens: `/agent/`, `/agent/engagements/<id>/`, `/passport/`, `/wallet/connect/`.
