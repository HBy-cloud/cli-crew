const timeFormat = new Intl.DateTimeFormat(undefined, {hour: '2-digit', minute: '2-digit', second: '2-digit'});

export function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function pill(status) {
  const active = /running|waiting/i.test(status);
  const issue = /failed|timeout|orphan|stale|blocked|unknown|interrupted|exit [1-9]/i.test(status);
  return element('span', status, active ? 'pill active' : issue ? 'pill issue' : 'pill');
}

function time(at) {
  const date = new Date(at);
  if (!at || !Number.isFinite(date.valueOf())) return element('time', 'Unknown');
  const node = element('time', timeFormat.format(date));
  node.dateTime = date.toISOString();
  node.title = date.toLocaleString();
  return node;
}

export function duration(from, to) {
  const seconds = Math.floor((new Date(to || Date.now()) - new Date(from)) / 1000);
  if (!Number.isFinite(seconds) || seconds < 0) return 'Elapsed time unknown';
  return seconds >= 60 ? `${Math.floor(seconds / 60)}m ${seconds % 60}s` : `${seconds}s`;
}

export function commandLabel(record) {
  return ['crew', record.command, record.task_id].filter(Boolean).join(' ');
}

function commandStatus(record) {
  if (record.interrupted) return 'Interrupted';
  if (!record.ended) return 'Running';
  return record.exit_code == null ? 'Failed' : `Exit ${record.exit_code}`;
}

function commandEvents(record) {
  const label = commandLabel(record);
  const events = [{at: record.started, label: `Started ${label}`}];
  if (record.ended) events.push({at: record.ended, label: `Finished ${label}`, status: commandStatus(record)});
  return events;
}

export function buildEntries(view) {
  const tasks = view.tasks.map(task => ({key: `task:${task.task_id}`, kind: 'task', title: task.task_id,
    group: task.plan || 'Unlinked workers', status: task.status, data: task}));
  const contracts = (view.pending_contracts || []).map(contract => ({key: `task:${contract.task_id}`,
    kind: 'contract', title: contract.task_id, group: contract.plan || 'Saved contracts', status: 'Not run', data: contract}));
  const sessions = Object.entries(view.sessions).map(([id, session]) => ({key: `main:${id}`, kind: 'session',
    title: `Claude · ${id}`, group: session.plan || 'Main sessions', data: session,
    status: session.children.some(child => child.status === 'Running') ? 'Running' : 'Observed'}));
  const commands = view.commands.map(record => ({key: `command:${record.id}`, kind: 'command',
    title: commandLabel(record), status: commandStatus(record), data: record}));
  return {runs: [...tasks, ...sessions, ...contracts], commands};
}

export function renderBoard(container, entries, selection, onSelect) {
  const columns = new Map([['Not run', []], ['Running', []], ['Needs review', []], ['Complete', []]]);
  const cards = entries.map(entry => ({entry, attempt: entry.data.attempts?.at(-1)?.n, verdict: entry.data.verdict}));
  const fingerprint = JSON.stringify([selection, cards.map(({entry, attempt, verdict}) =>
    [entry.key, entry.kind, entry.status, entry.data.contract, attempt, verdict])]);
  if (container.dataset.fingerprint === fingerprint) return;
  container.dataset.fingerprint = fingerprint;
  for (const card of cards) {
    const {entry, verdict} = card;
    const lane = entry.kind === 'contract' ? 'Not run' : verdict === 'ACCEPTED' ? 'Complete'
      : entry.status === 'RUNNING' ? 'Running' : 'Needs review';
    columns.get(lane).push(card);
  }

  const focused = document.activeElement?.dataset?.entry;
  let restoreFocus;
  container.replaceChildren(...Array.from(columns, ([label, cardsInLane], index) => {
    const section = element('section', null, 'board-lane');
    const heading = element('h3', `${label} (${cardsInLane.length})`);
    heading.id = `lane-${index}`;
    section.setAttribute('aria-labelledby', heading.id);
    section.append(heading);
    for (const {entry, attempt, verdict} of cardsInLane) {
      const button = element('button', null, 'entry board-card');
      button.type = 'button';
      button.dataset.entry = entry.key;
      button.setAttribute('aria-current', String(entry.key === selection));
      button.append(element('span', entry.title), pill(entry.status));
      if (attempt || verdict) button.append(element('span', [attempt && `Attempt ${attempt}`, verdict].filter(Boolean).join(' · '), 'muted'));
      button.onclick = () => onSelect(entry);
      section.append(button);
      if (entry.key === focused) restoreFocus = button;
    }
    return section;
  }));
  restoreFocus?.focus({preventScroll: true});
}

export function renderList(container, entries, selection, onSelect) {
  const fingerprint = JSON.stringify([selection, entries.map(({key, title, group, status}) => [key, title, group, status])]);
  if (container.dataset.fingerprint === fingerprint) return;
  container.dataset.fingerprint = fingerprint;
  const focused = document.activeElement?.dataset?.entry;
  const groups = new Map();
  for (const entry of entries) {
    if (!groups.has(entry.group)) groups.set(entry.group, []);
    groups.get(entry.group).push(entry);
  }
  const nodes = [];
  let restoreFocus;
  for (const [group, items] of groups) {
    const section = element('div', null, 'run-group');
    if (group) section.append(element('h3', group));
    for (const entry of items) {
      const button = element('button', null, 'entry');
      button.type = 'button';
      button.dataset.entry = entry.key;
      button.setAttribute('aria-current', String(entry.key === selection));
      button.append(element('span', entry.title), pill(entry.status));
      button.onclick = () => onSelect(entry.key);
      section.append(button);
      if (entry.key === focused) restoreFocus = button;
    }
    nodes.push(section);
  }
  container.replaceChildren(...nodes);
  restoreFocus?.focus({preventScroll: true});
}

export function renderSummary(container, entry) {
  if (!entry) {
    container.replaceChildren();
    return;
  }
  const summary = [pill(entry.status)];
  if (entry.kind === 'task') {
    const task = entry.data;
    const model = task.model_actual || task.model_requested || task.model_resolved;
    const metadata = [task.agent, task.mode, model, task.plan_step].filter(Boolean).join(' · ');
    if (metadata) summary.push(element('span', metadata, 'muted'));
    if (task.started) {
      const clock = element('span', duration(task.started, task.ended), 'muted');
      if (!task.ended && task.status === 'RUNNING') clock.dataset.started = task.started;
      summary.push(clock);
    }
  } else if (entry.kind === 'command' && entry.data.attempt) {
    summary.push(element('span', `Attempt ${entry.data.attempt}`, 'muted'));
  }
  container.replaceChildren(...summary);
}

function timeline(events) {
  if (!events.length) return element('p', 'No activity recorded.', 'empty');
  const list = element('ul', null, 'timeline');
  const sorted = events.slice().sort((a, b) => (Date.parse(a.at) || Infinity) - (Date.parse(b.at) || Infinity));
  for (const event of sorted) {
    const row = element('li');
    const label = [event.label, event.child, event.status].filter(Boolean).join(' · ');
    row.append(time(event.at), element('span', label));
    list.append(row);
  }
  return list;
}

function childrenList(children) {
  const list = element('ul', null, 'children');
  for (const child of children) {
    const row = element('li');
    row.append(pill(child.status || 'Observed'), element('span', child.id));
    if (child.observed) row.append(element('span', ' · last observed ', 'muted'), time(child.observed));
    list.append(row);
  }
  return list;
}

function taskSubagents(attempts, open) {
  const details = element('details', null, 'subagents');
  details.open = open;
  details.append(element('summary', 'Subagents by attempt'));
  for (const attempt of attempts) {
    details.append(element('h3', `Attempt ${attempt.n}`));
    if (attempt.children.length) details.append(childrenList(attempt.children));
    else details.append(element('p', attempt.subagent_used === true ? 'Usage detected · identities unavailable' :
      attempt.subagent_used === false ? 'No subagent usage reported' : 'Subagent visibility unavailable', 'muted'));
  }
  return details;
}

export function renderTimeline(container, entry, commands) {
  const data = entry.data;
  if (entry.kind === 'command') {
    container.replaceChildren(timeline(commandEvents(data)));
    return;
  }
  if (entry.kind === 'session') {
    container.replaceChildren(timeline(data.events || []), childrenList(data.children));
    return;
  }
  const events = [...data.events, ...data.attempts.flatMap(attempt => attempt.events), ...commands.flatMap(commandEvents)];
  const open = container.querySelector('details')?.open || false;
  const focused = container.querySelector('summary') === document.activeElement;
  container.replaceChildren(timeline(events));
  if (data.attempts.length) {
    const subagents = taskSubagents(data.attempts, open);
    container.append(subagents);
    if (focused) subagents.querySelector('summary').focus({preventScroll: true});
  }
}

export function renderOutput(container, record, emptyText) {
  if (!record) {
    container.replaceChildren(element('p', emptyText, 'empty'));
    return;
  }
  const summary = element('p', `${commandLabel(record)} · ${commandStatus(record)}`, 'muted');
  const lines = [summary];
  if (record.error) lines.push(element('p', record.error, 'feedback'));
  if (record.output.length) {
    const output = element('pre', null, 'command-output');
    for (const chunk of record.output) {
      const span = element('span', chunk.text, chunk.channel);
      span.title = chunk.channel;
      output.append(span);
    }
    lines.push(output);
  }
  if (record.truncated) lines.push(element('p', 'Saved output was truncated.', 'muted'));
  container.replaceChildren(...lines);
}

export function renderVerification(container, task) {
  const summary = element('dl', null, 'verification');
  for (const [label, verdict] of [['Review verdict', task.review_verdict], ['Main decision', task.verdict]]) {
    summary.append(element('dt', label), element('dd', verdict || 'Unrecorded'));
  }
  container.replaceChildren(summary);
}
