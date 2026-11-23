import {workerControls} from './controls.js';
import {buildEntries, duration, element, renderBoard, renderList, renderOutput, renderSummary, renderTimeline,
  renderVerification} from './panels.js';

const connection = document.querySelector('#connection');
const projectStatus = document.querySelector('#status-query');
const projectFeedback = document.querySelector('#project-feedback');
const taskFeedback = document.querySelector('#task-feedback');
const resultButton = document.querySelector('#load-result');
const checkButton = document.querySelector('#run-check');
const history = document.querySelector('#history');
const runsList = document.querySelector('#runs-list');
const board = document.querySelector('#runs-board');
const boardFilter = document.querySelector('#board-filter');
const boardPlan = document.querySelector('#board-plan');
const showList = document.querySelector('#show-list');
const showBoard = document.querySelector('#show-board');
const openContract = document.querySelector('#open-contract');
const tabs = document.querySelector('#tabs');
const tabNames = ['timeline', 'output', 'verification'];
const tabButtons = Object.fromEntries(tabNames.map(name => [name, document.querySelector(`#tab-${name}`)]));
const tabPanels = Object.fromEntries(tabNames.map(name => [name, document.querySelector(`#panel-${name}`)]));
let view;
let current;
let selection;
let selectionRevision = 0;
let waitingForCommand;
let pendingLaunch;
let boardActive = false;
let activeTab = 'timeline';
let queryPending = false;

async function post(body) {
  const response = await fetch('/api/commands', {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({project: view.project, ...body})});
  const result = await response.json().catch(() => null);
  if (!response.ok || !result) throw new Error(result?.error || 'Could not reach the command server.');
  return result;
}

const controls = workerControls(post, showCommand);

function showCommand(id, launchedTask) {
  if (launchedTask) {
    if (launchedTask.selectionRevision === selectionRevision) {
      pendingLaunch = id;
      selection = launchedTask.key;
    }
  } else {
    waitingForCommand = id;
    selectionRevision += 1;
  }
  history.open = true;
  render();
}

function selectEntry(key) {
  if (key === selection) return;
  waitingForCommand = undefined;
  pendingLaunch = undefined;
  selection = key;
  selectionRevision += 1;
  activeTab = key.startsWith('command:') ? 'output' : 'timeline';
  taskFeedback.textContent = '';
  tabPanels.timeline.replaceChildren();
  render();
}

function visibleTabs() {
  if (current?.kind === 'task') return tabNames;
  if (current?.kind === 'command') return ['timeline', 'output'];
  return ['timeline'];
}

function renderTabs() {
  const visible = visibleTabs();
  if (!visible.includes(activeTab)) activeTab = visible[0];
  tabs.hidden = !current || visible.length === 1;
  for (const name of tabNames) {
    const selected = name === activeTab;
    tabButtons[name].hidden = !visible.includes(name);
    tabButtons[name].setAttribute('aria-selected', String(selected));
    tabButtons[name].tabIndex = selected ? 0 : -1;
    tabPanels[name].hidden = !current || !selected;
  }
}

function renderDetail() {
  const task = current?.kind === 'task' ? current.data : undefined;
  const contract = current?.kind === 'contract' ? current : undefined;
  const commands = task ? view.commands.filter(record => record.task_id === task.task_id) : [];
  document.querySelector('#task-heading').textContent = current?.title || 'Run details';
  document.querySelector('#empty-detail').hidden = Boolean(current);
  renderSummary(document.querySelector('#task-summary'), current);
  controls.update(view, task);
  renderTabs();
  openContract.hidden = !contract;
  resultButton.hidden = !task;
  checkButton.hidden = !task;
  resultButton.disabled = checkButton.disabled = projectStatus.disabled = queryPending || !view.project;

  if (!current || contract) {
    tabPanels.timeline.hidden = true;
    return;
  }
  if (activeTab === 'timeline') renderTimeline(tabPanels.timeline, current, commands);
  if (activeTab === 'output') {
    const output = task ? commands.find(record => record.command === 'result') : current.data;
    renderOutput(document.querySelector('#output-content'), output, 'Load the saved worker result to see its output.');
  }
  if (activeTab === 'verification' && task) {
    renderVerification(document.querySelector('#verification-summary'), task);
    renderOutput(document.querySelector('#check-content'), commands.find(record => record.command === 'check'),
      'Run a check to view the recorded evidence.');
  }
}

function render() {
  if (!view) return;
  document.querySelector('#project').textContent = view.project;
  document.querySelector('#cli-session').hidden = !view.interactive;
  const {runs, commands} = buildEntries(view);
  const entries = [...runs, ...commands];
  if (waitingForCommand && commands.some(entry => entry.data.id === waitingForCommand)) {
    selection = `command:${waitingForCommand}`;
    waitingForCommand = undefined;
    activeTab = 'output';
  }
  current = entries.find(entry => entry.key === selection);
  if (pendingLaunch && (current?.kind === 'task' ||
      commands.some(entry => entry.data.id === pendingLaunch && (entry.data.ended || entry.data.interrupted)))) {
    pendingLaunch = undefined;
  }
  if (!current && !pendingLaunch) {
    current = entries[0];
    selection = current?.key;
    activeTab = current?.kind === 'command' ? 'output' : 'timeline';
    tabPanels.timeline.replaceChildren();
  }
  history.hidden = !commands.length;
  renderList(runsList, runs, selection, selectEntry);
  if (boardActive) renderKanban(runs);
  renderList(document.querySelector('#command-list'), commands, selection, selectEntry);
  renderDetail();
}

function renderKanban(runs) {
  const entries = runs.filter(entry => entry.kind !== 'session');
  const plans = [...new Set(entries.map(entry => entry.data.plan).filter(plan => typeof plan === 'string' && plan))].sort();
  const fingerprint = JSON.stringify(plans);
  if (boardPlan.dataset.fingerprint !== fingerprint) {
    const previous = boardPlan.value;
    const all = element('option', 'All plans');
    all.value = '';
    boardPlan.replaceChildren(all, ...plans.map(plan => element('option', plan)));
    boardPlan.value = plans.includes(previous) ? previous : '';
    boardPlan.dataset.fingerprint = fingerprint;
  }
  renderBoard(board, entries.filter(entry => !boardPlan.value || entry.data.plan === boardPlan.value), selection, entry => {
    selectEntry(entry.key);
    if (entry.kind === 'contract') controls.previewContract(entry, selectionRevision);
  });
}

function switchView(kanban) {
  boardActive = kanban;
  document.querySelector('main').classList.toggle('kanban', kanban);
  runsList.hidden = kanban;
  board.hidden = boardFilter.hidden = !kanban;
  showList.setAttribute('aria-pressed', String(!kanban));
  showBoard.setAttribute('aria-pressed', String(kanban));
  render();
}

showList.onclick = () => switchView(false);
showBoard.onclick = () => switchView(true);
boardPlan.onchange = render;
openContract.onclick = () => controls.previewContract(current, selectionRevision);

function selectTab(name, focus = false) {
  activeTab = name;
  renderDetail();
  if (focus) tabButtons[name].focus();
}

for (const name of tabNames) tabButtons[name].onclick = () => selectTab(name);
tabs.onkeydown = event => {
  const available = visibleTabs();
  const position = available.indexOf(activeTab);
  const targets = {ArrowLeft: (position + available.length - 1) % available.length,
    ArrowRight: (position + 1) % available.length, Home: 0, End: available.length - 1};
  if (!Object.hasOwn(targets, event.key)) return;
  event.preventDefault();
  selectTab(available[targets[event.key]], true);
};

async function query(command) {
  if (queryPending || !view?.project) return;
  const taskId = command === 'status' ? '' : current?.kind === 'task' && current.data.task_id;
  if (taskId === false || taskId == null) return;
  const feedback = command === 'status' ? projectFeedback : taskFeedback;
  feedback.textContent = '';
  queryPending = true;
  renderDetail();
  try {
    const response = await post({command, task_id: taskId});
    if (command === 'status') showCommand(response.id);
  } catch (error) {
    if (!taskId || current?.key === `task:${taskId}`) feedback.textContent = error.message;
  } finally {
    queryPending = false;
    renderDetail();
  }
}

projectStatus.onclick = () => query('status');
resultButton.onclick = () => query('result');
checkButton.onclick = () => query('check');

const stream = new EventSource('/api/events');
stream.onopen = () => {
  connection.textContent = 'Live · local';
  connection.classList.remove('offline');
};
stream.onmessage = event => {
  view = JSON.parse(event.data);
  render();
};
stream.onerror = () => {
  connection.textContent = 'Disconnected · retrying';
  connection.classList.add('offline');
};
setInterval(() => {
  document.querySelectorAll('[data-started]').forEach(node => { node.textContent = duration(node.dataset.started); });
}, 1000);
