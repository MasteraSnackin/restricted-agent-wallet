# Daml challenge D1 judging runbook

## One-sentence pitch

The AI agent can buy from one exact merchant Party without holding the owner's
authority: Daml enforces the cap, Party allow-list, instrument, expiry and a
stable co-signed control with owner-only revocation while atomically moving
real LocalNet Amulet and writing the human audit record.

## Three-minute proof

1. Show `TokenMandate.ChargeToken` in `daml/TokenMandate.daml`: the contract
   fetches the stable control, checks ledger time, amount, exact Party,
   cumulative cap, memo and every input Holding.
2. Show that `sender = owner`, `instrumentId` and `expectedAdmin` come from the
   stored mandate when it exercises the stable token-standard
   `TransferFactory_Transfer` interface.
3. Show that only a completed direct transfer can create the successor mandate
   and co-signed `TokenChargeAudit`; pending or failed results abort everything.
4. Show the participant rights: the agent user has only `CanActAs agent` and
   `CanReadAs owner`, never `CanActAs owner` or `ParticipantAdmin`.
5. Run `python3 daml-token-wallet/agent_wallet.py smoke` from the repository root.
   Point out `registryTransferKind: direct`, five disclosures and the exact
   merchant balance increase.
6. Run `python3 daml-token-wallet/agent_wallet.py statements --mandate-id <id>`
   and read the human statement derived from the co-signed ledger audit.
7. Show the five rejection rows: over cap, forbidden Party, direct factory
   bypass, agent revocation and post-owner-revocation charge.
8. Run the separate Daml test package and show 59 successful test transactions.

## Optional interactive judge console

`DEMO.md` adds a stepwise browser console without changing the Daml policy. Run
`agent_wallet.py provision`, then start `demo_server.py` with a pre-minted
`d1-agent-user` token through a process-substitution file descriptor.

The page lets a judge submit an allowed charge, a cumulative over-cap amount
and an arbitrary different Party through the same agent identity. It shows the
ledger update or exact Daml rejection, the human statement, the operational
submission log and four source excerpts. Owner revocation remains a separate
`d1-owner-user` Terminal command; after refresh, the page classifies the
mandate as revoked and can submit a final charge that the ledger rejects.

The browser cannot supply `userId`, `actAs`, `readAs`, template or contract IDs,
Holdings, factory context, disclosures, endpoints or arbitrary command JSON.
There is no owner, administrator, package or generic-ledger HTTP route.

## What is stronger than the starter

- It moves a real token-standard asset, not only a custom demo value contract.
- The agent cannot bypass the policy through a direct factory call because the
  factory choice requires the owner's authorisation.
- Revocation has one stable contract ID that is not rotated by charges, so the
  agent cannot make an owner revocation command stale by creating successors.
- Activation, successful-charge and revocation audit rows are co-signed,
  preventing either owner or agent from fabricating or archiving them alone.
  The control is also co-signed, so neither party can unilaterally use implicit
  `Archive` to bypass the explicit owner-only, audited `Revoke` path.
- The deployed participant user is least-privilege; authorisation is verified
  at both Party and user-right layers.
- The production and mock test packages are separate, and the mock factory is
  proven absent from LocalNet.

## Human statement

Each `TokenChargeAudit` records the mandate ID, owner, agent, exact
counterparty, administrator, instrument, requested amount, memo, ledger time,
cap, spend before and after, exact allow-list snapshot, expiry and receiver
Holding IDs. The merchant sees its received Holding but not the owner's private
cap or cumulative-spend record.

Failed requests leave no on-ledger audit contract because the complete Daml
transaction rolls back. The smoke adapter surfaces their Ledger API error codes
and messages; a deployed service must retain correlated operational logs.

## Honest boundary

- Verified locally against Splice 0.6.8 Amulet only. DevNet and production
  package vetting, topology, identity provider and multi-participant behaviour
  remain unverified.
- LocalNet's JWT secret is intentionally unsafe and development-only.
- The cap covers requested transfer principal, not separate traffic or token
  fees.
- The registry is required for context and private disclosures, but it is not a
  policy authority; Daml reconstructs every security-sensitive transfer field.
- The memo is agent-authored text, not verified reasoning.
- No LLM, MCP integration or autonomous planner is claimed. Those can submit
  only through the restricted adapter after the ledger boundary is accepted.
