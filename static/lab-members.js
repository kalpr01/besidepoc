/* Lab · test members, Lithic association, delegated cards (create / test / remove),
 * and the W4 Level 1 / Level 2 rule-evaluation view. Sandbox only. */
(() => {
'use strict';
const $ = id => document.getElementById(id);
const el = (tag, cls, text) => { const x = document.createElement(tag); if (cls) x.className = cls; if (text !== undefined && text !== null) x.textContent = String(text); return x; };
const money = n => typeof n === 'number' ? (n / 100).toLocaleString('en-US', { style: 'currency', currency: 'USD' }) : '—';
const merchantName = id => ({ GROCERY_101: 'Grocery store', TICKETS_303: 'Ticket seller', ELECTRONICS_202: 'Electronics store' })[id] || id;
const api = async (path, body) => {
  const r = await fetch(path, { method: body === undefined ? 'GET' : 'POST', headers: { 'Content-Type': 'application/json' }, cache: 'no-store', ...(body !== undefined ? { body: JSON.stringify(body) } : {}) });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw Error(typeof d.detail === 'string' ? d.detail : JSON.stringify(d.detail || d));
  return d;
};
const cents = v => { const n = Number(v); if (!Number.isFinite(n) || n < 0 || n > 100000) throw Error('Enter a valid dollar amount'); return Math.round(n * 100); };
const b64 = x => btoa(String.fromCharCode(...new Uint8Array(x)));
const keys = new Map(); // Flow A agent private keys live only in this tab
let busy = false;

const style = el('style');
style.textContent = `
.lm-table{width:100%;border-collapse:collapse;font-size:13px}.lm-table th,.lm-table td{text-align:left;padding:7px 8px;border-bottom:1px solid #2d4a46;vertical-align:top}
.lm-table th{font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:#9cc7bf}
.lm-pill{display:inline-block;padding:2px 8px;border-radius:999px;font-size:11px;font-weight:700;background:#24403c;color:#cdeee7}
.lm-pill.ok{background:#1d4d3c;color:#9ff0c9}.lm-pill.bad{background:#5a2626;color:#ffc2c2}.lm-pill.skip{background:#333;color:#bbb}.lm-pill.warn{background:#5a4a20;color:#ffe3a3}
.lm-actions{display:flex;gap:6px;flex-wrap:wrap}.lm-actions button{padding:6px 10px;font-size:12px}
.lm-buy{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-top:6px}.lm-buy input{width:90px;padding:6px}
.lm-layers{display:grid;grid-template-columns:1fr 1fr;gap:14px}@media(max-width:800px){.lm-layers{grid-template-columns:1fr}}
.lm-layer{border:1px solid #3a6960;border-radius:12px;padding:12px}.lm-layer h3{margin:0 0 8px;font-size:15px}
.lm-rule{display:grid;grid-template-columns:52px 1fr auto;align-items:start;gap:8px;padding:7px 0;border-top:1px solid #24403c;font-size:13px}.lm-rule:first-of-type{border-top:0}
.lm-rule small{display:block;color:#9cc7bf;margin-top:2px}.lm-rule.decisive{background:rgba(255,120,120,.08);border-radius:8px;padding-left:6px}
.lm-verdict{margin:0 0 12px;padding:10px 12px;border-radius:10px;font-weight:700}.lm-verdict.ok{background:#173f33;color:#a8f5d0}.lm-verdict.bad{background:#4a2020;color:#ffd0d0}`;
document.head.append(style);

function feedback(msg, bad = false) { const n = $('lm-feedback'); if (!n) return; n.textContent = msg; n.className = 'notice ' + (bad ? 'bad' : 'ok'); }
async function run(fn) {
  if (busy) return; busy = true;
  const buttons = [...document.querySelectorAll('#lm-panel button')]; const was = buttons.map(b => b.disabled);
  buttons.forEach(b => b.disabled = true);
  try { await fn(); } catch (e) { feedback(e.message, true); }
  finally { busy = false; buttons.forEach((b, i) => b.disabled = was[i]); }
}

// ------------------------------------------------------------------ members
async function loadMembers() {
  const r = await api('/api/lab/members');
  const host = $('lm-members'); host.replaceChildren();
  const t = el('table', 'lm-table'); const head = el('tr');
  for (const h of ['Member', 'Funding arrangement', 'Lithic parent card', 'Delegated cards', '']) head.append(el('th', null, h));
  t.append(head);
  for (const m of r.members) {
    const tr = el('tr');
    const who = el('td'); who.append(el('b', null, m.name), el('br'), el('small', null, m.id + ' '));
    if (m.active) who.append(el('span', 'lm-pill ok', 'ACTIVE'));
    tr.append(who,
      el('td', null, m.arrangement_id ? m.arrangement_id + ' · ' + (m.ledger_mode || '') : 'Not set up'),
      el('td', null, m.parent_card_linked ? '•••• ' + (m.parent_card_last_four || '????') : 'Not associated'),
      el('td', null, m.active_cards + ' active (' + m.lithic_cards + ' on Lithic)' + (m.removed_cards ? ' · ' + m.removed_cards + ' removed' : '')));
    const act = el('td'); const box = el('div', 'lm-actions');
    if (m.active && m.arrangement_id) { const f = el('button', 'secondary', 'Bring available to…'); f.type = 'button'; f.onclick = () => { const v = prompt('Bring ' + m.name + "'s available balance to ($). Repays the settled amount due first, then adds funds.", '500'); if (v === null) return; run(async () => { const r = await api('/api/member/funding', { kind: 'RESTORE', target_available_cents: cents(v), reference: 'lab-' + crypto.randomUUID() }); feedback(r.entries.map(x => (x.kind === 'REPAYMENT' ? 'Repaid ' : 'Added ') + money(x.amount_cents)).join(' + ') + ' for ' + m.name + ' · available now ' + money(r.available_cents) + ' · amount due ' + money(r.amount_due_cents)); }); }; box.append(f); }
    if (!m.active) { const b = el('button', 'secondary', 'Switch to'); b.type = 'button'; b.onclick = () => run(async () => { await api('/api/lab/members/' + encodeURIComponent(m.id) + '/activate', {}); location.reload(); }); box.append(b); }
    act.append(box); tr.append(act); t.append(tr);
    if (m.active) { $('lm-active-name').textContent = m.name; $('lm-active-name-2').textContent = m.name; $('lm-associate').hidden = m.parent_card_linked || !m.arrangement_id; }
  }
  host.append(t);
}

async function findLithicCards(selectId) {
  const r = await api('/api/member/parent/lithic/cards'); const s = $(selectId); s.replaceChildren();
  for (const c of r.cards) { const o = el('option', null, '•••• ' + (c.last_four || '????') + ' · ' + (c.memo || c.type || 'card') + ' · ' + c.state + ' · account •••• ' + String(c.account_token).slice(-5)); o.value = c.card_token; o.dataset.account = c.account_token; s.append(o); }
  if (!r.cards.length) { const o = el('option', null, 'No open cards in your Lithic sandbox'); o.value = ''; s.append(o); }
  feedback(r.cards.length + ' open Lithic sandbox card(s) found.', !r.cards.length);
}

$('lm-lithic').onchange = () => { $('lm-link-fields').hidden = $('lm-lithic').value !== 'LINK'; };
$('lm-find').onclick = () => run(() => findLithicCards('lm-link-card'));
$('lm-assoc-find').onclick = () => run(() => findLithicCards('lm-assoc-card'));
$('lm-assoc-go').onclick = () => run(async () => {
  const o = $('lm-assoc-card').selectedOptions[0]; if (!o || !o.value) throw Error('Choose “Find Lithic cards” and pick a card first.');
  const r = await api('/api/member/parent/lithic/link-existing', { account_token: o.dataset.account, card_token: o.value });
  feedback('Lithic card •••• ' + (r.last_four || '') + ' associated as the parent card.'); await refreshAll();
});
$('lm-assoc-new').onclick = () => run(async () => { const r = await api('/api/member/parent/lithic/create', {}); feedback('New Lithic parent card •••• ' + (r.last_four || '') + ' created and associated.'); await refreshAll(); });

$('lm-create').onsubmit = e => { e.preventDefault(); run(async () => {
  const mode = $('lm-lithic').value; const body = { display_name: $('lm-name').value.trim(), opening_balance_cents: cents($('lm-balance').value), trip_protection_cents: cents($('lm-trip').value), lithic_parent: mode };
  if (mode === 'LINK') { const o = $('lm-link-card').selectedOptions[0]; if (!o || !o.value) throw Error('Choose “Find Lithic cards” and pick a card to associate.'); body.account_token = o.dataset.account; body.card_token = o.value; }
  const r = await api('/api/lab/members', body);
  try { sessionStorage.setItem('lm-flash', 'Created ' + r.member.name + ' and switched to them.' + (r.warning ? ' ' + r.warning : '')); } catch (_) {}
  location.reload();
}); };

// ------------------------------------------------------------------ delegated cards
let view = null;
async function loadCards() {
  view = await api('/api/member/experience');
  const host = $('lm-cards'); host.replaceChildren();
  const live = view.cards.filter(c => c.status !== 'REVOKED'), removed = view.cards.filter(c => c.status === 'REVOKED');
  if (!live.length) host.append(el('p', 'meta', 'No delegated cards for this member yet.'));
  for (const c of live) {
    const a = view.agents.find(x => x.agent_id === c.agent_id); const row = el('div', 'event');
    const title = el('b', null, (a?.name || c.alias) + ' · ' + (c.lithic_card_token ? 'Lithic •••• ' + (c.lithic_last_four || '****') : 'Demo card (not on Lithic)'));
    const flowA = c.authorization_flow === 'SIGNED_INTENT_REQUIRED';
    row.append(title, el('div', 'meta', [(c.purpose || '') + ' · ' + (c.allowed_merchants || []).map(merchantName).join(', '), 'per purchase ' + money(c.per_transaction_cents) + ' · budget ' + money(c.budget_cents) + ' · used ' + money(c.spent_cents) + ' · auto-approve ≤ ' + money(c.approval_threshold_cents), flowA ? 'Flow A · signed intent' + (keys.has(c.agent_id) ? '' : ' (key not in this tab: create a new card here to test it)') : 'Flow B · card-bound'].join(' | ')));
    const buy = el('div', 'lm-buy'); const amt = el('input'); amt.type = 'number'; amt.min = '0.01'; amt.step = '0.01'; amt.value = '20'; amt.setAttribute('aria-label', 'Test purchase amount for ' + (a?.name || c.alias));
    const local = el('button', 'secondary', 'Test purchase · local'); local.type = 'button'; local.onclick = () => run(() => testPurchase(c, cents(amt.value), 'LOCAL'));
    const lith = el('button', 'secondary', 'Test purchase · Lithic'); lith.type = 'button'; lith.disabled = !c.lithic_card_token; lith.onclick = () => run(() => testPurchase(c, cents(amt.value), 'LITHIC'));
    buy.append(el('span', 'meta', '$'), amt, local, lith);
    if (!c.lithic_card_token && view.parent.parent_card_linked) { const iss = el('button', 'secondary', 'Issue Lithic card'); iss.type = 'button'; iss.onclick = () => run(async () => { const r = await api('/api/member/cards/' + encodeURIComponent(c.id) + '/lithic/create', {}); feedback('Lithic sandbox card •••• ' + r.last_four + ' issued.'); await refreshAll(); }); buy.append(iss); }
    const rm = el('button', 'danger', 'Remove'); rm.type = 'button'; rm.onclick = () => removeCard(c, a); buy.append(rm);
    row.append(buy); host.append(row);
  }
  if (removed.length) {
    const det = el('details'); det.append(el('summary', null, 'Removed cards (' + removed.length + ')'));
    for (const c of removed) { const a = view.agents.find(x => x.agent_id === c.agent_id); det.append(el('div', 'meta', (a?.name || c.alias) + ' · removed ' + String(c.removed_at || '').replace('T', ' ').slice(0, 16) + ' UTC · Lithic card ' + (c.lithic_state || 'not issued') + ' · used ' + money(c.spent_cents))); }
    host.append(det);
  }
}

function removeCard(c, a) {
  const label = a?.name || c.alias;
  if (!confirm('Remove ' + label + '? Future purchases will be declined' + (c.lithic_card_token ? ' and its Lithic sandbox card will be closed (cannot be undone in Lithic).' : '.') + ' History is kept.')) return;
  run(async () => {
    const r = await api('/api/member/cards/' + encodeURIComponent(c.id) + '/revoke', {});
    feedback(label + ' removed.' + (r.lithic_state === 'CLOSED' ? ' Lithic card closed.' : r.lithic_state === 'CLOSE_FAILED' ? ' Closing the Lithic card failed; Occlet still declines it.' : ''), r.lithic_state === 'CLOSE_FAILED');
    await refreshAll();
  });
}

$('lm-purpose').onchange = () => { const t = $('lm-purpose').value === 'TICKETS'; $('lm-cname').value = t ? 'Ticket assistant' : 'Grocery assistant'; $('lm-goal').value = t ? 'Purchase event tickets selected by the member' : 'Buy weekly household groceries'; };
$('lm-card').onsubmit = e => { e.preventDefault(); run(async () => {
  if (!crypto?.subtle) throw Error('Use HTTPS or localhost for the agent key.');
  const max = cents($('lm-max').value), budget = cents($('lm-budget').value), auto = cents($('lm-auto').value);
  if (!max || !budget || !auto) throw Error('Limits must be greater than $0.');
  if (auto > max) throw Error('Auto-approve cannot exceed the per-purchase limit.');
  const purpose = $('lm-purpose').value; const kp = await crypto.subtle.generateKey({ name: 'Ed25519' }, false, ['sign', 'verify']);
  const pub = await crypto.subtle.exportKey('raw', kp.publicKey); const agentId = 'agent-' + crypto.randomUUID().slice(0, 12);
  await api('/api/agents', { agent_id: agentId, name: $('lm-cname').value.trim(), purpose, purchase_goal: $('lm-goal').value.trim(), public_key_b64: b64(pub), registered_per_transaction_cents: max, registered_budget_cents: budget, registered_approval_threshold_cents: auto });
  keys.set(agentId, kp.privateKey);
  const card = await api('/api/member/cards', { agent_id: agentId, per_transaction_cents: max, budget_cents: budget, approval_threshold_cents: auto, allowed_merchants: [purpose === 'TICKETS' ? 'TICKETS_303' : 'GROCERY_101'], authorization_flow: $('lm-flow').value });
  let msg = 'Delegated card created for ' + $('lm-active-name').textContent + '.';
  if ($('lm-on-lithic').checked) {
    if (!view?.parent?.parent_card_linked) msg += ' Not issued on Lithic: associate a Lithic parent card first.';
    else { try { const r = await api('/api/member/cards/' + encodeURIComponent(card.id) + '/lithic/create', {}); msg += ' Lithic sandbox card •••• ' + r.last_four + ' issued.'; } catch (err) { msg += ' Lithic issuance failed: ' + err.message; } }
  }
  feedback(msg); await refreshAll();
}); };

async function testPurchase(card, amount, route) {
  const merchant = (card.allowed_merchants || ['GROCERY_101'])[0];
  if (card.authorization_flow === 'CARD_BOUND_NETWORK') {
    if (route === 'LITHIC') return lithicDecision(await api('/api/lithic/simulate', { card_id: card.id, merchant_id: merchant, amount_cents: amount }));
    const r = await api('/api/network/direct/authorize', { card_reference: card.id, merchant_id: merchant, amount_cents: amount, network_reference: 'lab-' + crypto.randomUUID() });
    feedback('Local network authorization: ' + r.decision + ' · ' + (r.reason || 'policy passed'), r.decision !== 'APPROVED'); return refreshAll();
  }
  const key = keys.get(card.agent_id); if (!key) throw Error('This Flow A agent key is not in this browser tab. Create a new Flow A card here to test signed intents.');
  const nonce = crypto.randomUUID(), ts = Date.now();
  const canonical = `${card.agent_id}|${card.id}|${merchant}|${amount}|USD|${nonce}|${ts}`;
  const sig = await crypto.subtle.sign('Ed25519', key, new TextEncoder().encode(canonical));
  let intent = await api('/api/agent/intents', { agent_id: card.agent_id, card_id: card.id, merchant_id: merchant, amount_cents: amount, currency: 'USD', nonce, timestamp_ms: ts, signature_b64: b64(sig) });
  let token = intent.payment_token;
  if (intent.status === 'AWAITING_MEMBER_APPROVAL') {
    if (!confirm(money(amount) + ' is above the auto-approve limit. Approve it as the member?')) { feedback('Left awaiting member approval (see the Beside member page).', true); return refreshAll(); }
    token = (await api('/api/member/intents/' + encodeURIComponent(intent.intent_id) + '/approval', { approved: true })).payment_token;
  }
  if (route === 'LITHIC') return lithicDecision(await api('/api/lithic/simulate', { card_id: card.id, merchant_id: merchant, amount_cents: amount }));
  const r = await api('/api/network/authorize', { payment_token: token, merchant_id: merchant, amount_cents: amount, network_reference: 'lab-' + crypto.randomUUID() });
  feedback('Signed-intent authorization: ' + r.decision + (r.reason ? ' · ' + r.reason : ''), r.decision !== 'APPROVED'); return refreshAll();
}

async function lithicDecision(sim) {
  const t = sim && sim.transaction_token; if (!t) { feedback('Lithic accepted the simulation but returned no transaction token.', true); return; }
  feedback('Sent to Lithic sandbox. Waiting for the ASA decision…');
  for (let i = 0; i < 10; i++) {
    await new Promise(r => setTimeout(r, 1500));
    const v = await api('/api/member/experience');
    const hits = v.transactions.filter(x => x.reference === t || x.reference === 'lithic:' + t);
    const asa = hits.find(x => x.source === 'LITHIC_SANDBOX_ASA') || hits[0];
    if (asa) { const ok = asa.status === 'APPROVED' || asa.status === 'AUTHORIZED'; feedback('Lithic ASA decision: ' + (ok ? 'APPROVED' : asa.status) + ' · ' + money(asa.amount_cents) + (asa.reason ? ' · ' + asa.reason : ''), !ok); return refreshAll(); }
  }
  feedback('No ASA decision yet for Lithic transaction ' + t.slice(0, 8) + '…: check the ASA enrollment.', true);
}

// ------------------------------------------------------------------ W4 Level 1 / Level 2 rule view
window.occletRenderLayers = layers => {
  const host = $('w4-layers'); if (!host || layers === undefined) return;
  host.replaceChildren();
  if (!layers) { host.append(el('p', 'meta', 'No parent-card authorization on this W4 arrangement yet. Attempt the $350 purchase to see both layers.')); return; }
  const fin = layers.final || {}; const ok = fin.decision === 'APPROVED';
  const by = layers.decided_by === 'LEVEL_1' ? 'Level 1 (errand card check)' : layers.decided_by === 'LEVEL_2' ? 'Level 2 (enhanced authorization)' : fin.note ? 'SQL row-lock recheck' : 'Level 1 + Level 2 (every rule passed)';
  const v = el('div', 'lm-verdict ' + (ok ? 'ok' : 'bad'), (ok ? 'APPROVED' : 'DECLINED') + ' · ' + (fin.reason || '') + ' · decided by ' + by + (layers.source ? ' · via ' + (layers.source === 'LITHIC_SANDBOX_ASA' ? 'Lithic ASA' : 'local W4 simulator') : ''));
  host.append(v);
  const i = layers.inputs || {};
  host.append(el('p', 'meta', 'Inputs · purchase ' + money(i.amount_cents) + ' at ' + merchantName(i.merchant_id) + ' · available ' + money(i.available_cents) + ' · protected trip ' + money(i.protected_cents) + ' · yield ' + (i.yield_enabled ? 'ON' : 'OFF') + ' · room ' + money(i.room_cents) + (fin.note ? ' · ' + fin.note : '')));
  const grid = el('div', 'lm-layers');
  for (const [lvl, title, sub] of [[1, 'Level 1 · Errand card check', 'Hard rules in a fixed order against the arrangement and ledger. First failure declines.'], [2, 'Level 2 · Enhanced authorization', 'Competing-intent order and precomputed signals. Runs only after Level 1 passes and can only tighten.']]) {
    const box = el('div', 'lm-layer'); box.append(el('h3', null, title), el('p', 'meta', sub));
    for (const r of layers.rules.filter(x => x.level === lvl)) {
      const row = el('div', 'lm-rule' + (r.reason_code && r.result === 'FAIL' ? ' decisive' : ''));
      const name = el('div'); name.append(el('b', null, r.name), el('small', null, r.detail + (r.reason_code ? ' → ' + r.reason_code : '')));
      row.append(el('span', 'meta', r.id), name, el('span', 'lm-pill ' + (r.result === 'PASS' ? 'ok' : r.result === 'FAIL' ? 'bad' : 'skip'), r.result === 'NOT_REACHED' ? 'NOT REACHED' : r.result));
      box.append(row);
    }
    grid.append(box);
  }
  host.append(grid);
};

async function refreshAll() { await loadMembers(); await loadCards(); }
$('lm-refresh').onclick = () => run(refreshAll);
try { const flash = sessionStorage.getItem('lm-flash'); if (flash) { sessionStorage.removeItem('lm-flash'); feedback(flash, /without a Lithic/.test(flash)); } } catch (_) {}
refreshAll().catch(e => feedback(e.message, true));
})();
