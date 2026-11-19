/* Member-facing projection over the same localhost-only Occlet API.
 * Browser signing keys are intentionally ephemeral: they are not in SQL or web storage.
 */
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const money = (cents) => cents == null ? '—' : (cents / 100).toLocaleString('en-US', {style:'currency',currency:'USD'});
  const short = (str) => str ? String(str).slice(0,8) + '…' + String(str).slice(-6) : 'Not linked';
  const fmtPurpose = {GROCERIES:'Groceries',TICKETS:'Event tickets'};
  const pages = ['overview','arrangements','agents','w4','activity'];
  const keys = new Map(); // agent ID -> nonextractable Ed25519 private key in current page session
  let data = {state:null,w4:null,sql:null};
  let pendingPayment = null;
  let lastW4Authorization = null;
  let lastW4Lithic = null;
  let w4Result = {kind:'neutral',title:'Ready for W4',description:'Initialize the W4 scenario to begin.'};
  let currentToast = null;
  function node(tag, className, text){const e=document.createElement(tag);if(className)e.className=className;if(text!==undefined)e.textContent=text;return e;}
  function clear(el){el.replaceChildren()}
  function toast(message,error=false){const el=$('toast');el.textContent=message;el.className='toast'+(error?' error':'');el.hidden=false;clearTimeout(currentToast);currentToast=setTimeout(()=>{el.hidden=true},6500)}
  async function api(path, body){const r=await fetch(path,{method:body===undefined?'GET':'POST',headers:{'Content-Type':'application/json'},...(body!==undefined?{body:JSON.stringify(body)}:{})});let result;try{result=await r.json()}catch{throw Error(`Request ${r.status} from ${path}`)}if(!r.ok){let detail=result.detail||result.message||JSON.stringify(result);if(Array.isArray(detail))detail=detail.map(x=>x.msg).join('; ');throw Error(detail)}return result}
  async function busy(task){const buttons=[...document.querySelectorAll('button')];buttons.forEach(b=>b.disabled=true);try{await task()}catch(e){toast(e.message,true)}finally{buttons.forEach(b=>b.disabled=false);await refresh().catch(e=>toast('Refresh failed: '+e.message,true))}}
  function on(id,event,fn){$(id).addEventListener(event,fn)}
  function go(page){if(!pages.includes(page))page='overview';for(const name of pages){$('page-'+name).hidden=name!==page;const link=document.querySelector(`[data-page="${name}"]`);link?.classList.toggle('selected',name===page)}$('breadcrumb').textContent={overview:'Overview',arrangements:'Accounts & arrangements',agents:'My agents',w4:'W4 · Member-first protection',activity:'Activity & evidence'}[page];if(location.hash!==`#${page}`)history.replaceState(null,'',`#${page}`);window.scrollTo({top:0,behavior:'instant'})}
  document.querySelectorAll('[data-page]').forEach(a=>a.addEventListener('click',e=>{e.preventDefault();go(a.dataset.page)}));
  document.querySelectorAll('[data-go]').forEach(b=>b.addEventListener('click',()=>go(b.dataset.go)));
  window.addEventListener('hashchange',()=>go(location.hash.slice(1)));
  async function refresh(){const state=await api('/api/state');const sql=await api('/api/sql/status').catch(()=>({connected:false}));let w4=null;if(state.parent.ledger_mode==='OCCLET_SQL_LEDGER'){w4=await api('/api/use-cases/w4/status').catch(()=>null)}data={state,sql,w4};render()}
  function makeNode(icon,title,detail){const outer=node('div','node');outer.append(node('div','node-icon',icon));const text=node('div');text.append(node('b',null,title),node('span',null,detail));outer.append(text);return outer}
  function joinTree(container,nodes){clear(container);nodes.forEach((entry,i)=>{if(i)container.append(node('div','connector'));container.append(makeNode(...entry))})}
  function agentCards(){const s=data.state;return s.cards.map(c=>({...c,agent:s.agents.find(a=>a.agent_id===c.agent_id)}))}
  function render(){const s=data.state,p=s.parent,w=data.w4;if(!p)return;
    const sqlOk=data.sql?.connected;const pill=$('db-pill');pill.textContent=sqlOk?'PostgreSQL connected':'SQL not connected';pill.className='pill '+(sqlOk?'good':'warn');
    $('overview-balance').textContent=p.ledger_mode==='OCCLET_SQL_LEDGER'?money(p.available_cents):'Not initialized';
    $('funding-mode-note').textContent=p.ledger_mode==='OCCLET_SQL_LEDGER'?' · PostgreSQL demo ledger':' · Demo funding not yet configured';
    $('overview-cash').textContent=p.ledger_mode==='OCCLET_SQL_LEDGER'?money(p.cash_cents):'—';
    $('overview-holds').textContent=p.ledger_mode==='OCCLET_SQL_LEDGER'?money(p.held_cents):'—';
    $('overview-protected').textContent=w?money(w.protected_cents):'—';
    $('setup-account-banner').hidden=p.enabled&&p.ledger_mode==='OCCLET_SQL_LEDGER';
    $('hero-card').textContent=p.parent_card_last_four?'•••• '+p.parent_card_last_four:'Not linked';
    joinTree($('overview-linkage'),[
      ['♙','Test Member','Member ID · member_demo'],
      ['▣','Funding arrangement',p.id+' · '+(p.status||'UNCONFIGURED')],
      ['▤','Parent card',p.parent_card_last_four?'Lithic sandbox •••• '+p.parent_card_last_four:'No Lithic card linked'],
      ['✦','Delegated instruments',s.cards.length+' virtual card(s) · same funding arrangement']]);
    const arrangements=$('overview-arrangements');clear(arrangements);if(w)arrangements.append(arrangementItem('◈','Trip booking · protected','YIELD OFF · registry protection',money(w.protected_cents),'protected'));for(const c of agentCards())arrangements.append(arrangementItem('✦',c.agent?.name||c.alias,(fmtPurpose[c.purpose]||'Delegated')+' · '+(c.authorization_flow==='SIGNED_INTENT_REQUIRED'?'Signed intent':'Card-bound'),money(c.budget_cents),'budget'));
    if(!w&&!s.cards.length)arrangements.append(node('div','empty','No spending purposes yet. Start W4 or add an agent.'));
    $('fact-arrangement').textContent=p.enabled?p.id:'Not initialized';$('fact-mode').textContent=p.ledger_mode;$('fact-available').textContent=p.ledger_mode==='OCCLET_SQL_LEDGER'?money(p.available_cents):'Not authoritative';$('fact-holds').textContent=p.ledger_mode==='OCCLET_SQL_LEDGER'?money(p.held_cents):'—';$('fact-lithic-account').textContent=p.lithic_account_token||'Not linked';$('fact-card').textContent=p.parent_card_last_four?'•••• '+p.parent_card_last_four+' · '+short(p.lithic_parent_card_token):'Not linked';
    $('parent-status-tag').textContent=p.enabled?p.status:'INACTIVE';$('parent-status-tag').className='tag '+(p.status==='PAUSED'?'warn':'');
    joinTree($('arrangement-tree'),[
      ['♙','Test Member','Owns the funding account'],
      ['▣','Occlet funding arrangement',p.id],
      ['▤','Parent card',p.parent_card_last_four?'Lithic •••• '+p.parent_card_last_four:'Sandbox card not linked'],
      ['◈','Policy arrangements',(w?'Protected trip + ':'')+s.cards.length+' agent delegate(s) · zero independent balances']]);
    clear($('arrangement-list'));if(w)$('arrangement-list').append(arrangementItem('◈','Trip booking','Priority: protected · yield OFF · member-controlled',money(w.protected_cents),'protected'));for(const c of agentCards())$('arrangement-list').append(arrangementItem('✦',c.agent?.name||c.alias,(c.agent?.purchase_goal||c.purpose||'Delegation')+' · '+c.authorization_flow,money(c.budget_cents),'budget'));if(!w&&!s.cards.length)$('arrangement-list').append(node('div','empty','No arrangements configured yet.'));
    renderAgents();renderW4();renderActivity();
  }
  function arrangementItem(icon,title,description,cents,label){const a=node('div','arrangement-item');a.append(node('div','a-icon',icon));const b=node('div');b.append(node('b',null,title),node('p',null,description));const c=node('div','right');c.append(document.createTextNode(cents),node('small',null,label));a.append(b,c);return a}
  function renderAgents(){const list=$('agent-list'),cards=agentCards();$('agent-count').textContent=cards.length+' registered agent'+(cards.length===1?'':'s');clear(list);if(!cards.length)list.append(node('div','empty','No agents yet. Add your first grocery or ticket-purchasing agent.'));
    for(const c of cards){const tile=node('article','agent-tile');const top=node('div','agent-top');top.append(node('div','agent-bubble',c.purpose==='TICKETS'?'♧':'✦'),node('span','tag'+(c.status==='ACTIVE'?'':' warn'),c.status));tile.append(top,node('h3',null,c.agent?.name||c.alias),node('p',null,c.agent?.purchase_goal||'No purchase goal'));
      const fields=node('div','agent-fields');for(const [label,value] of [['Purpose',fmtPurpose[c.purpose]||c.purpose||'—'],['Per transaction',money(c.per_transaction_cents)],['Budget remaining',money(c.remaining_cents)],['Auto approve up to',money(c.approval_threshold_cents)]]){const f=node('div');f.append(node('span',null,label),node('b',null,value));fields.append(f)}tile.append(fields);
      const foot=node('div','agent-footer');foot.append(node('small',null,c.authorization_flow==='SIGNED_INTENT_REQUIRED'?'Signed-intent · '+(keys.has(c.agent_id)?'key available':'key not in this session'):'Network-direct · attributed by card'));const use=node('button','text-button','Test agent →');use.type='button';use.addEventListener('click',()=>{go('agents');$('purchase-card').value=c.id;$('purchase-amount').value='20';syncMerchant()});foot.append(use);tile.append(foot);list.append(tile)}
    const select=$('purchase-card');const previous=select.value;clear(select);for(const c of cards){const opt=node('option',null,(c.agent?.name||c.alias)+' · '+c.alias);opt.value=c.id;select.append(opt)}if(cards.some(c=>c.id===previous))select.value=previous;$('purchase-panel').hidden=!cards.length;if(cards.length && previous!==select.value)syncMerchant();
  }
  function renderW4(){const w=data.w4;for(const [id,val] of [['w4-protect',w&&money(w.protected_cents)],['w4-available',w&&money(w.balance.available_cents)],['w4-free',w&&money(w.free_to_spend_cents)],['w4-cash',w&&money(w.balance.cash_cents)],['w4-holds',w&&money(w.balance.held_cents)],['w4-protected-detail',w&&money(w.protected_cents)],['w4-releases-count',w&&String(w.releases.length)]])$(id).textContent=val||'—';
    $('w4-result-title').textContent=w4Result.title;$('w4-result-message').textContent=w4Result.description;$('w4-outcome-tag').textContent=w4Result.kind==='neutral'?'READY':w4Result.kind.toUpperCase();$('w4-outcome-tag').className='tag'+(w4Result.kind==='declined'?' warn':'');
    const evidence=$('w4-evidence');clear(evidence);if(!w){evidence.append(node('div','empty','Initialize W4 to view decision and financial evidence.'));return}
    const lines=[];for(const d of w.decisions||[])lines.push(['Decision',d.decision,(d.reason||'POLICY_AND_FUNDING_OK')+' · '+money(d.amount_cents)]);for(const r of w.releases||[])lines.push(['Permission released','NO JOURNAL',money(r.amount_cents)+' removed from trip protection']);for(const a of w.balance?.authorizations||[])lines.push(['Ledger authorization',a.status,money(a.amount_cents)+' · '+a.reference]);for(const e of w.lithic_events||[])lines.push(['Lithic event',e.action||e.status||'EVENT',e.webhook_id||e.transaction_token||'Event received']);if(!lines.length)evidence.append(node('div','empty','No W4 purchases or releases yet.'));
    for(const [label,status,details] of lines.slice(0,20)){const row=node('div','evidence-row');row.append(node('b',null,label),node('span',null,status),node('p',null,details));evidence.append(row)}
  }
  function renderActivity(){const s=data.state;const trace=$('activity-traces');const evs=$('activity-events');clear(trace);clear(evs);for(const t of (s.authorization_traces||[]).slice(0,20)){const row=node('div','arrangement-item');const d=node('div');d.append(node('b',null,t.decision+' · '+money(t.amount_cents)),node('p',null,(t.reason||'')+' · '+t.source+' · '+(t.agent_id||'Member parent card')));row.append(d);trace.append(row)}if(!trace.childElementCount)trace.append(node('div','empty','No authorization decisions yet.'));for(const t of (s.events||[]).slice(0,20)){const row=node('div','arrangement-item');const d=node('div');d.append(node('b',null,t.action+' · '+t.result),node('p',null,t.details+' · '+t.at));row.append(d);evs.append(row)}if(!evs.childElementCount)evs.append(node('div','empty','No events yet.'))}
  function cents(id){const amount=Number($(id).value);if(!Number.isFinite(amount)||amount<=0)throw Error('Enter positive dollar amounts');return Math.round(amount*100)}
  function b64(buffer){return btoa(String.fromCharCode(...new Uint8Array(buffer)))}
  async function signedAgentIntent(agentId,card,merchant,amount){const key=keys.get(agentId);if(!key)throw Error('This agent’s signing key existed only in the browser session where it was registered. Register a new agent to demonstrate signed purchases.');const req={agent_id:agentId,card_id:card.id,merchant_id:merchant,amount_cents:amount,currency:'USD',nonce:crypto.randomUUID(),timestamp_ms:Date.now()};const canonical=[req.agent_id,req.card_id,req.merchant_id,req.amount_cents,req.currency,req.nonce,req.timestamp_ms].join('|');req.signature_b64=b64(await crypto.subtle.sign('Ed25519',key,new TextEncoder().encode(canonical)));const intent=await api('/api/agent/intents',req);return {...intent,merchant_id:merchant,amount_cents:amount,card_id:card.id}}
  function syncMerchant(){const card=data.state?.cards.find(c=>c.id===$('purchase-card').value);if(card){$('purchase-merchant').value=card.purpose==='TICKETS'?'TICKETS_303':'GROCERY_101';$('purchase-route').value=card.lithic_card_token?'LITHIC':'LOCAL'}}
  async function finishPurchase(){if(!pendingPayment)throw Error('No pending purchase');const p=pendingPayment;let result;if(p.route==='LITHIC'){result=await api('/api/lithic/simulate',{card_id:p.card_id,merchant_id:p.merchant_id,amount_cents:p.amount_cents});pendingPayment=null;showPurchase('Lithic simulation submitted','Check Activity for the ASA result and Lithic transaction status. Test-only; no real network funds.', 'warning');}
    else{result=await api('/api/network/authorize',{payment_token:p.payment_token,merchant_id:p.merchant_id,amount_cents:p.amount_cents,network_reference:'member_'+crypto.randomUUID()});pendingPayment=null;showPurchase('APPROVED · '+money(p.amount_cents),'Signed agent intent matched; one financial hold on the shared parent account.','ok')}
    $('purchase-actions').hidden=true;return result}
  function showPurchase(title,message,kind){const el=$('purchase-result');el.hidden=false;el.className='member-decision'+(kind==='error'?' decline':kind==='warning'?' warning':'');el.textContent=title+'\n'+message}

  on('refresh','click',()=>busy(async()=>{toast('Member data refreshed')}));on('activity-refresh','click',()=>busy(async()=>toast('Activity refreshed')));on('w4-refresh','click',()=>busy(async()=>{toast('W4 evidence refreshed')}));
  on('open-link-form','click',()=>{$('link-panel').hidden=false});on('close-link-panel','click',()=>{$('link-panel').hidden=true});
  on('link-form','submit',e=>{e.preventDefault();busy(async()=>{const result=await api('/api/member/parent/lithic/link-existing',{account_token:$('link-account').value.trim(),card_token:$('link-card').value.trim()});$('link-panel').hidden=true;toast('Lithic parent card verified and linked')})});
  on('pause-parent','click',()=>busy(async()=>{await api('/api/member/parent/status',{status:'PAUSED'});toast('Parent paused. Delegated cards inherit this restriction.')}));
  on('resume-parent','click',()=>busy(async()=>{await api('/api/member/parent/status',{status:'ACTIVE'});toast('Parent arrangement resumed.')}));
  on('show-agent-form','click',()=>{$('agent-form-panel').hidden=false;$('agent-name').focus()});on('close-agent-form','click',()=>{$('agent-form-panel').hidden=true});
  on('agent-purpose','change',()=>{const ticket=$('agent-purpose').value==='TICKETS';$('agent-name').value=ticket?'Ticket booking assistant':'Grocery assistant';$('agent-goal').value=ticket?'Purchase concert and event tickets':'Purchase weekly household groceries'});
  on('agent-form','submit',e=>{e.preventDefault();busy(async()=>{
    if(!data.state.parent.enabled)throw Error('Initialize your shared parent funding arrangement in W4 before adding agents.');if(!crypto.subtle)throw Error('Use localhost/HTTPS for browser Ed25519 signing');
    const max=cents('agent-max'),budget=cents('agent-budget'),threshold=cents('agent-threshold');if(threshold>max)throw Error('Auto-approval threshold must be at or below max per purchase.');
    const agentId='agent-'+crypto.randomUUID().slice(0,12),purpose=$('agent-purpose').value,merchant=purpose==='TICKETS'?'TICKETS_303':'GROCERY_101';const kp=await crypto.subtle.generateKey({name:'Ed25519'},false,['sign','verify']);const publicKey=await crypto.subtle.exportKey('raw',kp.publicKey);
    const agent=await api('/api/agents',{agent_id:agentId,name:$('agent-name').value.trim(),public_key_b64:b64(publicKey),purpose,purchase_goal:$('agent-goal').value.trim(),registered_per_transaction_cents:max,registered_budget_cents:budget,registered_approval_threshold_cents:threshold});
    keys.set(agentId,kp.privateKey);
    const card=await api('/api/member/cards',{agent_id:agentId,per_transaction_cents:max,budget_cents:budget,approval_threshold_cents:threshold,allowed_merchants:[merchant],authorization_flow:$('agent-flow').value});
    $('agent-form-panel').hidden=true;toast('Agent registered and virtual card '+card.alias+' issued. All spending uses the same parent funding account.');
  })});
  on('purchase-card','change',syncMerchant);
  on('purchase-form','submit',e=>{e.preventDefault();busy(async()=>{
    const card=data.state.cards.find(c=>c.id===$('purchase-card').value);if(!card)throw Error('Select a card');const amount=cents('purchase-amount'),merchant=$('purchase-merchant').value,route=$('purchase-route').value;
    $('purchase-actions').hidden=true;pendingPayment=null;
    if(card.authorization_flow==='CARD_BOUND_NETWORK'){
      if(route==='LITHIC'){if(!card.lithic_card_token)throw Error('Issue this agent’s Lithic sandbox card first');await api('/api/lithic/simulate',{card_id:card.id,merchant_id:merchant,amount_cents:amount});showPurchase('Submitted to Lithic ASA','This is card-bound attribution, not cryptographic proof of the agent. Check Activity for the final decision.','warning')}
      else{const r=await api('/api/network/direct/authorize',{card_reference:card.id,merchant_id:merchant,amount_cents:amount,network_reference:'member_'+crypto.randomUUID()});showPurchase(r.decision+' · '+money(amount),'Attributed using the registered card. Agent signature not present.','ok')}
      return;
    }
    if(route==='LITHIC'&&!card.lithic_card_token)throw Error('Issue a Lithic sandbox card for this agent first');
    const intent=await signedAgentIntent(card.agent_id,card,merchant,amount);pendingPayment={...intent,route,card_id:card.id};
    if(intent.status==='AWAITING_MEMBER_APPROVAL'){$('purchase-actions').hidden=false;$('purchase-approve').hidden=false;$('purchase-complete').hidden=true;showPurchase('Member approval required','The agent signed its payment intent, but exceeded its automatic spending threshold. Review and approve before network authorization.','warning')}
    else{$('purchase-actions').hidden=false;$('purchase-approve').hidden=true;$('purchase-complete').hidden=false;showPurchase('Signed intent verified','Delegation and signature passed. Continue to simulate the separate card authorization.','ok')}
  })});
  on('purchase-approve','click',()=>busy(async()=>{if(!pendingPayment)throw Error('No pending intent');const r=await api(`/api/member/intents/${pendingPayment.intent_id}/approval`,{approved:true});pendingPayment.payment_token=r.payment_token;$('purchase-approve').hidden=true;$('purchase-complete').hidden=false;showPurchase('Member approved','One-time payment permission is now READY for authorization.','ok')}));
  on('purchase-complete','click',()=>busy(async()=>{await finishPurchase()}));
  on('link-agent-lithic','click',()=>busy(async()=>{const cardId=$('purchase-card').value;if(!cardId)throw Error('Select an agent card');const r=await api(`/api/member/cards/${cardId}/lithic/create`,{});toast('Lithic sandbox virtual card linked: •••• '+(r.last_four||'****'))}));
  function showW4(kind,title,description){w4Result={kind,title,description};renderW4()}
  on('overview-start','click',()=>busy(async()=>{const count=data.state.agents.length;if(count&&!window.confirm(`Initialize a fresh W4 account? This clears ${count} registered agent(s).`))return;await api('/api/use-cases/w4/setup',{});keys.clear();pendingPayment=null;lastW4Authorization=null;lastW4Lithic=null;toast('Your member account is initialized. Add an agent from My agents.');}));
  on('w4-init','click',()=>busy(async()=>{const count=data.state.agents.length;if(count&&!window.confirm(`Starting fresh W4 will clear ${count} previously registered agent(s) and their in-memory keys. Continue?`))return;await api('/api/use-cases/w4/setup',{});keys.clear();pendingPayment=null;lastW4Authorization=null;lastW4Lithic=null;showW4('neutral','Ready to test','The SQL account has $500 cash and $300 trip protection. $200 remains free. Try the $350 grocery purchase.');toast('W4 initialized. Your existing Lithic parent card linkage is preserved.')}));
  async function w4Attempt(){const r=await api('/api/use-cases/w4/authorize',{amount_cents:35000,reference:'member_w4_'+crypto.randomUUID()});if(r.authorization_ref)lastW4Authorization=r.authorization_ref;showW4(r.decision==='APPROVED'?'approved':'declined',r.decision+' · '+r.reason,r.decision==='APPROVED'?'One $350 financial hold created in PostgreSQL. Trip protection remains a permission rule.':'The transaction fails the protected trip policy. No hold or financial journal was posted.');toast('W4: '+r.decision+' · '+r.reason,r.decision==='DECLINED')}
  on('w4-attempt','click',()=>busy(w4Attempt));on('w4-retry','click',()=>busy(w4Attempt));
  on('w4-release','click',()=>busy(async()=>{if(!data.w4)throw Error('Initialize W4 first');const r=await api('/api/use-cases/w4/release',{release_cents:15000,release_ref:'member_w4_release_'+crypto.randomUUID()});showW4('approved','Protection updated · No money moved','You released $150 of the trip protection. The trip still protects $150; retry the grocery purchase.');toast('Member permission changed; financial cash unchanged.')}));
  on('w4-settle','click',()=>busy(async()=>{const ref=lastW4Authorization||(data.w4?.balance?.authorizations||[]).find(a=>a.status==='AUTHORIZED'&&String(a.reference||'').startsWith('w4-local:'))?.reference;if(!ref)throw Error('Run a W4 local approval first to create a $350 authorization hold.');await api('/api/demo-ledger/clear',{authorization_ref:ref,settled_cents:34500});showW4('approved','Settled $345 · $5 released','The original $350 hold was cleared for $345. Cash is now $155 with no outstanding hold.');toast('W4 clearing posted to PostgreSQL.')}));
  on('w4-lithic','click',()=>busy(async()=>{if(!data.state.parent.lithic_parent_card_token)throw Error('Link your Lithic sandbox parent card in Arrangements first');const r=await api('/api/member/parent/lithic/simulate',{merchant_id:'GROCERY_101',amount_cents:35000});lastW4Lithic=r.transaction_token;showW4('neutral','Lithic sandbox request submitted','Wait for Lithic ASA to call Occlet. Check the transaction result and the signed event history.');toast('Lithic sandbox simulation submitted.')}));
  on('w4-lithic-status','click',()=>busy(async()=>{if(!lastW4Lithic)throw Error('No Lithic transaction submitted in this browser session.');const r=await api('/api/lithic/transactions/'+encodeURIComponent(lastW4Lithic));showW4(r.result==='APPROVED'?'approved':'declined','Lithic · '+r.result,'Status '+r.status+' · transaction '+short(lastW4Lithic));toast('Lithic transaction status refreshed')}));
  go(location.hash.slice(1));refresh().catch(e=>toast('Could not load member data: '+e.message,true));
})();
