# D1 production package: token-standard AI-agent wallet

This package implements the Cantor8 Daml challenge D1 against the stable Canton
Token Standard V1 interfaces. An owner can authorise a separate agent Party to
pay only exact allow-listed counterparties, in one pinned instrument, before a
ledger deadline and within a lifetime cap. The parties retain a stable,
co-signed control whose explicit revocation choice is owner-only.

The policy check, real token transfer, cumulative-spend update and successful
charge audit happen in one Daml transaction. The Python adapter is plumbing: it
selects visible owner holdings, asks the registry for its opaque context and
disclosed contracts, and submits one root `TokenMandate.ChargeToken` exercise.
It cannot choose a different sender, instrument, administrator, cap, expiry or
allow-list without the Daml transaction failing.

## Package split

- `daml/TokenMandate.daml` is the deployable production contract.
- `agent_wallet.py` is the LocalNet adapter and adversarial smoke runner.
- `THIRD_PARTY.md` records the three pinned stable-interface dependencies.
- `LOCALNET_SMOKE.md` records the latest real-Amulet verification evidence.
- `SUBMISSION.md` is the short judging runbook.
- `DEMO.md` documents the optional interactive judge console and its capability
  split.
- `demo_server.py` is a loopback, fixed-agent HTTP service with no owner or
  administrator route; `demo/` contains its dependency-free page.
- `tests/` verifies the HTTP, fixed-token identity, package-pinning and
  idempotency boundaries.
- `../daml-token-wallet-test/` is a separate test-only package containing mock
  `Holding` and `TransferFactory` implementations. Never upload that DAR to a
  shared participant.

The production package does not depend on the concrete `splice-amulet` package
and does not include `daml-script`.

## On-ledger model

```text
Owner creates TokenMandateProposal
  -> agent accepts the exact policy
     -> stable co-signed TokenMandateControl with owner-only Revoke
     -> co-signed TokenMandate
     -> co-signed TokenActivationAudit

Agent exercises TokenMandate.ChargeToken
  -> fetches the still-active stable control gate
  -> checks ledger expiry, amount, memo, exact Party allow-list and total cap
  -> checks every selected Holding's owner, instrument, lock and amount
  -> exercises TransferFactory_Transfer with sender fixed to the owner
  -> requires a completed direct transfer, not a pending offer
  -> creates the successor TokenMandate and co-signed TokenChargeAudit

Owner exercises TokenMandateControl.Revoke
  -> consumes the one stable gate even after mandate successors exist
  -> creates TokenRevocationAudit
  -> every later charge fails while fetching the archived gate
```

The stable control contract is deliberately not rotated on each payment. An
agent therefore cannot repeatedly make the owner's revocation command stale by
advancing to another mandate successor. If a charge and revocation are ordered
concurrently, at most the charge ordered before revocation can commit; every
transaction ordered after the control is consumed fails.

## Build and test

The checked environment uses Daml SDK 3.4.10 and Java 21. Run these commands
from the repository root:

```bash
(cd daml-token-wallet && daml build)
(cd daml-token-wallet-test && daml build)
(cd daml-token-wallet-test && daml test --all --show-coverage)
```

The current test suite has three scripts and 59 successful ledger
transactions. It covers real mandate policy through a deterministic test-only
factory: exact cap and expiry, allow-list attacks, direct factory bypass,
incorrect holdings and administrator, failed and pending transfer rollback,
stale mandate, stable revocation, unilateral implicit-archive bypass rejection,
post-revocation failure and audit authenticity.

The test package produces the expected warning that it contains templates and
depends on `daml-script`. That is safe because it is isolated and is never
uploaded. The production package builds without that warning.

The optional judge-console tests use only Python's standard library:

```bash
python3 -m unittest discover \
  -s daml-token-wallet/tests \
  -p 'test_*.py' \
  -v
```

## Upload and run on LocalNet

Start the pinned loopback-only Splice 0.6.8 LocalNet first. From the repository
root, upload only the production DAR. The access token is supplied through a
temporary process-substitution file descriptor and is not printed or stored in
the repository.

```bash
daml ledger upload-dar \
  --host localhost \
  --port 2901 \
  --access-token-file <(python3 -c \
    'import c8lab; print(c8lab.token(c8lab.ADMIN))') \
  daml-token-wallet/.daml/dist/cantor8-d1-token-wallet-0.1.2.dar

python3 daml-token-wallet/agent_wallet.py smoke
python3 daml-token-wallet/agent_wallet.py statements
```

For the interactive, stepwise judge flow, create one fresh mandate and start
the agent-only loopback service. The token is read from a process-substitution
file descriptor and is never exposed to the browser:

```bash
python3 daml-token-wallet/agent_wallet.py provision

python3 daml-token-wallet/demo_server.py \
  --host 127.0.0.1 \
  --port 8791 \
  --agent-token-file <(python3 -c \
    'import c8lab; print(c8lab.token("d1-agent-user"))')
```

Open `http://127.0.0.1:8791/`. Owner revocation deliberately remains a separate
Terminal command rather than an agent-server route. See `DEMO.md` for the exact
flow and boundaries.

The smoke runner enforces the default loopback Ledger API and registry ports,
the `scan.localhost` registry host, no registry prefix, no external identity
provider and the expected Splice 0.6.8 Amulet package. It then creates or
validates these exact participant-user rights:

| User | Rights |
|---|---|
| `d1-agent-user` | `CanActAs` agent; `CanReadAs` owner |
| `d1-owner-user` | `CanActAs` owner only |
| `d1-merchant-auditor-user` | `CanReadAs` merchant only |

It refuses to proceed if one of those users has broader rights. In particular,
the agent has neither `CanActAs owner` nor `ParticipantAdmin`. `CanReadAs`
permits holding discovery but provides no signing authority.

The smoke runner then:

1. creates and accepts a cap-2 Amulet mandate for one exact merchant Party;
2. moves 0.25 Amulet through the real registry-selected factory;
3. verifies the merchant received exactly 0.25;
4. proves a cumulative over-cap charge is rejected;
5. proves a different Party is rejected;
6. proves the agent cannot call `TransferFactory_Transfer` directly;
7. proves the agent cannot revoke the control contract;
8. lets the owner consume the stable control; and
9. proves the successor mandate cannot charge after revocation.

`statements` is read-only. It queries the active co-signed charge audits visible
to the agent and renders each audit as a human sentence naming the time, agent,
recipient, amount, memo, exact permission, cumulative spend and expiry. Use
`--mandate-id <id>` to select one run.

## Real-factory privacy boundary

A registry-selected Amulet factory can be private to the DSO. The mandate must
therefore not separately fetch the factory merely to inspect its interface
view: that pre-fetch would require a DSO stakeholder authoriser. Instead, it
uses `TransferFactory_Transfer` as the public token-standard boundary and passes
the pinned `expectedAdmin`. The vetted factory implementation is responsible
for validating that field. Before the exercise, the mandate independently
requires the stored instrument administrator and every input Holding's
administrator to equal the same pinned Party.

This implementation accepts only
`TransferInstructionResult_Completed`. A receiver without an accepted transfer
preapproval would produce a pending result, causing the entire charge,
cumulative-spend update and audit to roll back.

## Security and evidence boundaries

- LocalNet uses the known `unsafe` HS256 development secret. It proves Daml and
  participant-right behaviour under selected identities, not production
  credential security.
- The verified concrete token was LocalNet Amulet from Splice 0.6.8. This is
  not evidence of Cantor8 DevNet package vetting, topology or identity-provider
  configuration.
- The cap covers the requested transfer principal. Canton traffic and any
  separate token fees are not represented by a portable token-standard field
  and are outside this cap.
- Registry disclosures and choice context are untrusted inputs. They can make a
  valid transfer succeed or fail, but they cannot change the policy values
  recomputed by Daml.
- A rejected Daml transaction cannot create an on-ledger failure audit because
  the whole transaction rolls back. The adapter reports the Ledger API error;
  production monitoring must retain correlated completion or participant logs.
- Successful activation, charge and revocation audits are co-signed by owner
  and agent, so neither can create or archive one alone. The stable control is
  also co-signed: its implicit `Archive` requires both parties, while explicit
  `Revoke` remains owner-only and creates the revocation audit.
- The memo is an agent-supplied explanation, not independently verified model
  reasoning. It must not contain prompts, credentials or other secrets.
- No LLM, MCP client or autonomous planner is claimed here. The optional judge
  console is a deterministic restricted-agent submission path, not an LLM. Any
  future model or MCP layer must retain the same identity boundary.
