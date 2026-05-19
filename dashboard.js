document.addEventListener('DOMContentLoaded', async () => {
  // 1. Validar sesión con token real — sin fallback local
  const token = localStorage.getItem('proxdeep_token');
  if (!token) {
    window.location.href = 'login.html';
    return;
  }

  let user;
  try {
    const res = await fetch('/api/auth/me', {
      headers: { 'Authorization': `Bearer ${token}` }
    });

    if (!res.ok) {
      // Token inválido o expirado — limpiar sesión y redirigir
      localStorage.removeItem('proxdeep_token');
      localStorage.removeItem('proxdeep_user');
      window.location.href = 'login.html';
      return;
    }

    // /api/auth/me devuelve: { id, email, name, role, tenant_id, area_id, area_name, is_active }
    user = await res.json();
    localStorage.setItem('proxdeep_user', JSON.stringify(user));

  } catch (e) {
    // Error de red — no permitir acceso sin autenticación verificada
    localStorage.removeItem('proxdeep_token');
    localStorage.removeItem('proxdeep_user');
    window.location.href = 'login.html';
    return;
  }

  // 2. Poblar UI con datos reales del backend
  const initials = user.name.split(' ').map(w => w[0]).join('').substring(0, 2).toUpperCase();
  document.querySelectorAll('.dash-user-avatar').forEach(el => el.textContent = initials);
  document.querySelectorAll('.dash-user-name').forEach(el => el.textContent = user.name);

  const roleEl = document.getElementById('sidebar-role');
  if (roleEl) roleEl.textContent = user.role;

  // Usar area_name del backend (no user.area que no existe en el modelo real)
  const areaDisplay = user.area_name || '—';
  const areaTitles = document.querySelectorAll('#area-title-name, #emp-area-name');
  areaTitles.forEach(el => el.textContent = areaDisplay);

  // 3. Role-Based Access Control (RBAC) & View Setup
  applyRBAC(user.role);
  const defaultView = user.role === 'employee' ? 'employee' : (user.role === 'leader' ? 'area' : 'dashboard');
  showView(defaultView);

  // 4. Navigation
  document.querySelectorAll('.dash-nav-item[data-view]').forEach(item => {
    item.addEventListener('click', e => {
      e.preventDefault();
      showView(item.dataset.view);
    });
  });

  // Logout — limpiar token y usuario, redirigir a login
  document.getElementById('logout-btn').addEventListener('click', () => {
    localStorage.removeItem('proxdeep_token');
    localStorage.removeItem('proxdeep_user');
    window.location.href = 'login.html';
  });

  // 4. Global Search
  setupGlobalSearch();

  // 5. Chat UI (Employee View)
  setupEmployeeChat(user);

  // 6. Dynamic Content by Area (Resources, Actions, Problems)
  renderAreaContent(user);

  // 7. Data Loaders
  if (['ceo', 'superadmin', 'admin', 'leader'].includes(user.role)) {
    loadUsersTable(user);
  }
  if (['ceo', 'superadmin'].includes(user.role)) {
    loadAreasGrid();
  }
});

// --- RBAC & Views ---
function applyRBAC(role) {
  const allowedViews = {
    'superadmin': ['dashboard', 'area', 'workspace', 'employee', 'users', 'areas', 'integrations', 'settings'],
    'ceo': ['dashboard', 'area', 'workspace', 'employee', 'users', 'areas', 'integrations', 'settings'],
    'admin': ['dashboard', 'area', 'employee', 'users', 'integrations', 'settings'],
    'leader': ['area', 'workspace', 'employee', 'users'],
    'employee': ['employee']
  };

  const navItems = document.querySelectorAll('.dash-nav-item');
  navItems.forEach(item => {
    const view = item.dataset.view;
    if (allowedViews[role] && allowedViews[role].includes(view)) {
      item.style.display = 'flex';
    } else {
      item.style.display = 'none';
    }
  });

  const navSections = document.querySelectorAll('.dash-nav-section');
  navSections.forEach(sec => {
    if (role === 'employee' && sec.textContent !== 'General') sec.style.display = 'none';
    if (role === 'leader' && sec.textContent === 'Sistema') sec.style.display = 'none';
  });
}

function showView(viewId) {
  document.querySelectorAll('.dash-view').forEach(v => v.classList.remove('active'));
  document.querySelectorAll('.dash-nav-item').forEach(a => a.classList.remove('active'));
  
  const view = document.getElementById('view-' + viewId);
  if (view) view.classList.add('active');
  
  const navItem = document.querySelector(`[data-view="${viewId}"]`);
  if (navItem) navItem.classList.add('active');

  const titles = {
    'dashboard': 'Vista Global',
    'employee': 'Portal de Empleado',
    'area': 'Mi Departamento',
    'workspace': 'Workspace Analítico',
    'users': 'Directorio de Usuarios',
    'areas': 'Departamentos',
    'integrations': 'Integraciones',
    'settings': 'Configuración'
  };
  
  const titleEl = document.getElementById('view-title');
  if (titleEl) titleEl.textContent = titles[viewId] || 'Dashboard';
}

// --- Global Search ---
function setupGlobalSearch() {
  const input = document.getElementById('global-search-input');
  const modal = document.getElementById('search-results');
  if (!input || !modal) return;

  // Shortcut Ctrl+K
  document.addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
      e.preventDefault();
      input.focus();
    }
  });

  input.addEventListener('input', (e) => {
    const val = e.target.value.trim();
    if (val.length > 2) {
      modal.classList.add('active');
      modal.innerHTML = `
        <p class="search-group-title">Documentos Internos</p>
        <div class="search-item">
          <div class="search-item-icon"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="var(--dash-accent)" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg></div>
          <div class="search-item-info"><p>Política de Vacaciones 2026.pdf</p><span>Coincidencia en texto: "...${val}..."</span></div>
        </div>
        <p class="search-group-title">Personas</p>
        <div class="search-item">
          <div class="search-item-icon" style="border-radius: 50%;">U</div>
          <div class="search-item-info"><p>Usuario con "${val}"</p><span>RRHH</span></div>
        </div>
      `;
    } else {
      modal.classList.remove('active');
    }
  });

  // Cierra modal si se hace clic fuera
  document.addEventListener('click', (e) => {
    if (!e.target.closest('.dash-global-search')) {
      modal.classList.remove('active');
    }
  });
}

// --- Chat IA ---
function setupEmployeeChat(user) {
  const input = document.getElementById('emp-chat-input');
  const send = document.getElementById('emp-chat-send');
  const msgs = document.getElementById('emp-chat-msgs');
  if (!input || !send || !msgs) return;

  // session_id se mantiene durante la sesión activa del dashboard
  // el backend lo crea automáticamente en el primer mensaje si no se envía
  let currentSessionId = null;

  function appendMsg(html, isUser) {
    const d = document.createElement('div');
    d.className = 'msg ' + (isUser ? 'user' : 'ai');
    d.innerHTML = `<div class="msg-content"><p>${html}</p></div>`;
    msgs.appendChild(d);
    msgs.scrollTop = msgs.scrollHeight;
  }

  async function sendMsg() {
    const t = input.value.trim();
    if (!t) return;

    appendMsg(t.replace(/</g, '&lt;'), true);
    input.value = '';
    input.disabled = true;
    send.disabled = true;

    const typing = document.createElement('div');
    typing.className = 'msg ai';
    typing.innerHTML = '<div class="msg-content"><p style="opacity:.5">Analizando base de conocimiento...</p></div>';
    msgs.appendChild(typing);
    msgs.scrollTop = msgs.scrollHeight;

    try {
      const token = localStorage.getItem('proxdeep_token');

      // Payload real: { session_id, content }
      // session_id es null en el primer mensaje — el backend crea la sesión y devuelve el id
      const res = await fetch('/api/chat/message', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify({
          session_id: currentSessionId,
          content: t
        })
      });

      typing.remove();

      if (res.ok) {
        // Response real: { session_id, response, message_id }
        const data = await res.json();
        currentSessionId = data.session_id; // mantener sesión para mensajes siguientes
        appendMsg(data.response.replace(/</g, '&lt;'), false);
      } else {
        const errData = await res.json().catch(() => ({}));
        const errMsg = errData.detail || `Error ${res.status}`;
        appendMsg(`⚠️ ${errMsg}`, false);
      }

    } catch (e) {
      typing.remove();
      appendMsg(`⚠️ No se pudo conectar al servidor. Verifica que el backend esté corriendo.`, false);
    } finally {
      input.disabled = false;
      send.disabled = false;
      input.focus();
    }
  }

  send.addEventListener('click', sendMsg);
  input.addEventListener('keypress', e => { if (e.key === 'Enter' && !e.shiftKey) sendMsg(); });
}

// --- Dynamic Area Content ---
function renderAreaContent(user) {
  // Recursos Destacados (usados en Vista Empleado y Vista Área)
  const resourcesHTML = `
    <div class="resource-item">
      <div class="resource-icon"><svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg></div>
      <div class="resource-info"><p>Manual Operativo Base</p><span>Actualizado hace 2 días</span></div>
    </div>
    <div class="resource-item">
      <div class="resource-icon"><svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M9 21V9"/></svg></div>
      <div class="resource-info"><p>FAQ del Departamento</p><span>Preguntas frecuentes</span></div>
    </div>
  `;
  const areaRes = document.getElementById('area-resources-list');
  const empRes = document.getElementById('emp-resources-list');
  if (areaRes) areaRes.innerHTML = resourcesHTML;
  if (empRes) empRes.innerHTML = resourcesHTML;

  // Acciones Rápidas (Empleado) — basadas en area_name del backend
  const areaName = user.area_name || '';
  let actionsHTML = '';
  if (areaName.toLowerCase().includes('rrhh') || areaName.toLowerCase().includes('recursos')) {
    actionsHTML = `<button class="quick-action-btn">Solicitar Vacaciones</button><button class="quick-action-btn">Ver Póliza de Salud</button>`;
  } else if (areaName.toLowerCase().includes('venta')) {
    actionsHTML = `<button class="quick-action-btn">Registrar Oportunidad</button><button class="quick-action-btn">Guía de Precios</button>`;
  } else {
    actionsHTML = `<button class="quick-action-btn">Reportar Incidencia</button><button class="quick-action-btn">Solicitar Aprobación</button>`;
  }
  const empActions = document.getElementById('emp-quick-actions');
  if (empActions) empActions.innerHTML = actionsHTML;

  // Problemas del Área (Líder)
  const problemsTbody = document.getElementById('area-problems-tbody');
  if (problemsTbody) {
    problemsTbody.innerHTML = `
      <tr>
        <td>Falta documentación en nuevo proyecto</td>
        <td>12</td>
        <td><span class="badge badge-warning">En revisión</span></td>
      </tr>
      <tr>
        <td>Consultas repetitivas sobre onboarding</td>
        <td>45</td>
        <td><span class="badge badge-success">Automatizado por IA</span></td>
      </tr>
    `;
  }
}

// --- Data Fetching (Users & Areas) ---
async function loadUsersTable(currentUser) {
  const tbody = document.getElementById('users-tbody');
  if (!tbody) return;

  let users = [];
  try {
    const token = localStorage.getItem('proxdeep_token');
    const res = await fetch('/api/admin/users', {
      headers: { 'Authorization': `Bearer ${token}` }
    });
    if (res.ok) {
      users = await res.json();
    } else {
      const errData = await res.json().catch(() => ({}));
      tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;color:var(--dash-text-muted);padding:1.5rem;">
        ⚠️ No se pudo cargar el directorio: ${errData.detail || `Error ${res.status}`}
      </td></tr>`;
      return;
    }
  } catch(e) {
    tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;color:var(--dash-text-muted);padding:1.5rem;">
      ⚠️ No se pudo conectar al servidor para cargar usuarios.
    </td></tr>`;
    return;
  }

  if (currentUser.role === 'leader') {
    users = users.filter(u => u.area_id === currentUser.area_id);
  }

  tbody.innerHTML = '';
  users.forEach(u => {
    const tr = document.createElement('tr');
    
    let actions = '';
    if (currentUser.role === 'ceo' || currentUser.role === 'admin' || currentUser.role === 'superadmin') {
      actions += `<button class="btn-ghost btn-sm" onclick="openPermissionsModal('${u.name}')">Avanzado</button>`;
    }

    tr.innerHTML = `
      <td>${u.name}<br><span style="font-size: 0.75rem; color: var(--dash-text-muted);">${u.email}</span></td>
      <td>${u.area_name || '—'}</td>
      <td><span class="role-badge ${u.role}">${u.role}</span></td>
      <td><span class="badge ${u.is_active ? 'badge-success' : 'badge-neutral'}">${u.is_active ? 'Activo' : 'Inactivo'}</span></td>
      <td class="text-right">${actions || '-'}</td>
    `;
    tbody.appendChild(tr);
  });
}

function loadAreasGrid() {
  const grid = document.getElementById('areas-grid');
  if (!grid) return;
  const areas = ["RRHH", "Ventas", "Operaciones", "Finanzas", "Tecnologia", "Soporte", "Gerencia"];
  
  let html = '';
  areas.forEach(a => {
    html += `
      <div class="dash-card">
        <p class="dash-card-title mb-3">${a}</p>
        <p style="font-size: 0.8rem; color: var(--dash-text-muted);">Base de conocimiento activa. Indexando repositorios de Drive y Notion.</p>
        <div style="margin-top: 1rem; display: flex; gap: 0.5rem;">
          <button class="btn-secondary btn-sm">Reglas de Área</button>
          <button class="btn-secondary btn-sm">Métricas</button>
        </div>
      </div>
    `;
  });
  grid.innerHTML = html;
}

// --- Integrations Setup (Phase 2) ---
document.querySelectorAll('.toggle-switch').forEach(toggle => {
  toggle.addEventListener('click', () => {
    toggle.classList.toggle('active');
  });
});

// --- Permissions Modal (Phase 2) ---
window.openPermissionsModal = function(userName) {
  document.getElementById('modal-user-name').textContent = userName;
  document.getElementById('permissions-modal').classList.add('active');
};
