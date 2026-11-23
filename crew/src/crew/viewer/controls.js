import {element} from './panels.js';

function confirmation(kind, post, accepted) {
  const panel = document.querySelector(`#${kind}-confirmation`);
  const description = document.querySelector(`#${kind}-description`);
  const confirm = document.querySelector(`#confirm-${kind}`);
  const dismissButton = document.querySelector(`#dismiss-${kind}`);
  const previewButton = document.querySelector(`#preview-${kind}`);
  const feedback = document.querySelector(kind === 'run' ? '#run-feedback' : '#task-feedback');
  let token;
  let generation = 0;

  function dismiss() {
    generation += 1;
    token = undefined;
    panel.hidden = true;
  }

  async function preview(target) {
    dismiss();
    feedback.textContent = '';
    const request = generation;
    try {
      const response = await post({command: `${kind}-preview`, ...target});
      if (request !== generation) return;
      token = response.preview_id;
      description.textContent = response.description;
      panel.hidden = false;
      confirm.focus();
    } catch (error) {
      if (request === generation) feedback.textContent = error.message;
    }
  }

  dismissButton.onclick = () => {
    dismiss();
    previewButton.focus();
  };

  confirm.onclick = async () => {
    if (!token) return;
    const previewId = token;
    dismiss();
    const request = generation;
    previewButton.focus();
    try {
      const response = await post({command: kind, preview_id: previewId});
      if (request === generation) accepted(response.id);
    } catch (error) {
      if (request === generation) feedback.textContent = error.message;
    }
  };

  return {dismiss, preview};
}

export function workerControls(post, showCommand) {
  const newRun = document.querySelector('#new-run');
  const runPanel = document.querySelector('#run-panel');
  const contract = document.querySelector('#run-contract');
  const previewRun = document.querySelector('#preview-run');
  const previewCancel = document.querySelector('#preview-cancel');
  let contractsKey;
  let taskId;
  let launchedFromCard;
  const run = confirmation('run', post, id => {
    const selection = launchedFromCard;
    toggleRun(false);
    newRun.focus();
    showCommand(id, selection);
  });
  const cancel = confirmation('cancel', post, showCommand);

  function toggleRun(open) {
    runPanel.hidden = !open;
    newRun.setAttribute('aria-expanded', String(open));
    if (open) contract.focus();
    else {
      launchedFromCard = undefined;
      run.dismiss();
    }
  }

  function previewContract(entry, selectionRevision) {
    if (!Array.from(contract.options).some(option => option.value === entry.data.contract)) return;
    toggleRun(true);
    contract.value = entry.data.contract;
    previewRun.disabled = false;
    launchedFromCard = {key: entry.key, selectionRevision};
    return run.preview({contract: contract.value});
  }

  newRun.onclick = () => toggleRun(runPanel.hidden);
  contract.onchange = () => {
    launchedFromCard = undefined;
    run.dismiss();
    previewRun.disabled = !contract.value;
  };
  previewRun.onclick = () => run.preview({contract: contract.value});
  previewCancel.onclick = () => cancel.preview({task_id: taskId});

  function update(view, task) {
    newRun.disabled = !view.project;
    const contracts = view.contracts || [];
    const key = JSON.stringify(contracts);
    if (key !== contractsKey) {
      const selected = contract.value;
      const placeholder = element('option', contracts.length ? 'Select a contract' : 'No saved contracts');
      placeholder.value = '';
      contract.replaceChildren(placeholder, ...contracts.map(name => element('option', name)));
      if (contracts.includes(selected)) contract.value = selected;
      else run.dismiss();
      contractsKey = key;
    }
    previewRun.disabled = !contract.value;
    const cancellable = task && ['RUNNING', 'ORPHANED', 'ORPHANED?'].includes(task.status);
    if (task?.task_id !== taskId || (!cancellable && !previewCancel.hidden)) cancel.dismiss();
    taskId = task?.task_id;
    previewCancel.hidden = !cancellable;
  }

  return {update, previewContract};
}
