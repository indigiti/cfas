(() => {
  const panelId = 'cfas-face-engine-setup';

  function apiUrl(path) {
    const base = new URL(window.location.href);
    base.search = '';
    base.hash = '';
    if (!base.pathname.endsWith('/')) base.pathname += '/';
    return new URL(path.replace(/^\//, ''), base).toString();
  }

  function removePanel() {
    document.getElementById(panelId)?.remove();
  }

  function createPanel(config) {
    let panel = document.getElementById(panelId);
    if (!panel) {
      panel = document.createElement('section');
      panel.id = panelId;
      panel.className = 'notice';
      const shell = document.querySelector('.shell');
      const tabs = shell?.querySelector('.tabs');
      if (shell && tabs) shell.insertBefore(panel, tabs);
      else if (shell) shell.appendChild(panel);
    }

    panel.innerHTML = '';
    const text = document.createElement('div');
    const strong = document.createElement('strong');
    strong.textContent = 'Face engine setup required. ';
    text.appendChild(strong);
    text.appendChild(document.createTextNode(
      config.admin_authenticated
        ? 'Install DeepFace, TensorFlow and ArcFace once into persistent private server storage. The application source and future release artifacts remain small.'
        : 'Sign in as administrator to install the face engine from the browser.'
    ));
    panel.appendChild(text);

    if (config.admin_authenticated) {
      const button = document.createElement('button');
      button.className = 'primary';
      button.style.marginTop = '12px';
      button.textContent = 'Install face engine';
      button.addEventListener('click', async () => {
        button.disabled = true;
        button.textContent = 'Downloading and installing…';
        text.textContent = 'Downloading TensorFlow, DeepFace and the ArcFace model to the server. Keep this page open; this can take several minutes.';
        try {
          const response = await fetch(apiUrl('api/admin/face-engine/install'), {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: '{}',
          });
          const data = await response.json().catch(() => ({ ok: false, message: 'Invalid server response.' }));
          if (!response.ok || !data.ok) throw new Error(data.detail || data.message || `Installation failed (${response.status})`);
          text.textContent = data.message || 'Face engine installed successfully.';
          button.textContent = 'Face engine ready';
          setTimeout(() => window.location.reload(), 800);
        } catch (error) {
          text.textContent = `Face engine installation failed: ${error.message}`;
          button.disabled = false;
          button.textContent = 'Retry face engine installation';
        }
      });
      panel.appendChild(button);
    }
  }

  async function refresh() {
    try {
      const response = await fetch(apiUrl('api/config'), { credentials: 'same-origin', cache: 'no-store' });
      const config = await response.json();
      if (!config.ok || config.setup_required || config.face_engine_ready) {
        removePanel();
        return;
      }
      createPanel(config);
    } catch (_) {
      // The main application already reports connectivity failures.
    }
  }

  window.addEventListener('DOMContentLoaded', () => {
    refresh();
    setInterval(refresh, 3000);
  });
})();
