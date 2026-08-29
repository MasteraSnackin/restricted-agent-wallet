# D1 interactive judge console

The judge console is an optional presentation layer over the verified D1
contract. It does not add spending policy in Python. The browser proposes an
amount, counterparty and memo; `TokenMandate.ChargeToken` still decides on the
ledger whether the transaction can commit.

The console is intentionally split into three capabilities:

```text
One-shot provisioning CLI
  participant admin + owner + agent
  creates/checks users and one fresh mandate, writes non-secret config, exits

Browser -> loopback agent server
  fixed d1-agent-user bearer token only
  CanActAs agent; CanReadAs owner
  reads state and submits ChargeToken

Owner terminal
  fixed d1-owner-user only
  consumes the stable control through Revoke
```

There is no HTTP route for participant administration, package upload, Party
allocation, owner commands, revocation or arbitrary Ledger API forwarding.

## Start a fresh demonstration

LocalNet and the production DAR must already be running and installed. From the
repository root:

```bash
python3 daml-token-wallet/agent_wallet.py provision

python3 daml-token-wallet/demo_server.py \
  --host 127.0.0.1 \
  --port 8791 \
  --agent-token-file <(python3 -c \
    'import c8lab; print(c8lab.token("d1-agent-user"))')
```

Open:

```text
http://127.0.0.1:8791/
```

The bearer token travels through a temporary process-substitution file
descriptor. It is not printed in the browser, placed in an argument, written to
the repository or stored in the operations database.

Before serving HTTP, the process checks the token's public LocalNet JWT form and
requires subject `d1-agent-user`; Canton still authenticates the signature. It
then reads that user's exact rights and the live package list using the same
fixed bearer. Startup fails if owner/admin rights are present, if any required
pinned package is missing, or if the test-only mock package is installed.

`provision` performs the privileged setup once and then exits. It:

- enforces the pinned loopback LocalNet endpoints;
- confirms the expected Amulet and production package IDs;
- confirms the test-only mock package is absent;
- creates or verifies the exact least-privilege participant users;
- creates an owner-authored proposal and accepts it as the agent; and
- writes a non-secret `.daml/demo-config.json` containing exact demonstration
  Party IDs, including a known forbidden Party for the judge's attack.

The `.daml` directory is ignored by Git.

## Three-minute judge flow

1. **Show the mandate and identity.**

   Confirm `ACTIVE`, cap `2`, spent `0`, one exact allowed Party and the future
   ledger expiry. Show that the service reports only `CanActAs agent` and
   `CanReadAs owner`, with no owner act-as or participant-admin capability.

2. **Commit one permitted purchase.**

   Enter `0.25`, retain the allowed merchant Party and add a human memo. The
   result must show `COMMITTED`, the ledger update ID, charge-audit CID,
   receiver Holding IDs, direct transfer kind, disclosure count and statement.

3. **Let the judge exceed the cap.**

   Enter `2.00` against the successor mandate. Cumulative spend would be `2.25`.
   The result must show `REJECTED`, evidence source `LEDGER`, code
   `DAML_FAILURE` and `charge would exceed the total cap`.

4. **Let the judge change the Party.**

   Select or paste the known forbidden Party and enter a small amount. The
   result must show a ledger `DAML_FAILURE` with `counterparty is not allowed`.
   The browser is allowed to propose an arbitrary Party specifically so this
   on-ledger assertion can be demonstrated; it cannot choose an identity,
   template, contract, factory, Holding, disclosure or command envelope.

5. **Revoke outside the agent process.**

   In a separate terminal, use the command shown in the owner panel:

   ```bash
   python3 daml-token-wallet/agent_wallet.py owner-revoke \
     --mandate-id <displayed-mandate-id> \
     --reason "Owner ended the demonstration mandate"
   ```

   Refresh the page. The stable control becomes absent and the mandate is
   classified `REVOKED` even though its latest successor contract remains
   active.

6. **Repeat the formerly valid purchase.**

   Submit another small payment to the allowed merchant. The agent server still
   sends the request to `ChargeToken`; the ledger rejects it because the stable
   control CID has been consumed.

7. **Show the evidence.**

   Read the human statement, distinguish the activation/charge/revocation audit
   contracts from the off-ledger rejected-submission log, and open the four exact
   Daml source excerpts.

## HTTP boundary

The server binds only to loopback and serves only:

```text
GET  /
GET  /app.js
GET  /styles.css
GET  /healthz
GET  /api/state
POST /api/charge
```

`POST /api/charge` accepts exactly five strings:

```json
{
  "requestId": "UUIDv4",
  "mandateId": "displayed mandate ID",
  "counterparty": "exact Canton Party ID",
  "amount": "0.25",
  "memo": "Route-risk data subscription"
}
```

The server derives the current mandate CID, owner, agent, administrator,
instrument, Holdings, factory, timestamps, choice context, disclosures,
`userId`, `actAs` and `readAs`. Unknown fields are rejected. Same-origin,
`application/json`, an in-memory CSRF nonce, exact `Host`, bounded request size
and a restrictive Content Security Policy are required.

## Idempotency and operational evidence

Each UUID is persisted before submission in:

```text
daml-token-wallet/.daml/demo-operations.sqlite3
```

The file is created with mode `0600` where supported and is ignored by Git. A
repeated UUID with the same intent returns the recorded result and cannot submit
a second payment. Reusing the UUID with different intent returns a conflict.
Concurrent charge requests are serialised.

If the process stops while a request is pending, that UUID becomes `UNCERTAIN`
and is not automatically resubmitted. This favours preventing a double payment
over guessing after a lost response.

Successful activation, charge and revocation records are ledger contracts.
Rejected Daml transactions cannot leave audit contracts because they roll back;
the SQLite submission log therefore labels them as operational evidence rather
than on-ledger audits. It stores no bearer token, disclosed contract blob or
registry choice context.

## Verified live on 29 August 2026

The complete console path was exercised against the pinned Splice 0.6.8
LocalNet before the final judge mandate was provisioned:

- a `3.0` request against a `2.0` cap was rejected by the ledger with
  `DAML_FAILURE` and `charge would exceed the total cap`;
- an allowed `0.01` Amulet request committed with update ID
  `1220f03274f4e829cb890b66522b8fa5dcd183101aeadb9a82067f790ca4e38492d4`,
  a charge-audit contract, a receiver Holding and five disclosed contracts;
- replaying the same request UUID returned the same recorded update ID without
  submitting a second payment;
- a `0.01` request to the known forbidden Party was rejected by the ledger with
  `counterparty is not allowed`;
- the owner revoked that temporary mandate through the separate CLI; and
- a subsequent allowed-Party request was rejected because the stable control
  contract had been consumed.

The committed transaction, charge-audit contract, receiver Holding and human
statement are the evidence for the `0.01` integration payment. The merchant's
aggregate LocalNet balance had also changed through unrelated activity, so it
is not presented as an isolated `0.01` before/after balance proof. The earlier
adversarial smoke run separately retains exact `0.25` balance-delta evidence.

The first fresh mandate was subsequently used for a complete browser-driven
judge walkthrough:

- `0.25` Amulet committed through the visible form with update ID
  `122033170683171cc6edb102e614ce116778c7a882519da82dda98660873d364de28`,
  a direct transfer, five disclosures, a receiver Holding and an on-ledger
  charge audit;
- a cumulative `2.00` request was rejected with
  `charge would exceed the total cap`, leaving spend at `0.25`;
- a `0.01` forbidden-Party request was rejected with
  `counterparty is not allowed`, again leaving spend unchanged;
- `d1-owner-user` revoked the exact displayed control through the separate CLI
  with update ID
  `1220ef5f860916501e0c52d54a0c575caf4eaa4a283aee210557cd49311a97eda130`;
  and
- the same browser then submitted a formerly valid `0.01` allowed-Party
  request, which the ledger rejected with `CONTRACT_NOT_FOUND` for the consumed
  stable control.

That walkthrough exposed and fixed one presentation defect: revoked mandates
were initially rendered with a disabled form even though the documented flow
requires a deliberate post-revocation rejection. `REVOKED` now enables only
that clearly labelled expected-failure challenge; `EXPIRED` and `AMBIGUOUS`
remain blocked.

The recording captured this fresh, untouched mandate after the walkthrough:

```text
mandate ID  d1-demo-20260829T130525Z-1ffc98
status      ACTIVE
cap         2.0 Amulet
spent       0.0 Amulet
remaining   2.0 Amulet
expiry      2026-08-29T15:05:25Z
```

That mandate was ephemeral LocalNet state with a fixed expiry. Its identifiers
are retained as dated evidence only; this document does not claim it is still
active.

This is LocalNet evidence only. It does not establish DevNet or production
deployment, production authentication, an LLM integration or an MCP server.

## Tests

```bash
python3 -m unittest discover \
  -s daml-token-wallet/tests \
  -p 'test_*.py' \
  -v
```

These tests cover the loopback boundary, exact JWT subject and user rights,
live package checks, exact production-package pinning, strict request schema,
decimal handling, persistent idempotency, ledger-rejection projection,
revoked-state classification, security headers and absence of owner/generic
ledger routes. They supplement rather than replace the 59-transaction Daml
suite.

## Honest limits

- The service is for the loopback Splice 0.6.8 LocalNet only. It is not a DevNet
  or production deployment.
- LocalNet's known `unsafe` HS256 secret can mint development tokens. The
  fixed-token process design demonstrates role discipline, not production key
  custody.
- The cap covers requested transfer principal, not separate traffic or token
  fees.
- The registry can make preparation succeed or fail, but Daml reconstructs the
  security-sensitive transfer fields and remains the policy authority.
- The memo is agent-authored text and must not contain prompts, credentials or
  secrets.
- No LLM, MCP server or autonomous reasoning is claimed. This is a restricted
  agent-identity transaction console.
