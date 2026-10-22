// Copyright (c) 2026 Robert Backhaus

'use strict';

const $ = id => document.getElementById(id);
const terminal = new Set(['completed', 'failed', 'partial', 'cancelled', 'timed_out', 'interrupted']);
const recoverable = new Set(['interrupted', 'cancelled', 'timed_out']);
let current = null, displayedRun = null, poll = null, stream = null, historyStream = null, historyRunId = '', validatedPath = '';
let conversationId = '', activeProject = '', projects = [], conversationsForProject = [];
// Per run: received SSE event ids and the timeline entries, so the right panel can
// switch between the selected chat's run and the live run without losing events.
const seenEvents = new Map(), timelines = new Map();
let renderedTrace = { text: null, context: null };
let mcpServers = [], editingMcp = null; const mcpTests = new Map();
const closedTabs = new Map();
const decisionTraces = new Map(), modelThinking = new Map(), runtimeLogs = new Map();
const storeKey = 'reaper-ai-workspace', conversationKey = 'reaper-ai-conversation', projectKey = 'reaper-ai-project';
const leftPanelKey = 'reaper-ai-left-panel-collapsed', rightPanelKey = 'reaper-ai-right-panel-collapsed';

// Context of the selected navigation entry: 'legacy' (chats without a project),
// 'chat' (general chat project without a workspace), or 'coding' (workspace project).
function projectKind(project) { return project?.kind === 'chat' ? 'chat' : 'coding'; }
function contextKind() { const project = projects.find(item => item.project_id === activeProject); return project ? projectKind(project) : 'legacy'; }
function isCodingContext() { return contextKind() === 'coding'; }
function welcomeHtml() { return isCodingContext() ? '<div class="welcome"><h2>Was soll REAPER ändern?</h2><p>Projekt prüfen, Auftrag eingeben und starten. Ergebnisse sind nachprüfbar über Run-Status und Git-Diff.</p></div>' : '<div class="welcome"><h2>Worüber möchtest du sprechen?</h2><p>Allgemeiner Chat ohne Workspace: Fragen, Recherche und MCP-Werkzeuge.</p></div>'; }
function info(id, message, cls = 'muted') { const element = $(id); element.textContent = message; element.className = cls; }
async function request(url, options = {}) { const response = await fetch(url, { ...options, headers: { 'Content-Type': 'application/json', ...(options.headers || {}) } }); if (response.status === 204) return null; let data; try { data = await response.json(); } catch { throw Error(`HTTP ${response.status}`); } if (!response.ok) throw Error(typeof data.detail === 'string' ? data.detail : `HTTP ${response.status}`); return data; }
async function copyText(text) { try { await navigator.clipboard.writeText(text); info('activity', 'In Zwischenablage kopiert', 'ok'); } catch { info('activity', 'Kopieren nicht verfügbar', 'err'); } }
function copyButton(text) { const button = document.createElement('button'); button.className = 'secondary copy'; button.textContent = 'Kopieren'; button.addEventListener('click', () => copyText(text)); return button; }
function bubble(kind, text, final = false) { document.querySelector('.welcome')?.remove(); const element = document.createElement('div'); element.className = `bubble ${kind}${final ? ' final-answer' : ''}`; if (final) { const head = document.createElement('div'); head.className = 'answer-head'; const title = document.createElement('strong'); title.textContent = 'Sentinel'; head.append(title, copyButton(text)); const body = document.createElement('div'); body.textContent = text; element.append(head, body); } else element.textContent = text; $('thread').appendChild(element); $('thread').scrollTop = $('thread').scrollHeight; return element; }
function detail(label, text, open = false) { const box = document.createElement('details'); box.className = 'detail'; box.open = open; const summary = document.createElement('summary'); summary.textContent = label; const pre = document.createElement('pre'); pre.textContent = text; box.append(summary, pre, copyButton(text)); return box; }
function resultText(result, status) { const changed = Array.isArray(result?.changed_files) && result.changed_files.length ? result.changed_files.join(', ') : 'keine'; const checks = Array.isArray(result?.checks) ? result.checks.length : 0; const lines = [`Status: ${status || result?.status || 'unbekannt'}`, `Verifikationsniveau: ${result?.verification_level || 'keines'}`, `Geänderte Dateien: ${changed}`, `Ausgeführte Prüfungen: ${checks}`]; if (result?.reason) lines.push(`Hinweis: ${result.reason}`); if (Array.isArray(result?.limitations) && result.limitations.length) lines.push(`Grenzen: ${result.limitations.join(' · ')}`); return lines.join('\n'); }
function setRunStatus(status) { $('run-status').textContent = status ? RunStatus.statusLabel(status) : 'Bereit'; }
// P06: agent status and run summary per run id, built only from SSE events and stored results.
const runStats = new Map();
function statsFor(id) { if (!runStats.has(id)) runStats.set(id, RunStatus.newStats()); return runStats.get(id); }
function setText(id, text) { if ($(id).textContent !== text) $(id).textContent = text; }
function renderRunPanel() {
  const run = displayedRun; if (!run) return;
  const stats = runStats.get(run.run_id) || null; const live = RunStatus.liveStatus(run, stats);
  setText('active-run-status', live.label); $('active-run-status').dataset.tone = live.tone;
  setText('run-activity', live.activity); setText('run-last-action', live.lastAction); setText('run-elapsed', live.elapsed);
  const terminalRun = terminal.has(run.status);
  const counts = terminalRun ? RunStatus.summarize(run, stats) : live;
  setText('run-calls', `${counts.modelCalls ?? 'nicht erfasst'} Modell · ${counts.toolCalls ?? 'nicht erfasst'} Tool${terminalRun ? '' : ' (bisher)'}`);
  if (terminalRun) setText('run-elapsed', counts.duration || 'unbekannt');
  $('run-summary-section').hidden = !terminalRun; if (terminalRun) renderRunSummary(counts);
}
// P07: values may carry file references; `files: true` marks entries that are file paths.
function summaryRow(label, value, cls = '', { files = false } = {}) { const row = document.createElement('div'); row.className = `summary-row${cls ? ` ${cls}` : ''}`; const title = document.createElement('strong'); title.textContent = label; row.append(title); const context = codeLinkContext(displayedRun); if (Array.isArray(value)) { const list = document.createElement('ul'); for (const item of value) { const entry = document.createElement('li'); const text = typeof item === 'string' ? item : item.text; if (typeof item !== 'string') entry.className = item.cls || ''; const target = files ? CodeLinks.resolve(text, context, { force: true }) : null; if (target) entry.appendChild(codeLink(text, target)); else appendLinkedText(entry, text, context); list.appendChild(entry); } row.appendChild(list); } else { const text = document.createElement('span'); appendLinkedText(text, value, context); row.appendChild(text); } return row; }
function renderRunSummary(summary) {
  const context = codeLinkContext(displayedRun); const text = RunStatus.summaryText(summary); const key = `${text}\n${context ? `${activeProject}:${context.files.size}` : ''}`; const box = $('run-summary'); if (box.dataset.key === key) return; box.dataset.key = key; box.dataset.text = text;
  const unknown = 'nicht erfasst'; const rows = [summaryRow('Status', summary.label, `tone-${summary.tone}`)];
  if (summary.outcome) rows.push(summaryRow('Ergebnis', summary.outcome));
  rows.push(summaryRow('Geänderte Dateien', summary.changedFiles === null ? unknown : summary.changedFiles.length ? summary.changedFiles : 'keine', '', { files: true }));
  rows.push(summaryRow('Tests', summary.tests === null ? unknown : summary.tests.length ? summary.tests.map(test => ({ text: `${test.ok ? '✓' : '✗'} ${test.command}${test.exitCode !== null ? ` (Exit-Code ${test.exitCode})` : ''}`, cls: test.ok ? 'ok' : 'err' })) : 'keine Testläufe erfasst'));
  if (summary.errors.length) rows.push(summaryRow('Fehler', summary.errors, 'tone-err'));
  if (summary.open.length) rows.push(summaryRow('Offene Punkte', summary.open));
  rows.push(summaryRow('Laufzeit', summary.duration || unknown));
  rows.push(summaryRow('Aufrufe', `${summary.modelCalls ?? unknown} Modell · ${summary.toolCalls ?? unknown} Tool`));
  box.replaceChildren(...rows);
}
setInterval(() => { if (displayedRun && !terminal.has(displayedRun.status)) setText('run-elapsed', RunStatus.liveStatus(displayedRun, runStats.get(displayedRun.run_id)).elapsed); }, 1000);
// The right panel always shows the selected chat: its live run while that run is
// active, otherwise the chat's last recorded run. Events of other runs only update caches.
function showPanelRun(run, { active = false, action = '' } = {}) { const switched = displayedRun?.run_id !== run.run_id; displayedRun = run; $('active-run').hidden = false; $('no-run').hidden = true; $('active-run-title').textContent = `${active ? 'Aktiver' : 'Letzter'} Run ${run.run_id}`; setAction(action); renderRunPanel(); if (switched) { renderTimeline(run.run_id); renderDiagnostics(run.run_id); } }
function diagnosticRunId(data) { return data?.api_run_id || data?.run_id || displayedRun?.run_id || current?.run_id || ''; }
function renderDiagnostics(id) { if (!id || displayedRun?.run_id !== id) return; const traces = [...(decisionTraces.get(id)?.values() || [])]; const traceText = traces.length ? JSON.stringify(traces, null, 2) : 'Noch keine Modellentscheidung aufgezeichnet.'; const trace = $('decision-trace'); const context = traceText.length <= 300_000 ? codeLinkContext(displayedRun) : null; if (renderedTrace.text !== traceText || renderedTrace.context !== context || !trace.firstChild) { renderedTrace = { text: traceText, context }; trace.replaceChildren(); appendLinkedText(trace, traceText, context); } const thinking = [...(modelThinking.get(id)?.values() || [])]; const thinkingText = thinking.map(item => item.available ? `Schritt ${item.step} · ${item.model}\n${item.thinking}${item.truncated ? '\n[Thinking gekürzt]' : ''}` : `Schritt ${item.step} · ${item.model}\n${item.reason || 'Kein Ollama-Thinking geliefert.'}`).join('\n\n'); const log = runtimeLogs.get(id) || ''; $('active-log').textContent = [thinkingText, log].filter(Boolean).join(thinkingText && log ? '\n\n--- Laufzeitlog ---\n' : ''); $('active-log').scrollTop = $('active-log').scrollHeight; }
function recordDiagnostic(type, data, id = diagnosticRunId(data)) { if (!id) return; if (type === 'decision_trace') { const traces = decisionTraces.get(id) || new Map(); traces.set(data.trace_id, { ...(traces.get(data.trace_id) || {}), ...data }); decisionTraces.set(id, traces); } if (type === 'model_thinking') { const thinking = modelThinking.get(id) || new Map(); thinking.set(data.trace_id, data); modelThinking.set(id, thinking); } renderDiagnostics(id); }
function hidePanelRun() { displayedRun = null; $('active-run').hidden = true; $('no-run').hidden = false; $('active-timeline').replaceChildren(); $('decision-trace').textContent = ''; $('active-log').textContent = ''; $('run-summary-section').hidden = true; $('run-summary').replaceChildren(); delete $('run-summary').dataset.key; }
function liveRunSelected() { return Boolean(current && current.conversation_id && current.conversation_id === conversationId); }
function renderActive() { setRunStatus(current ? current.status || 'running' : ''); if (liveRunSelected()) showPanelRun(current, { active: true }); }
function setAction(text) { $('active-action').textContent = text; }
function stopFeed() { if (stream) { stream.close(); stream = null; } if (poll) { clearInterval(poll); poll = null; } }
function clearActive() { current = null; stopFeed(); setRunStatus(''); $('start').disabled = false; $('cancel').disabled = true; }
function showRecordedRun(run) { if (liveRunSelected()) { renderActive(); return; } if (!run) { hidePanelRun(); return; } if (!timelines.has(run.run_id)) eventFeed(run.run_id, { historical: true }); showPanelRun(run, { action: 'Gespeicherter Run – keine Live-Ausführung aktiv.' }); updateLog(run.run_id); }
function timelineEntry(type, data) { const item = document.createElement('div'); item.className = 'event-entry'; const title = document.createElement('strong'); title.textContent = type.replaceAll('_', ' '); const details = document.createElement('span'); const context = codeLinkContext(displayedRun); const target = type === 'tool_started' ? CodeLinks.actionTarget(data.action || {}, context) : null; if (target) { details.append(`${data.tool || 'Werkzeug'} · `); details.appendChild(codeLink(`${target.path}${target.line ? `:${target.line}${target.endLine && target.endLine !== target.line ? `-${target.endLine}` : ''}` : ''}`, target)); } else if (Array.isArray(data.files) && data.files.length && !data.phase && !data.tool && !data.status && !data.next_stage) data.files.forEach((file, index) => { if (index) details.append(', '); const fileTarget = CodeLinks.resolve(String(file), context, { force: true }); if (fileTarget) details.appendChild(codeLink(String(file), fileTarget)); else details.append(String(file)); }); else appendLinkedText(details, data.phase || data.tool || data.status || data.next_stage || (data.model || ''), context); item.append(title, details); return item; }
function refreshPanelLinks() { if (!displayedRun) return; renderTimeline(displayedRun.run_id); renderDiagnostics(displayedRun.run_id); renderRunPanel(); }
function renderTimeline(id) { const box = $('active-timeline'); box.replaceChildren(...(timelines.get(id) || []).map(([type, data]) => timelineEntry(type, data))); box.scrollTop = box.scrollHeight; }
function addTimeline(type, data, id = diagnosticRunId(data)) { if (!id) return; if (!timelines.has(id)) timelines.set(id, []); timelines.get(id).push([type, data]); if (displayedRun?.run_id !== id) return; $('active-timeline').appendChild(timelineEntry(type, data)); $('active-timeline').scrollTop = $('active-timeline').scrollHeight; }
function clearThread() { $('thread').replaceChildren(); }

async function models() { try { const data = await request('/models'); const select = $('model'); const previous = select.value; select.replaceChildren(); for (const name of data.models) select.appendChild(new Option(name, name)); if (data.models.includes(previous)) select.value = previous; else if (data.models.includes('qwen3.6:35b')) select.value = 'qwen3.6:35b'; info('api-status', 'API und Ollama verbunden', 'ok'); $('api-dot').classList.add('online'); } catch (error) { info('api-status', `Modelle nicht erreichbar: ${error.message}`, 'err'); $('api-dot').classList.remove('online'); } }
async function recoveryControl(runId, status) { if (!recoverable.has(status)) return null; try { const recovery = await request(`/runs/${encodeURIComponent(runId)}/recovery`); if (!recovery.can_resume) return null; const control = document.createElement('p'); control.className = 'ok'; control.textContent = `Recovery: ${recovery.reason}`; const button = document.createElement('button'); button.className = 'secondary'; button.textContent = `Ab ${recovery.checkpoint.next_stage} fortsetzen`; button.addEventListener('click', () => resume(runId)); control.append(' ', button); return control; } catch { return null; } }
async function renderConversation(data) { clearThread(); const shown = new Set(); for (const message of data.messages || []) { const result = message.metadata?.result; const isResult = message.role === 'assistant' && result; const summary = isResult && typeof result.summary === 'string' && result.summary.trim() ? result.summary : message.content; const element = bubble(message.role === 'user' ? 'user' : 'agent', summary, isResult && Boolean(result.summary)); if (isResult) { shown.add(message.run_id); element.appendChild(detail('Gespeichertes Ergebnis', resultText(result, message.metadata.status))); const control = await recoveryControl(message.run_id, message.metadata.status); if (control) element.appendChild(control); } } for (const run of data.runs || []) { if (shown.has(run.run_id) || !recoverable.has(run.status)) continue; const element = bubble('agent', `Run ${run.run_id}\nStatus: ${run.status}`); const control = await recoveryControl(run.run_id, run.status); if (control) element.appendChild(control); } if (!(data.messages || []).length) $('thread').innerHTML = welcomeHtml(); showRecordedRun((data.runs || []).at(-1)); }
async function loadConversation(id) { if (!id) { conversationId = ''; localStorage.removeItem(conversationKey); clearThread(); $('thread').innerHTML = welcomeHtml(); showRecordedRun(null); info('conversation-status', 'Neues Gespräch', 'muted'); renderConversationTabs(); return; } try { const data = await request(`/conversations/${encodeURIComponent(id)}`); conversationId = data.id; localStorage.setItem(conversationKey, id); await renderConversation(data); info('conversation-status', `${(data.messages || []).length} gespeicherte Nachrichten`, 'ok'); renderConversationTabs(); } catch (error) { info('conversation-status', error.message, 'err'); } }
function projectForId(id = activeProject) { return projects.find(item => item.project_id === id) || null; }
function selectedKind() { return document.querySelector('input[name="project-kind"]:checked')?.value === 'chat' ? 'chat' : 'coding'; }
function applyKindToForm() { const kind = selectedKind(); $('workspace-field').hidden = kind !== 'coding'; $('project-status').hidden = kind !== 'coding'; }
function fillProject(project) { const kind = project ? projectKind(project) : 'coding'; for (const input of document.querySelectorAll('input[name="project-kind"]')) { input.checked = input.value === kind; input.disabled = Boolean(project); } $('project-kind-note').textContent = project ? 'Der Typ ist beim Erstellen festgelegt.' : 'Typ des neuen Projekts wählen.'; applyKindToForm(); $('project-name').value = project?.name || ''; $('project-description').value = project?.description || ''; $('project-system-prompt').value = project?.system_prompt || ''; $('workspace').value = project?.workspace || localStorage.getItem(storeKey) || ''; $('model').value = project?.model || $('model').value; $('temperature').value = project?.temperature ?? 0; $('top-p').value = project?.top_p ?? 0.9; $('top-k').value = project?.top_k ?? 40; $('num-ctx').value = project?.num_ctx ?? 16384; renderProjectMcp(project); validatedPath = project?.workspace || ''; $('project-title').textContent = project?.name || (activeProject ? 'Projekt' : 'Legacy-Chats'); applyContext(); }
function projectPayload() { const kind = selectedKind(); const payload = { name: $('project-name').value.trim(), workspace: $('workspace').value.trim(), description: $('project-description').value.trim(), system_prompt: $('project-system-prompt').value, model: $('model').value, temperature: Number($('temperature').value), top_p: Number($('top-p').value), top_k: Number($('top-k').value), num_ctx: Number($('num-ctx').value), mcp_servers: selectedProjectMcp() }; if (kind === 'chat') delete payload.workspace; if (!activeProject) payload.kind = kind; return payload; }
const navIcons = { chat: '<path d="M7.9 20A9 9 0 1 0 4 16.1L2 22Z"/>', coding: '<polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/>' };
function navIcon(kind) { const icon = document.createElement('span'); icon.className = `project-kind-icon ${kind}`; icon.innerHTML = `<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${navIcons[kind]}</svg>`; return icon; }
function renderProjectList() { const list = $('project-list'); list.replaceChildren(); const group = label => { const heading = document.createElement('div'); heading.className = 'project-group'; heading.textContent = label; list.appendChild(heading); }; const add = (id, name, kind, title) => { const button = document.createElement('button'); button.className = `project-item secondary ${kind}${activeProject === id ? ' active' : ''}`; button.type = 'button'; button.dataset.kind = kind; const text = document.createElement('span'); text.textContent = name; button.append(navIcon(kind), text); button.title = title; button.addEventListener('click', () => selectProject(id)); list.appendChild(button); }; group('Allgemeine Chats'); add('', 'Legacy-Chats', 'chat', 'Gespräche ohne Projektzuordnung · kein Workspace'); for (const project of projects.filter(item => projectKind(item) === 'chat')) add(project.project_id, project.name || 'Unbenannt', 'chat', `${project.name || 'Unbenannt'} · Allgemeiner Chat ohne Workspace`); group('Coding-Projekte'); const coding = projects.filter(item => projectKind(item) === 'coding'); for (const project of coding) add(project.project_id, project.name || 'Unbenannt', 'coding', `${project.name || 'Unbenannt'} · ${project.workspace}`); if (!coding.length) { const empty = document.createElement('small'); empty.className = 'muted project-group-empty'; empty.textContent = 'Noch kein Coding-Projekt'; list.appendChild(empty); } }
async function loadProjects(preferred) { try { const data = await request('/projects'); projects = data.projects || []; if (preferred === undefined && !activeProject && $('settings-dialog').open) { renderProjectList(); return; } const wanted = preferred ?? (localStorage.getItem(projectKey) || activeProject); activeProject = projects.some(item => item.project_id === wanted) ? wanted : ''; localStorage.setItem(projectKey, activeProject); fillProject(projectForId()); renderProjectList(); info('project-select-status', ({ legacy: 'Legacy-Chats aktiv', chat: 'Allgemeiner Chat aktiv', coding: 'Coding-Projekt aktiv' })[contextKind()], 'ok'); await conversations(); } catch (error) { info('project-select-status', error.message, 'err'); } }
async function selectProject(id) { activeProject = id; localStorage.setItem(projectKey, activeProject); conversationId = ''; localStorage.removeItem(conversationKey); fillProject(projectForId()); renderProjectList(); await conversations(''); await loadProjectMemory(); await loadIndexStatus(); }
function newProject() { activeProject = ''; conversationId = ''; conversationsForProject = []; fillProject(null); renderProjectList(); renderConversationTabs(); $('project-name').focus(); $('settings-dialog').showModal(); info('project-select-status', 'Neues Projekt konfigurieren und speichern', 'muted'); }
async function saveProject() { const payload = projectPayload(); const coding = (activeProject ? projectKind(projectForId()) : payload.kind) === 'coding'; if (!payload.name || (coding && !payload.workspace) || !payload.model) { info('project-select-status', coding ? 'Name, Workspace und Modell sind erforderlich' : 'Name und Modell sind erforderlich', 'err'); return; } try { const project = activeProject ? await request(`/projects/${encodeURIComponent(activeProject)}`, { method: 'PATCH', body: JSON.stringify(payload) }) : await request('/projects', { method: 'POST', body: JSON.stringify(payload) }); activeProject = project.project_id; await loadProjects(activeProject); await loadProjectMemory(); await loadIndexStatus(); info('project-select-status', 'Projekt gespeichert', 'ok'); } catch (error) { info('project-select-status', error.message, 'err'); } }
function confirmProjectDeletion(name) { const dialog = $('delete-project-dialog'); $('delete-project-message').textContent = `Projekt „${name}“ wirklich löschen?\n\nDabei werden auch die gespeicherten Chats, Run-Verläufe und das Projektwissen dauerhaft gelöscht. Die Dateien im Workspace bleiben erhalten.`; return new Promise(resolve => { dialog.addEventListener('close', () => resolve(dialog.returnValue === 'confirm'), { once: true }); dialog.showModal(); }); }
async function deleteProject() { if (!activeProject) return; const project = projectForId(); const name = project?.name || activeProject; if (!await confirmProjectDeletion(name)) return; try { await request(`/projects/${encodeURIComponent(activeProject)}`, { method: 'DELETE' }); activeProject = ''; conversationId = ''; localStorage.removeItem(conversationKey); await loadProjects(''); info('project-select-status', 'Projekt und gespeicherte Daten gelöscht', 'ok'); } catch (error) { info('project-select-status', error.message, 'err'); } }
function tabStateKey() { return activeProject || '__legacy__'; }
const tabIcons = { 'eye-off': '<path d="M10.733 5.076a10.744 10.744 0 0 1 11.205 6.575 1 1 0 0 1 0 .696 10.747 10.747 0 0 1-1.444 2.49"/><path d="M14.084 14.158a3 3 0 0 1-4.242-4.242"/><path d="M17.479 17.499a10.75 10.75 0 0 1-15.417-5.151 1 1 0 0 1 0-.696 10.75 10.75 0 0 1 4.446-5.143"/><path d="m2 2 20 20"/>', 'trash-2': '<path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/><line x1="10" x2="10" y1="11" y2="17"/><line x1="14" x2="14" y1="11" y2="17"/>' };
function tabAction(icon, label, action, extra = '') { const button = document.createElement('span'); button.className = `conversation-tab-action${extra ? ` ${extra}` : ''}`; button.setAttribute('role', 'button'); button.setAttribute('aria-label', label); button.title = label; button.dataset.icon = icon; button.innerHTML = `<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${tabIcons[icon]}</svg>`; button.addEventListener('click', event => { event.stopPropagation(); action(); }); return button; }
function renderConversationTabs() { const container = $('conversation-tabs'); container.replaceChildren(); const hidden = closedTabs.get(tabStateKey()) || new Set(); for (const conversation of conversationsForProject.filter(item => !hidden.has(item.id))) { const tab = document.createElement('button'); tab.className = `conversation-tab${conversation.id === conversationId ? ' active' : ''}`; tab.type = 'button'; tab.dataset.conversationId = conversation.id; const label = document.createElement('span'); label.textContent = conversation.title || 'Gespräch'; tab.addEventListener('click', () => loadConversation(conversation.id)); tab.append(label, tabAction('eye-off', 'Tab ausblenden (Chat bleibt erhalten)', () => closeConversationTab(conversation.id)), tabAction('trash-2', 'Chat endgültig löschen', () => deleteConversation(conversation.id), 'danger')); container.appendChild(tab); } if (!conversationId) { const draft = document.createElement('span'); draft.className = 'conversation-tab draft active'; draft.textContent = 'Neues Gespräch'; container.appendChild(draft); } renderHiddenConversations(hidden); }
function renderHiddenConversations(hidden) { const select = $('hidden-conversations'); const items = conversationsForProject.filter(item => hidden.has(item.id)); select.hidden = !items.length; select.replaceChildren(new Option(`Ausgeblendet (${items.length})`, '')); for (const item of items) select.appendChild(new Option(item.title || 'Gespräch', item.id)); }
function reopenConversationTab(id) { if (!id) return; (closedTabs.get(tabStateKey()) || new Set()).delete(id); loadConversation(id); }
function nextOpenConversation(id) { const hidden = closedTabs.get(tabStateKey()) || new Set(); return conversationsForProject.find(item => item.id !== id && !hidden.has(item.id))?.id || ''; }
function closeConversationTab(id) { const hidden = closedTabs.get(tabStateKey()) || new Set(); if (conversationId === id) { const next = nextOpenConversation(id); hidden.add(id); closedTabs.set(tabStateKey(), hidden); loadConversation(next); } else { hidden.add(id); closedTabs.set(tabStateKey(), hidden); renderConversationTabs(); } }
async function conversations(preferred, fallback = true) { try { const url = activeProject ? `/projects/${encodeURIComponent(activeProject)}/conversations` : '/conversations?legacy=true'; const data = await request(url); conversationsForProject = data.conversations || []; const wanted = preferred ?? localStorage.getItem(conversationKey); const target = conversationsForProject.some(item => item.id === wanted) ? wanted : (fallback ? conversationsForProject[0]?.id || '' : ''); if (target) (closedTabs.get(tabStateKey()) || new Set()).delete(target); await loadConversation(target); renderConversationTabs(); } catch (error) { info('conversation-status', error.message, 'err'); } }
function newConversation() { conversationId = ''; localStorage.removeItem(conversationKey); loadConversation(''); }
const pause = milliseconds => new Promise(resolve => window.setTimeout(resolve, milliseconds));
async function stopActiveRunForDeletion(conversation = null) { const active = await request('/runs/active'); const run = active.run; if (!run || (conversation && run.conversation_id !== conversation)) return; await request(`/runs/${encodeURIComponent(run.run_id)}/cancel`, { method: 'POST' }); let latest = run; while (!terminal.has(latest.status)) { await pause(250); latest = await request(`/runs/${encodeURIComponent(run.run_id)}`); } if (current?.run_id === run.run_id) clearActive(); }
function removeClosedTab(id) { for (const hidden of closedTabs.values()) hidden.delete(id); }
async function deleteConversation(id = conversationId) { if (!id) return; const conversation = conversationsForProject.find(item => item.id === id); if (!confirm(`„${conversation?.title || 'dieses Gespräch'}“ einschließlich Nachrichten, Runs, Ereignissen und Checkpoints dauerhaft löschen?`)) return; try { await stopActiveRunForDeletion(id); await request(`/conversations/${encodeURIComponent(id)}`, { method: 'DELETE' }); const next = id === conversationId ? nextOpenConversation(id) : conversationId; removeClosedTab(id); if (id === conversationId) { conversationId = ''; localStorage.removeItem(conversationKey); } await conversations(next, false); info('conversation-status', 'Gespräch und zugehörige Daten gelöscht', 'ok'); } catch (error) { info('conversation-status', error.message, 'err'); } }
async function deleteAllConversations() { if (!confirm('Wirklich alle gespeicherten Chats in allen Projekten dauerhaft löschen? Projektwissen und globale Memory-Einträge bleiben erhalten.')) return; try { await stopActiveRunForDeletion(); await request('/conversations', { method: 'DELETE' }); closedTabs.clear(); conversationId = ''; conversationsForProject = []; localStorage.removeItem(conversationKey); await conversations(''); info('conversation-status', 'Alle gespeicherten Chats gelöscht', 'ok'); } catch (error) { info('conversation-status', error.message, 'err'); } }
async function validate() { const path = $('workspace').value.trim(); validatedPath = ''; if (!path) { info('project-status', 'Pfad eingeben', 'err'); return false; } try { const data = await request('/projects/validate', { method: 'POST', body: JSON.stringify({ workspace: path }) }); validatedPath = data.workspace; localStorage.setItem(storeKey, validatedPath); info('project-status', `Workspace gültig: ${validatedPath}`, 'ok'); return true; } catch (error) { info('project-status', error.message, 'err'); return false; } }
async function loadProjectMemory() { if (!activeProject) { info('memory-status', 'Legacy-Chats: Wissen wird global gespeichert', 'muted'); return; } try { const data = await request(`/projects/${encodeURIComponent(activeProject)}/memory`); info('memory-status', data.memory?.length ? `${data.memory.length} gespeicherte Entscheidung(en)` : 'Noch kein Projektwissen', 'muted'); } catch (error) { info('memory-status', error.message, 'err'); } }
async function saveMemory() { const content = $('memory').value.trim(); if (!content) { info('memory-status', 'Entscheidung eingeben', 'err'); return; } try { if (activeProject) await request(`/projects/${encodeURIComponent(activeProject)}/memory`, { method: 'POST', body: JSON.stringify({ content }) }); else await request('/memory', { method: 'POST', body: JSON.stringify({ content }) }); $('memory').value = ''; await loadProjectMemory(); info('memory-status', 'Als bestätigte Projektentscheidung gespeichert', 'ok'); } catch (error) { info('memory-status', error.message, 'err'); } }
function indexStatusText(data) { return [`Letzter Scan: ${data.scanned_at || 'noch keiner'}`, `Dateien: ${data.file_count ?? 0} · Python: ${data.python_file_count ?? 0} · Symbole: ${data.symbol_count ?? 0}`, `Git: ${data.git_branch || 'kein Branch'} ${data.git_head ? data.git_head.slice(0, 12) : ''}`].join('\n'); }
async function loadIndexStatus() { if (!isCodingContext()) { $('index-status').textContent = 'Nur für Coding-Projekte verfügbar.'; $('index-results').textContent = ''; return; } try { $('index-status').textContent = indexStatusText(await request(`/projects/${encodeURIComponent(activeProject)}/index`)); } catch (error) { $('index-status').textContent = `Indexfehler: ${error.message}`; } }
async function refreshIndex() { if (!isCodingContext()) { info('activity', 'Für den Index ein Projekt auswählen.', 'err'); return; } try { const data = await request(`/projects/${encodeURIComponent(activeProject)}/index`, { method: 'POST' }); $('index-status').textContent = indexStatusText(data); info('activity', `Index aktualisiert: ${data.file_count} Dateien`, 'ok'); } catch (error) { info('activity', `Indexfehler: ${error.message}`, 'err'); } }
async function searchIndex() { if (!isCodingContext()) { info('activity', 'Für die Suche ein Projekt auswählen.', 'err'); return; } const query = $('index-query').value.trim(); if (!query) { $('index-results').textContent = 'Suchbegriff eingeben.'; return; } try { const root = `/projects/${encodeURIComponent(activeProject)}/index`; const [files, symbols] = await Promise.all([request(`${root}/files?query=${encodeURIComponent(query)}`), request(`${root}/symbols?name=${encodeURIComponent(query)}`)]); const lines = [...(files.files || []).map(item => `Datei  ${item.path} (${item.language}, ${item.line_count} Zeilen)`), ...(symbols.symbols || []).map(item => `Symbol ${item.qualname} · ${item.path}:${item.start_line}-${item.end_line}`)]; $('index-results').textContent = lines.length ? lines.join('\n') : 'Keine passenden Dateien oder Symbole.'; } catch (error) { $('index-results').textContent = `Suchfehler: ${error.message}`; } }
function handleEvent(type, data, id = diagnosticRunId(data), historical = false) { if (type === 'decision_trace' || type === 'model_thinking') recordDiagnostic(type, data, id); addTimeline(type, data, id); if (id) { RunStatus.applyEvent(statsFor(id), type, data); if (displayedRun?.run_id === id) renderRunPanel(); } if (!historical && type === 'run_completed' && current?.run_id) refresh(current.run_id); }
// One live stream for the active run and one replay stream for a recorded run; both
// fill the per-run caches. An unfinished replay is discarded so it reloads completely later.
function forgetRunEvents(id) { timelines.delete(id); seenEvents.delete(id); runStats.delete(id); }
function closeHistoryFeed({ discard = true } = {}) { if (!historyStream) return; historyStream.close(); historyStream = null; if (discard && historyRunId && historyRunId !== current?.run_id) forgetRunEvents(historyRunId); historyRunId = ''; }
function eventFeed(id, { historical = false } = {}) { if (historical) closeHistoryFeed(); else { if (stream) stream.close(); if (historyRunId === id) closeHistoryFeed({ discard: false }); } timelines.set(id, []); seenEvents.set(id, new Set()); runStats.set(id, RunStatus.newStats()); const source = new EventSource(`/runs/${encodeURIComponent(id)}/events`); if (historical) { historyStream = source; historyRunId = id; } else stream = source; const events = ['run_started', 'recovery_started', 'phase_changed', 'model_activity', 'model_thinking', 'decision_trace', 'agent_decision', 'tool_started', 'tool_completed', 'files_changed', 'verification_completed', 'checkpoint_saved', 'run_completed']; for (const type of events) source.addEventListener(type, event => { const seen = seenEvents.get(id); if (!seen || seen.has(event.lastEventId)) return; seen.add(event.lastEventId); try { handleEvent(type, JSON.parse(event.data), id, historical); } catch { info('activity', type); } }); source.onerror = () => { if (historical) { source.close(); if (historyStream === source) { historyStream = null; historyRunId = ''; } return; } info('activity', 'SSE-Verbindung unterbrochen; Statusabfrage bleibt aktiv.', 'err'); }; }
async function updateLog(id) { try { const data = await request(`/runs/${encodeURIComponent(id)}/log`); runtimeLogs.set(id, data.log || data.notice || ''); renderDiagnostics(id); } catch {} }
async function refresh(id) { try { const run = await request(`/runs/${encodeURIComponent(id)}`); if (!current || current.run_id !== id) return; current = { ...current, ...run }; renderActive(); if (terminal.has(run.status)) { const owner = current.conversation_id; const showResult = owner === conversationId; const result = run.result; clearActive(); if (showResult) showRecordedRun(run); if (showResult) { const summary = result && typeof result.summary === 'string' && result.summary.trim() ? result.summary : `Run ${id}\nStatus: ${run.status}\nExit-Code: ${run.exit_code ?? 'n/a'}`; const element = bubble('agent', summary, Boolean(result?.summary)); if (result) element.appendChild(detail('Verifikation', resultText(result, run.status))); if (projectKind(projects.find(item => item.project_id === run.project_id)) === 'coding' && run.project_id) try { const diff = await request(`/runs/${encodeURIComponent(id)}/diff`); if (diff.diff) element.appendChild(detail('Git-Diff (aktueller Workspace-Stand)', diff.diff)); if (diff.notice) element.appendChild(detail('Diff-Hinweis', diff.notice)); } catch (error) { element.appendChild(detail('Diff nicht verfügbar', error.message)); } } info('activity', showResult ? `Lauf ${run.status}` : `Run ${id} in einem anderen Gespräch beendet: ${RunStatus.statusLabel(run.status)}`, run.status === 'completed' ? 'ok' : 'muted'); } else await updateLog(id); } catch (error) { info('activity', `Statusfehler: ${error.message}`, 'err'); } }
function watchRun(run) { current = { ...run }; decisionTraces.delete(run.run_id); modelThinking.delete(run.run_id); runtimeLogs.delete(run.run_id); eventFeed(run.run_id); renderActive(); if (displayedRun?.run_id === run.run_id) { renderTimeline(run.run_id); renderDiagnostics(run.run_id); } $('cancel').disabled = false; $('start').disabled = true; if (poll) clearInterval(poll); poll = setInterval(() => refresh(run.run_id), 1500); refresh(run.run_id); }
async function restoreActive() { try { const data = await request('/runs/active'); if (data.run) watchRun(data.run); } catch (error) { info('activity', `Aktiver Run nicht verfügbar: ${error.message}`, 'err'); } }
async function resume(id) { if (current) return; try { watchRun(await request(`/runs/${encodeURIComponent(id)}/resume`, { method: 'POST' })); info('activity', 'Recovery ab Checkpoint gestartet', 'ok'); } catch (error) { info('activity', `Recovery fehlgeschlagen: ${error.message}`, 'err'); } }
async function start() { if (current) return; const task = $('task').value.trim(); if (!task) { info('activity', 'Bitte Auftrag eingeben.', 'err'); return; } const kind = contextKind(); if (kind === 'coding' && !await validate()) return; const timeout = Number($('timeout').value); if (!Number.isInteger(timeout) || timeout < 1 || timeout > 3600) { info('activity', 'Zeitlimit muss zwischen 1 und 3600 Sekunden liegen.', 'err'); return; } const model = $('model').value; if (!model) { info('activity', 'Modell auswählen.', 'err'); return; } $('start').disabled = true; try { const body = activeProject ? { project_id: activeProject, task, timeout } : { task, model, timeout }; if (conversationId) body.conversation_id = conversationId; const run = await request('/runs', { method: 'POST', body: JSON.stringify(body) }); const hidden = closedTabs.get(tabStateKey()) || new Set(); hidden.delete(run.conversation_id); closedTabs.set(tabStateKey(), hidden); await conversations(run.conversation_id); $('task').value = ''; watchRun(run); info('activity', `Run ${run.run_id} gestartet`, 'ok'); } catch (error) { $('start').disabled = false; info('activity', `Start fehlgeschlagen: ${error.message}`, 'err'); } }
async function cancel() { if (!current) return; $('cancel').disabled = true; try { await request(`/runs/${encodeURIComponent(current.run_id)}/cancel`, { method: 'POST' }); info('activity', 'Abbruch angefordert …'); } catch (error) { info('activity', `Abbruchfehler: ${error.message}`, 'err'); $('cancel').disabled = false; } }
function applyContext() {
  const kind = contextKind(), coding = kind === 'coding';
  info('project-select-status', ({ legacy: 'Legacy-Chats aktiv', chat: 'Allgemeiner Chat aktiv', coding: 'Coding-Projekt aktiv' })[kind], 'ok');
  for (const name of ['editor', 'diff']) { const tab = $(`show-${name}`); tab.disabled = !coding; tab.title = coding ? '' : 'Nur in Coding-Projekten verfügbar'; }
  if (!coding && ($('editor-panel').hidden === false || $('diff-panel').hidden === false)) showWorkspaceView('chat');
  $('index-section').hidden = !coding;
  if (!coding) renderFileTree();
  $('context-subtitle').textContent = coding ? 'Coding-Projekt · Ein lokaler Lauf zur Zeit · Keine automatischen Git-Commits' : kind === 'chat' ? 'Allgemeiner Chat · Kein Workspace erforderlich' : 'Allgemeine Gespräche ohne Projekt · Kein Workspace erforderlich';
  $('task').placeholder = coding ? 'Coding-Auftrag beschreiben …' : 'Frage oder Rechercheauftrag eingeben …';
  $('start').textContent = coding ? 'Run starten' : 'Senden';
  document.querySelector('.welcome')?.replaceWith(Object.assign(document.createElement('template'), { innerHTML: welcomeHtml() }).content.firstChild);
}
function setPanelCollapsed(side, collapsed) { $('layout').classList.toggle(`${side}-collapsed`, collapsed); localStorage.setItem(side === 'left' ? leftPanelKey : rightPanelKey, collapsed ? '1' : '0'); }
function setupPanels() { setPanelCollapsed('left', localStorage.getItem(leftPanelKey) === '1'); setPanelCollapsed('right', localStorage.getItem(rightPanelKey) === '1'); $('toggle-left').addEventListener('click', () => setPanelCollapsed('left', true)); $('show-left').addEventListener('click', () => setPanelCollapsed('left', false)); $('toggle-right').addEventListener('click', () => setPanelCollapsed('right', true)); $('show-right').addEventListener('click', () => setPanelCollapsed('right', false)); }

setupPanels();
$('open-settings').addEventListener('click', () => { $('settings-dialog').showModal(); loadMcpServers(); });
$('validate').addEventListener('click', validate); $('reload').addEventListener('click', async () => { await models(); fillProject(projectForId()); }); $('workspace').addEventListener('input', () => { validatedPath = ''; info('project-status', 'Noch nicht geprüft'); });
$('new-project').addEventListener('click', newProject); for (const input of document.querySelectorAll('input[name="project-kind"]')) input.addEventListener('change', applyKindToForm); $('save-project').addEventListener('click', saveProject); $('delete-project').addEventListener('click', deleteProject); $('refresh-index').addEventListener('click', refreshIndex); $('search-index').addEventListener('click', searchIndex); $('index-query').addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); searchIndex(); } });
$('new-conversation').addEventListener('click', newConversation); $('delete-conversation').addEventListener('click', () => deleteConversation()); $('hidden-conversations').addEventListener('change', event => reopenConversationTab(event.target.value)); $('delete-all-conversations').addEventListener('click', deleteAllConversations); $('start').addEventListener('click', start); $('cancel').addEventListener('click', cancel); $('save-memory').addEventListener('click', saveMemory);
$('copy-events').addEventListener('click', () => copyText([...$('active-timeline').children].map(item => item.innerText).join('\n'))); $('copy-trace').addEventListener('click', () => copyText($('decision-trace').textContent)); $('copy-log').addEventListener('click', () => copyText($('active-log').textContent)); $('copy-summary').addEventListener('click', () => copyText($('run-summary').dataset.text || '')); $('task').addEventListener('keydown', event => { if (event.key === 'Enter' && event.ctrlKey) { event.preventDefault(); start(); } });
(async () => { await models(); await loadProjects(); await loadProjectMemory(); await loadIndexStatus(); await loadFileTree(); restoreActive(); })();

// Project files stay in the existing index; the editor keeps only the files the
// user has explicitly opened and relies on the API's existing hash protection.
const editorFiles = new Map();
let activeEditorFile = '';

function editorLanguage(path) {
  const extension = path.split('.').pop()?.toLowerCase() || '';
  return ({ py: 'python', js: 'javascript', mjs: 'javascript', cjs: 'javascript', json: 'json', html: 'html', htm: 'html', css: 'css', md: 'markdown', markdown: 'markdown' })[extension] || 'text';
}
function escapeHtml(value) { return value.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#39;'); }
function tokenClass(token, language) {
  if (/^(#|\/\/|\/\*)/.test(token)) return 'token-comment';
  if (/^['"`]/.test(token)) return 'token-string';
  if (/^\d/.test(token)) return 'token-number';
  if (language === 'html' && /^<\/?/.test(token)) return 'token-tag';
  if (language === 'json' && /^['"]/.test(token) && /:\s*$/.test(token)) return 'token-property';
  return 'token-keyword';
}
function highlightLine(line, language) {
  if (language === 'text') return escapeHtml(line);
  const keywords = language === 'python' ? 'and|as|assert|async|await|break|class|def|del|elif|else|except|False|finally|for|from|if|import|in|is|lambda|None|not|or|pass|raise|return|True|try|while|with|yield' : language === 'javascript' ? 'async|await|break|case|catch|class|const|continue|default|delete|else|export|false|finally|for|from|function|if|import|in|let|new|null|return|switch|throw|true|try|typeof|undefined|while' : language === 'css' ? 'color|display|flex|grid|margin|padding|position|background|border|width|height' : 'true|false|null';
  const pattern = new RegExp(`(#[^\\n]*|//[^\\n]*|/\\*.*?\\*/|</?[A-Za-z][^>]*?>|\"(?:\\\\.|[^\"])*\"|'(?:\\\\.|[^'])*'|\\b(?:${keywords})\\b|\\b\\d+(?:\\.\\d+)?\\b)`, 'g');
  let cursor = 0;
  let output = '';
  for (const match of line.matchAll(pattern)) {
    output += escapeHtml(line.slice(cursor, match.index));
    output += `<span class="${tokenClass(match[0], language)}">${escapeHtml(match[0])}</span>`;
    cursor = match.index + match[0].length;
  }
  return output + escapeHtml(line.slice(cursor));
}
function editorCurrent() { return editorFiles.get(activeEditorFile) || null; }
function renderEditor() {
  const file = editorCurrent();
  const empty = $('editor-empty'); const workspace = $('editor-workspace');
  empty.hidden = Boolean(file); workspace.hidden = !file;
  if (!file) return;
  $('editor-content').value = file.content;
  $('editor-path').textContent = file.path;
  $('editor-notice').textContent = editorMessage || file.notice || ''; $('editor-notice').className = editorMessage ? 'err' : 'muted';
  $('editor-file-status').textContent = file.truncated ? 'Nur Teilansicht' : file.dirty ? 'Ungespeichert' : 'Gespeichert';
  $('save-file').disabled = Boolean(file.truncated) || !file.dirty;
  const language = editorLanguage(file.path);
  $('editor-highlight').innerHTML = file.content.split('\n').map((line, index) => `<span class="editor-line${file.marks?.has(index + 1) ? ' editor-line-mark' : ''}"><span class="editor-line-number">${index + 1}</span>${highlightLine(line, language) || ' '}</span>`).join('');
  const tabs = $('editor-file-tabs'); tabs.replaceChildren();
  for (const [path, item] of editorFiles) { const tab = document.createElement('button'); tab.type = 'button'; tab.className = `editor-file-tab${path === activeEditorFile ? ' active' : ''}`; tab.textContent = `${item.dirty ? '● ' : ''}${path.split('/').at(-1)}`; tab.title = path; tab.addEventListener('click', () => { activeEditorFile = path; editorMessage = ''; renderEditor(); }); tabs.appendChild(tab); }
  renderFileTree();
}
function showWorkspaceView(view) {
  if (view !== 'chat' && !isCodingContext()) view = 'chat';
  for (const name of ['chat', 'editor', 'diff']) { $(`${name}-panel`).hidden = view !== name; $(`show-${name}`).classList.toggle('active', view === name); }
  if (view === 'editor') renderEditor();
  if (view === 'diff') loadChanges();
}
function treeNode() { return { folders: new Map(), files: [] }; }
function renderFileTree() {
  const root = $('file-tree'); root.replaceChildren();
  if (!isCodingContext()) { root.innerHTML = '<p class="muted">Allgemeiner Chat ohne Workspace – keine Projektdateien.</p>'; return; }
  const items = root._files || [];
  if (!items.length) { root.innerHTML = '<p class="muted">Keine indexierten Dateien.</p>'; return; }
  const tree = treeNode();
  for (const item of items) { let node = tree; const parts = item.path.split('/'); for (const part of parts.slice(0, -1)) { if (!node.folders.has(part)) node.folders.set(part, treeNode()); node = node.folders.get(part); } node.files.push(item); }
  const renderBranch = (node, container) => {
    for (const [name, child] of [...node.folders].sort(([left], [right]) => left.localeCompare(right))) { const details = document.createElement('details'); details.open = true; const summary = document.createElement('summary'); summary.textContent = name; const nested = document.createElement('div'); renderBranch(child, nested); details.append(summary, nested); container.appendChild(details); }
    for (const item of [...node.files].sort((left, right) => left.path.localeCompare(right.path))) { const button = document.createElement('button'); button.type = 'button'; button.className = `file-leaf${item.path === activeEditorFile ? ' active' : ''}`; button.textContent = item.path.split('/').at(-1); button.title = `${item.path} · ${item.line_count || 0} Zeilen`; button.addEventListener('click', () => openEditorFile(item.path)); container.appendChild(button); }
  };
  renderBranch(tree, root);
}
async function loadFileTree() {
  const tree = $('file-tree');
  if (!isCodingContext()) { renderFileTree(); return; }
  tree.innerHTML = '<p class="muted">Dateibaum wird geladen …</p>';
  try { const data = await request(`/projects/${encodeURIComponent(activeProject)}/index/files?limit=2000`); tree._files = data.files || []; renderFileTree(); refreshPanelLinks(); }
  catch (error) { tree.innerHTML = `<p class="err">Dateibaum: ${escapeHtml(error.message)}</p>`; }
}
// Opens a project file; an open file with unsaved edits is only switched to, never reloaded.
async function openEditorFile(path) {
  if (!isCodingContext()) return false;
  showWorkspaceView('editor');
  if (editorFiles.get(path)?.dirty) { activeEditorFile = path; editorMessage = 'Ungespeicherte Änderungen bleiben erhalten – Datei wurde nicht neu geladen.'; renderEditor(); return true; }
  if (!editorFiles.size) { $('editor-empty').textContent = `Lade ${path} …`; $('editor-empty').hidden = false; $('editor-workspace').hidden = true; }
  try {
    const file = await request(`/projects/${encodeURIComponent(activeProject)}/files/content?path=${encodeURIComponent(path)}`);
    editorFiles.set(path, { path: file.path || path, content: file.content || '', hash: file.sha256, truncated: Boolean(file.truncated), dirty: false, notice: file.truncated ? `Große Datei: ${file.captured_length} von ${file.original_length} Zeichen geladen. Speichern ist deaktiviert.` : `${file.end_line || 0} Zeilen · ${editorLanguage(path)}` });
    activeEditorFile = path; editorMessage = ''; renderEditor(); return true;
  } catch (error) {
    const message = CodeLinks.missingFileMessage(path, error.message);
    if (editorFiles.size) { editorMessage = message; renderEditor(); } else { $('editor-empty').textContent = message; $('editor-empty').hidden = false; $('editor-workspace').hidden = true; }
    info('activity', message, 'err'); return false;
  }
}
// P07: navigation from run output and the Diff view into the editor. Only paths of the
// selected coding project become links; opening never changes or saves the file.
let editorMessage = '', linkContextCache = null;
function codeLinkContext(run = displayedRun) {
  if (!isCodingContext() || (run?.project_id && run.project_id !== activeProject)) return null;
  const workspace = projectForId()?.workspace; const items = $('file-tree')._files || [];
  if (!workspace) return null;
  if (linkContextCache?.workspace !== workspace || linkContextCache.items !== items) linkContextCache = { workspace, items, files: new Set(items.map(item => item.path)) };
  return linkContextCache;
}
function codeLink(text, target) {
  const link = document.createElement('button'); link.type = 'button'; link.className = `code-link${target.known ? '' : ' unindexed'}`; link.textContent = text;
  link.title = `Im Editor öffnen: ${target.path}${target.line ? `, Zeile ${target.line}` : ''}${target.known ? '' : ' (nicht im Index – evtl. neu oder gelöscht)'}`;
  link.addEventListener('click', event => { event.preventDefault(); event.stopPropagation(); navigateToCode(target); }); return link;
}
function appendLinkedText(container, text, context) { const value = String(text ?? ''); if (!context) { container.append(value); return; } for (const part of CodeLinks.findReferences(value, context)) container.append(part.target ? codeLink(part.text, part.target) : part.text); }
function lineRange(start, end) { const lines = []; for (let line = start; line <= Math.min(end, start + 2000); line += 1) lines.push(line); return lines; }
async function navigateToCode(target) {
  if (!isCodingContext() || !target?.path) return;
  if (!await openEditorFile(target.path)) return;
  const file = editorFiles.get(target.path); if (!file) return;
  const total = file.content.split('\n').length; let marks = [];
  if (target.highlightLines?.length) marks = target.highlightLines;
  else if (target.highlightText) { const found = CodeLinks.textLines(file.content, target.highlightText); if (found) marks = lineRange(found.start, found.end); else if (!editorMessage) editorMessage = 'Geänderte Stelle ist im aktuellen Dateistand nicht mehr auffindbar.'; }
  else if (target.line) marks = lineRange(target.line, target.endLine || target.line);
  marks = marks.filter(line => line <= total); file.marks = new Set(marks);
  const line = target.line || marks[0] || null;
  if (line && line > total && !editorMessage) editorMessage = `Zeile ${line} existiert nicht mehr (Datei hat ${total} Zeilen).`;
  renderEditor(); if (line) revealEditorLine(Math.min(line, total));
}
function revealEditorLine(line) {
  const textarea = $('editor-content'); const file = editorCurrent(); if (!file) return;
  const offset = file.content.split('\n').slice(0, line - 1).reduce((sum, text) => sum + text.length + 1, 0);
  textarea.focus({ preventScroll: true }); textarea.setSelectionRange(offset, offset);
  const row = $('editor-highlight').children[line - 1];
  textarea.scrollTop = Math.max(0, (row ? row.offsetTop : 0) - textarea.clientHeight / 3); $('editor-highlight').scrollTop = textarea.scrollTop;
}
async function saveEditorFile() {
  const file = editorCurrent(); if (!file || file.truncated || !file.dirty || !activeProject) return;
  $('save-file').disabled = true; $('editor-file-status').textContent = 'Speichert …';
  try { const saved = await request(`/projects/${encodeURIComponent(activeProject)}/files/content`, { method: 'PUT', body: JSON.stringify({ path: file.path, content: file.content, expected_hash: file.hash }) }); file.hash = saved.after_hash; file.dirty = false; file.notice = `Gespeichert · ${saved.bytes} Bytes`; renderEditor(); await loadFileTree(); info('activity', `Datei gespeichert: ${file.path}`, 'ok'); }
  catch (error) { file.notice = error.message.includes('conflict') ? 'Konflikt: Die Datei wurde extern geändert. Neu laden, bevor gespeichert wird.' : error.message; renderEditor(); info('activity', `Speichern fehlgeschlagen: ${error.message}`, 'err'); }
}

// Read-only Git comparisons: working tree against HEAD, one commit against its
// parent, or a release tag against HEAD (optionally with uncommitted changes).
// Only the GET change endpoints are called, so user changes are never touched.
let diffFiles = [], activeDiffFile = '', diffRefreshTimer = null, diffRefs = null, diffLoad = 0;
const diffState = { mode: 'worktree', commit: '', release: '' };
const diffWidthKey = 'reaper-ai-diff-list-width';
const diffStatusLabels = { modified: ['M', 'geändert'], added: ['A', 'hinzugefügt'], deleted: ['D', 'gelöscht'], renamed: ['R', 'umbenannt'], copied: ['C', 'kopiert'], conflict: ['U', 'Konflikt'], untracked: ['?', 'neu, ungetrackt'] };
function diffLineClass(line) {
  if (/^(diff |index |--- |\+\+\+ |new file|deleted file|old mode|new mode|similarity |dissimilarity |rename |copy |Binary |\\)/.test(line)) return 'diff-meta';
  if (line.startsWith('@@')) return 'diff-hunk';
  if (line.startsWith('+')) return 'diff-add';
  if (line.startsWith('-')) return 'diff-del';
  return 'diff-ctx';
}
// P07: with `numbers` (new-file line per diff line) lines become editor jump targets.
function renderDiffText(text, numbers = null) {
  const lines = text.replace(/\n$/, '').split('\n');
  $('diff-content').innerHTML = text ? lines.map((line, index) => { const target = numbers?.[index]; return `<span class="diff-line ${diffLineClass(line)}${target ? ` navigable" data-line="${target}" title="Im Editor öffnen, Zeile ${target}` : ''}">${escapeHtml(line) || ' '}</span>`; }).join('') : '<span class="diff-line diff-meta">Keine Textänderungen (z. B. nur Dateimodus).</span>';
}
// Line numbers match the editor only when the new side of the comparison is the working tree.
let diffNavigation = null;
function diffShowsWorktree() { return diffState.mode === 'worktree' || (diffState.mode === 'release' && $('diff-worktree').checked); }
function setDiffNavigation(path = '', status = '', text = '') {
  const button = $('diff-open-editor'); const lines = text && diffShowsWorktree() ? CodeLinks.diffLines(text) : null;
  diffNavigation = path && status !== 'deleted' && isCodingContext() ? { path, lines } : null;
  button.hidden = !path; button.disabled = !diffNavigation;
  button.title = !path ? '' : status === 'deleted' ? 'Datei wurde in diesem Vergleich gelöscht – im Editor nicht verfügbar.' : lines ? 'Datei im Editor öffnen und Änderungen hervorheben' : 'Datei im Editor öffnen (Working-Tree-Stand; Zeilen dieses Vergleichs können abweichen)';
  return lines;
}
function openDiffInEditor(line = null) {
  if (!diffNavigation) return;
  const { path, lines } = diffNavigation;
  navigateToCode({ path, line: line || lines?.first || null, highlightLines: lines?.added || [] });
}
function renderDiffList() {
  const list = $('diff-file-list'); list.replaceChildren();
  for (const item of diffFiles) {
    const [letter, label] = diffStatusLabels[item.status] || ['M', item.status];
    const button = document.createElement('button'); button.type = 'button'; button.className = `diff-file${item.path === activeDiffFile ? ' active' : ''}`;
    button.title = `${item.old_path ? `${item.old_path} → ` : ''}${item.path} · ${label}`;
    const badge = document.createElement('span'); badge.className = `diff-badge ${item.status}`; badge.textContent = letter;
    const slash = item.path.lastIndexOf('/');
    const text = document.createElement('span'); text.className = 'diff-file-text';
    const name = document.createElement('span'); name.className = 'diff-file-name'; name.textContent = item.path.slice(slash + 1);
    const dir = document.createElement('span'); dir.className = 'diff-file-dir'; dir.textContent = slash >= 0 ? item.path.slice(0, slash) : '.';
    text.append(name, dir); button.append(badge, text); button.addEventListener('click', () => showFileDiff(item.path)); list.appendChild(button);
  }
}
function resetDiffView(message = 'Keine Datei gewählt') { activeDiffFile = ''; $('diff-path').textContent = message; $('diff-content').replaceChildren(); setDiffNavigation(); }
function diffQuery() {
  const params = new URLSearchParams({ mode: diffState.mode });
  if (diffState.mode === 'worktree') params.set('untracked', $('diff-untracked').checked);
  else params.set('ref', diffState[diffState.mode]);
  if (diffState.mode === 'release') params.set('worktree', $('diff-worktree').checked);
  return params;
}
function diffComparisonText() {
  if (diffState.mode === 'commit') return `Commit ${diffState.commit.slice(0, 12)} gegen Vorgänger`;
  if (diffState.mode === 'release') return `${diffState.release} gegen ${$('diff-worktree').checked ? 'Working Tree' : 'HEAD'}`;
  return `Working Tree gegen HEAD${$('diff-untracked').checked ? ' inkl. ungetrackter Dateien' : ''}`;
}
function renderDiffControls() {
  const mode = diffState.mode, select = $('diff-ref');
  $('diff-mode').value = mode;
  $('diff-untracked-option').hidden = mode !== 'worktree'; $('diff-worktree-option').hidden = mode !== 'release'; select.hidden = mode === 'worktree';
  if (mode === 'worktree') return;
  const options = mode === 'commit' ? (diffRefs?.commits || []).map(item => [item.sha, `${item.short} · ${item.subject}`]) : (diffRefs?.tags || []).map(tag => [tag, tag]);
  select.replaceChildren(...options.map(([value, label]) => new Option(label, value)));
  if (!options.some(([value]) => value === diffState[mode])) diffState[mode] = options[0]?.[0] || '';
  select.value = diffState[mode];
}
async function loadDiffRefs() {
  try { const data = await request(`/projects/${encodeURIComponent(activeProject)}/changes/refs`); diffRefs = data.git ? data : { commits: [], tags: [] }; }
  catch (error) { diffRefs = { commits: [], tags: [] }; info('diff-summary', `Commits/Tags nicht verfügbar: ${error.message}`, 'err'); }
}
async function loadChanges({ refs = false } = {}) {
  if (!isCodingContext()) { diffFiles = []; renderDiffList(); resetDiffView(); info('diff-summary', 'Nur für Coding-Projekte verfügbar.'); return; }
  const load = ++diffLoad;
  if (diffState.mode !== 'worktree' && (refs || !diffRefs)) await loadDiffRefs();
  renderDiffControls();
  if (diffState.mode !== 'worktree' && !diffState[diffState.mode]) { diffFiles = []; renderDiffList(); resetDiffView(); info('diff-summary', diffState.mode === 'commit' ? 'Keine lokalen Commits vorhanden.' : 'Keine lokalen Release-Tags vorhanden.', 'err'); return; }
  info('diff-summary', 'Änderungen werden geladen …');
  try {
    const data = await request(`/projects/${encodeURIComponent(activeProject)}/changes?${diffQuery()}`);
    if (load !== diffLoad) return;
    if (!data.git) { diffFiles = []; renderDiffList(); resetDiffView(); info('diff-summary', 'Workspace ist kein Git-Repository.', 'err'); return; }
    diffFiles = data.files || [];
    info('diff-summary', diffFiles.length ? `${data.total} geänderte Datei(en)${data.truncated ? `, erste ${diffFiles.length} angezeigt` : ''}` : 'Keine Änderungen in diesem Vergleich.', diffFiles.length ? 'muted' : 'ok');
    if (!diffFiles.some(item => item.path === activeDiffFile)) resetDiffView(diffFiles.length ? 'Datei links auswählen' : 'Keine Datei gewählt');
    renderDiffList();
    if (activeDiffFile) await showFileDiff(activeDiffFile);
  } catch (error) { if (load === diffLoad) info('diff-summary', `Änderungen nicht verfügbar: ${error.message}`, 'err'); }
}
async function showFileDiff(path) {
  if (!activeProject) return;
  activeDiffFile = path; renderDiffList(); $('diff-path').textContent = path; setDiffNavigation();
  const params = diffQuery(); params.set('path', path); const comparison = diffComparisonText();
  try {
    const data = await request(`/projects/${encodeURIComponent(activeProject)}/changes/diff?${params}`);
    if (activeDiffFile !== path) return;
    $('diff-path').textContent = data.old_path ? `${data.old_path} → ${data.path}` : data.path;
    $('diff-notice').textContent = `${(diffStatusLabels[data.status] || ['', data.status])[1]} · ${comparison} · nur lesend${data.truncated ? ' · Diff gekürzt' : ''}`;
    const lines = setDiffNavigation(data.path || path, data.status, data.diff || '');
    renderDiffText(data.diff || '', diffNavigation && lines ? lines.numbers : null);
  } catch (error) { if (activeDiffFile === path) renderDiffText(`Diff nicht verfügbar: ${error.message}`); }
}
function scheduleDiffRefresh() { if ($('diff-panel').hidden) return; clearTimeout(diffRefreshTimer); diffRefreshTimer = setTimeout(loadChanges, 400); }
function setDiffListWidth(width, save = true) {
  const panel = $('diff-panel').getBoundingClientRect().width || window.innerWidth;
  const clamped = Math.round(Math.max(220, Math.min(width, panel * 0.7)));
  $('diff-panel').style.setProperty('--diff-list-width', `${clamped}px`);
  if (save) try { localStorage.setItem(diffWidthKey, String(clamped)); } catch {}
}
function setupDiffResizer() {
  const handle = $('diff-resizer');
  const saved = Number(localStorage.getItem(diffWidthKey));
  if (saved) $('diff-panel').style.setProperty('--diff-list-width', `${Math.max(220, saved)}px`);
  handle.addEventListener('pointerdown', event => {
    event.preventDefault(); handle.setPointerCapture(event.pointerId); handle.classList.add('dragging');
    const left = $('diff-panel').getBoundingClientRect().left;
    const move = moveEvent => setDiffListWidth(moveEvent.clientX - left, false);
    const stop = () => { handle.classList.remove('dragging'); handle.removeEventListener('pointermove', move); setDiffListWidth($('diff-files').getBoundingClientRect().width); };
    handle.addEventListener('pointermove', move); handle.addEventListener('pointerup', stop, { once: true }); handle.addEventListener('pointercancel', stop, { once: true });
  });
  handle.addEventListener('keydown', event => { if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return; event.preventDefault(); setDiffListWidth($('diff-files').getBoundingClientRect().width + (event.key === 'ArrowLeft' ? -20 : 20)); });
}

const baseSelectProject = selectProject;
selectProject = async function(projectId) { $('file-tree')._files = []; $('file-tree').innerHTML = '<p class="muted">Dateibaum wird geladen …</p>'; await baseSelectProject(projectId); editorFiles.clear(); activeEditorFile = ''; diffFiles = []; diffRefs = null; diffState.commit = ''; diffState.release = ''; resetDiffView(); showWorkspaceView('chat'); await loadFileTree(); };
const baseHandleEvent = handleEvent;
handleEvent = function(type, data, id, historical = false) { baseHandleEvent(type, data, id, historical); if (!historical && ['tool_completed', 'files_changed', 'run_completed'].includes(type)) scheduleDiffRefresh(); };
setupDiffResizer();
$('show-diff').addEventListener('click', () => showWorkspaceView('diff'));
$('refresh-diff').addEventListener('click', () => loadChanges({ refs: true }));
$('diff-open-editor').addEventListener('click', () => openDiffInEditor());
$('diff-content').addEventListener('click', event => { const line = event.target.closest?.('.diff-line[data-line]'); if (!line || String(window.getSelection?.() || '')) return; openDiffInEditor(Number(line.dataset.line)); });
$('diff-mode').addEventListener('change', event => { diffState.mode = event.target.value; loadChanges(); });
$('diff-ref').addEventListener('change', event => { diffState[diffState.mode] = event.target.value; loadChanges(); });
$('diff-untracked').addEventListener('change', () => loadChanges());
$('diff-worktree').addEventListener('change', () => loadChanges());
$('refresh-file-tree').addEventListener('click', loadFileTree);
$('show-chat').addEventListener('click', () => showWorkspaceView('chat'));
$('show-editor').addEventListener('click', () => showWorkspaceView('editor'));
$('editor-content').addEventListener('input', event => { const file = editorCurrent(); if (!file) return; file.content = event.target.value; file.dirty = true; file.marks = null; editorMessage = ''; renderEditor(); });
$('editor-content').addEventListener('scroll', event => { $('editor-highlight').scrollTop = event.target.scrollTop; $('editor-highlight').scrollLeft = event.target.scrollLeft; });
$('save-file').addEventListener('click', saveEditorFile);

// P05: global MCP servers live only in mcp.json; a project stores just the IDs
// of the servers its agents may use. Changes apply to the next run.
function mcpSummary(server) {
  const config = server.config || {};
  if (server.transport === 'http') return config.url || '';
  const env = config.env && typeof config.env === 'object' ? Object.keys(config.env) : [];
  return [config.command, ...(Array.isArray(config.args) ? config.args : [])].filter(Boolean).join(' ') + (env.length ? `  · env: ${env.join(', ')}` : '') + (config.cwd ? `  · cwd: ${config.cwd}` : '');
}
function mcpButton(label, handler, cls = 'secondary') { const button = document.createElement('button'); button.type = 'button'; button.className = cls; button.textContent = label; button.addEventListener('click', handler); return button; }
function mcpBadge(text, cls = '') { const badge = document.createElement('span'); badge.className = `mcp-badge ${cls}`.trim(); badge.textContent = text; return badge; }
function renderMcpServers() {
  const list = $('mcp-server-list'); list.replaceChildren();
  if (!mcpServers.length) { const empty = document.createElement('p'); empty.className = 'muted'; empty.textContent = 'Noch keine MCP-Server konfiguriert.'; list.appendChild(empty); }
  for (const server of mcpServers) {
    const test = mcpTests.get(server.id);
    const card = document.createElement('div'); card.className = `mcp-server${server.enabled ? '' : ' disabled'}`;
    const head = document.createElement('div'); head.className = 'mcp-server-head';
    const title = document.createElement('div'); title.className = 'mcp-server-title';
    const name = document.createElement('strong'); name.textContent = server.id;
    const status = test?.pending ? mcpBadge('Teste …') : test ? (test.ok ? mcpBadge(`Verbunden · ${test.tools.length} Tool(s)`, 'ok') : mcpBadge('Verbindung fehlgeschlagen', 'err')) : mcpBadge('Nicht getestet');
    title.append(name, mcpBadge(server.transport === 'http' ? 'HTTP' : 'stdio'), mcpBadge(server.enabled ? 'Aktiviert' : 'Deaktiviert', server.enabled ? 'ok' : ''), status);
    const actions = document.createElement('div'); actions.className = 'path-row';
    actions.append(mcpButton('Testen', () => testMcpServer(server.id)), mcpButton('Bearbeiten', () => editMcpServer(server)), mcpButton(server.enabled ? 'Deaktivieren' : 'Aktivieren', () => toggleMcpServer(server)), mcpButton('Entfernen', () => deleteMcpServer(server.id), 'secondary danger'));
    head.append(title, actions);
    const summary = document.createElement('code'); summary.textContent = mcpSummary(server);
    card.append(head, summary);
    if (server.config_error) { const problem = document.createElement('span'); problem.className = 'err'; problem.textContent = `Konfigurationsfehler: ${server.config_error}`; card.appendChild(problem); }
    if (test && !test.pending && !test.ok) { const problem = document.createElement('span'); problem.className = 'err'; problem.textContent = test.error; card.appendChild(problem); }
    if (test?.ok) {
      const box = document.createElement('details'); const label = document.createElement('summary'); label.textContent = `Verfügbare Tools (${test.tools.length})`;
      const tools = document.createElement('ul'); tools.className = 'mcp-tools';
      for (const tool of test.tools) { const item = document.createElement('li'); const toolName = document.createElement('strong'); toolName.textContent = tool.name; const description = document.createElement('span'); description.className = 'muted'; description.textContent = tool.description ? ` – ${tool.description}` : ''; item.append(toolName, description); const parameters = (tool.parameters || []).map(param => `${param.name}${param.required ? '' : '?'}${param.type ? `: ${param.type}` : ''}${param.description ? ` (${param.description})` : ''}`); if (parameters.length) { const params = document.createElement('div'); params.className = 'mcp-params'; params.textContent = `Parameter: ${parameters.join(', ')}`; item.appendChild(params); } tools.appendChild(item); }
      box.append(label, tools); card.appendChild(box);
    }
    list.appendChild(card);
  }
}
function selectedProjectMcp() { return [...document.querySelectorAll('#project-mcp-servers input:checked')].map(input => input.value).filter(id => mcpServers.some(server => server.id === id)); }
function renderProjectMcp(project = projectForId()) {
  const container = $('project-mcp-servers'); container.replaceChildren();
  const selected = new Set(project?.mcp_servers || []);
  if (!mcpServers.length) { const empty = document.createElement('span'); empty.className = 'muted'; empty.textContent = 'Keine globalen MCP-Server konfiguriert.'; container.appendChild(empty); return; }
  for (const server of mcpServers) {
    const label = document.createElement('label'); const input = document.createElement('input'); input.type = 'checkbox'; input.value = server.id; input.checked = selected.has(server.id);
    const text = document.createElement('span'); text.textContent = server.id; label.append(input, text);
    if (!server.enabled) { const note = document.createElement('span'); note.className = 'muted'; note.textContent = '(global deaktiviert)'; label.appendChild(note); }
    container.appendChild(label);
  }
}
async function loadMcpServers() {
  try { const data = await request('/mcp/servers'); mcpServers = data.servers || []; info('mcp-config-path', `Quelle: ${data.config_path}${data.exists ? '' : ' (wird beim ersten Speichern angelegt)'}`, 'muted'); }
  catch (error) { mcpServers = []; info('mcp-config-path', `MCP-Konfiguration nicht lesbar: ${error.message}`, 'err'); }
  renderMcpServers(); renderProjectMcp(activeProject ? projectForId() : { mcp_servers: selectedProjectMcp() });
}
async function refreshProjectSelections() { try { projects = (await request('/projects')).projects || []; renderProjectMcp(activeProject ? projectForId() : { mcp_servers: selectedProjectMcp() }); } catch (error) { info('project-select-status', error.message, 'err'); } }
async function testMcpServer(id) {
  mcpTests.set(id, { pending: true }); renderMcpServers();
  try { mcpTests.set(id, await request(`/mcp/servers/${encodeURIComponent(id)}/test`, { method: 'POST' })); }
  catch (error) { mcpTests.set(id, { ok: false, error: error.message, tools: [] }); }
  renderMcpServers();
}
function showMcpTransport() { const transport = $('mcp-transport').value; for (const label of document.querySelectorAll('#mcp-form [data-mcp]')) label.hidden = label.dataset.mcp !== transport; }
function editMcpServer(server = null) {
  editingMcp = server; const config = server?.config || {};
  $('mcp-id').value = server?.id || ''; $('mcp-transport').value = server?.transport || 'stdio';
  $('mcp-url').value = config.url || ''; $('mcp-command').value = config.command || '';
  $('mcp-args').value = Array.isArray(config.args) ? config.args.join('\n') : '';
  $('mcp-env').value = config.env && typeof config.env === 'object' ? Object.entries(config.env).map(([key, value]) => `${key}=${value}`).join('\n') : '';
  $('mcp-cwd').value = config.cwd || ''; $('mcp-timeout').value = config.timeout ?? '';
  $('mcp-form').hidden = false; showMcpTransport(); $('mcp-id').focus();
  info('mcp-status', server ? `Bearbeite ${server.id}` : 'Neuen MCP-Server anlegen', 'muted');
}
function mcpFormConfig() {
  // Keep keys the form does not know (for example disabled) and replace the transport fields.
  const config = { ...(editingMcp?.config || {}) };
  for (const key of ['url', 'command', 'args', 'env', 'cwd', 'timeout']) delete config[key];
  if ($('mcp-transport').value === 'http') config.url = $('mcp-url').value.trim();
  else {
    config.command = $('mcp-command').value.trim();
    config.args = $('mcp-args').value.split('\n').map(line => line.trim()).filter(Boolean);
    const env = {};
    for (const line of $('mcp-env').value.split('\n').map(item => item.trim()).filter(Boolean)) { const index = line.indexOf('='); if (index < 1) throw Error(`Ungültige Umgebungsvariable: ${line}`); env[line.slice(0, index).trim()] = line.slice(index + 1); }
    if (Object.keys(env).length) config.env = env;
    if ($('mcp-cwd').value.trim()) config.cwd = $('mcp-cwd').value.trim();
  }
  if ($('mcp-timeout').value !== '') config.timeout = Number($('mcp-timeout').value);
  return config;
}
async function saveMcpServer() {
  const id = $('mcp-id').value.trim();
  if (!id) { info('mcp-status', 'Name (Server-ID) ist erforderlich', 'err'); return; }
  try {
    const body = { config: mcpFormConfig() }; if (editingMcp && editingMcp.id !== id) body.previous_id = editingMcp.id;
    await request(`/mcp/servers/${encodeURIComponent(id)}`, { method: 'PUT', body: JSON.stringify(body) });
    if (editingMcp) mcpTests.delete(editingMcp.id); mcpTests.delete(id);
    editingMcp = null; $('mcp-form').hidden = true; await loadMcpServers(); await refreshProjectSelections();
    info('mcp-status', `MCP-Server ${id} gespeichert · gilt ab dem nächsten Run`, 'ok');
  } catch (error) { info('mcp-status', error.message, 'err'); }
}
async function toggleMcpServer(server) {
  try { await request(`/mcp/servers/${encodeURIComponent(server.id)}`, { method: 'PATCH', body: JSON.stringify({ enabled: !server.enabled }) }); await loadMcpServers(); info('mcp-status', `${server.id} ${server.enabled ? 'deaktiviert' : 'aktiviert'} · gilt ab dem nächsten Run`, 'ok'); }
  catch (error) { info('mcp-status', error.message, 'err'); }
}
async function deleteMcpServer(id) {
  if (!confirm(`MCP-Server „${id}“ aus mcp.json entfernen? Die Auswahl in allen Projekten wird ebenfalls entfernt.`)) return;
  try { await request(`/mcp/servers/${encodeURIComponent(id)}`, { method: 'DELETE' }); mcpTests.delete(id); if (editingMcp?.id === id) { editingMcp = null; $('mcp-form').hidden = true; } await loadMcpServers(); await refreshProjectSelections(); info('mcp-status', `MCP-Server ${id} entfernt`, 'ok'); }
  catch (error) { info('mcp-status', error.message, 'err'); }
}
$('mcp-new').addEventListener('click', () => editMcpServer(null));
$('mcp-reload').addEventListener('click', loadMcpServers);
$('mcp-transport').addEventListener('change', showMcpTransport);
$('mcp-save').addEventListener('click', saveMcpServer);
$('mcp-cancel').addEventListener('click', () => { editingMcp = null; $('mcp-form').hidden = true; info('mcp-status', 'Bearbeitung abgebrochen', 'muted'); });
loadMcpServers();
