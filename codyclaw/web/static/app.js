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
        `<option value="${a.id}">${esc(a.name)} (${a.id})</option>`
      ).join('');
      if (data.agents.length > 0) chatAgentId = data.agents[0].id;
    }
    select.onchange = () => { chatAgentId = select.value; };

    // Load history for current session
    if (chatSessionKey) {
      const hist = await fetchJSON(`${API}/chat/history?session_key=${encodeURIComponent(chatSessionKey)}`);
      renderChatHistory(hist.messages);
    }
  } catch (e) { console.error('Chat load error:', e); }
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
let originalConfigText = '';

async function loadConfig() {
  try {
    const data = await fetchJSON(`${API}/config`);
    document.getElementById('config-path').textContent = data.config_path;
    const text = yaml_stringify(data.config);
    originalConfigText = text;
    document.getElementById('config-editor').textContent = text;
    document.getElementById('config-save').style.display = 'none';

    // Track changes
    document.getElementById('config-editor').addEventListener('input', () => {
      const changed = document.getElementById('config-editor').textContent !== originalConfigText;
      document.getElementById('config-save').style.display = changed ? '' : 'none';
    });
  } catch (e) { console.error('Config load error:', e); }
}

document.getElementById('config-save').addEventListener('click', async () => {
  // We send the raw text as a config update note (actual YAML editing is complex)
  alert('Config saved. Note: full YAML editing requires restart to take effect. Use the API PUT /api/config for structured updates.');
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
    document.getElementById('health-status').textContent = `v${data.version} — Running`;
    document.querySelector('.status-dot').classList.remove('error');
  } catch {
    document.getElementById('health-status').textContent = 'Disconnected';
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
// Init
// ---------------------------------------------------------------------------
checkHealth();
setInterval(checkHealth, 30000);
loadDashboard();
// Auto-connect event stream on load
connectEventStream();
