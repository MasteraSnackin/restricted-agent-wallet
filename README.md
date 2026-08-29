# Restricted Agent Wallet

Restricted Agent Wallet is a Cantor8 D1 prototype that lets a software-agent
Party spend an owner's Canton Token Standard asset without receiving the
owner's signing authority. Daml enforces one exact Party allow-list, one
instrument and administrator, a ledger expiry, a cumulative lifetime cap and
an owner-only revocation path.

An allowed payment, cumulative-spend update and co-signed audit record either
commit together or all roll back. The Python components select visible
holdings, obtain registry disclosures and submit the fixed Daml choice; they do
not replace the on-ledger policy.

## Verification status

| Scope | Status |
|---|---|
| Daml build and deterministic contract tests | Verified locally with Daml SDK 3.4.10 and Java 21 |
| Contract suite | 3 scripts; 59 successful test-ledger transactions |
| Python judge-console suite | Verified with Python's standard library |
| Token integration | Verified on loopback-only Splice 0.6.8 LocalNet using Amulet |
| Cantor8 DevNet or production | Not verified or deployed |
| LLM, MCP or autonomous planner | Not included or claimed |

The dated transaction IDs, package hashes, rights and rejection evidence are in
[`daml-token-wallet/LOCALNET_SMOKE.md`](daml-token-wallet/LOCALNET_SMOKE.md).
They record a completed local run; they are not a claim about current DevNet or
production state.

## Repository layout

- `daml-token-wallet/` contains the production Daml contract, LocalNet adapter,
  loopback judge console, tests and evidence notes.
- `daml-token-wallet-test/` contains deterministic test-only mock Token Standard
  interfaces. Build it locally, but never upload its DAR to a shared participant.
- `dars/` contains the three pinned stable Canton Token Standard V1 interface
  DARs used for compilation.
- `third-party-licenses/` contains the Apache-2.0 licence shipped with the pinned
  Splice bundle.
- `c8lab.py` is the dependency-free LocalNet Ledger API and registry helper used
  by `agent_wallet.py`.
- [`DEMO_VIDEO.md`](DEMO_VIDEO.md) identifies and scopes the two-minute release
  video.

## Build and test

Install Daml SDK 3.4.10 and Java 21, then run from the repository root:

```bash
(cd daml-token-wallet && daml build)
(cd daml-token-wallet-test && daml build && daml test --all --show-coverage)
python3 -m unittest discover -s daml-token-wallet/tests -p 'test_*.py' -v
```

On macOS, if several Java versions are installed, select Java 21 for the current
shell with:

```bash
export JAVA_HOME=$(/usr/libexec/java_home -v 21)
```

Building generates `.daml/` directories. The demo service also creates a local
SQLite operations log beneath `.daml/`. Both are deliberately excluded from
version control.

## LocalNet demonstration

This repository does not include or start Splice LocalNet. With the pinned
loopback-only Splice 0.6.8 environment already running, follow:

- [`daml-token-wallet/README.md`](daml-token-wallet/README.md) for package upload
  and the adversarial smoke runner;
- [`daml-token-wallet/DEMO.md`](daml-token-wallet/DEMO.md) for the stepwise browser
  console; and
- [`daml-token-wallet/SUBMISSION.md`](daml-token-wallet/SUBMISSION.md) for the
  judging narrative.

Only the production DAR belongs on a shared participant. The test DAR contains
mock token implementations and is isolated for local contract testing.

## Security and evidence boundary

- LocalNet uses a known development JWT secret. This demonstrates role and
  contract behaviour, not production credential custody.
- The loopback service has a fixed agent identity and no owner, participant-admin,
  package-upload, revocation or arbitrary Ledger API route.
- Rejected Daml transactions roll back completely. Their errors are operational
  evidence, not on-ledger failure-audit contracts.
- Registry context is treated as untrusted input. Security-sensitive transfer
  fields are reconstructed and checked by Daml.
- The cap covers transfer principal, not separate traffic or token fees.
- The memo is agent-authored text, not verified reasoning, and must never contain
  credentials or prompts.

## Licence status

No licence was supplied for the original project code, so this private repository
does not grant a public reuse licence. The bundled third-party Token Standard DARs
come from Splice 0.6.8 and are accompanied by their Apache-2.0 licence. See
[`LICENSE_STATUS.md`](LICENSE_STATUS.md) and
[`daml-token-wallet/THIRD_PARTY.md`](daml-token-wallet/THIRD_PARTY.md).
