// CodyClaw Console — Frontend Application

const API = '/api';

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
let currentPage = 'dashboard';
let chatSessionKey = '';
let chatAgentId = '';
let chatStreaming = false;
let eventSource = null;

// ---------------------------------------------------------------------------
// Navigation
// ---------------------------------------------------------------------------
document.querySelectorAll('.nav-item').forEach(item => {
  item.addEventListener('click', e => {
    e.preventDefault();
    navigateTo(item.dataset.page);
  });
});

function navigateTo(page) {
  currentPage = page;
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  document.querySelector(`.nav-item[data-page="${page}"]`)?.classList.add('active');
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.getElementById(`page-${page}`)?.classList.add('active');
  loadPage(page);
}

function loadPage(page) {
  const loaders = {
    dashboard: loadDashboard,
    chat: loadChat,
    agents: loadAgents,
    skills: loadSkills,
    cron: loadCron,
    sessions: loadSessions,
    config: loadConfig,
    events: loadEvents,
  };
  loaders[page]?.();
}

// ---------------------------------------------------------------------------
// Dashboard
// ---------------------------------------------------------------------------
async function loadDashboard() {
  try {
    const data = await fetchJSON(`${API}/dashboard`);

    // 状态提示
    const alertEl = document.getElementById('dashboard-alert');
    try {
      const health = await fetchJSON('/health');
      if (health.lark_connected) {
        alertEl.className = 'dashboard-alert alert-success';
        alertEl.innerHTML = '<strong>Feishu Connected</strong> — The bot is online and ready to receive messages. Try sending a message to the bot in Feishu!';
      } else {
        alertEl.className = 'dashboard-alert alert-warning';
        alertEl.innerHTML = '<strong>Feishu Disconnected</strong> — Please check your Lark App ID and App Secret in the <a href="#" onclick="navigateTo(\'config\')">Config</a> page.';
      }
      alertEl.style.display = 'block';
    } catch { alertEl.style.display = 'none'; }

    const grid = document.getElementById('stats-grid');
    grid.innerHTML = [
      statCard('Agents', data.agents.length, 'info'),
      statCard('Active Sessions', data.sessions.count, 'success'),
      statCard('Active Runs', data.active_runs, data.active_runs > 0 ? 'warning' : 'muted'),
      statCard('Cron Tasks', data.cron_tasks.length, 'info'),
      statCard('Chat Messages', data.chat_messages_count, 'muted'),
    ].join('');

    const cronEl = document.getElementById('dashboard-cron-list');
    if (data.cron_tasks.length === 0) {
      cronEl.innerHTML = '<p class="empty-state">No cron tasks configured.</p>';
    } else {
      cronEl.innerHTML = `<table>
        <thead><tr><th>Name</th><th>Status</th><th>Next Run</th></tr></thead>
        <tbody>${data.cron_tasks.map(t => `
          <tr>
            <td><strong>${esc(t.name)}</strong></td>
            <td>${t.enabled ? '<span class="badge badge-success">Enabled</span>' : '<span class="badge badge-muted">Disabled</span>'}</td>
            <td>${esc(t.next_run || '-')}</td>
          </tr>`).join('')}
        </tbody></table>`;
    }
  } catch (e) { console.error('Dashboard load error:', e); }
}

function statCard(label, value, type) {
  return `<div class="stat-card">
    <div class="stat-label">${label}</div>
    <div class="stat-value">${value}</div>
  </div>`;
}

// ---------------------------------------------------------------------------
// Chat
// ---------------------------------------------------------------------------
async function loadChat() {
  try {
    const data = await fetchJSON(`${API}/dashboard`);
    const select = document.getElementById('chat-agent-select');
    if (select.children.length <= 1) {
      select.innerHTML = data.agents.map(a =>
        `<option value="${esc(a.id)}">${esc(a.name)} (${esc(a.id)})</option>`
      ).join('');
      if (data.agents.length > 0) chatAgentId = data.agents[0].id;
    }
    select.onchange = () => { chatAgentId = select.value; };

    // Load history for current session
    if (chatSessionKey) {
      const hist = await fetchJSON(`${API}/chat/history?session_key=${encodeURIComponent(chatSessionKey)}`);
      renderChatHistory(hist.messages);
    }

    // 空聊天时显示欢迎引导
    const container = document.getElementById('chat-messages');
    if (container.children.length === 0) {
      container.innerHTML = `<div class="chat-welcome">
        <h3>Start a conversation</h3>
        <p>Try asking your agent something:</p>
        <div class="chat-suggestions">
          <button class="suggestion" onclick="useSuggestion(this)">Help me write a Python script</button>
          <button class="suggestion" onclick="useSuggestion(this)">Explain the project structure</button>
          <button class="suggestion" onclick="useSuggestion(this)">Create a cron task for daily reports</button>
        </div>
      </div>`;
    }
  } catch (e) { console.error('Chat load error:', e); }
}

function useSuggestion(btn) {
  document.getElementById('chat-input').value = btn.textContent;
  document.getElementById('chat-messages').innerHTML = '';
  document.getElementById('chat-input').focus();
}

function renderChatHistory(messages) {
  const container = document.getElementById('chat-messages');
  // Only render if empty (don't overwrite active chat)
  if (container.children.length > 0) return;
  messages.forEach(m => appendChatMessage(m.role, m.content));
}

function appendChatMessage(role, content, streaming = false) {
  const container = document.getElementById('chat-messages');
  const div = document.createElement('div');
  div.className = `chat-msg ${role}${streaming ? ' streaming' : ''}`;
  div.textContent = content;
  if (streaming) div.id = 'streaming-msg';
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
  return div;
}

function updateStreamingMessage(content) {
  let el = document.getElementById('streaming-msg');
  if (!el) {
    el = appendChatMessage('assistant', content, true);
  } else {
    el.textContent = content;
  }
  const container = document.getElementById('chat-messages');
  container.scrollTop = container.scrollHeight;
}

function finalizeStreamingMessage() {
  const el = document.getElementById('streaming-msg');
  if (el) {
    el.classList.remove('streaming');
    el.removeAttribute('id');
  }
}

// Chat Send
document.getElementById('chat-send').addEventListener('click', sendChatMessage);
document.getElementById('chat-input').addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendChatMessage();
  }
});
document.getElementById('chat-new-session').addEventListener('click', () => {
  chatSessionKey = '';
  document.getElementById('chat-messages').innerHTML = '';
});

async function sendChatMessage() {
  if (chatStreaming) return;
  const input = document.getElementById('chat-input');
  const message = input.value.trim();
  if (!message) return;

  input.value = '';
  appendChatMessage('user', message);
  chatStreaming = true;

  const sendBtn = document.getElementById('chat-send');
  sendBtn.disabled = true;
  sendBtn.textContent = '...';

  let accumulated = '';
  try {
    const response = await fetch(`${API}/chat/send`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        agent_id: chatAgentId,
        message: message,
        session_key: chatSessionKey,
      }),
    });

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const payload = line.slice(6).trim();
        if (!payload) continue;
        try {
          const event = JSON.parse(payload);
          if (event.type === 'text') {
            accumulated += event.content;
            updateStreamingMessage(accumulated);
          } else if (event.type === 'tool') {
            accumulated += `\n[Tool: ${event.name}]\n`;
            updateStreamingMessage(accumulated);
          } else if (event.type === 'approval') {
            accumulated += `\n[Auto-approved: ${event.content}]\n`;
            updateStreamingMessage(accumulated);
          } else if (event.type === 'done') {
            chatSessionKey = event.session_key || chatSessionKey;
          } else if (event.type === 'error') {
            accumulated += `\n[Error: ${event.message}]`;
            updateStreamingMessage(accumulated);
          }
        } catch (_) {}
      }
    }
  } catch (e) {
    if (!accumulated) {
      appendChatMessage('assistant', `[Connection error: ${e.message}]`);
    }
  } finally {
    finalizeStreamingMessage();
    chatStreaming = false;
    sendBtn.disabled = false;
    sendBtn.textContent = 'Send';
  }
}

// ---------------------------------------------------------------------------
// Agents
// ---------------------------------------------------------------------------
async function loadAgents() {
  try {
    const data = await fetchJSON(`${API}/dashboard`);
    const el = document.getElementById('agents-list');
    if (data.agents.length === 0) {
      el.innerHTML = '<p class="empty-state">No agents configured.</p>';
      return;
    }
    el.innerHTML = data.agents.map(a => `
      <div class="agent-card">
        <h3>${esc(a.name)}</h3>
        <div class="agent-id">${esc(a.id)}</div>
        <div class="agent-detail"><strong>Model:</strong> ${esc(a.model)}</div>
        <div class="agent-detail"><strong>Workdir:</strong> ${esc(a.workdir)}</div>
      </div>
    `).join('');
  } catch (e) { console.error('Agents load error:', e); }
}

// ---------------------------------------------------------------------------
// Skills
// ---------------------------------------------------------------------------
async function loadSkills() {
  try {
    const data = await fetchJSON(`${API}/skills`);
    const el = document.getElementById('skills-list');
    if (data.skills.length === 0) {
      el.innerHTML = '<p class="empty-state">No skills found.</p>';
      return;
    }
    el.innerHTML = data.skills.map(s => `
      <div class="skill-card">
        <h3><span class="badge badge-info">${esc(s.name)}</span></h3>
        <div class="skill-content">${esc(s.content || '(Empty SKILL.md)')}</div>
      </div>
    `).join('');
  } catch (e) { console.error('Skills load error:', e); }
}

// ---------------------------------------------------------------------------
// Cron Tasks
// ---------------------------------------------------------------------------
async function loadCron() {
  try {
    const data = await fetchJSON(`${API}/cron`);
    const el = document.getElementById('cron-list');
    if (data.tasks.length === 0) {
      el.innerHTML = '<p class="empty-state">No cron tasks.</p>';
      return;
    }
    el.innerHTML = `<table>
      <thead><tr><th>ID</th><th>Name</th><th>Schedule</th><th>Status</th><th>Next Run</th></tr></thead>
      <tbody>${data.tasks.map(t => `
        <tr>
          <td><code>${esc(t.id)}</code></td>
          <td>${esc(t.name)}</td>
          <td><code>${esc(t.schedule)}</code></td>
          <td>${t.enabled ? '<span class="badge badge-success">Enabled</span>' : '<span class="badge badge-muted">Disabled</span>'}</td>
          <td>${esc(t.next_run || '-')}</td>
        </tr>`).join('')}
      </tbody></table>`;
  } catch (e) { console.error('Cron load error:', e); }
}

// ---------------------------------------------------------------------------
// Sessions
// ---------------------------------------------------------------------------
async function loadSessions() {
  try {
    const data = await fetchJSON(`${API}/sessions`);
    const el = document.getElementById('sessions-list');
    if (data.sessions.length === 0) {
      el.innerHTML = '<p class="empty-state">No active sessions.</p>';
      return;
    }
    el.innerHTML = `<table>
      <thead><tr><th>Session Key</th><th>Session ID</th></tr></thead>
      <tbody>${data.sessions.map(s => `
        <tr>
          <td><code>${esc(s.key)}</code></td>
          <td><code>${esc(s.session_id)}</code></td>
        </tr>`).join('')}
      </tbody></table>`;
  } catch (e) { console.error('Sessions load error:', e); }
}

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------
let configLoaded = false;

async function loadConfig() {
  try {
    const data = await fetchJSON(`${API}/config`);
    document.getElementById('config-path').textContent = data.config_path;
    document.getElementById('config-editor').textContent = yaml_stringify(data.config);

    // 填充 Quick Edit 表单
    const lark = data.config.lark || {};
    const gw = data.config.gateway || {};
    const cody = data.config.cody || {};
    const agents = data.config.agents || [];
    // Lark
    document.getElementById('cfg-lark-app-id').value = lark.app_id || '';
    document.getElementById('cfg-lark-app-secret').value = '';
    document.getElementById('cfg-lark-bot-id').value = lark.bot_open_id || '';
    // Model
    document.getElementById('cfg-model').value = agents.length > 0 ? (agents[0].model || '') : '';
    document.getElementById('cfg-api-key').value = '';
    document.getElementById('cfg-base-url').value = cody.base_url || '';
    // Gateway
    document.getElementById('cfg-host').value = gw.host || '0.0.0.0';
    document.getElementById('cfg-port').value = gw.port || 8080;
    document.getElementById('cfg-log-level').value = gw.log_level || 'info';

    // 仅绑定一次监听器
    if (!configLoaded) {
      configLoaded = true;
      const saveBtn = document.getElementById('config-save-btn');
      document.querySelectorAll('.cfg-input').forEach(el => {
        el.addEventListener('input', () => { saveBtn.style.display = ''; });
      });
    }
  } catch (e) { console.error('Config load error:', e); }
}

document.getElementById('config-save-btn').addEventListener('click', async () => {
  const btn = document.getElementById('config-save-btn');
  const msg = document.getElementById('config-edit-msg');
  btn.disabled = true;
  btn.textContent = 'Saving...';
  msg.textContent = '';

  // 收集所有字段
  const quickPayload = {
    // Lark
    lark_app_id: document.getElementById('cfg-lark-app-id').value.trim(),
    lark_app_secret: document.getElementById('cfg-lark-app-secret').value.trim(),
    lark_bot_open_id: document.getElementById('cfg-lark-bot-id').value.trim(),
    // Model
    api_key: document.getElementById('cfg-api-key').value.trim(),
    model: document.getElementById('cfg-model').value.trim(),
    base_url: document.getElementById('cfg-base-url').value.trim(),
    // Gateway
    gateway_host: document.getElementById('cfg-host').value.trim(),
    gateway_port: parseInt(document.getElementById('cfg-port').value) || 8080,
    gateway_log_level: document.getElementById('cfg-log-level').value,
  };

  try {
    const res = await fetch(`${API}/config/quick`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(quickPayload),
    });

    const data = await res.json();
    if (res.ok) {
      msg.style.color = 'var(--success)';
      msg.textContent = 'Saved! Restart to apply changes.';
      btn.style.display = 'none';
      loadConfig();
    } else {
      msg.style.color = 'var(--danger)';
      msg.textContent = data.error || 'Save failed';
    }
  } catch (e) {
    msg.style.color = 'var(--danger)';
    msg.textContent = `Error: ${e.message}`;
  }
  btn.disabled = false;
  btn.textContent = 'Save Changes';
});

// Simple YAML-like pretty printer for display
function yaml_stringify(obj, indent = 0) {
  if (obj === null || obj === undefined) return 'null';
  if (typeof obj === 'string') return obj.includes('\n') ? `|\n${obj.split('\n').map(l => '  '.repeat(indent + 1) + l).join('\n')}` : obj;
  if (typeof obj === 'number' || typeof obj === 'boolean') return String(obj);
  if (Array.isArray(obj)) {
    if (obj.length === 0) return '[]';
    return obj.map(item => {
      if (typeof item === 'object' && item !== null) {
        const lines = yaml_stringify(item, indent + 1);
        const first = lines.split('\n');
        return '  '.repeat(indent) + '- ' + first[0].trim() + (first.length > 1 ? '\n' + first.slice(1).join('\n') : '');
      }
      return '  '.repeat(indent) + '- ' + yaml_stringify(item, indent + 1);
    }).join('\n');
  }
  if (typeof obj === 'object') {
    return Object.entries(obj).map(([k, v]) => {
      if (typeof v === 'object' && v !== null && !Array.isArray(v)) {
        return '  '.repeat(indent) + k + ':\n' + yaml_stringify(v, indent + 1);
      }
      if (Array.isArray(v)) {
        return '  '.repeat(indent) + k + ':\n' + yaml_stringify(v, indent + 1);
      }
      return '  '.repeat(indent) + k + ': ' + yaml_stringify(v, indent);
    }).join('\n');
  }
  return String(obj);
}

// ---------------------------------------------------------------------------
// Events (live SSE feed)
// ---------------------------------------------------------------------------
function loadEvents() {
  if (eventSource) return; // already connected
  connectEventStream();
}

function connectEventStream() {
  if (eventSource) {
    eventSource.close();
    eventSource = null;
  }
  eventSource = new EventSource(`${API}/events/stream`);
  eventSource.onmessage = (e) => {
    try {
      const event = JSON.parse(e.data);
      appendEvent(event);
    } catch (_) {}
  };
  eventSource.onerror = () => {
    // Will auto-reconnect
  };
}

function appendEvent(event) {
  const feed = document.getElementById('events-feed');
  const line = document.createElement('div');
  line.className = 'event-line';
  line.innerHTML = `<span class="event-time">${esc(event.time)}</span> <span class="event-type">${esc(event.type)}</span> <span class="event-data">${esc(JSON.stringify(event.data))}</span>`;
  feed.appendChild(line);
  // Keep max 200 lines
  while (feed.children.length > 200) feed.removeChild(feed.firstChild);
  feed.scrollTop = feed.scrollHeight;
}

document.getElementById('events-clear').addEventListener('click', () => {
  document.getElementById('events-feed').innerHTML = '';
});

// ---------------------------------------------------------------------------
// Health check
// ---------------------------------------------------------------------------
async function checkHealth() {
  try {
    const data = await fetchJSON('/health');
    const dot = document.querySelector('.status-dot');
    if (data.lark_connected) {
      document.getElementById('health-status').textContent = 'Feishu Connected';
      dot.className = 'status-dot';
    } else if (data.configured) {
      document.getElementById('health-status').textContent = 'Feishu Disconnected';
      dot.className = 'status-dot error';
    } else {
      document.getElementById('health-status').textContent = 'Setup Required';
      dot.className = 'status-dot error';
    }
  } catch {
    document.getElementById('health-status').textContent = 'Server Offline';
    document.querySelector('.status-dot').classList.add('error');
  }
}

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------
async function fetchJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

function esc(str) {
  if (str === null || str === undefined) return '';
  const div = document.createElement('div');
  div.textContent = String(str);
  return div.innerHTML;
}

// ---------------------------------------------------------------------------
// Setup Wizard
// ---------------------------------------------------------------------------
async function checkSetup() {
  try {
    const data = await fetchJSON(`${API}/setup/status`);
    if (data.setup_mode || !data.configured) {
      showSetup();
      return true;
    }
  } catch { /* server not ready yet */ }
  return false;
}

function showSetup() {
  document.getElementById('setup-overlay').style.display = 'flex';
  document.querySelector('.sidebar').style.display = 'none';
  document.querySelector('.main').style.display = 'none';
}

function hideSetup() {
  document.getElementById('setup-overlay').style.display = 'none';
  document.querySelector('.sidebar').style.display = '';
  document.querySelector('.main').style.display = '';
}

// Model select → custom input toggle
document.getElementById('s-model').addEventListener('change', (e) => {
  const custom = document.getElementById('s-model-custom');
  custom.style.display = e.target.value === '' ? 'block' : 'none';
  if (e.target.value !== '') custom.value = '';
});

// Test Lark connection
document.getElementById('test-lark-btn').addEventListener('click', async () => {
  const btn = document.getElementById('test-lark-btn');
  const result = document.getElementById('test-lark-result');
  btn.disabled = true;
  btn.textContent = 'Testing...';
  result.textContent = '';
  result.className = 'test-result';
  try {
    const res = await fetch(`${API}/setup/test-lark`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        app_id: document.getElementById('s-lark-app-id').value,
        app_secret: document.getElementById('s-lark-app-secret').value,
      }),
    });
    const data = await res.json();
    result.textContent = data.ok ? data.message : data.error;
    result.className = `test-result ${data.ok ? 'ok' : 'fail'}`;
  } catch (e) {
    result.textContent = `Network error: ${e.message}`;
    result.className = 'test-result fail';
  }
  btn.disabled = false;
  btn.textContent = 'Test Connection';
});

document.getElementById('setup-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const errEl = document.getElementById('setup-error');
  const successEl = document.getElementById('setup-success');
  const btn = document.getElementById('setup-submit');
  errEl.style.display = 'none';
  successEl.style.display = 'none';
  btn.disabled = true;
  btn.textContent = 'Saving...';

  const modelSelect = document.getElementById('s-model').value;
  const modelCustom = document.getElementById('s-model-custom').value;
  const payload = {
    lark_app_id: document.getElementById('s-lark-app-id').value,
    lark_app_secret: document.getElementById('s-lark-app-secret').value,
    lark_bot_open_id: document.getElementById('s-lark-bot-id').value,
    api_key: document.getElementById('s-api-key').value,
    base_url: document.getElementById('s-base-url').value,
    agent_model: modelSelect || modelCustom || 'claude-sonnet-4-20250514',
    agent_name: document.getElementById('s-agent-name').value,
    agent_workdir: document.getElementById('s-agent-workdir').value,
  };

  try {
    const res = await fetch(`${API}/setup/save`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) {
      errEl.textContent = data.error || 'Save failed';
      errEl.style.display = 'block';
      btn.disabled = false;
      btn.textContent = 'Save & Start';
    } else if (data.restarting) {
      successEl.textContent = 'Configuration saved! Restarting...';
      successEl.style.display = 'block';
      btn.textContent = 'Restarting...';
      // 轮询等待重启完成
      waitForRestart();
    } else {
      successEl.textContent = data.message;
      successEl.style.display = 'block';
    }
  } catch (err) {
    errEl.textContent = `Network error: ${err.message}`;
    errEl.style.display = 'block';
    btn.disabled = false;
    btn.textContent = 'Save & Start';
  }
});

async function waitForRestart() {
  const successEl = document.getElementById('setup-success');
  let attempts = 0;
  const maxAttempts = 30;
  const check = async () => {
    attempts++;
    try {
      const data = await fetchJSON('/health');
      if (data.configured) {
        // 重启成功，配置已生效
        successEl.innerHTML = 'CodyClaw is ready! Redirecting...';
        setTimeout(() => window.location.reload(), 500);
        return;
      }
    } catch { /* server still restarting */ }
    if (attempts < maxAttempts) {
      setTimeout(check, 2000);
    } else {
      successEl.innerHTML = 'Restart is taking longer than expected. Please refresh the page manually.';
    }
  };
  setTimeout(check, 3000); // 等待 3 秒后开始检查
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
(async function init() {
  checkHealth();
  setInterval(checkHealth, 30000);
  const needsSetup = await checkSetup();
  if (!needsSetup) {
    loadDashboard();
    connectEventStream();
  }
})();
