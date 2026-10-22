// Copyright (c) 2026 Robert Backhaus

// P07: pure helpers that map file references in run output (tool arguments,
// decision traces, summaries, Git diffs) to paths of the current coding project.
// No DOM access here, so the same file runs in the browser (window.CodeLinks)
// and under Node tests.
'use strict';

(function (root) {
  // Optional drive or root, directories, and a file name with an extension that
  // starts with a letter; an optional ":line", ":line:column" or ":start-end".
  const referencePattern = /((?:[A-Za-z]:[\\/]+|[\\/]+|\.{1,2}[\\/]+)?(?:[\w.@+-]+[\\/]+)*[\w@+-][\w.@+-]*\.[A-Za-z][A-Za-z0-9_]{0,9})(?::(\d+)(?:(-|:)(\d+))?)?/g;

  function isAbsolute(path) { return /^[A-Za-z]:\//.test(path) || path.startsWith('/'); }

  // Returns the project-relative POSIX path, or null when the path leaves the workspace.
  function projectPath(workspace, raw) {
    if (typeof raw !== 'string' || !raw.trim()) return null;
    let path = raw.trim().replace(/\\/g, '/').replace(/\/{2,}/g, '/');
    if (isAbsolute(path)) {
      if (typeof workspace !== 'string' || !workspace.trim()) return null;
      const root = workspace.trim().replace(/\\/g, '/').replace(/\/{2,}/g, '/').replace(/\/+$/, '');
      // Windows paths compare case-insensitively; POSIX paths exactly.
      const windows = /^[A-Za-z]:/.test(root);
      const same = (left, right) => windows ? left.toLowerCase() === right.toLowerCase() : left === right;
      if (!same(path.slice(0, root.length), root) || path.charAt(root.length) !== '/') return null;
      path = path.slice(root.length + 1);
    }
    const parts = [];
    for (const part of path.split('/')) {
      if (!part || part === '.') continue;
      if (part === '..') { if (!parts.length) return null; parts.pop(); continue; }
      parts.push(part);
    }
    return parts.length ? parts.join('/') : null;
  }

  function positiveInt(value) { const number = Number(value); return Number.isInteger(number) && number > 0 ? number : null; }

  // Navigation target for one reference: {path, line, endLine, known} or null.
  // A reference counts when it lies inside the workspace and is either a file of
  // the project index, contains a directory, or was named explicitly (force).
  function resolve(raw, context, { line = null, endLine = null, force = false } = {}) {
    if (!context) return null;
    const path = projectPath(context.workspace, raw);
    if (!path) return null;
    const known = Boolean(context.files && context.files.has(path));
    if (!known && !force && !path.includes('/')) return null;
    const start = positiveInt(line);
    const end = start ? positiveInt(endLine) : null;
    return { path, line: start, endLine: end && end >= start ? end : null, known };
  }

  // Splits free text into plain strings and resolved references.
  function findReferences(text, context) {
    const parts = [];
    if (typeof text !== 'string' || !text || !context) return [{ text: text || '' }];
    let cursor = 0;
    for (const match of text.matchAll(referencePattern)) {
      const start = match.index;
      // Skip URLs, e-mail addresses and fragments of longer words.
      const head = text.slice(text.slice(0, start).search(/\S*$/), start + 3);
      if (/[\w@]$/.test(text.slice(0, start)) || head.includes('://')) continue;
      const token = match[0], raw = match[1], endLine = match[3] === '-' ? match[4] : null;
      let line = match[2];
      // Python tracebacks: File "path", line 12
      const traceback = !line && /^",? line (\d+)/.exec(text.slice(start + token.length));
      if (traceback) line = traceback[1];
      const target = resolve(raw, context, { line, endLine });
      if (!target) continue;
      if (start > cursor) parts.push({ text: text.slice(cursor, start) });
      parts.push({ text: token, target });
      cursor = start + token.length;
    }
    if (cursor < text.length || !parts.length) parts.push({ text: text.slice(cursor) });
    return parts;
  }

  // Target of a tool action argument, with the lines the action affected when known.
  function actionTarget(action, context) {
    const args = action && typeof action.args === 'object' && action.args ? action.args : {};
    if (typeof args.path !== 'string' || !args.path) return null;
    const target = resolve(args.path, context, { line: args.start_line, endLine: args.end_line, force: true });
    if (target && action.tool === 'apply_patch' && typeof args.replacement === 'string' && args.replacement.trim()) target.highlightText = args.replacement;
    return target;
  }

  // New-file line number for each line of a unified diff, plus all added lines.
  function diffLines(text) {
    const lines = typeof text === 'string' && text ? text.replace(/\n$/, '').split('\n') : [];
    const numbers = []; const added = []; let next = null;
    for (const line of lines) {
      const hunk = /^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(line);
      if (hunk) { next = Number(hunk[1]); numbers.push(Math.max(1, next)); continue; }
      if (next === null || /^(diff |index |--- |\+\+\+ |\\)/.test(line)) { numbers.push(null); continue; }
      if (line.startsWith('-')) { numbers.push(Math.max(1, next)); continue; }
      if (line.startsWith('+')) added.push(next);
      numbers.push(next); next += 1;
    }
    return { numbers, added, first: added[0] ?? numbers.find(value => value !== null) ?? null };
  }

  // 1-based line range of the first occurrence of text in content, or null.
  function textLines(content, text) {
    if (typeof content !== 'string' || typeof text !== 'string' || !text.trim()) return null;
    const index = content.indexOf(text);
    if (index < 0) return null;
    const start = content.slice(0, index).split('\n').length;
    const end = start + text.replace(/\n$/, '').split('\n').length - 1;
    return { start, end };
  }

  function missingFileMessage(path, error) {
    const message = String(error || '');
    if (/no such file|errno 2\b|not found|cannot find|winerror 2\b|winerror 3\b/i.test(message)) return `Datei nicht mehr vorhanden: ${path} (gelöscht, verschoben oder umbenannt).`;
    if (/is a directory|errno 21\b|errno 13\b|permission denied/i.test(message)) return `${path} kann nicht als Textdatei geöffnet werden: ${message}`;
    return `${path} kann nicht im Editor geöffnet werden: ${message}`;
  }

  const api = { projectPath, resolve, findReferences, actionTarget, diffLines, textLines, missingFileMessage };
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.CodeLinks = api;
})(typeof window !== 'undefined' ? window : globalThis);
