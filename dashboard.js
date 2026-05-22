/**
 * dashboard.js — ProxDeep Enterprise Dashboard
 * Organizado por módulos:
 *   1. API Helpers
 *   2. Auth & Session
 *   3. UI Helpers (Toast, etc.)
 *   4. RBAC & Navigation
 *   5. Chat Context Builder
 *   6. Employee Chat (ChatGPT-style)
 *   7. Workspace
 *   8. Global Search
 *   9. Data Loaders (Users, Areas)
 *  10. Integrations & Settings
 *  11. Bootstrap (init)
 */

'use strict';

// ============================================================
// 1. API HELPERS
// ============================================================

const API = {
  /** GET autenticado */
  async get(path) {
    const token = localStorage.getItem('proxdeep_token');
    const res = await fetch(path, {
      headers: { 'Authorization': `Bearer ${token}` }
    });
    if (res.status === 401) { Auth.logout(); return null; }
    return res;
  },

  /** POST autenticado con JSON */
  async post(path, body) {
    const token = localStorage.getItem('proxdeep_token');
    const res = await fetch(path, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`
      },
      body: JSON.stringify(body)
    });
    if (res.status === 401) { Auth.logout(); return null; }
    return res;
  }
};

// ============================================================
// 2. AUTH & SESSION
// ============================================================

const Auth = {
  logout() {
    localStorage.removeItem('proxdeep_token');
    localStorage.removeItem('proxdeep_user');
    window.location.href = 'login.html';
  },

  async validateAndGetUser() {
    const token = localStorage.getItem('proxdeep_token');
    if (!token) { this.logout(); return null; }

    try {
      const res = await fetch('/api/auth/me', {
        headers: { 'Authorization': `Bearer ${token}` }
      });
      if (!res.ok) { this.logout(); return null; }
      const user = await res.json();
      localStorage.setItem('proxdeep_user', JSON.stringify(user));
      return user;
    } catch {
      // Fallback para modo demo / sin backend:
      console.warn('Backend no disponible. Usando usuario almacenado localmente (Modo Demo).');
      const cached = localStorage.getItem('proxdeep_user');
      if (cached) return JSON.parse(cached);
      
      this.logout();
      return null;
    }
  }
};

// ============================================================
// 3. UI HELPERS
// ============================================================

const UI = {
  /** Muestra un toast de notificación */
  toast(message, type = 'info', duration = 3000) {
    const container = document.getElementById('toast-container');
    if (!container) return;
    const t = document.createElement('div');
    t.className = `toast ${type}`;
    const icons = {
      success: '✓',
      error: '✕',
      info: 'ℹ'
    };
    t.innerHTML = `<span>${icons[type] || 'ℹ'}</span> ${message}`;
    container.appendChild(t);
    setTimeout(() => {
      t.style.opacity = '0';
      t.style.transform = 'translateY(10px)';
      t.style.transition = 'all 0.3s ease';
      setTimeout(() => t.remove(), 300);
    }, duration);
  },

  /** Actualiza el título del topbar */
  setTitle(title) {
    const el = document.getElementById('view-title');
    if (el) el.textContent = title;
  },

  /** Muestra un empty state en un contenedor */
  emptyState(container, { icon, title, description, action } = {}) {
    container.innerHTML = `
      <div class="empty-state">
        <div class="empty-state-icon">
          ${icon || '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>'}
        </div>
        <h3>${title || 'Sin datos'}</h3>
        <p>${description || 'No hay información disponible en este momento.'}</p>
        ${action ? `<button class="btn-primary" onclick="${action.fn}">${action.label}</button>` : ''}
      </div>
    `;
  }
};

// ============================================================
// 4. RBAC & NAVIGATION
// ============================================================

const RBAC = {
  allowedViews: {
    superadmin: ['dashboard','area','workspace','employee','users','areas','integrations','settings'],
    ceo:        ['dashboard','area','workspace','employee','users','areas','integrations','settings'],
    admin:      ['dashboard','area','employee','users','integrations','settings'],
    leader:     ['area','workspace','employee','users'],
    employee:   ['employee']
  },

  apply(role) {
    const allowed = this.allowedViews[role] || ['employee'];
    document.querySelectorAll('.dash-nav-item[data-view]').forEach(item => {
      item.style.display = allowed.includes(item.dataset.view) ? 'flex' : 'none';
    });
    document.querySelectorAll('.dash-nav-section').forEach(sec => {
      if (role === 'employee' && sec.textContent.trim() !== 'General') sec.style.display = 'none';
      if (role === 'leader' && sec.textContent.trim() === 'Sistema') sec.style.display = 'none';
    });
    // Para empleados: sidebar compacto
    if (role === 'employee') {
      document.querySelector('.dash-wrap')?.classList.add('role-employee');
    }
  }
};

const Nav = {
  titles: {
    dashboard:    'Vista Global',
    employee:     'Asistente IA',
    area:         'Mi Departamento',
    workspace:    'Workspace',
    users:        'Directorio de Usuarios',
    areas:        'Departamentos',
    integrations: 'Integraciones',
    settings:     'Configuración'
  },

  // Vista activa actual
  current: null,

  show(viewId) {
    document.querySelectorAll('.dash-view').forEach(v => v.classList.remove('active'));
    document.querySelectorAll('.dash-nav-item').forEach(a => a.classList.remove('active'));

    const view = document.getElementById('view-' + viewId);
    if (view) view.classList.add('active');

    const navItem = document.querySelector(`[data-view="${viewId}"]`);
    if (navItem) navItem.classList.add('active');

    UI.setTitle(this.titles[viewId] || 'Dashboard');

    // Ajustar padding del dash-content según la vista
    const content = document.getElementById('dash-content');
    if (content) {
      const noPadding = ['employee', 'workspace'];
      content.style.padding = noPadding.includes(viewId) ? '0' : '';
      content.style.maxWidth = noPadding.includes(viewId) ? 'none' : '';
    }

    this.current = viewId;
  },

  init(user) {
    document.querySelectorAll('.dash-nav-item[data-view]').forEach(item => {
      item.addEventListener('click', e => {
        e.preventDefault();
        this.show(item.dataset.view);
      });
    });

    document.getElementById('logout-btn')?.addEventListener('click', () => Auth.logout());

    const defaultView = user.role === 'employee' ? 'employee'
      : user.role === 'leader' ? 'area'
      : 'dashboard';
    this.show(defaultView);
  }
};

// ============================================================
// 5. CHAT CONTEXT BUILDER
// ============================================================

/**
 * buildChatContext(user, currentView)
 *
 * Construye el contexto completo del usuario para enviarlo al backend.
 * El backend puede usar estos campos para personalizar el system prompt
 * y las respuestas de la IA según el rol, área y vista activa.
 *
 * Payload enviado a POST /api/chat/message:
 * {
 *   session_id: string | null,
 *   content: string,
 *   context: {
 *     user_id: string,
 *     user_name: string,
 *     user_email: string,
 *     role: string,
 *     tenant_id: string,
 *     area_id: string,
 *     area_name: string,
 *     current_view: string,   // "employee", "workspace", etc.
 *     source: string          // "dashboard_employee", "dashboard_workspace"
 *   }
 * }
 *
 * TODO BACKEND: El endpoint /api/chat/message debe leer context.role,
 * context.area_name y context.source para ajustar el system prompt
 * dinámicamente antes de llamar al LLM.
 */
function buildChatContext(user, currentView) {
  return {
    user_id:      user.id,
    user_name:    user.name,
    user_email:   user.email,
    role:         user.role,
    tenant_id:    user.tenant_id,
    area_id:      user.area_id,
    area_name:    user.area_name || '—',
    current_view: currentView,
    source:       `dashboard_${currentView}`
  };
}

async function buildOrganizationContext(user, currentView) {
  const baseContext = buildChatContext(user, currentView);
  try {
    const res = await API.get(`/api/org/context?view=${encodeURIComponent(currentView)}`);
    if (res && res.ok) {
      const orgData = await res.json();
      baseContext.org_context = orgData;
    }
  } catch (err) {
    console.warn('Could not fetch organizational context, falling back to base chat context.', err);
  }
  return baseContext;
}

/** Genera el saludo inicial según rol y área */
function buildWelcomeMessage(user) {
  const area = user.area_name || 'tu área';
  const name = user.name?.split(' ')[0] || 'allí';

  if (user.role === 'employee') {
    return `Hola ${name}. Soy tu asistente de **${area}**. Tengo acceso a la base de conocimiento de tu departamento. ¿En qué puedo ayudarte hoy?`;
  }
  if (user.role === 'leader') {
    return `Hola ${name}. Soy el asistente de **${area}**. Puedo ayudarte con métricas del equipo, análisis de procesos y generación de reportes. ¿Qué necesitas?`;
  }
  if (['admin', 'ceo', 'superadmin'].includes(user.role)) {
    return `Hola ${name}. Tengo acceso transversal a todas las áreas de la organización. Puedo ayudarte con análisis, reportes y consultas sobre cualquier departamento.`;
  }
  return `Hola ${name}. ¿En qué puedo ayudarte?`;
}

/** Chips de sugerencia según área */
function buildWelcomeChips(user) {
  const area = (user.area_name || '').toLowerCase();
  if (area.includes('rrhh') || area.includes('recursos')) {
    return ['¿Cuántos días de vacaciones tengo?', 'Proceso de onboarding', 'Política de salud'];
  }
  if (area.includes('venta')) {
    return ['Guía de objeciones', 'Estado del pipeline', 'Playbook de ventas'];
  }
  if (area.includes('operacion')) {
    return ['Checklist del día', 'Reportar incidencia', 'Manual de QA'];
  }
  if (['admin', 'ceo', 'superadmin'].includes(user.role)) {
    return ['Resumen de métricas', 'Áreas con más tickets', 'Reporte semanal'];
  }
  return ['Reportar incidencia', 'Solicitar aprobación', 'Consultar proceso'];
}

// ============================================================
// 6. EMPLOYEE CHAT — ChatGPT/Perplexity style
// ============================================================

const EmployeeChat = {
  user: null,
  sessionId: null,
  isLoading: false,
  currentAction: null,

  init(user) {
    this.user = user;

    // Header
    const agentName = document.getElementById('emp-agent-name');
    const agentArea = document.getElementById('emp-agent-area');
    const contextLabel = document.getElementById('emp-context-label');
    if (agentName) agentName.textContent = 'Asistente ProxDeep';
    if (agentArea) agentArea.textContent = user.area_name || '—';
    if (contextLabel) contextLabel.textContent = `Rol: ${user.role} · ${user.area_name || 'Sin área'}`;

    // Welcome state
    const welcomeTitle = document.getElementById('emp-welcome-title');
    const welcomeSub   = document.getElementById('emp-welcome-sub');
    const chipsEl      = document.getElementById('emp-welcome-chips');

    if (welcomeTitle) welcomeTitle.textContent = `Hola, ${user.name?.split(' ')[0] || 'allí'} 👋`;
    if (welcomeSub) {
      const area = user.area_name || 'tu área';
      welcomeSub.textContent = `Soy tu asistente de ${area}. Tengo acceso a la base de conocimiento de tu departamento.`;
    }

    // Chips de sugerencia
    if (chipsEl) {
      const chips = buildWelcomeChips(user);
      chipsEl.innerHTML = chips.map(c =>
        `<button class="emp-chip" data-text="${c}">${c}</button>`
      ).join('');
      chipsEl.querySelectorAll('.emp-chip').forEach(btn => {
        btn.addEventListener('click', () => {
          const ta = document.getElementById('emp-textarea');
          if (ta) { ta.value = btn.dataset.text; ta.focus(); this._updateSendBtn(); }
        });
      });
    }

    // Acciones rápidas en sidebar
    this._renderQuickActions(user);

    // Nuevo chat
    document.getElementById('emp-new-chat-btn')?.addEventListener('click', () => this.newChat());

    // Botones de acción rápida
    document.getElementById('emp-btn-resolve')?.addEventListener('click', () => this.send('resolve'));
    document.getElementById('emp-btn-plan')?.addEventListener('click', () => this.send('plan'));

    // Textarea auto-resize (sendBtn) sendBtn.addEventListener('click', () => this.send());
    const ta = document.getElementById('emp-textarea');
    const sendBtn = document.getElementById('emp-send-btn');

    if (ta) {
      ta.addEventListener('input', () => {
        ta.style.height = 'auto';
        ta.style.height = Math.min(ta.scrollHeight, 160) + 'px';
        this._updateSendBtn();
      });
      ta.addEventListener('keydown', e => {
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          if (!sendBtn.disabled) this.send();
        }
      });
    }
    if (sendBtn) sendBtn.addEventListener('click', () => this.send());
  },

  _updateSendBtn() {
    const ta = document.getElementById('emp-textarea');
    const btn = document.getElementById('emp-send-btn');
    if (ta && btn) btn.disabled = !ta.value.trim() || this.isLoading;
  },

  _renderQuickActions(user) {
    const list = document.getElementById('emp-quick-actions-list');
    if (!list) return;
    const chips = buildWelcomeChips(user);
    list.innerHTML = chips.map(c => `
      <div class="emp-side-item" data-text="${c}">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="16"/><line x1="8" y1="12" x2="16" y2="12"/>
        </svg>
        ${c}
      </div>
    `).join('');
    list.querySelectorAll('.emp-side-item').forEach(item => {
      item.addEventListener('click', () => {
        const ta = document.getElementById('emp-textarea');
        if (ta) { ta.value = item.dataset.text; ta.focus(); this._updateSendBtn(); }
      });
    });
  },

  newChat() {
    this.sessionId = null;
    const msgs = document.getElementById('emp-messages');
    if (!msgs) return;
    // Limpiar mensajes y mostrar welcome
    msgs.querySelectorAll('.emp-msg').forEach(m => m.remove());
    const welcome = document.getElementById('emp-welcome');
    if (welcome) welcome.style.display = 'flex';
    const ta = document.getElementById('emp-textarea');
    if (ta) { ta.value = ''; ta.style.height = 'auto'; }
    this._updateSendBtn();
  },

  async send(action) {
    const ta = document.getElementById('emp-textarea');
    if (!ta || !ta.value.trim() || this.isLoading) return;

    const text = ta.value.trim();
    ta.value = '';
    ta.style.height = 'auto';
    this.isLoading = true;
    this._updateSendBtn();

    // Ocultar welcome
    const welcome = document.getElementById('emp-welcome');
    if (welcome) welcome.style.display = 'none';

    // Agregar mensaje del usuario
    this._appendMsg(text, 'user');

    // Typing indicator
    const typingEl = this._appendTyping();

    try {
      const context = await buildOrganizationContext(this.user, 'employee');
      const payload = {
        session_id: this.sessionId,
        content: text,
        context
      };
      if (action) payload.action = action;
      const res = await API.post('/api/chat/message', payload);

      typingEl.remove();

      if (!res || !res.ok) {
        const err = res ? await res.json().catch(() => ({})) : {};
        this._appendMsg(`⚠️ ${err.detail || 'Error al conectar con el asistente.'}`, 'ai');
      } else {
        const data = await res.json();
        this.sessionId = data.session_id;
        this._appendMsg(data.response, 'ai');
        this._addToHistory(text);
      }
    } catch {
      typingEl.remove();
      this._appendMsg('⚠️ No se pudo conectar al servidor. Verifica que el backend esté corriendo.', 'ai');
    } finally {
      this.isLoading = false;
      this._updateSendBtn();
    }
  }

  },

  _appendMsg(text, role) {
    const msgs = document.getElementById('emp-messages');
    if (!msgs) return;
    const initials = role === 'user'
      ? (this.user?.name?.split(' ').map(w => w[0]).join('').substring(0, 2).toUpperCase() || 'U')
      : 'IA';
    const div = document.createElement('div');
    div.className = `emp-msg ${role}`;
    div.innerHTML = `
      <div class="emp-msg-avatar">${initials}</div>
      <div class="emp-msg-bubble">${text.replace(/</g, '&lt;').replace(/\n/g, '<br>')}</div>
    `;
    msgs.appendChild(div);
    msgs.scrollTop = msgs.scrollHeight;
    return div;
  },

  _appendTyping() {
    const msgs = document.getElementById('emp-messages');
    const div = document.createElement('div');
    div.className = 'emp-msg ai';
    div.innerHTML = `
      <div class="emp-msg-avatar">IA</div>
      <div class="emp-typing"><span></span><span></span><span></span></div>
    `;
    msgs.appendChild(div);
    msgs.scrollTop = msgs.scrollHeight;
    return div;
  },

  _addToHistory(text) {
    const list = document.getElementById('emp-history-list');
    if (!list) return;
    // Remover placeholder
    list.querySelectorAll('[style*="text-subtle"]').forEach(el => el.remove());
    const item = document.createElement('div');
    item.className = 'emp-side-item';
    item.innerHTML = `
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
      </svg>
      ${text.substring(0, 28)}${text.length > 28 ? '…' : ''}
    `;
    list.insertBefore(item, list.firstChild);
  }
};

// ============================================================
// 7. WORKSPACE — Funcional
// ============================================================

const Workspace = {
  user: null,
  sessionId: null,
  isDirty: false,
  saveTimeout: null,

  // TODO BACKEND: Endpoint para guardar documentos
  // POST /api/workspace/documents
  // Body: { title, content, area_id, tenant_id }
  // GET  /api/workspace/documents → lista de documentos del usuario

  init(user) {
    this.user = user;

    const titleEl = document.getElementById('ws-title');
    const bodyEl  = document.getElementById('ws-body');

    // Marcar como modificado al editar
    [titleEl, bodyEl].forEach(el => {
      el?.addEventListener('input', () => this._markDirty());
    });

    // Guardar
    document.getElementById('ws-btn-save')?.addEventListener('click', () => this.save());

    // Exportar PDF
    document.getElementById('ws-btn-export')?.addEventListener('click', () => this.exportPDF());

    // Nuevo documento
    document.getElementById('ws-btn-clear')?.addEventListener('click', () => this.newDoc());

    // Chat del workspace
    const wsInput = document.getElementById('ws-input');
    const wsSend  = document.getElementById('ws-send-btn');
    wsSend?.addEventListener('click', () => this._sendWorkspaceMsg());
    wsInput?.addEventListener('keydown', e => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        this._sendWorkspaceMsg();
      }
    });

    // Cargar documento guardado desde backend o local
    this.load();
  },

  async load() {
    try {
      const res = await API.get('/api/workspace/documents');
      if (res && res.ok) {
        const data = await res.json();
        const titleEl = document.getElementById('ws-title');
        const bodyEl  = document.getElementById('ws-body');
        if (titleEl) titleEl.value = data.title || '';
        if (bodyEl) bodyEl.innerHTML = data.content || '';
        this.isDirty = false;
        if (data.updated_at) this._setStatus(`Último guardado: ${new Date(data.updated_at).toLocaleString()}`, 'saved');
        return;
      }
    } catch {
      // Fallback local si el backend no está disponible
    }

    this.restoreLocal();
  },

  _markDirty() {
    this.isDirty = true;
    this._setStatus('Sin guardar', 'saving');
    clearTimeout(this.saveTimeout);
    // Auto-save después de 3 segundos de inactividad
    this.saveTimeout = setTimeout(() => this.save(), 3000);
  },

  _setStatus(text, type = '') {
    const el = document.getElementById('ws-status');
    const textEl = document.getElementById('ws-status-text');
    if (!el || !textEl) return;
    el.className = `ws-status ${type}`;
    textEl.textContent = text;
  },

  async save() {
    if (!this.isDirty) return;
    const title   = document.getElementById('ws-title')?.value?.trim() || 'Sin título';
    const bodyEl  = document.getElementById('ws-body');
    const content = bodyEl?.innerText?.trim() || '';

    if (!title && !content) {
      this._setStatus('Documento vacío', '');
      return;
    }

    this._setStatus('Guardando...', 'saving');

    try {
      const res = await API.post('/api/workspace/documents', {
        title,
        content
      });

      if (!res || !res.ok) {
        throw new Error('save-failed');
      }

      const data = await res.json();
      const doc = { title: data.title, content: data.content, savedAt: data.updated_at || new Date().toISOString() };
      localStorage.setItem('proxdeep_ws_doc', JSON.stringify(doc));

      this.isDirty = false;
      this._setStatus('Guardado', 'saved');
      UI.toast('Documento guardado en el backend', 'success');
      setTimeout(() => this._setStatus('Sin cambios', ''), 3000);
    } catch {
      const doc = { title, content, savedAt: new Date().toISOString() };
      localStorage.setItem('proxdeep_ws_doc', JSON.stringify(doc));
      this.isDirty = false;
      this._setStatus('Guardado local', 'saved');
      UI.toast('Guardado local. El backend no está disponible.', 'info');
      setTimeout(() => this._setStatus('Sin cambios', ''), 3000);
    }
  },

  exportPDF() {
    const title   = document.getElementById('ws-title')?.value || 'Documento';
    const bodyEl  = document.getElementById('ws-body');
    const content = bodyEl?.innerHTML || '';

    if (!content.trim()) {
      UI.toast('El documento está vacío', 'info');
      return;
    }

    // Usar window.print() con estilos de impresión
    const printWin = window.open('', '_blank');
    printWin.document.write(`
      <!DOCTYPE html><html><head>
      <meta charset="UTF-8">
      <title>${title}</title>
      <style>
        body { font-family: 'Inter', sans-serif; padding: 2rem 3rem; color: #111; line-height: 1.7; }
        h1 { font-size: 1.8rem; margin-bottom: 1.5rem; }
        .ai-block { background: #f0f4ff; border-left: 3px solid #2563eb; padding: 1rem; margin: 1rem 0; border-radius: 0 6px 6px 0; }
      </style>
      </head><body>
      <h1>${title}</h1>
      <div>${content}</div>
      </body></html>
    `);
    printWin.document.close();
    printWin.focus();
    setTimeout(() => { printWin.print(); printWin.close(); }, 500);
    UI.toast('Abriendo diálogo de impresión/PDF', 'info');
  },

  newDoc() {
    if (this.isDirty) {
      if (!confirm('¿Descartar cambios y crear un nuevo documento?')) return;
    }
    const titleEl = document.getElementById('ws-title');
    const bodyEl  = document.getElementById('ws-body');
    if (titleEl) titleEl.value = '';
    if (bodyEl)  bodyEl.innerHTML = '';
    this.isDirty = false;
    this.sessionId = null;
    this._setStatus('Sin cambios', '');
  },

  async _sendWorkspaceMsg() {
    const input = document.getElementById('ws-input');
    if (!input || !input.value.trim()) return;

    const text = input.value.trim();
    input.value = '';

    const msgs = document.getElementById('ws-messages');
    if (!msgs) return;

    // Mensaje del usuario
    const userMsg = document.createElement('div');
    userMsg.className = 'ws-msg user';
    userMsg.textContent = text;
    msgs.appendChild(userMsg);

    // Typing
    const pending = document.createElement('div');
    pending.className = 'ws-msg-pending';
    pending.innerHTML = '<span></span><span></span><span></span> Generando...';
    msgs.appendChild(pending);
    msgs.scrollTop = msgs.scrollHeight;

    try {
      const context = await buildOrganizationContext(this.user, 'workspace');
      const res = await API.post('/api/chat/message', {
        session_id: this.sessionId,
        content: text,
        context
      });

      pending.remove();

      if (!res || !res.ok) {
        const aiMsg = document.createElement('div');
        aiMsg.className = 'ws-msg ai';
        aiMsg.textContent = '⚠️ Error al conectar con el asistente.';
        msgs.appendChild(aiMsg);
        return;
      }

      const data = await res.json();
      this.sessionId = data.session_id;

      // Mostrar respuesta con opción de insertar en el documento
      const aiMsg = document.createElement('div');
      aiMsg.className = 'ws-msg ai';
      aiMsg.innerHTML = `
        ${data.response.replace(/</g, '&lt;').replace(/\n/g, '<br>')}
        <div style="margin-top:0.5rem;">
          <button class="ws-ai-block-insert" data-content="${data.response.replace(/"/g, '&quot;')}">
            + Insertar en documento
          </button>
        </div>
      `;
      aiMsg.querySelector('.ws-ai-block-insert')?.addEventListener('click', (e) => {
        this._insertInDoc(e.target.dataset.content);
      });
      msgs.appendChild(aiMsg);
      msgs.scrollTop = msgs.scrollHeight;

    } catch {
      pending.remove();
      const aiMsg = document.createElement('div');
      aiMsg.className = 'ws-msg ai';
      aiMsg.textContent = '⚠️ No se pudo conectar al servidor.';
      msgs.appendChild(aiMsg);
    }
  },

  _insertInDoc(content) {
    const bodyEl = document.getElementById('ws-body');
    if (!bodyEl) return;
    const block = document.createElement('div');
    block.className = 'ws-ai-block';
    block.innerHTML = `
      <div class="ws-ai-block-label">✦ Generado por IA ProxDeep</div>
      <div>${content.replace(/\n/g, '<br>')}</div>
    `;
    bodyEl.appendChild(block);
    bodyEl.appendChild(document.createElement('br'));
    this._markDirty();
    UI.toast('Bloque insertado en el documento', 'success');
  },

  /** Restaurar último documento guardado localmente */
  restoreLocal() {
    try {
      const saved = localStorage.getItem('proxdeep_ws_doc');
      if (!saved) return;
      const doc = JSON.parse(saved);
      const titleEl = document.getElementById('ws-title');
      const bodyEl  = document.getElementById('ws-body');
      if (titleEl && doc.title) titleEl.value = doc.title;
      if (bodyEl  && doc.content) bodyEl.innerHTML = doc.content;
      this._setStatus(`Restaurado: ${new Date(doc.savedAt).toLocaleTimeString()}`, 'saved');
    } catch { /* ignorar */ }
  }
};

// ============================================================
// 8. GLOBAL SEARCH
// ============================================================

function initGlobalSearch() {
  const input = document.getElementById('global-search-input');
  const modal = document.getElementById('search-results');
  if (!input || !modal) return;

  document.addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
      e.preventDefault();
      input.focus();
    }
  });

  input.addEventListener('input', async e => {
    const val = e.target.value.trim();
    if (val.length > 2) {
      modal.classList.add('active');
      modal.innerHTML = `<div class="search-item">Buscando...</div>`;

      const res = await API.get(`/api/search?q=${encodeURIComponent(val)}`);
      if (!res) {
        modal.innerHTML = `<div class="search-item search-empty">No hay conexión con el servidor.</div>`;
        return;
      }

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        modal.innerHTML = `<div class="search-item search-empty">⚠️ ${err.detail || 'Error al buscar'}</div>`;
        return;
      }

      const data = await res.json();
      if (!data.results || !data.results.length) {
        modal.innerHTML = `<div class="search-item search-empty">No se encontraron resultados para "${val}".</div>`;
        return;
      }

      modal.innerHTML = data.results.map(item => `
        <button class="search-item" data-path="${item.path}">
          <div class="search-item-icon">${item.type === 'Usuario' ? '<span class="search-item-avatar">U</span>' : '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="var(--dash-accent)" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>'}</div>
          <div class="search-item-info">
            <p>${item.title}</p>
            <span>${item.subtitle} · ${item.detail}</span>
          </div>
        </button>
      `).join('');

      modal.querySelectorAll('.search-item').forEach(btn => {
        btn.addEventListener('click', () => {
          const path = btn.dataset.path;
          if (path) {
            if (path === 'workspace') Nav.show('workspace');
            else if (path === 'users') Nav.show('users');
            else if (path === 'areas') Nav.show('areas');
            else Nav.show('dashboard');
          }
          modal.classList.remove('active');
        });
      });
    } else {
      modal.classList.remove('active');
    }
  });

  document.addEventListener('click', e => {
    if (!e.target.closest('.dash-global-search')) modal.classList.remove('active');
  });
}

// ============================================================
// 9. DATA LOADERS
// ============================================================

async function loadUsersTable(currentUser) {
  const tbody = document.getElementById('users-tbody');
  if (!tbody) return;

  tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;padding:2rem;color:var(--dash-text-muted);">Cargando...</td></tr>`;

  try {
    const res = await API.get('/api/admin/users');
    if (!res || !res.ok) {
      const err = res ? await res.json().catch(() => ({})) : {};
      tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;padding:2rem;color:var(--dash-text-muted);">
        ⚠️ ${err.detail || `Error ${res?.status || ''}`}
      </td></tr>`;
      return;
    }

    let users = await res.json();
    if (currentUser.role === 'leader') {
      users = users.filter(u => u.area_id === currentUser.area_id);
    }

    if (!users.length) {
      UI.emptyState(tbody.parentElement.parentElement, {
        title: 'Sin usuarios',
        description: 'No hay usuarios registrados en este momento.'
      });
      return;
    }

    tbody.innerHTML = '';
    users.forEach(u => {
      const tr = document.createElement('tr');
      const canManage = ['ceo','admin','superadmin'].includes(currentUser.role);
      tr.innerHTML = `
        <td>
          ${u.name}
          <br><span style="font-size:0.75rem;color:var(--dash-text-muted);">${u.email}</span>
        </td>
        <td>${u.area_name || '—'}</td>
        <td><span class="role-badge ${u.role}">${u.role}</span></td>
        <td><span class="badge ${u.is_active ? 'badge-success' : 'badge-neutral'}">${u.is_active ? 'Activo' : 'Inactivo'}</span></td>
        <td class="text-right">
          ${canManage ? `<button class="btn-ghost btn-sm" onclick="openPermissionsModal('${u.name}')">Permisos</button>` : '—'}
        </td>
      `;
      tbody.appendChild(tr);
    });
  } catch {
    tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;padding:2rem;color:var(--dash-text-muted);">
      ⚠️ No se pudo conectar al servidor.
    </td></tr>`;
  }
}

async function loadAreasGrid() {
  const grid = document.getElementById('areas-grid');
  if (!grid) return;

  grid.innerHTML = `<div style="grid-column: 1/-1; text-align: center; padding: 2rem; color: var(--dash-text-muted);">Cargando departamentos...</div>`;

  try {
    const res = await API.get('/api/org/areas');
    if (!res || !res.ok) {
      grid.innerHTML = `<div style="grid-column: 1/-1; text-align: center; padding: 2rem; color: var(--dash-text-muted);">⚠️ Error al cargar departamentos.</div>`;
      return;
    }
    const areas = await res.json();
    if (!areas || !areas.length) {
      grid.innerHTML = `<div style="grid-column: 1/-1; text-align: center; padding: 2rem; color: var(--dash-text-muted);">No hay departamentos disponibles.</div>`;
      return;
    }
    grid.innerHTML = areas.map(a => {
      const docsCount = a.documents ? a.documents.length : 0;
      const connectionsCount = a.connected_areas ? a.connected_areas.length : 0;
      return `
        <div class="dash-card">
          <p class="dash-card-title" style="margin-bottom:0.5rem;">${a.name}</p>
          <p style="font-size:0.78rem;color:var(--dash-text-muted);margin-bottom:1rem;">
            Líder: ${a.leader || '—'}<br>
            ${a.metrics ? a.metrics.tickets_open : 0} tickets abiertos · ${docsCount} docs indexados<br>
            ${connectionsCount} conexiones con otras áreas
          </p>
          <div style="display:flex;gap:0.5rem;">
            <button class="btn-secondary btn-sm" onclick="UI.toast('Reglas de ${a.name} cargadas', 'info')">Reglas</button>
            <button class="btn-secondary btn-sm" onclick="UI.toast('Métricas de ${a.name}: satisfacción ${a.metrics?.satisfaction || 0}', 'info')">Métricas</button>
          </div>
        </div>
      `;
    }).join('');
  } catch (err) {
    grid.innerHTML = `<div style="grid-column: 1/-1; text-align: center; padding: 2rem; color: var(--dash-text-muted);">⚠️ No se pudo conectar al servidor.</div>`;
  }
}

async function renderAreaContent(user) {
  // Área del líder
  const areaTitleEl = document.getElementById('area-title-name');
  if (areaTitleEl) areaTitleEl.textContent = user.area_name || 'tu Departamento';

  const areaRes = document.getElementById('area-resources-list');
  const problemsTbody = document.getElementById('area-problems-tbody');

  if (areaRes) areaRes.innerHTML = `<div style="text-align:center;color:var(--dash-text-muted);padding:1rem;">Cargando recursos...</div>`;
  if (problemsTbody) problemsTbody.innerHTML = `<tr><td colspan="3" style="text-align:center;color:var(--dash-text-muted);padding:1rem;">Cargando bloqueos...</td></tr>`;

  try {
    const res = await API.get('/api/org/context?view=area');
    if (!res || !res.ok) {
      if (areaRes) areaRes.innerHTML = `<div style="text-align:center;color:var(--dash-text-muted);padding:1rem;">⚠️ Error al cargar recursos.</div>`;
      if (problemsTbody) problemsTbody.innerHTML = `<tr><td colspan="3" style="text-align:center;color:var(--dash-text-muted);padding:1rem;">⚠️ Error al cargar bloqueos.</td></tr>`;
      return;
    }
    const data = await res.json();
    const areaContext = data.area_context;

    // Actualizar nombre si es necesario
    if (areaTitleEl && areaContext.name) areaTitleEl.textContent = areaContext.name;

    // Actualizar tarjetas de métricas
    const statCards = document.querySelectorAll('#view-area .dash-stats .dash-stat-card');
    if (statCards.length >= 3 && areaContext.metrics) {
      const resRate = areaContext.metrics.resolution_rate !== undefined 
        ? `${Math.round(areaContext.metrics.resolution_rate * 100)}%`
        : '—';
      statCards[0].querySelector('.dash-stat-value').textContent = resRate;
      statCards[1].querySelector('.dash-stat-value').textContent = areaContext.metrics.tickets_open ?? 0;
      statCards[2].querySelector('.dash-stat-value').textContent = areaContext.metrics.docs_indexed ?? 0;
    }

    // Poblar recursos
    if (areaRes) {
      const resources = areaContext.resources || [];
      if (!resources.length) {
        areaRes.innerHTML = `<div style="text-align:center;color:var(--dash-text-muted);padding:1rem;">Sin recursos destacados registrados.</div>`;
      } else {
        areaRes.innerHTML = resources.map(r => `
          <div class="resource-item">
            <div class="resource-icon">
              <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                <polyline points="14 2 14 8 20 8"/>
              </svg>
            </div>
            <div class="resource-info">
              <p>${r}</p>
              <span>Recurso del departamento</span>
            </div>
          </div>
        `).join('');
      }
    }

    // Poblar bloqueos / dependencias
    if (problemsTbody) {
      const deps = data.dependencies || [];
      if (!deps.length) {
        problemsTbody.innerHTML = `<tr><td colspan="3" style="text-align:center;color:var(--dash-text-muted);padding:1rem;">Sin bloqueos ni dependencias activas.</td></tr>`;
      } else {
        problemsTbody.innerHTML = deps.map(d => {
          const isOutgoing = d.from_area === areaContext.id;
          const otherAreaId = isOutgoing ? d.to_area : d.from_area;
          const otherArea = otherAreaId.toUpperCase();
          const severityBadge = ['critical', 'high'].includes(d.severity) ? 'badge-warning' : 'badge-neutral';
          const statusText = isOutgoing ? 'Nos Bloquea' : 'Les Bloquea';
          const desc = isOutgoing 
            ? `Dependemos de ${otherArea}: ${d.description}`
            : `${otherArea} depende de nosotros: ${d.description}`;
          
          return `
            <tr>
              <td>${desc}</td>
              <td><span class="badge ${severityBadge}">${d.severity.toUpperCase()}</span></td>
              <td><span class="badge ${isOutgoing ? 'badge-warning' : 'badge-neutral'}">${statusText}</span></td>
            </tr>
          `;
        }).join('');
      }
    }
  } catch (err) {
    console.error("Error rendering area content:", err);
    if (areaRes) areaRes.innerHTML = `<div style="text-align:center;color:var(--dash-text-muted);padding:1rem;">⚠️ Error de red al cargar recursos.</div>`;
    if (problemsTbody) problemsTbody.innerHTML = `<tr><td colspan="3" style="text-align:center;color:var(--dash-text-muted);padding:1rem;">⚠️ Error de red al cargar bloqueos.</td></tr>`;
  }
}

/* ============================================================
   INTELLIGENCE WIDGET RENDER HELPERS
   ============================================================ */

function renderProjects(projects, areas) {
  if (!projects || !projects.length) {
    return `<div style="text-align:center;padding:2rem;color:var(--dash-text-muted);">Sin proyectos transversales activos.</div>`;
  }
  return `<div class="org-projects-list">` + projects.map(p => {
    const areasNames = p.areas.map(aid => areas.find(a => a.id === aid)?.name || aid);
    const progressPct = Math.round((p.metrics?.progress || 0) * 100);
    return `
      <div class="org-project-item">
        <div class="org-project-header">
          <span class="org-project-name">${p.name}</span>
          <span class="badge ${p.priority === 'critical' ? 'badge-warning' : p.priority === 'high' ? 'badge-warning' : 'badge-neutral'}">${p.priority.toUpperCase()}</span>
        </div>
        <p class="org-project-desc">${p.description}</p>
        <div class="org-project-areas">
          ${areasNames.map(name => `<span class="org-area-tag">${name}</span>`).join('')}
        </div>
        <div style="font-size:0.75rem;color:var(--dash-text-muted);display:flex;justify-content:space-between;margin-bottom:0.25rem;">
          <span>Progreso del Proyecto</span>
          <span>${progressPct}%</span>
        </div>
        <div class="org-progress-bar">
          <div class="org-progress-fill" style="width: ${progressPct}%;"></div>
        </div>
      </div>
    `;
  }).join('') + `</div>`;
}

function renderRelations(relationships, areas) {
  if (!relationships || !relationships.length) {
    return `<div style="text-align:center;padding:2rem;color:var(--dash-text-muted);">Sin relaciones detectadas.</div>`;
  }
  return `<div class="org-relations-list">` + relationships.map(r => {
    const areasNames = r.areas.map(aid => areas.find(a => a.id === aid)?.name || aid);
    const typeLabel = r.type === 'shared_project' ? 'Proyecto Compartido' : 'Dependencia Técnica';
    return `
      <div class="org-relation-item">
        <div class="org-relation-title">${areasNames.join(' ↔ ')} (${typeLabel})</div>
        <div class="org-relation-insight">${r.insight}</div>
      </div>
    `;
  }).join('') + `</div>`;
}

function renderDependencies(dependencies, areas) {
  if (!dependencies || !dependencies.length) {
    return `<div style="text-align:center;padding:2rem;color:var(--dash-text-muted);">Sin dependencias registradas.</div>`;
  }
  return `<div class="org-dependencies-list">` + dependencies.map(d => {
    const fromArea = areas.find(a => a.id === d.from_area)?.name || d.from_area;
    const toArea = areas.find(a => a.id === d.to_area)?.name || d.to_area;
    const sevClass = d.severity === 'critical' ? 'critical' : d.severity === 'high' ? 'high' : 'medium';
    return `
      <div class="org-dependency-item ${sevClass}">
        <div class="org-dependency-text">
          <h4>${fromArea} depende de ${toArea}</h4>
          <p>${d.description}</p>
        </div>
        <span class="badge ${d.severity === 'critical' ? 'badge-warning' : d.severity === 'high' ? 'badge-warning' : 'badge-neutral'}">${d.severity.toUpperCase()}</span>
      </div>
    `;
  }).join('') + `</div>`;
}

let treeData = null;
async function loadDashboardData(currentUser) {
  const tabContent = document.getElementById('org-intel-content');
  if (!tabContent) return;
  tabContent.innerHTML = `<div style="text-align:center;padding:2rem;color:var(--dash-text-muted);">Cargando inteligencia organizacional...</div>`;
  
  try {
    const res = await API.get('/api/org/tree');
    if (!res || !res.ok) {
      tabContent.innerHTML = `<div style="text-align:center;padding:2rem;color:var(--dash-text-muted);">⚠️ Error al cargar inteligencia organizacional.</div>`;
      return;
    }
    treeData = await res.json();
    
    // Populate Critical Problems Table
    const problemsTbody = document.getElementById('org-problems-tbody');
    if (problemsTbody) {
      const criticalDeps = treeData.dependencies.filter(d => ['high', 'critical'].includes(d.severity));
      if (!criticalDeps.length) {
        problemsTbody.innerHTML = `<tr><td colspan="4" style="text-align:center;color:var(--dash-text-muted);">Sin problemas críticos detectados.</td></tr>`;
      } else {
        problemsTbody.innerHTML = criticalDeps.map(d => {
          const fromArea = treeData.areas.find(a => a.id === d.from_area)?.name || d.from_area;
          const toArea = treeData.areas.find(a => a.id === d.to_area)?.name || d.to_area;
          const badgeClass = d.severity === 'critical' ? 'badge-warning' : 'badge-neutral';
          return `
            <tr>
              <td><strong>${fromArea}</strong></td>
              <td>Fricción con ${toArea}: ${d.description}</td>
              <td><span class="badge ${badgeClass}">${d.severity.toUpperCase()}</span></td>
              <td><span class="badge badge-warning">En revisión</span></td>
            </tr>
          `;
        }).join('');
      }
    }

    // Populate Adoption Activity List
    const adoptionList = document.getElementById('adoption-activity-list');
    if (adoptionList && treeData.areas) {
      const totalTickets = treeData.areas.reduce((sum, a) => sum + (a.metrics?.tickets_open || 0), 0) || 1;
      adoptionList.innerHTML = treeData.areas.map(a => {
        const openTickets = a.metrics?.tickets_open || 0;
        const pct = Math.round((openTickets / totalTickets) * 100);
        return `
          <div class="activity-item">
            <div class="activity-bar"><div class="activity-fill" style="width: ${pct}%;"></div></div>
            <div class="activity-info"><span>${a.name}</span><span>${pct}% (${openTickets} tickets)</span></div>
          </div>
        `;
      }).join('');
    }

    // Setup Tab Buttons event listeners
    const tabProjects = document.getElementById('btn-tab-projects');
    const tabRelations = document.getElementById('btn-tab-relations');
    const tabDeps = document.getElementById('btn-tab-deps');

    const selectTab = (activeBtn, tabName) => {
      [tabProjects, tabRelations, tabDeps].forEach(btn => {
        if (btn) btn.classList.remove('active');
      });
      if (activeBtn) activeBtn.classList.add('active');

      if (tabName === 'projects') {
        tabContent.innerHTML = renderProjects(treeData.projects, treeData.areas);
      } else if (tabName === 'relations') {
        tabContent.innerHTML = renderRelations(treeData.cross_area_relationships, treeData.areas);
      } else if (tabName === 'dependencies') {
        tabContent.innerHTML = renderDependencies(treeData.dependencies, treeData.areas);
      }
    };

    if (tabProjects) {
      tabProjects.onclick = () => selectTab(tabProjects, 'projects');
    }
    if (tabRelations) {
      tabRelations.onclick = () => selectTab(tabRelations, 'relations');
    }
    if (tabDeps) {
      tabDeps.onclick = () => selectTab(tabDeps, 'dependencies');
    }

    // Select default tab
    selectTab(tabProjects, 'projects');

  } catch (err) {
    console.error("Error loading dashboard data:", err);
    tabContent.innerHTML = `<div style="text-align:center;padding:2rem;color:var(--dash-text-muted);">⚠️ No se pudo conectar al servidor.</div>`;
  }
}

// ============================================================
// 10. INTEGRATIONS & SETTINGS (Fase 2)
// ============================================================
// Las integraciones y configuración por ahora son estáticas y 
// están resueltas directamente en el HTML de dashboard.html.

// ============================================================
// 11. BOOTSTRAP (init)
// ============================================================

function initIntegrations() {
  document.querySelectorAll('.toggle-switch').forEach(toggle => {
    toggle.addEventListener('click', () => {
      toggle.classList.toggle('active');
      const label = toggle.closest('.dash-card')?.querySelector('.dash-card-title')?.textContent || 'Integración';
      const state = toggle.classList.contains('active') ? 'activada' : 'desactivada';
      UI.toast(`${label} ${state}`, 'info');
      // TODO BACKEND: PATCH /api/admin/integrations/{id} { enabled: bool }
    });
  });
}

function renderSettings(user) {
  const container = document.getElementById('view-settings');
  if (!container) return;

  container.innerHTML = `
    <div class="view-header">
      <div>
        <p class="section-title">Configuración del Sistema</p>
        <p class="section-sub">Gestiona tu cuenta, seguridad y preferencias.</p>
      </div>
    </div>
    <div class="dash-grid-2">

      <div class="dash-card">
        <p class="dash-card-title">Mi cuenta</p>
        <div class="settings-row">
          <div class="settings-row-info">
            <h4>${user.name}</h4>
            <p>${user.email} · <span class="role-badge ${user.role}" style="font-size:0.68rem;">${user.role}</span></p>
          </div>
          <button class="btn-secondary btn-sm" disabled title="TODO BACKEND">Editar</button>
        </div>
        <div class="settings-row">
          <div class="settings-row-info">
            <h4>Área asignada</h4>
            <p>${user.area_name || 'Sin área asignada'}</p>
          </div>
        </div>
        <div class="settings-row">
          <div class="settings-row-info">
            <h4>Contraseña</h4>
            <p>Última actualización: desconocida</p>
          </div>
          <button class="btn-secondary btn-sm" disabled title="TODO BACKEND">Cambiar</button>
        </div>
      </div>

      <div class="dash-card">
        <p class="dash-card-title">Seguridad</p>
        <div class="settings-row">
          <div class="settings-row-info">
            <h4>Autenticación 2FA</h4>
            <p>Añade una capa extra de seguridad a tu cuenta</p>
          </div>
          <div class="toggle-switch" title="TODO BACKEND"></div>
        </div>
        <div class="settings-row">
          <div class="settings-row-info">
            <h4>Sesiones activas</h4>
            <p>Ver y cerrar sesiones en otros dispositivos</p>
          </div>
          <button class="btn-secondary btn-sm" disabled title="TODO BACKEND">Ver</button>
        </div>
      </div>

      <div class="dash-card">
        <p class="dash-card-title">Facturación</p>
        <div class="settings-row">
          <div class="settings-row-info">
            <h4>Plan actual</h4>
            <p><strong>Enterprise</strong> · Activo</p>
          </div>
          <button class="btn-secondary btn-sm" disabled title="TODO BACKEND">Gestionar</button>
        </div>
        <div class="settings-row">
          <div class="settings-row-info">
            <h4>Próxima factura</h4>
            <p>— · TODO BACKEND</p>
          </div>
        </div>
      </div>

      <div class="dash-card">
        <p class="dash-card-title">Preferencias</p>
        <div class="settings-row">
          <div class="settings-row-info">
            <h4>Idioma</h4>
            <p>Español (predeterminado)</p>
          </div>
          <button class="btn-secondary btn-sm" disabled title="TODO BACKEND">Cambiar</button>
        </div>
        <div class="settings-row">
          <div class="settings-row-info">
            <h4>Notificaciones</h4>
            <p>Email y en plataforma</p>
          </div>
          <div class="toggle-switch active" title="TODO BACKEND"></div>
        </div>
      </div>

    </div>
  `;

  // Re-init toggles dentro de settings
  container.querySelectorAll('.toggle-switch').forEach(toggle => {
    toggle.addEventListener('click', () => toggle.classList.toggle('active'));
  });
}

// ============================================================
// 11. PERMISSIONS MODAL
// ============================================================

window.openPermissionsModal = function(userName) {
  document.getElementById('modal-user-name').textContent = userName;
  document.getElementById('permissions-modal').classList.add('active');
};

// ============================================================
// 12. BOOTSTRAP — Punto de entrada principal
// ============================================================

document.addEventListener('DOMContentLoaded', async () => {

  // ── 1. Validar sesión ──────────────────────────────────────
  const user = await Auth.validateAndGetUser();
  if (!user) return; // Auth.logout() ya redirigió

  // ── 2. Poblar UI con datos del usuario ─────────────────────
  const initials = user.name
    .split(' ')
    .map(w => w[0])
    .join('')
    .substring(0, 2)
    .toUpperCase();

  document.querySelectorAll('.dash-user-avatar').forEach(el => el.textContent = initials);
  document.querySelectorAll('.dash-user-name').forEach(el => el.textContent = user.name);

  const roleEl = document.getElementById('sidebar-role');
  if (roleEl) roleEl.textContent = user.role;

  // ── 3. RBAC ────────────────────────────────────────────────
  RBAC.apply(user.role);

  // ── 4. Navegación ──────────────────────────────────────────
  Nav.init(user);

  // ── 5. Módulos por rol ─────────────────────────────────────

  // Employee chat — siempre disponible
  EmployeeChat.init(user);

  // Workspace — para roles con acceso
  if (RBAC.allowedViews[user.role]?.includes('workspace')) {
    Workspace.init(user);
  }

  // Búsqueda global
  initGlobalSearch();

  // Contenido del área (vista líder)
  renderAreaContent(user);

  // Settings dinámico
  renderSettings(user);

  // Integraciones
  initIntegrations();

  // Tabla de usuarios
  if (['ceo', 'superadmin', 'admin', 'leader'].includes(user.role)) {
    loadUsersTable(user);
  }

  // Grid de áreas
  if (['ceo', 'superadmin'].includes(user.role)) {
    loadAreasGrid();
  }

  // Cargar datos de inteligencia organizacional en dashboard
  if (['ceo', 'superadmin', 'admin'].includes(user.role)) {
    loadDashboardData(user);
  }

  // ── 6. Modal de permisos ───────────────────────────────────
  document.getElementById('close-modal')?.addEventListener('click', () => {
    document.getElementById('permissions-modal').classList.remove('active');
  });

  // ── 7. Ajustar topbar según vista activa ──────────────────
  // Las vistas fullscreen (employee, workspace) usan position:absolute
  // sobre dash-main con inset:0, cubriendo topbar y contenido.
  // No necesitamos manipular el DOM — el CSS lo maneja solo.
  // Solo re-aplicamos la vista actual para disparar la animación.
  Nav.show(Nav.current);

});
