'use strict';

const $ = (selector) => document.querySelector(selector);
const el = (tag, className, text) => { const node = document.createElement(tag); if (className) node.className = className; if (text !== undefined) node.textContent = text; return node; };
const money = (fen) => (Number(fen || 0) / 100).toFixed(2).replace(/\.00$/, '');
const defaultPeople = () => [
  {id: 'p1', name: '阿青', needs: {main: 1, drink: 1, side: 0}, exclude_tags: ['牛肉'], must_tags: [], prefer_tags: ['鸡肉']},
  {id: 'p2', name: '小林', needs: {main: 1, drink: 1, side: 0}, exclude_tags: [], must_tags: ['咖啡'], prefer_tags: []},
  {id: 'p3', name: '小周', needs: {main: 1, drink: 1, side: 0}, exclude_tags: [], must_tags: [], prefer_tags: ['鸡肉']}
];
let people = defaultPeople();
let catalog = null;
let demoRequest = null;
let currentResult = null;
let currentRequest = null;
let revision = 0;
let loading = false;
let nextId = 4;

function announce(text) { $('#result-announcement').textContent = text; }
function setState(text, className = '') { const node = $('#results-state'); node.textContent = text; node.className = `state-pill ${className}`; }
function checkChip(label, checked, onChange, restriction = false) {
  const wrap = el('label', `choice-chip${restriction ? ' restriction' : ''}`);
  const input = el('input'); input.type = 'checkbox'; input.checked = checked;
  input.addEventListener('change', () => { onChange(input.checked); markChanged(); });
  wrap.append(input, el('span', '', label)); return wrap;
}
function editTag(person, field, tag, checked) { person[field] = person[field].filter((value) => value !== tag); if (checked) person[field].push(tag); }
function renderPeople() {
  const list = $('#people-list'); list.replaceChildren();
  people.forEach((person, index) => {
    const card = el('div', 'person-card');
    const top = el('div', 'person-top');
    const nameLabel = el('label', 'sr-only', `第 ${index + 1} 位搭子的姓名`); nameLabel.htmlFor = `name-${person.id}`;
    const input = el('input', 'name-input'); input.id = nameLabel.htmlFor; input.value = person.name; input.maxLength = 20; input.required = true; input.autocomplete = 'off'; input.placeholder = '这位搭子叫什么';
    input.addEventListener('input', () => { person.name = input.value; markChanged(); });
    const remove = el('button', 'remove-person', '×'); remove.type = 'button'; remove.title = '移除这位搭子'; remove.setAttribute('aria-label', `移除第 ${index + 1} 位搭子`); remove.disabled = people.length <= 2;
    remove.addEventListener('click', () => { people = people.filter((value) => value.id !== person.id); renderPeople(); markChanged(); });
    top.append(el('span', 'avatar', String(index + 1).padStart(2, '0')), nameLabel, input, remove);
    const needs = el('div', 'person-row'); needs.append(el('span', 'person-row-title', '需要'));
    [['main','主食'],['drink','饮料'],['side','配餐']].forEach(([key,label]) => needs.append(checkChip(label, !!person.needs[key], (checked) => { person.needs[key] = checked ? 1 : 0; })));
    const tags = el('div', 'person-row'); tags.append(el('span', 'person-row-title', '口味'));
    [['exclude_tags','牛肉','不吃牛肉',true],['exclude_tags','辣','不吃辣',true],['must_tags','咖啡','必须咖啡',true],['prefer_tags','鸡肉','偏好鸡肉',false]].forEach(([field,tag,label,restriction]) => tags.append(checkChip(label, person[field].includes(tag), (checked) => editTag(person,field,tag,checked),restriction)));
    card.append(top,needs,tags); list.append(card);
  });
  $('#add-person').disabled = people.length >= 6 || loading;
}
function markChanged() {
  revision += 1;
  if (currentResult) { renderResults(currentResult, true); setState('需求已变 · 待重算', 'stale'); announce('需求已修改，之前的清单已过期，请重新计算。'); }
  else if (loading) { setState('需求已变 · 待重算', 'stale'); }
}
function collectRequest() {
  return {budget_fen: Math.round(Number($('#budget').value) * 100), strategy: document.querySelector('input[name="strategy"]:checked').value, quote_limit: 8, people: people.map((person) => ({...person,name:person.name.trim(),needs:{...person.needs},exclude_tags:[...person.exclude_tags],must_tags:[...person.must_tags],prefer_tags:[...person.prefer_tags]}))};
}
function normalizeItems(items) {
  if (!items) return [];
  if (Array.isArray(items)) return items.map((item) => typeof item === 'string' ? {name:item,quantity:1} : item);
  return Object.entries(items).map(([id,value]) => typeof value === 'number' ? {id,quantity:value} : {...value,id});
}
function catalogItems() { return Array.isArray(catalog) ? catalog : (catalog?.items || catalog?.offers || []); }
function itemLabel(item) {
  const record = catalogItems().find((value) => value.id === (item.id || item.item_id || item.offer_id));
  return item.name || item.item_name || item.title || record?.name || record?.title || item.id || item.item_id || '餐品';
}
function qty(item) { return Number(item.quantity ?? item.qty ?? item.count ?? 1); }
function normalizeAssignments(candidate) {
  const source = candidate.assignments || [];
  const rows = Array.isArray(source) ? source : Object.entries(source).map(([person_id,items]) => ({person_id,items}));
  return rows.map((row,index) => {
    const personId = row.person_id || row.id || row.person?.id;
    const person = currentRequest?.people.find((value) => value.id === personId) || currentRequest?.people[index];
    return {name:row.person_name || row.name || row.person?.name || person?.name || `搭子 ${index + 1}`, items:normalizeItems(row.components || row.items || row.assigned_items || row.offers || []), total:row.estimated_cost_fen ?? row.total_fen ?? row.estimated_total_fen ?? row.subtotal_fen,extras:normalizeItems(row.extras),personId};
  });
}
function friendlyMessage(message,fallback) { return typeof message==='string' && /[\u3400-\u9fff]/.test(message) ? message : fallback; }
function flattenMessages(unmet) {
  if (!unmet || (Array.isArray(unmet) && !unmet.length)) return [];
  if (Array.isArray(unmet)) return unmet.map((item) => friendlyMessage(typeof item === 'string' ? item : (item.message || item.reason),'有一项必需条件尚未满足，请调整后重算'));
  if (typeof unmet === 'string') return [friendlyMessage(unmet,'有必需条件尚未满足，请调整后重算')];
  return Object.entries(unmet).filter(([,value]) => value?.length || value === true).map(()=> '有必需条件尚未满足，请调整后重算');
}
function planText(candidate,index) {
  const rows = normalizeAssignments(candidate);
  const total=Number(candidate.estimated_total_fen ?? candidate.total_fen ?? 0);
  const remainder=(currentRequest?.budget_fen||0)-total;
  const extraItems=normalizeItems(candidate.extras);
  return ['麦桌搭子 · 多人点餐清单','模拟演示 · 非实时门店价格',`方案 ${index + 1}｜估算总价 ¥${money(total)}${remainder<0?'（估算超预算，供调整参考）':''}`, ...rows.flatMap((row) => [`${row.name}${row.total!==undefined?`（估算 ¥${money(row.total)}）`:''}：${row.items.map((item) => `${itemLabel(item)} × ${qty(item)}`).join('、')}`,...(row.extras.length?[`  其中套餐额外包含：${row.extras.map((item)=>`${itemLabel(item)} × ${qty(item)}`).join('、')}（已计入上述餐品及套餐费用）`]:[])]),...(extraItems.length&&!rows.some((row)=>row.extras.length)?[`套餐额外包含（已计入原套餐费用）：${extraItems.map((item)=>`${itemLabel(item)} × ${qty(item)}`).join('、')}`]:[]),`预算 ¥${money(currentRequest?.budget_fen)}；${remainder>=0?'余量':'超出'} ¥${money(Math.abs(remainder))}`,'实际价格与供应情况以门店为准。'].join('\n');
}
async function copyPlan(candidate,index,button) {
  const text = planText(candidate,index);
  try {
    if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(text);
    else { const field = el('textarea'); field.value = text; field.style.position = 'fixed'; field.style.left = '-9999px'; document.body.append(field); field.select(); const copied = document.execCommand('copy'); field.remove(); if (!copied) throw new Error('copy'); }
    button.firstChild.textContent = '已复制，发给搭子就行'; announce('点餐清单已复制到本机剪贴板。'); setTimeout(() => { if (button.isConnected) button.firstChild.textContent = '复制这一桌的清单'; },2500);
  } catch { button.firstChild.textContent = '复制失败，请重试'; announce('无法访问剪贴板，请再次点击复制。'); }
}
function renderPlan(candidate,index,stale) {
  const card = el('article','plan-card');
  const heading = el('div','plan-card-header'); const headingText = el('div');
  const total = Number(candidate.estimated_total_fen ?? candidate.total_fen ?? 0); const price = el('div','total-price'); price.append(el('span','','¥'),document.createTextNode(money(total))); heading.append(headingText,price);
  const overBudget=total>(currentRequest?.budget_fen||0);
  headingText.append(el('h3','',overBudget ? `方案${index===0?'一':'二'} · 超预算参考` : (candidate.title || (index === 0 ? '方案一 · 推荐这一桌' : '方案二 · 换个搭法'))), el('small','',overBudget ? '估算超过预算 · 调整后重新计算' : (index === 0 ? '根据这桌人的需求生成 · 模拟估算' : '另一种满足需求的组合 · 模拟估算')));
  const list = el('div','assignment-list'); const rows = normalizeAssignments(candidate);
  rows.forEach((row,i) => { const node = el('div','assignment-row'); const person = el('div','assignment-person'); const name = el('div','assignment-name'); name.append(el('span','avatar',String(i + 1).padStart(2,'0')),el('span','',row.name)); person.append(name); if (row.total !== undefined) person.append(el('span','assignment-cost',`估算 ¥${money(row.total)}`)); const items = el('p','assignment-items',row.items.length ? row.items.map((item) => `${itemLabel(item)} × ${qty(item)}`).join(' · ') : '本次未分配餐品'); node.append(person,items);if(row.extras.length)node.append(el('p','assignment-items',`其中套餐额外包含：${row.extras.map((item)=>`${itemLabel(item)} × ${qty(item)}`).join(' · ')}（已计入上述餐品及费用）`));list.append(node); });
  const meta = el('div','plan-meta'); const unmet = flattenMessages(candidate.unmet); const allItems = normalizeItems(candidate.items); const count = allItems.length ? allItems.reduce((sum,item) => sum + qty(item),0) : rows.flatMap((row) => row.items).reduce((sum,item) => sum + qty(item),0);
  const remainder=(currentRequest?.budget_fen || 0) - total;
  meta.append(el('span','',unmet.length ? `有 ${unmet.length} 项需求未满足` : '模拟菜单内：必需条件满足'), el('span','',remainder >= 0 ? `预算余量 ¥${money(remainder)}` : `超出预算 ¥${money(-remainder)}`),el('span','',`${count} 份点单项目`));
  const extras = normalizeItems(candidate.extras); if(extras.length&&!rows.some((row)=>row.extras.length)) meta.append(el('span','',`套餐额外包含：${extras.map((item) => `${itemLabel(item)} × ${qty(item)}`).join('、')}（已计入套餐费用）`));
  const copy = el('button','copy-button'); copy.type='button'; copy.disabled=stale; copy.append(el('span','',stale ? '重算后可复制最新清单' : '复制这一桌的清单'),el('span','','↗')); copy.addEventListener('click',() => copyPlan(candidate,index,copy));
  card.append(heading,list,meta); if(unmet.length) card.append(el('div','notice-banner',unmet.join('；'))); card.append(copy); return card;
}
function renderResults(result,stale=false) {
  const root = $('#results'); root.replaceChildren();
  if(stale) root.append(el('div','stale-banner','需求已经改变。这是上一次的清单，点击「帮我们搭一桌」重新计算。'));
  const candidates = result.candidates || result.plans || [];
  const overBudget=result.status==='estimated_over_budget'||(candidates.length>0&&candidates.every((candidate)=>(candidate.estimated_total_fen??candidate.total_fen)>currentRequest?.budget_fen));
  if(overBudget) root.append(el('div','notice-banner','找到符合口味和必需条件的组合，但估算金额超过预算。可提高预算，或由你调整需求后重新计算。'));
  if(!candidates.length) { const state = el('div','error-state'); state.append(el('h3','','暂时没有找到可行组合'),el('p','',friendlyMessage(result.message || result.reason,'模拟菜单里没有找到满足全部必需条件的搭配。请检查必需需求是否冲突，或调整配餐和预算后再试。'))); root.append(state); }
  else candidates.slice(0,2).forEach((candidate,index) => root.append(renderPlan(candidate,index,stale)));
  const bounded=(result.warnings||[]).some((warning)=>typeof warning==='string'&&/bounded|limit|exhaustive/i.test(warning));
  if(bounded)root.append(el('p','result-source','本次只比较了部分可行组合，结果是参考方案，未证明为全局最低价格。'));
  root.append(el('p','result-source','模拟菜单与价格 · 本次计算仅用于演示多人需求协调。价格为估算，门店价格、优惠与供应情况需通过实际 MCP 服务核验。'));
}
function showError(message) { const root=$('#results'); root.replaceChildren(); const state=el('div','error-state'); state.append(el('h3','','暂时没能搭好这一桌'),el('p','',message)); root.append(state);setState('需要调整','error');announce(message); }
async function generate(event) {
  event.preventDefault(); if(loading) return;
  const request=collectRequest();
  if(!Number.isFinite(request.budget_fen)||request.budget_fen<=0) { $('#budget').focus();return; }
  if(request.people.some((person)=>!person.name)) { showError('请先给每位搭子填一个姓名，方便把餐品分清楚。');return; }
  loading=true; const sentRevision=revision; currentRequest=request; currentResult=null; $('#generate').disabled=true; $('#generate-label').textContent='正在搭配…'; $('#add-person').disabled=true;
  $('#results').replaceChildren();const wait=el('div','loading-state');wait.append(el('span','loading-spinner'),el('p','','正在比较大家都合适的组合…'));$('#results').append(wait);setState('正在计算');announce('正在计算点餐方案。');
  try {
    const response=await fetch('/api/plan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({catalog,request})});
    const result=await response.json(); if(!response.ok) throw new Error(friendlyMessage(result.error || result.message,`计算请求失败（${response.status}），请检查需求后重试。`));
    currentResult=result;const stale=revision!==sentRevision;renderResults(result,stale);const count=(result.candidates||result.plans||[]).length;
    const overBudget=result.status==='estimated_over_budget'||(count>0&&(result.candidates||result.plans).every((candidate)=>(candidate.estimated_total_fen??candidate.total_fen)>request.budget_fen));
    setState(stale?'需求已变 · 待重算':(overBudget?'估算超出预算':(count?'清单已生成':'暂无可行方案')),stale?'stale':(overBudget?'error':(count?'ready':'error')));announce(stale?'需求在计算时发生变化，请重新计算。':(overBudget?'方案估算金额超过预算，请调整预算或需求。':(count?`已生成 ${Math.min(count,2)} 个模拟方案。`:'没有找到可行方案，请调整需求或预算。')));
  } catch(error) { if(revision!==sentRevision){showError('需求已修改，上一次计算未能完成。请按最新需求重新计算。');setState('需求已变 · 待重算','stale');}else{showError(error.message === 'Failed to fetch' ? '本地演示服务没有连接上。请确认服务已启动，然后再次点击搭配。' : friendlyMessage(error.message,'计算没有完成，请检查需求后重试。'));} }
  finally { loading=false;$('#generate').disabled=false;$('#generate-label').textContent='帮我们搭一桌';$('#add-person').disabled=people.length>=6; }
}
function resetDemo() { people=demoRequest?.people ? JSON.parse(JSON.stringify(demoRequest.people)) : defaultPeople();$('#budget').value=money(demoRequest?.budget_fen||8000);document.querySelector('input[name="strategy"][value="balanced"]').checked=true;nextId=4;renderPeople();markChanged(); }
$('#plan-form').addEventListener('submit',generate);
$('#budget').addEventListener('input',markChanged);
document.querySelectorAll('input[name="strategy"]').forEach((input)=>input.addEventListener('change',markChanged));
$('#add-person').addEventListener('click',()=>{if(people.length>=6)return;people.push({id:`p${nextId++}`,name:`搭子 ${people.length+1}`,needs:{main:1,drink:1,side:0},exclude_tags:[],must_tags:[],prefer_tags:[]});renderPeople();markChanged();document.querySelector('.person-card:last-child .name-input').focus();});
$('#reset-demo').addEventListener('click',resetDemo);
renderPeople();
fetch('/api/demo').then(async(response)=>{if(!response.ok)throw new Error('demo');return response.json();}).then((data)=>{catalog=data.catalog||null;demoRequest=data.request||null;if(data.request?.people&&revision===0){people=data.request.people.map((person)=>({...person,needs:{main:0,drink:0,side:0,...person.needs},exclude_tags:person.exclude_tags||[],must_tags:person.must_tags||[],prefer_tags:person.prefer_tags||[]}));$('#budget').value=money(data.request.budget_fen||8000);renderPeople();}}).catch(()=>{setState('演示服务未连接','error');announce('演示服务尚未连接，生成方案时可重试。');});
