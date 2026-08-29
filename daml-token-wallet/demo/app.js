'use strict';

const API = Object.freeze({
  state: '/api/state',
  charge: '/api/charge',
  health: '/healthz',
});

const ui = {};
const model = {
  state: null,
  selectedMandateId: '',
  loading: false,
  submitting: false,
  retry: null,
  latestOperation: null,
  clientOperations: [],
};

document.addEventListener('DOMContentLoaded', () => {
  collectElements();
  bindEvents();
  checkHealth();
  loadState();
});

function collectElements() {
  const ids = [
    'health-badge',
    'refresh-button',
    'checked-at',
    'state-alert',
    'status-live',
    'mandate-select',
    'mandate-status',
    'mandate-remaining',
    'mandate-cap',
    'mandate-spent',
    'mandate-progress',
    'mandate-id',
    'mandate-expiry',
    'mandate-control',
    'allow-list',
    'identity-user',
    'identity-agent',
    'identity-owner',
    'identity-rights',
    'submitter-badge',
    'challenge-guidance',
    'charge-form',
    'amount-input',
    'counterparty-input',
    'counterparty-options',
    'memo-input',
    'form-error',
    'request-id',
    'abandon-button',
    'charge-button',
    'provision-command-block',
    'provision-command',
    'copy-provision',
    'revoke-command-block',
    'revoke-command',
    'copy-revoke',
    'owner-guidance',
    'copy-status',
    'result-panel',
    'result-title',
    'result-status',
    'result-message',
    'result-evidence',
    'audit-count',
    'audit-timeline',
    'operation-log',
    'scope-badge',
    'proof-tests',
    'proof-packages',
    'proof-smoke',
    'source-excerpts',
    'limitations-list',
  ];

  ids.forEach((id) => {
    ui[toCamelCase(id)] = document.getElementById(id);
  });
}

function bindEvents() {
  ui.refreshButton.addEventListener('click', () => {
    checkHealth();
    loadState();
  });

  ui.mandateSelect.addEventListener('change', () => {
    model.selectedMandateId = ui.mandateSelect.value;
    suggestCounterparty(true);
    renderSelectedMandate();
  });

  ui.chargeForm.addEventListener('submit', submitCharge);
  ui.chargeForm.addEventListener('input', updateChargeButtonLabel);
  ui.abandonButton.addEventListener('click', abandonRetry);
  ui.copyProvision.addEventListener('click', () => {
    copyCommand(ui.provisionCommand.textContent, ui.provisionCommand);
  });
  ui.copyRevoke.addEventListener('click', () => {
    copyCommand(ui.revokeCommand.textContent, ui.revokeCommand);
  });
}

async function checkHealth() {
  setHealth('checking', 'API checking');
  try {
    const response = await fetch(API.health, {
      headers: { Accept: 'application/json' },
      cache: 'no-store',
    });
    if (!response.ok) {
      throw new Error(`Health check returned HTTP ${response.status}`);
    }
    setHealth('ok', 'API ready');
  } catch (_error) {
    setHealth('error', 'API unavailable');
  }
}

async function loadState(options = {}) {
  if (model.loading) {
    return;
  }

  model.loading = true;
  ui.refreshButton.disabled = true;
  ui.refreshButton.textContent = 'Refreshing…';
  hideNotice(ui.stateAlert);

  try {
    const response = await fetch(API.state, {
      headers: { Accept: 'application/json' },
      cache: 'no-store',
    });
    const body = await readJson(response);
    if (!response.ok) {
      throw new Error(extractMessage(body, `State request returned HTTP ${response.status}`));
    }
    if (!isRecord(body)) {
      throw new Error('The state endpoint returned an invalid document.');
    }

    model.state = body;
    selectAvailableMandate();
    renderState();
    announce('Ledger evidence refreshed.');
  } catch (error) {
    showNotice(ui.stateAlert, `Could not refresh ledger evidence: ${errorMessage(error)}`);
    announce('Ledger evidence refresh failed.');
  } finally {
    model.loading = false;
    ui.refreshButton.disabled = false;
    ui.refreshButton.textContent = 'Refresh evidence';
    updateChargeAvailability();
  }

  if (options.focusResult && !ui.resultPanel.hidden) {
    ui.resultTitle.focus();
  }
}

function renderState() {
  const state = model.state || {};
  const checkedAt = valueText(state.checkedAt);
  ui.checkedAt.textContent = checkedAt === '—'
    ? 'Check time unavailable'
    : `Checked ${formatTimestamp(state.checkedAt, true)}`;
  ui.scopeBadge.textContent = valueText(state.scope);

  renderIdentity(state.identity);
  renderMandateSelector();
  renderSelectedMandate();
  renderOperationLog();
  renderProof(state.proof);
  renderLimitations(state.limitations);
}

function renderIdentity(identityValue) {
  const identity = isRecord(identityValue) ? identityValue : {};
  setText(ui.identityUser, identity.userId);
  setText(ui.identityAgent, identity.agentParty);
  setText(ui.identityOwner, identity.ownerParty);

  const userLabel = valueText(identity.userId);
  ui.submitterBadge.textContent = userLabel === '—' ? 'Identity unavailable' : `Submitting as ${userLabel}`;

  replaceChildren(ui.identityRights);
  const rights = arrayValue(identity.rights);
  if (rights.length === 0) {
    appendEmpty(ui.identityRights, 'No rights reported by the participant.');
    return;
  }

  rights.forEach((right) => {
    const item = document.createElement('li');
    item.textContent = exactRight(right);
    ui.identityRights.append(item);
  });
}

function renderMandateSelector() {
  const mandates = getMandates();
  replaceChildren(ui.mandateSelect);

  if (mandates.length === 0) {
    const option = document.createElement('option');
    option.value = '';
    option.textContent = 'No mandates available';
    ui.mandateSelect.append(option);
    ui.mandateSelect.disabled = true;
    return;
  }

  mandates.forEach((mandate) => {
    const option = document.createElement('option');
    option.value = valueText(mandate.mandateId, '');
    const status = normaliseStatus(mandate.status);
    option.textContent = `${option.value || 'Unnamed mandate'} · ${status}`;
    option.selected = option.value === model.selectedMandateId;
    ui.mandateSelect.append(option);
  });
  ui.mandateSelect.disabled = false;
}

function renderSelectedMandate() {
  const mandate = getSelectedMandate();
  if (!mandate) {
    renderEmptyMandate();
    renderAuditTimeline(null);
    renderOwnerPanel(null);
    updateChargeAvailability();
    return;
  }

  const status = normaliseStatus(mandate.status);
  setStatus(ui.mandateStatus, status);
  setMetric(ui.mandateCap, mandate.cap);
  setMetric(ui.mandateSpent, mandate.spent);
  setMetric(ui.mandateRemaining, mandate.remaining);
  setText(ui.mandateId, mandate.mandateId);
  ui.mandateExpiry.textContent = formatTimestamp(mandate.expiresAt, true);
  setText(ui.mandateControl, mandate.controlCid);
  renderProgress(mandate);
  renderAllowList(mandate.allowedCounterparties);
  renderCounterpartyOptions(mandate.allowedCounterparties);
  renderAuditTimeline(mandate);
  renderOwnerPanel(mandate);
  renderChallengeMode(mandate);
  updateChargeAvailability();
}

function renderEmptyMandate() {
  setStatus(ui.mandateStatus, 'UNAVAILABLE');
  setMetric(ui.mandateCap, null);
  setMetric(ui.mandateSpent, null);
  setMetric(ui.mandateRemaining, null);
  setText(ui.mandateId, null);
  setText(ui.mandateExpiry, null);
  setText(ui.mandateControl, null);
  ui.mandateProgress.value = 0;
  ui.mandateProgress.max = 1;
  renderAllowList([]);
  renderCounterpartyOptions([]);
  ui.challengeGuidance.hidden = true;
  updateChargeButtonLabel();
}

function renderChallengeMode(mandate) {
  const postRevocation = normaliseStatus(mandate.status) === 'REVOKED';
  ui.challengeGuidance.hidden = !postRevocation;
  updateChargeButtonLabel();
}

function renderProgress(mandate) {
  const cap = decimalNumber(mandate.cap);
  const spent = decimalNumber(mandate.spent);
  if (cap !== null && spent !== null && cap > 0) {
    ui.mandateProgress.max = cap;
    ui.mandateProgress.value = Math.min(Math.max(spent, 0), cap);
    ui.mandateProgress.textContent = `${spent} of ${cap} Amulet spent`;
  } else {
    ui.mandateProgress.max = 1;
    ui.mandateProgress.value = 0;
    ui.mandateProgress.textContent = 'Spend information unavailable';
  }
}

function renderAllowList(value) {
  replaceChildren(ui.allowList);
  const parties = arrayValue(value);
  if (parties.length === 0) {
    appendEmpty(ui.allowList, 'No allowed counterparties reported.');
    return;
  }

  parties.forEach((party) => {
    const item = document.createElement('li');
    item.textContent = valueText(party);
    ui.allowList.append(item);
  });
}

function renderCounterpartyOptions(value) {
  replaceChildren(ui.counterpartyOptions);
  arrayValue(value).forEach((party) => {
    const option = document.createElement('option');
    option.value = valueText(party, '');
    ui.counterpartyOptions.append(option);
  });
  suggestCounterparty(false);
}

function suggestCounterparty(force) {
  const mandate = getSelectedMandate();
  const firstParty = mandate ? arrayValue(mandate.allowedCounterparties)[0] : null;
  if (firstParty !== undefined && firstParty !== null && (force || ui.counterpartyInput.value.trim() === '')) {
    ui.counterpartyInput.value = valueText(firstParty, '');
  }
}

function renderAuditTimeline(mandate) {
  replaceChildren(ui.auditTimeline);
  if (!mandate) {
    ui.auditCount.textContent = '0 records';
    appendEmpty(ui.auditTimeline, 'Select a mandate to inspect its audit history.');
    return;
  }

  const records = [];
  arrayValue(mandate.activationAudits).forEach((record) => {
    records.push({ kind: 'Activation audit', record });
  });
  const chargeAudits = arrayValue(mandate.chargeAudits);
  const statements = arrayValue(mandate.statements);
  if (chargeAudits.length > 0) {
    chargeAudits.forEach((record, index) => {
      const combined = isRecord(record) ? { ...record } : { value: record };
      if (statements[index] !== undefined) {
        combined.statement = statements[index];
      }
      records.push({ kind: 'Committed charge audit', record: combined });
    });
  } else {
    statements.forEach((record) => {
      records.push({ kind: 'Committed charge audit', record });
    });
  }
  arrayValue(mandate.revocationAudits).forEach((record) => {
    records.push({ kind: 'Revocation audit', record });
  });

  records.sort((left, right) => timestampOf(left.record) - timestampOf(right.record));
  ui.auditCount.textContent = `${records.length} ${records.length === 1 ? 'record' : 'records'}`;

  if (records.length === 0) {
    appendEmpty(ui.auditTimeline, 'No durable audit contracts were reported for this mandate.');
    return;
  }

  records.forEach(({ kind, record }) => {
    const item = document.createElement('li');
    item.className = 'timeline-item';

    const heading = document.createElement('div');
    heading.className = 'timeline-heading';
    const title = document.createElement('h3');
    title.textContent = kind;
    const time = document.createElement('time');
    time.className = 'timeline-time';
    const rawTime = firstPresent(record, ['recordedAt', 'createdAt', 'chargedAt', 'activatedAt', 'revokedAt', 'at']);
    time.textContent = formatTimestamp(rawTime, false);
    if (rawTime) {
      time.dateTime = String(rawTime);
    }
    heading.append(title, time);
    item.append(heading, createRecordDetails(record));
    ui.auditTimeline.append(item);
  });
}

function renderOwnerPanel(mandate) {
  const freshMandate = getMandates().some(isFreshActiveMandate);
  const provisionCommand = 'python3 daml-token-wallet/agent_wallet.py provision';
  ui.provisionCommand.textContent = provisionCommand;
  ui.provisionCommandBlock.hidden = freshMandate;

  const canRevoke = mandate && normaliseStatus(mandate.status) === 'ACTIVE';
  ui.revokeCommandBlock.hidden = !canRevoke;
  if (canRevoke) {
    ui.revokeCommand.textContent = `python3 daml-token-wallet/agent_wallet.py owner-revoke --mandate-id ${shellQuote(valueText(mandate.mandateId, ''))} --reason "Owner ended the demonstration mandate"`;
  } else {
    ui.revokeCommand.textContent = '';
  }

  if (!freshMandate && canRevoke) {
    ui.ownerGuidance.textContent = 'The selected active mandate is not fresh or spendable. Provision a new one, or explicitly revoke the selected record.';
  } else if (!freshMandate) {
    ui.ownerGuidance.textContent = 'No fresh active mandate is available. Provision one with the separate owner identity.';
  } else if (canRevoke) {
    ui.ownerGuidance.textContent = 'A fresh mandate is active. The owner can revoke it independently; the agent cannot block the command.';
  } else {
    ui.ownerGuidance.textContent = 'Select an active mandate to produce an owner revocation command.';
  }
}

function renderOperation(operation) {
  if (!isRecord(operation)) {
    return;
  }

  model.latestOperation = operation;
  ui.resultPanel.hidden = false;
  const status = normaliseStatus(operation.status);
  setStatus(ui.resultStatus, status);
  ui.resultMessage.textContent = extractMessage(operation, defaultOperationMessage(status));
  replaceChildren(ui.resultEvidence);

  const fields = [
    ['Request ID', firstPresent(operation, ['requestId'])],
    ['Mandate ID', firstPresent(operation, ['mandateId'])],
    ['Update ID', firstPresent(operation, ['updateId', 'ledgerUpdateId'])],
    ['Charge audit CID', firstPresent(operation, ['chargeAuditCid', 'auditCid'])],
    ['Successor mandate CID', firstPresent(operation, ['successorCid', 'successorMandateCid', 'nextMandateCid'])],
    ['Result code', firstPresent(operation, ['errorCode', 'code'])],
    ['Evidence source', firstPresent(operation, ['evidenceSource'])],
    ['Statement', firstPresent(operation, ['statement'])],
    ['Transfer kind', firstPresent(operation, ['registryTransferKind'])],
    ['Disclosed contracts', firstPresent(operation, ['disclosedContractCount'])],
    ['Receiver Holding CIDs', firstPresent(operation, ['receiverHoldingCids'])],
    ['Submitted as', firstPresent(operation, ['submittedAs'])],
    ['Checked at', firstPresent(operation, ['completedAt', 'recordedAt', 'createdAt'])],
  ];

  fields.forEach(([label, value]) => {
    if (value === undefined || value === null || value === '') {
      return;
    }
    const wrapper = document.createElement('div');
    const term = document.createElement('dt');
    const detail = document.createElement('dd');
    term.textContent = label;
    detail.textContent = label.endsWith('at') ? formatTimestamp(value, true) : valueText(value);
    if (label.includes('ID') || label.includes('CID') || label === 'Result code') {
      detail.className = 'mono';
    }
    wrapper.append(term, detail);
    ui.resultEvidence.append(wrapper);
  });

  if (ui.resultEvidence.childElementCount === 0) {
    const wrapper = document.createElement('div');
    const term = document.createElement('dt');
    const detail = document.createElement('dd');
    term.textContent = 'Evidence';
    detail.textContent = 'No safe evidence fields were returned.';
    wrapper.append(term, detail);
    ui.resultEvidence.append(wrapper);
  }
}

function renderOperationLog() {
  replaceChildren(ui.operationLog);
  const remote = arrayValue(model.state && model.state.operations);
  const combined = deduplicateOperations([...model.clientOperations, ...remote]);
  const rejected = combined.filter((operation) => {
    const status = normaliseStatus(operation && operation.status);
    return status === 'REJECTED' || status === 'UNCERTAIN';
  });

  if (rejected.length === 0) {
    appendEmpty(ui.operationLog, 'No rejected or uncertain operations reported.');
    return;
  }

  rejected
    .sort((left, right) => timestampOf(right) - timestampOf(left))
    .forEach((operation) => {
      const status = normaliseStatus(operation.status);
      const item = document.createElement('li');
      item.className = `operation-item status-border-${status.toLowerCase()}`;
      const heading = document.createElement('div');
      heading.className = 'operation-heading';
      const title = document.createElement('h3');
      title.textContent = `${status}: ${valueText(firstPresent(operation, ['errorCode', 'code']), 'No error code')}`;
      const time = document.createElement('time');
      time.className = 'operation-time';
      const rawTime = firstPresent(operation, ['completedAt', 'recordedAt', 'createdAt']);
      time.textContent = formatTimestamp(rawTime, false);
      if (rawTime) {
        time.dateTime = String(rawTime);
      }
      heading.append(title, time);

      const summary = document.createElement('p');
      summary.className = 'proof-item-detail';
      summary.textContent = extractMessage(operation, defaultOperationMessage(status));
      const details = createRecordDetails(operation, new Set(['status', 'message', 'error', 'completedAt', 'recordedAt', 'createdAt']));
      item.append(heading, summary, details);
      ui.operationLog.append(item);
    });
}

function renderProof(proofValue) {
  const proof = isRecord(proofValue) ? proofValue : {};
  renderProofList(ui.proofTests, proof.tests, 'No test evidence reported.');
  renderProofList(ui.proofPackages, proof.packages, 'No package evidence reported.');
  renderProofList(ui.proofSmoke, proof.recordedSmoke, 'No recorded smoke evidence reported.');
  renderSourceExcerpts(proof.sourceExcerpts);
}

function renderProofList(container, value, emptyMessage) {
  replaceChildren(container);
  const entries = proofEntries(value);
  if (entries.length === 0) {
    appendEmpty(container, emptyMessage, 'p');
    return;
  }

  entries.forEach((entry, index) => {
    const item = document.createElement('article');
    item.className = 'proof-item';
    const title = document.createElement('p');
    title.className = 'proof-item-title';
    title.textContent = entry.title || `Evidence ${index + 1}`;
    const detail = document.createElement('p');
    detail.className = 'proof-item-detail';
    detail.textContent = entry.detail || 'Reported without additional detail.';
    item.append(title, detail);
    container.append(item);
  });
}

function renderSourceExcerpts(value) {
  replaceChildren(ui.sourceExcerpts);
  const excerpts = arrayValue(value);
  if (excerpts.length === 0) {
    appendEmpty(ui.sourceExcerpts, 'No source excerpts reported.', 'p');
    return;
  }

  excerpts.forEach((excerpt, index) => {
    const record = isRecord(excerpt) ? excerpt : { code: excerpt };
    const details = document.createElement('details');
    details.className = 'source-excerpt';
    const summary = document.createElement('summary');
    const title = document.createElement('span');
    const meta = document.createElement('span');
    meta.className = 'source-meta';
    title.textContent = valueText(firstPresent(record, ['title', 'name', 'choice']), `Source excerpt ${index + 1}`);
    const file = valueText(firstPresent(record, ['file', 'path']), 'TokenMandate.daml');
    const suppliedLines = firstPresent(record, ['lines', 'lineRange']);
    const startLine = firstPresent(record, ['startLine']);
    const endLine = firstPresent(record, ['endLine']);
    const lines = suppliedLines !== undefined
      ? valueText(suppliedLines)
      : startLine !== undefined && endLine !== undefined
        ? `lines ${valueText(startLine)}–${valueText(endLine)}`
        : 'exact lines reported by service';
    meta.textContent = `${file} · ${lines}`;
    summary.append(title, meta);

    const pre = document.createElement('pre');
    const code = document.createElement('code');
    code.textContent = valueText(firstPresent(record, ['source', 'code', 'text', 'excerpt']), 'Source text unavailable.');
    pre.append(code);
    details.append(summary, pre);
    ui.sourceExcerpts.append(details);
  });
}

function renderLimitations(value) {
  replaceChildren(ui.limitationsList);
  const limitations = arrayValue(value);
  if (limitations.length === 0) {
    appendEmpty(ui.limitationsList, 'No limitations were reported by the service.');
    return;
  }
  limitations.forEach((limitation) => {
    const item = document.createElement('li');
    item.textContent = isRecord(limitation)
      ? valueText(firstPresent(limitation, ['text', 'message', 'limitation']), compactJson(limitation))
      : valueText(limitation);
    ui.limitationsList.append(item);
  });
}

async function submitCharge(event) {
  event.preventDefault();
  if (model.submitting) {
    return;
  }

  hideNotice(ui.formError);
  const mandate = getSelectedMandate();
  const payloadWithoutId = {
    mandateId: mandate ? valueText(mandate.mandateId, '') : '',
    counterparty: ui.counterpartyInput.value.trim(),
    amount: ui.amountInput.value.trim(),
    memo: ui.memoInput.value.trim(),
  };

  const validation = validateCharge(payloadWithoutId);
  if (validation) {
    showNotice(ui.formError, validation);
    return;
  }

  let requestId;
  if (model.retry && sameCharge(model.retry.payload, payloadWithoutId)) {
    requestId = model.retry.requestId;
  } else {
    requestId = createUuid();
    model.retry = null;
  }
  const payload = { requestId, ...payloadWithoutId };
  ui.requestId.textContent = requestId;

  setSubmitting(true);
  try {
    const csrfToken = valueText(model.state && model.state.csrfToken, '');
    if (!csrfToken) {
      throw new SubmissionConfigurationError('State did not include a CSRF token. Refresh the evidence before submitting.');
    }

    const response = await fetch(API.charge, {
      method: 'POST',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
        'X-D1-CSRF': csrfToken,
      },
      body: JSON.stringify(payload),
    });
    const body = await readJson(response);
    const operation = operationFromResponse(body, response.ok, requestId, payload.mandateId);

    if (!isRecord(operation)) {
      throw new SubmissionProtocolError(`Charge endpoint returned HTTP ${response.status} without an operation document.`);
    }

    if (!operation.requestId) {
      operation.requestId = requestId;
    }
    if (!operation.mandateId) {
      operation.mandateId = payload.mandateId;
    }
    if (!operation.status) {
      operation.status = response.ok ? 'COMMITTED' : 'REJECTED';
    }

    model.retry = null;
    model.clientOperations.unshift(operation);
    renderOperation(operation);
    renderOperationLog();
    resetRequestDisplay();
    announce(`Charge operation ${normaliseStatus(operation.status).toLowerCase()}.`);
    await loadState({ focusResult: true });
  } catch (error) {
    if (error instanceof SubmissionConfigurationError || error instanceof SubmissionProtocolError) {
      const rejected = {
        requestId,
        mandateId: payload.mandateId,
        status: 'REJECTED',
        errorCode: error instanceof SubmissionConfigurationError ? 'CLIENT_CONFIGURATION' : 'INVALID_RESPONSE',
        message: error.message,
        recordedAt: new Date().toISOString(),
      };
      model.retry = null;
      model.clientOperations.unshift(rejected);
      renderOperation(rejected);
      renderOperationLog();
      resetRequestDisplay();
      announce('Charge submission rejected before a safe ledger result was available.');
    } else {
      model.retry = { requestId, payload: payloadWithoutId };
      const uncertain = {
        requestId,
        mandateId: payload.mandateId,
        status: 'UNCERTAIN',
        errorCode: 'NETWORK_ERROR',
        message: `No definitive response was received: ${errorMessage(error)}. Retry with the same request ID before changing the request.`,
        recordedAt: new Date().toISOString(),
      };
      model.clientOperations.unshift(uncertain);
      renderOperation(uncertain);
      renderOperationLog();
      renderRetryState();
      announce('The charge outcome is uncertain. The request ID has been retained for an idempotent retry.');
    }
    ui.resultTitle.focus();
  } finally {
    setSubmitting(false);
  }
}

function validateCharge(payload) {
  const mandate = getSelectedMandate();
  if (!mandate) {
    return 'Select an available mandate before submitting.';
  }
  if (!isChargeableStatus(mandate.status)) {
    return `The selected mandate is ${normaliseStatus(mandate.status)}. Select an active mandate or a revoked mandate for the deliberate post-revocation rejection challenge.`;
  }
  if (!payload.counterparty) {
    return 'Enter the exact counterparty Party.';
  }
  if (!payload.amount) {
    return 'Enter an amount. The ledger will enforce the cap.';
  }
  if (!/^\d+(?:\.\d+)?$/.test(payload.amount) || Number(payload.amount) <= 0) {
    return 'Enter a positive decimal amount such as 0.25.';
  }
  if (!payload.memo) {
    return 'Enter a human-audit memo describing the purchase.';
  }
  return '';
}

function setSubmitting(pending) {
  model.submitting = pending;
  ui.chargeButton.disabled = pending || !canSubmitCharge();
  updateChargeButtonLabel();
  ui.refreshButton.disabled = pending || model.loading;
  ui.mandateSelect.disabled = pending || getMandates().length === 0;
}

function updateChargeAvailability() {
  const enabled = canSubmitCharge();
  ui.chargeButton.disabled = model.submitting || !enabled;
  ui.amountInput.disabled = !enabled;
  ui.counterpartyInput.disabled = !enabled;
  ui.memoInput.disabled = !enabled;
}

function canSubmitCharge() {
  const mandate = getSelectedMandate();
  return Boolean(
    model.state
      && valueText(model.state.csrfToken, '')
      && mandate
      && isChargeableStatus(mandate.status),
  );
}

function abandonRetry() {
  model.retry = null;
  resetRequestDisplay();
  announce('The uncertain request was abandoned. The next submission will receive a new request ID.');
}

function renderRetryState() {
  if (!model.retry) {
    resetRequestDisplay();
    return;
  }
  ui.requestId.textContent = model.retry.requestId;
  ui.abandonButton.hidden = false;
  updateChargeButtonLabel();
}

function resetRequestDisplay() {
  ui.requestId.textContent = 'Created on submission';
  ui.abandonButton.hidden = true;
  updateChargeButtonLabel();
}

function updateChargeButtonLabel() {
  const mandate = getSelectedMandate();
  const postRevocation = mandate && normaliseStatus(mandate.status) === 'REVOKED';
  const retryMatches = model.retry && sameCharge(model.retry.payload, currentChargeIntent());
  if (model.submitting) {
    ui.chargeButton.textContent = postRevocation
      ? 'Submitting expected-failure challenge…'
      : 'Submitting once…';
  } else if (retryMatches) {
    ui.chargeButton.textContent = postRevocation
      ? 'Retry same revocation challenge'
      : 'Retry same request';
  } else {
    ui.chargeButton.textContent = postRevocation
      ? 'Test post-revocation rejection'
      : 'Submit charge';
  }
}

function currentChargeIntent() {
  const mandate = getSelectedMandate();
  return {
    mandateId: mandate ? valueText(mandate.mandateId, '') : '',
    counterparty: ui.counterpartyInput.value.trim(),
    amount: ui.amountInput.value.trim(),
    memo: ui.memoInput.value.trim(),
  };
}

async function copyCommand(text, sourceElement) {
  if (!text) {
    return;
  }
  try {
    await navigator.clipboard.writeText(text);
    ui.copyStatus.textContent = 'Command copied to the clipboard.';
  } catch (_error) {
    const selection = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(sourceElement);
    selection.removeAllRanges();
    selection.addRange(range);
    ui.copyStatus.textContent = 'Clipboard access was unavailable. The command is selected; press Command-C to copy it.';
  }
}

function setHealth(state, label) {
  ui.healthBadge.className = `health-badge health-${state}`;
  ui.healthBadge.textContent = label;
}

function selectAvailableMandate() {
  const mandates = getMandates();
  if (mandates.some((mandate) => valueText(mandate.mandateId, '') === model.selectedMandateId)) {
    return;
  }
  const active = mandates.find((mandate) => normaliseStatus(mandate.status) === 'ACTIVE');
  const selected = active || mandates[0];
  model.selectedMandateId = selected ? valueText(selected.mandateId, '') : '';
}

function getMandates() {
  return arrayValue(model.state && model.state.mandates).filter(isRecord);
}

function getSelectedMandate() {
  return getMandates().find((mandate) => valueText(mandate.mandateId, '') === model.selectedMandateId) || null;
}

function isFreshActiveMandate(mandate) {
  if (normaliseStatus(mandate.status) !== 'ACTIVE') {
    return false;
  }
  const remaining = decimalNumber(mandate.remaining);
  if (remaining !== null && remaining <= 0) {
    return false;
  }
  const expiry = Date.parse(mandate.expiresAt);
  return Number.isFinite(expiry) && expiry > Date.now();
}

function isChargeableStatus(value) {
  const status = normaliseStatus(value);
  return status === 'ACTIVE' || status === 'REVOKED';
}

function createRecordDetails(value, excluded = new Set()) {
  const list = document.createElement('dl');
  list.className = 'record-details';
  if (!isRecord(value)) {
    const term = document.createElement('dt');
    const detail = document.createElement('dd');
    term.textContent = 'Value';
    detail.textContent = valueText(value);
    list.append(term, detail);
    return list;
  }

  Object.entries(value).forEach(([key, entry]) => {
    if (excluded.has(key) || entry === undefined || entry === null || entry === '') {
      return;
    }
    const term = document.createElement('dt');
    const detail = document.createElement('dd');
    term.textContent = humaniseKey(key);
    detail.textContent = valueText(entry);
    list.append(term, detail);
  });
  return list;
}

function proofEntries(value) {
  if (value === undefined || value === null) {
    return [];
  }
  if (Array.isArray(value)) {
    return value.map((entry, index) => proofEntry(entry, `Evidence ${index + 1}`));
  }
  if (isRecord(value)) {
    return Object.entries(value).map(([key, entry]) => proofEntry(entry, humaniseKey(key)));
  }
  return [{ title: 'Evidence', detail: valueText(value) }];
}

function proofEntry(value, fallbackTitle) {
  if (!isRecord(value)) {
    return { title: fallbackTitle, detail: valueText(value) };
  }
  const title = valueText(firstPresent(value, ['title', 'name', 'label', 'test', 'package']), fallbackTitle);
  const preferredDetail = firstPresent(value, ['detail', 'summary', 'result', 'status', 'value', 'sha256']);
  const detail = preferredDetail !== undefined
    ? valueText(preferredDetail)
    : Object.entries(value)
      .filter(([key]) => !['title', 'name', 'label', 'test', 'package'].includes(key))
      .map(([key, entry]) => `${humaniseKey(key)}: ${valueText(entry)}`)
      .join('\n');
  return { title, detail };
}

function deduplicateOperations(operations) {
  const seen = new Set();
  return operations.filter((operation, index) => {
    if (!isRecord(operation)) {
      return false;
    }
    const key = valueText(
      firstPresent(operation, ['operationId', 'requestId', 'updateId']),
      `anonymous-${index}`,
    );
    if (seen.has(key)) {
      return false;
    }
    seen.add(key);
    return true;
  });
}

function exactRight(value) {
  if (!isRecord(value)) {
    return valueText(value);
  }
  const type = firstPresent(value, ['type', 'kind', 'right']);
  const party = firstPresent(value, ['party', 'partyId']);
  if (type && party) {
    return `${type} ${party}`;
  }
  return compactJson(value);
}

function setStatus(element, statusValue) {
  const status = normaliseStatus(statusValue);
  const known = new Set(['ACTIVE', 'REVOKED', 'EXPIRED', 'AMBIGUOUS', 'COMMITTED', 'REJECTED', 'UNCERTAIN']);
  const cssStatus = known.has(status) ? status.toLowerCase() : 'neutral';
  element.className = `status-pill status-${cssStatus}`;
  element.textContent = status;
}

function setMetric(element, value) {
  const text = valueText(value);
  element.textContent = text;
  if (text === '—') {
    element.dataset.empty = 'true';
  } else {
    delete element.dataset.empty;
  }
}

function setText(element, value) {
  element.textContent = valueText(value);
}

function showNotice(element, message) {
  element.textContent = message;
  element.hidden = false;
}

function hideNotice(element) {
  element.textContent = '';
  element.hidden = true;
}

function appendEmpty(container, message, tag = 'li') {
  const element = document.createElement(tag);
  element.className = 'empty-state';
  element.textContent = message;
  container.append(element);
}

function replaceChildren(element) {
  element.replaceChildren();
}

function announce(message) {
  ui.statusLive.textContent = '';
  window.setTimeout(() => {
    ui.statusLive.textContent = message;
  }, 20);
}

async function readJson(response) {
  const text = await response.text();
  if (!text) {
    return null;
  }
  try {
    return JSON.parse(text);
  } catch (_error) {
    throw new SubmissionProtocolError(`Endpoint returned non-JSON content with HTTP ${response.status}.`);
  }
}

function normaliseStatus(value) {
  const status = valueText(value, 'UNAVAILABLE').toUpperCase();
  return status.replace(/[^A-Z_]/g, '_');
}

function decimalNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function formatTimestamp(value, includeSeconds) {
  if (!value) {
    return '—';
  }
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) {
    return valueText(value);
  }
  return new Intl.DateTimeFormat('en-GB', {
    dateStyle: 'medium',
    timeStyle: includeSeconds ? 'medium' : 'short',
    hour12: false,
  }).format(date);
}

function timestampOf(value) {
  if (!isRecord(value)) {
    return 0;
  }
  const raw = firstPresent(value, ['recordedAt', 'completedAt', 'createdAt', 'chargedAt', 'activatedAt', 'revokedAt', 'at']);
  const parsed = Date.parse(raw);
  return Number.isFinite(parsed) ? parsed : 0;
}

function firstPresent(value, keys) {
  if (!isRecord(value)) {
    return undefined;
  }
  for (const key of keys) {
    if (value[key] !== undefined && value[key] !== null && value[key] !== '') {
      return value[key];
    }
  }
  return undefined;
}

function valueText(value, fallback = '—') {
  if (value === undefined || value === null || value === '') {
    return fallback;
  }
  if (typeof value === 'string') {
    return value;
  }
  if (typeof value === 'number' || typeof value === 'boolean' || typeof value === 'bigint') {
    return String(value);
  }
  return compactJson(value);
}

function compactJson(value) {
  try {
    return JSON.stringify(value);
  } catch (_error) {
    return String(value);
  }
}

function arrayValue(value) {
  return Array.isArray(value) ? value : [];
}

function isRecord(value) {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function humaniseKey(value) {
  return String(value)
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .replace(/[_-]+/g, ' ')
    .replace(/^./, (character) => character.toUpperCase());
}

function extractMessage(value, fallback) {
  if (!isRecord(value)) {
    return fallback;
  }
  const direct = firstPresent(value, ['message', 'detail', 'summary']);
  if (direct !== undefined) {
    return valueText(direct, fallback);
  }
  if (isRecord(value.error)) {
    return valueText(firstPresent(value.error, ['message', 'detail']), fallback);
  }
  return fallback;
}

function operationFromResponse(body, responseOk, requestId, mandateId) {
  if (isRecord(body && body.operation)) {
    return body.operation;
  }
  if (isRecord(body && body.error)) {
    return {
      requestId,
      mandateId,
      status: 'REJECTED',
      evidenceSource: valueText(body.error.evidenceSource, 'SERVICE'),
      code: firstPresent(body.error, ['code', 'errorCode']),
      message: extractMessage(body.error, 'The request was rejected before ledger submission.'),
      correlationId: firstPresent(body.error, ['correlationId']),
      createdAt: new Date().toISOString(),
    };
  }
  if (isRecord(body)) {
    return body;
  }
  if (!responseOk) {
    return {
      requestId,
      mandateId,
      status: 'REJECTED',
      evidenceSource: 'SERVICE',
      code: 'EMPTY_ERROR_RESPONSE',
      message: 'The service rejected the request without a JSON error document.',
      createdAt: new Date().toISOString(),
    };
  }
  return null;
}

function defaultOperationMessage(status) {
  if (status === 'COMMITTED') {
    return 'The ledger committed the charge and its audit atomically.';
  }
  if (status === 'REJECTED') {
    return 'The request was rejected. No successful charge audit was created.';
  }
  if (status === 'UNCERTAIN') {
    return 'The service has no definitive outcome. Retry with the same request ID.';
  }
  return 'The operation returned without a recognised status.';
}

function createUuid() {
  if (typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function sameCharge(left, right) {
  return left
    && right
    && left.mandateId === right.mandateId
    && left.counterparty === right.counterparty
    && left.amount === right.amount
    && left.memo === right.memo;
}

function shellQuote(value) {
  return `'${String(value).replace(/'/g, `'"'"'`)}'`;
}

function toCamelCase(value) {
  return value.replace(/-([a-z])/g, (_match, character) => character.toUpperCase());
}

function errorMessage(error) {
  return error instanceof Error ? error.message : String(error);
}

class SubmissionConfigurationError extends Error {}
class SubmissionProtocolError extends Error {}
