(() => {
  const feedback = document.querySelector('#terminal-feedback');
  const connection = document.querySelector('#connection');
  const terminal = new Terminal({
    disableStdin: true, scrollback: 2000, windowsPty: {backend: 'conpty'},
    theme: {background: '#141b19', foreground: '#eff4f1'},
  });
  terminal.open(document.querySelector('#terminal-screen'));
  const stream = new EventSource('/api/terminal/events');
  let display = Promise.resolve();
  let input = [];
  let sending = false;
  let epoch = 0;

  async function send() {
    if (sending) return;
    sending = true;
    try {
      while (input.length) {
        const response = await fetch('/api/terminal/input', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({data: input.splice(0, 1000).join('')}),
        });
        if (!response.ok) throw new Error((await response.json()).error);
      }
      feedback.textContent = '';
    } catch (error) {
      input = [];
      feedback.textContent = error.message || 'Could not send terminal input.';
    } finally {
      sending = false;
    }
  }

  terminal.onData(data => {
    if (terminal.options.disableStdin) return;
    const characters = Array.from(data);
    if (input.length + characters.length > 16000) {
      feedback.textContent = 'Paste up to 16,000 characters at a time.';
      return;
    }
    input.push(...characters);
    send();
  });

  // The local console handles device queries for the shared CLI.
  for (const id of [{final: 'n'}, {prefix: '?', final: 'n'}, {final: 'c'},
    {prefix: '>', final: 'c'}, {prefix: '?', intermediates: '$', final: 'p'}]) {
    terminal.parser.registerCsiHandler(id, () => true);
  }

  stream.onmessage = event => {
    const frame = JSON.parse(event.data);
    display = display.then(() => new Promise(resolve => {
      if (frame.reset) terminal.reset();
      if (frame.cols !== terminal.cols || frame.rows !== terminal.rows) terminal.resize(frame.cols, frame.rows);
      terminal.write(frame.data, resolve);
    }));
  };
  stream.onopen = () => {
    connection.textContent = 'Syncing terminal';
  };
  stream.addEventListener('ready', () => {
    const generation = epoch;
    display = display.then(() => {
      if (generation !== epoch || stream.readyState !== EventSource.OPEN) return;
      terminal.options.disableStdin = false;
      connection.textContent = 'Live · local';
      connection.classList.remove('offline');
    });
  });
  function disconnected(label) {
    epoch += 1;
    input = [];
    terminal.options.disableStdin = true;
    connection.textContent = label;
    connection.classList.add('offline');
  }
  stream.onerror = () => disconnected('Disconnected · retrying');
  stream.addEventListener('ended', () => {
    stream.close();
    disconnected('CLI session ended');
  });
})();
