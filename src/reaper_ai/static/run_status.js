// Copyright (c) 2026 Robert Backhaus

// P06: pure helpers that turn the existing SSE events and the stored run result
// into the agent status and run summary of the right panel. No DOM access here,
// so the same file runs in the browser (window.RunStatus) and under Node tests.
'use strict';

(function (root) {
  const statusLabels = {
    running: 'läuft', cancelling: 'wird abgebrochen', completed: 'abgeschlossen', partial: 'teilweise abgeschlossen',
    cancelled: 'abgebrochen', timed_out: 'Timeout', failed: 'Fehler', interrupted: 'unterbrochen',
  };
  const phaseLabels = {
    agent_prepare: 'Run vorbereiten', agent_decide: 'Modell entscheidet über nächsten Schritt',
    agent_execute: 'Werkzeug ausführen', agent_observe: 'Ergebnis prüfen', agent_finalize: 'Abschluss erstellen',
  };
  const testPattern = /\b(pytest|unittest|nose2|tox|nox|npm (run )?test|pnpm (run )?test|yarn (run )?test|jest|vitest|mocha|go test|cargo test|dotnet test|mvn (-\S+ )*test|gradle(w)? test|ctest|phpunit|rspec|Invoke-Pester)\b/i;

  function statusLabel(status) { return statusLabels[status] || status || 'unbekannt'; }
  function statusTone(status) {
    if (status === 'running' || status === 'cancelling') return 'running';
    if (status === 'completed') return 'ok';
    if (status === 'partial' || status === 'cancelled' || status === 'interrupted') return 'warn';
    return status ? 'err' : 'muted';
  }
  function short(value, limit = 120) { const text = String(value ?? '').replace(/\s+/g, ' ').trim(); return text.length > limit ? `${text.slice(0, limit - 1)}…` : text; }
  function isTestCommand(command) { return typeof command === 'string' && testPattern.test(command); }

  // Human-readable activity for one agent action; only arguments the action really carried are shown.
  function toolActivity(tool, args = {}) {
    const name = typeof tool === 'string' ? tool : '';
    const path = typeof args?.path === 'string' ? args.path : '';
    if (name === 'read_file' || name === 'read_file_range') return `Datei lesen${path ? `: ${path}` : ''}`;
    if (name === 'write_file' || name === 'apply_patch') return `Code ändern${path ? `: ${path}` : ''}`;
    if (name === 'search_text') return `Code durchsuchen${args?.query ? `: ${short(args.query, 60)}` : ''}`;
    if (name === 'list_files' || name.startsWith('index_')) return `Projekt erkunden${path ? `: ${path}` : ''}`;
    if (name === 'git_status' || name === 'git_diff') return 'Änderungen prüfen (Git)';
    if (name === 'find_python') return 'Python-Umgebung ermitteln';
    if (name === 'shell') return `${isTestCommand(args?.command) ? 'Tests ausführen' : 'Befehl ausführen'}${args?.command ? `: ${short(args.command, 80)}` : ''}`;
    if (name.startsWith('mcp:')) return `MCP-Werkzeug: ${name.slice(4)}`;
    return name ? `Werkzeug: ${name}` : 'Werkzeug ausführen';
  }

  function newStats() {
    return { startedAt: null, finishedAt: null, status: '', activity: '', lastAction: null, pendingAction: null,
             events: 0, modelCalls: 0, toolCalls: 0, modelActive: false, completed: false, result: null };
  }

  // Applies one SSE event. Callers deduplicate by SSE event id before calling.
  function applyEvent(stats, type, data = {}) {
    const time = typeof data.timestamp === 'number' ? data.timestamp * 1000 : null;
    stats.events += 1;
    if (type === 'run_started' || type === 'recovery_started') { if (time && !stats.startedAt) stats.startedAt = time; stats.status = 'running'; if (type === 'recovery_started') stats.activity = `Recovery ab ${data.next_stage || 'Checkpoint'}`; }
    else if (type === 'phase_changed') { if (!stats.modelActive) stats.activity = phaseLabels[data.phase] || data.phase || stats.activity; }
    else if (type === 'model_activity') {
      if (data.status === 'started') { stats.modelCalls += 1; stats.modelActive = true; stats.activity = `Modell denkt nach${data.model ? ` (${data.model})` : ''}`; }
      else if (data.status === 'completed') stats.modelActive = false;
    } else if (type === 'agent_decision') {
      stats.activity = data.decision_kind === 'final' ? 'Ergebnis zusammenfassen' : data.decision_kind === 'invalid' ? 'Ungültige Modellentscheidung, neuer Versuch' : `Nächster Schritt: ${toolActivity(data.tool, data.args)}`;
    } else if (type === 'tool_started') {
      const args = data.action?.args || {};
      stats.pendingAction = { tool: data.tool, label: toolActivity(data.tool, args) };
      stats.activity = stats.pendingAction.label;
    } else if (type === 'tool_completed') {
      const started = stats.pendingAction && stats.pendingAction.tool === data.tool ? stats.pendingAction : { tool: data.tool, label: toolActivity(data.tool) };
      stats.toolCalls += 1;
      stats.lastAction = { tool: data.tool, label: started.label, ok: Boolean(data.ok), exitCode: data.exit_code ?? null,
                           durationMs: typeof data.duration_ms === 'number' ? data.duration_ms : null, error: data.ok ? '' : short(data.error, 200) };
      stats.pendingAction = null;
    } else if (type === 'run_completed') {
      stats.completed = true; stats.status = data.status || stats.status; stats.result = data.result || stats.result;
      if (time) stats.finishedAt = time;
      stats.activity = `Run ${statusLabel(stats.status)}`; stats.modelActive = false;
    }
    return stats;
  }

  function lastActionText(action) {
    if (!action) return '';
    const parts = [action.label, action.ok ? 'erfolgreich' : 'fehlgeschlagen'];
    if (action.exitCode !== null && action.exitCode !== undefined) parts.push(`Exit-Code ${action.exitCode}`);
    if (action.durationMs !== null) parts.push(formatDuration(action.durationMs / 1000));
    return parts.join(' · ') + (action.error ? `\n${action.error}` : '');
  }

  function formatDuration(seconds) {
    if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds < 0) return '';
    if (seconds < 10) return `${Math.round(seconds * 10) / 10} s`;
    const total = Math.round(seconds); const hours = Math.floor(total / 3600); const minutes = Math.floor((total % 3600) / 60); const rest = total % 60;
    if (hours) return `${hours} h ${minutes} min`;
    return minutes ? `${minutes} min ${String(rest).padStart(2, '0')} s` : `${rest} s`;
  }
  function parseTime(value) { if (typeof value !== 'string' || !value) return null; const time = Date.parse(value); return Number.isNaN(time) ? null : time; }
  function elapsedSeconds(run, stats, now = Date.now()) {
    const started = parseTime(run?.started_at) ?? stats?.startedAt;
    if (!started) return null;
    const finished = parseTime(run?.finished_at) ?? (stats?.completed ? stats.finishedAt : null);
    return Math.max(0, ((finished ?? now) - started) / 1000);
  }

  // Live status block while a run is displayed; counters come only from received events.
  function liveStatus(run, stats, now = Date.now()) {
    const status = run?.status || stats?.status || '';
    const terminal = !['running', 'cancelling', ''].includes(status);
    return {
      status, label: statusLabel(status), tone: statusTone(status),
      activity: terminal ? `Run ${statusLabel(status)}` : (status === 'cancelling' ? 'Abbruch angefordert …' : stats?.activity || 'Wartet auf erstes Ereignis …'),
      lastAction: lastActionText(stats?.lastAction) || (terminal ? 'Keine Aktion abgeschlossen.' : 'Noch keine Aktion abgeschlossen.'),
      elapsed: formatDuration(elapsedSeconds(run, stats, now)) || 'unbekannt',
      modelCalls: stats ? stats.modelCalls : null, toolCalls: stats ? stats.toolCalls : null,
    };
  }

  function checkEntries(result) {
    return Array.isArray(result?.checks) ? result.checks.filter(item => item && typeof item === 'object' && item.action && item.result) : null;
  }

  // Summary of a finished run from the stored result; missing facts stay missing.
  function summarize(run, stats) {
    const result = run?.result && typeof run.result === 'object' ? run.result : (stats?.result || null);
    const status = run?.status || result?.status || stats?.status || '';
    const checks = checkEntries(result);
    const changed = Array.isArray(result?.changed_files) ? [...new Set(result.changed_files.filter(item => typeof item === 'string'))] : null;
    const tests = checks ? checks.filter(item => item.action.tool === 'shell' && isTestCommand(item.action.args?.command)).map(item => ({
      command: short(item.action.args.command, 160), ok: Boolean(item.result.ok), exitCode: item.result.exit_code ?? null,
    })) : null;
    const errors = [];
    if (result?.reason && status !== 'completed') errors.push(short(result.reason, 400));
    if (checks) for (const item of checks.filter(entry => !entry.result.ok).slice(-5)) errors.push(`${toolActivity(item.action.tool, item.action.args)}: ${short(item.result.error || 'fehlgeschlagen', 200)}`);
    if (run?.storage_error) errors.push(`Speicherfehler: ${short(run.storage_error, 200)}`);
    const open = Array.isArray(result?.unresolved_questions) ? result.unresolved_questions.map(item => short(item.summary || item.question || '', 300)).filter(Boolean) : [];
    // A user cancel is not an error; other runs without a stored result are.
    if (!result && status === 'cancelled') open.push('Run wurde abgebrochen, bevor ein Ergebnis gespeichert wurde.');
    else if (!result && status && status !== 'completed') errors.push('Kein gespeichertes Ergebnis vorhanden.');
    const usage = result?.usage && typeof result.usage === 'object' ? result.usage : {};
    // Counted events are used only when the stored result lacks the numbers.
    const fromEvents = Boolean(stats && (stats.completed || stats.events));
    const duration = typeof usage.elapsed_seconds === 'number' ? usage.elapsed_seconds : elapsedSeconds(run, stats);
    return {
      runId: run?.run_id || result?.run_id || '', status, label: statusLabel(status), tone: statusTone(status),
      outcome: typeof result?.summary === 'string' && result.summary.trim() ? short(result.summary, 600) : '',
      changedFiles: changed, tests, errors, open,
      duration: formatDuration(duration ?? NaN),
      modelCalls: typeof usage.model_calls === 'number' ? usage.model_calls : fromEvents ? stats.modelCalls : null,
      toolCalls: typeof result?.tool_calls === 'number' ? result.tool_calls : checks && checks.length ? checks.length : fromEvents ? stats.toolCalls : null,
    };
  }

  function summaryText(summary) {
    const unknown = 'nicht erfasst';
    const lines = [`Run ${summary.runId}: ${summary.label}`];
    if (summary.outcome) lines.push(`Ergebnis: ${summary.outcome}`);
    lines.push(`Geänderte Dateien: ${summary.changedFiles === null ? unknown : summary.changedFiles.length ? summary.changedFiles.join(', ') : 'keine'}`);
    lines.push(`Tests: ${summary.tests === null ? unknown : summary.tests.length ? '' : 'keine Testläufe erfasst'}`.trimEnd());
    for (const test of summary.tests || []) lines.push(`  ${test.ok ? '✓' : '✗'} ${test.command}${test.exitCode !== null ? ` (Exit-Code ${test.exitCode})` : ''}`);
    if (summary.errors.length) { lines.push('Fehler:'); for (const error of summary.errors) lines.push(`  - ${error}`); }
    if (summary.open.length) { lines.push('Offene Punkte:'); for (const item of summary.open) lines.push(`  - ${item}`); }
    lines.push(`Laufzeit: ${summary.duration || unknown}`);
    lines.push(`Modellaufrufe: ${summary.modelCalls ?? unknown} · Tool-Aufrufe: ${summary.toolCalls ?? unknown}`);
    return lines.join('\n');
  }

  const api = { statusLabel, statusTone, toolActivity, isTestCommand, newStats, applyEvent, lastActionText, formatDuration, elapsedSeconds, liveStatus, summarize, summaryText };
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.RunStatus = api;
})(typeof window !== 'undefined' ? window : globalThis);
