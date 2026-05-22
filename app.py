from flask import Flask, send_from_directory, jsonify, request
import os
from datetime import datetime
from backend.utils.mock_db import read, write
app = Flask(__name__, static_url_path='', static_folder='.')

# ------------------- Mock data -------------------
USERS = [
    {"id": 1, "name": "Admin CEO", "email": "ceo@proxdeep.com", "role": "ceo", "area": "Gerencia", "tenant_id": "tenant-1", "area_id": "area-1", "area_name": "Gerencia", "status": "Activo"},
    {"id": 2, "name": "Maria Rodriguez", "email": "admin@proxdeep.com", "role": "admin", "area": "Operaciones", "tenant_id": "tenant-1", "area_id": "area-2", "area_name": "Operaciones", "status": "Activo"},
    {"id": 3, "name": "Carlos Andrade", "email": "leader@proxdeep.com", "role": "leader", "area": "Ventas", "tenant_id": "tenant-1", "area_id": "area-3", "area_name": "Ventas", "status": "Activo"},
    {"id": 4, "name": "Juan Torres", "email": "employee@proxdeep.com", "role": "employee", "area": "Soporte", "tenant_id": "tenant-1", "area_id": "area-4", "area_name": "Soporte", "status": "Activo"}
]

AREAS = ["RRHH", "Ventas", "Operaciones", "Finanzas", "Tecnologia", "Soporte", "Gerencia"]

ORG_CONTEXT = {
    "user_context": {
        "id": "78ccc76f-3625-4345-b222-26e9b5776e11",
        "name": "CEO ProxDeep",
        "email": "ceo@proxdeep.com",
        "role": "ceo",
        "area_id": "9b0932b8-58a1-4426-8ad9-dcf1f165b149",
        "area_name": "Gerencia",
        "tenant_id": "7960c48f-25cb-4843-a24a-588aac60988d",
        "current_view": "default",
        "source": "dashboard_default"
    },
    "area_context": {
        "id": "gerencia",
        "name": "Gerencia",
        "leader": "Roberto Silva",
        "metrics": {"tickets_open": 3, "resolution_rate": 1.0, "docs_indexed": 12, "satisfaction": 4.8},
        "documents": [{"id": "doc-ger-1", "name": "Plan Estratégico 2026", "shared_with": ["finanzas", "rrhh"]}],
        "resources": ["Power BI", "Notion"]
    },
    "org_tree": {
        "company": {"id": "proxdeep-corp", "name": "ProxDeep Corp", "industry": "IA Empresarial", "size": "50-200"},
        "total_areas": 7,
        "total_projects": 5,
        "areas_summary": [
            {"id": "rrhh", "name": "Recursos Humanos", "leader": "María Rodríguez"},
            {"id": "tecnologia", "name": "Tecnología", "leader": "Carlos Andrade"},
            {"id": "ventas", "name": "Ventas", "leader": "Juan Torres"},
            {"id": "operaciones", "name": "Operaciones", "leader": "Ana Gómez"},
            {"id": "soporte", "name": "Soporte Interno", "leader": "Pedro Díaz"},
            {"id": "finanzas", "name": "Finanzas", "leader": "Laura Peña"},
            {"id": "gerencia", "name": "Gerencia", "leader": "Roberto Silva"}
        ]
    }
}

# ------------------- Additional Mock data -------------------
BILLING_MOCK = {
    "plan": {
        "name": "Business",
        "status": "active",
        "renewal_date": "2026-06-21",
        "price_monthly": 890,
        "currency": "USD",
        "billing_cycle": "monthly",
        "seats_contracted": 25,
        "seats_used": 18,
        "models_allowed": ["gemma3:4b", "llama3.2:3b", "mistral:7b"],
        "default_model": "gemma3:4b",
        "features": ["IA Organizacional", "Workspace", "VS Code Extension", "API REST", "Soporte prioritario"]
    },
    "current_month": {
        "spend": 623,
        "limit": 890,
        "percent_used": 70,
        "messages_sent": 4820,
        "tokens_used": 1240000,
        "tokens_limit": 2000000,
        "estimated_eom": 812,
        "recommendation": None
    },
    "usage_by_area": [
        {"area": "RRHH", "messages": 1240, "tokens": 320000, "percent": 26},
        {"area": "Tecnología", "messages": 980, "tokens": 280000, "percent": 23},
        {"area": "Ventas", "messages": 860, "tokens": 220000, "percent": 18},
        {"area": "Operaciones", "messages": 740, "tokens": 190000, "percent": 15},
        {"area": "Soporte", "messages": 620, "tokens": 150000, "percent": 12},
        {"area": "Finanzas", "messages": 380, "tokens": 80000, "percent": 6}
    ],
    "payment_history": [
        {"date": "2026-05-01", "amount": 890, "status": "paid", "invoice": "INV-2026-05"},
        {"date": "2026-04-01", "amount": 890, "status": "paid", "invoice": "INV-2026-04"},
        {"date": "2026-03-01", "amount": 890, "status": "paid", "invoice": "INV-2026-03"},
        {"date": "2026-02-01", "amount": 890, "status": "paid", "invoice": "INV-2026-02"},
        {"date": "2026-01-01", "amount": 650, "status": "paid", "invoice": "INV-2026-01"}
    ],
    "upcoming_invoice": {"date": "2026-06-01", "amount": 890, "status": "scheduled"},
    "alerts": []
}

# Dynamically compute recommendation based on usage percent
_pct = BILLING_MOCK["current_month"]["percent_used"]
if _pct >= 90:
    BILLING_MOCK["current_month"]["recommendation"] = "upgrade"
    BILLING_MOCK["alerts"].append({
        "type": "warning",
        "message": f"Consumiste el {_pct}% del límite mensual. Considera hacer upgrade.",
        "action": "Ver planes"
    })
elif _pct < 40:
    BILLING_MOCK["current_month"]["recommendation"] = "downgrade"
elif _pct >= 75:
    BILLING_MOCK["alerts"].append({
        "type": "info",
        "message": f"Vas al {_pct}% del límite. Estimación fin de mes: ${BILLING_MOCK['current_month']['estimated_eom']}.",
        "action": "Ver consumo"
    })

ACTIVITY_MOCK = [
    {"time": "hace 5 min",  "area": "Soporte",     "event": "56 tickets abiertos — récord del mes", "type": "alert"},
    {"time": "hace 12 min", "area": "Tecnología",  "event": "Proyecto Incidencias CORE al 80% de avance", "type": "progress"},
    {"time": "hace 28 min", "area": "Ventas",      "event": "Pipeline cayó 12% vs semana pasada", "type": "warning"},
    {"time": "hace 1 h",    "area": "RRHH",        "event": "Onboarding IA: 3 nuevos empleados procesados", "type": "success"},
    {"time": "hace 2 h",    "area": "Operaciones", "event": "Cuello de botella detectado en validación QA", "type": "alert"},
    {"time": "hace 3 h",    "area": "Finanzas",    "event": "Presupuesto Q2 aprobado por Gerencia", "type": "success"},
    {"time": "hace 4 h",    "area": "Tecnología",  "event": "Deploy de infraestructura base completado", "type": "success"}
]

AI_ALERTS_MOCK = [
    {
        "id": "alert-1",
        "severity": "critical",
        "title": "Cuello de botella: Ventas → Operaciones",
        "description": "El pipeline de Ventas está generando órdenes más rápido de lo que Operaciones puede procesar. 34 tickets pendientes.",
        "areas": ["ventas", "operaciones"],
        "project": "Automatización de Pedidos",
        "recommendation": "Revisar capacidad de Operaciones o pausar campañas de Ventas.",
        "detected_at": "2026-05-21T09:15:00"
    },
    {
        "id": "alert-2",
        "severity": "high",
        "title": "Documentación desactualizada en Tecnología",
        "description": "3 documentos clave del repositorio CORE no se actualizan hace más de 60 días.",
        "areas": ["tecnologia"],
        "project": None,
        "recommendation": "Asignar responsable de documentación técnica.",
        "detected_at": "2026-05-21T08:30:00"
    },
    {
        "id": "alert-3",
        "severity": "medium",
        "title": "Baja adopción en Ventas",
        "description": "El área de Ventas tiene la tasa de resolución automática más baja (65%).",
        "areas": ["ventas"],
        "project": None,
        "recommendation": "Capacitación de 30 min sobre casos de uso del asistente.",
        "detected_at": "2026-05-21T07:45:00"
    }
]

ROI_MOCK = {
    "hours_saved_month": 1420,
    "tickets_avoided": 3850,
    "auto_resolution_rate": 0.82,
    "estimated_cost_saved_usd": 28400,
    "roi_multiplier": 31.9,
    "top_automation": "Respuestas automáticas de RRHH",
    "areas_by_adoption": [
        {"area": "RRHH", "score": 92},
        {"area": "Soporte", "score": 88},
        {"area": "Tecnología", "score": 76},
        {"area": "Operaciones", "score": 71},
        {"area": "Finanzas", "score": 65},
        {"area": "Ventas", "score": 58}
    ],
    "ai_maturity_score": 74,
    "ai_maturity_label": "Avanzado"
}

# ------------------- Helper functions -------------------

def get_user_from_token():
    """Simple mock token parsing – expects token to be 'Bearer <email>' or any value.
    Returns the first user matching the email in the token, otherwise the first user.
    """
    auth = request.headers.get('Authorization', '')
    if auth.startswith('Bearer '):
        token = auth.split(' ', 1)[1]
        # In mock mode token may be the email directly
        email = token
        user = next((u for u in USERS if u["email"] == email), None)
        if user:
            return user
    # Fallback to first user (CEO)
    return USERS[0]

# ------------------- Routes -------------------

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

# Authentication mock – returns current user based on token
@app.route('/api/auth/me', methods=['GET'])
def auth_me():
    user = get_user_from_token()
    return jsonify(user)

# Users list
@app.route('/api/users', methods=['GET'])
def get_users():
    return jsonify(USERS)

# Areas list
@app.route('/api/areas', methods=['GET'])
def get_areas():
    return jsonify(AREAS)

# Organizational context – optionally filter by view (ignored in mock)
@app.route('/api/org/context', methods=['GET'])
def org_context():
    view = request.args.get('view', 'default')
    # Return static mock (could be enriched per view later)
    return jsonify(ORG_CONTEXT)

# Workspace document handling (mock persistence in a simple JSON file)
WORKSPACE_FILE = os.path.join('backend', 'workspace_mock.json')

@app.route('/api/workspace/documents', methods=['GET'])
def ws_get_document():
    if os.path.exists(WORKSPACE_FILE):
        try:
            data = read(WORKSPACE_FILE)
            if not isinstance(data, dict):
                data = {}
            return jsonify({"title": "Documento", **data})
        except Exception:
            pass
    # Fallback empty document
    return jsonify({"title": "", "content": "", "updated_at": datetime.utcnow().isoformat()})

@app.route('/api/workspace/documents', methods=['POST'])
def ws_save_document():
    payload = request.json or {}
    title = payload.get('title', 'Sin título')
    content = payload.get('content', '')
    doc = {"title": title, "content": content, "updated_at": datetime.utcnow().isoformat()}
    # Persist to file (mock DB)
    try:
        write(WORKSPACE_FILE, doc)
    except Exception:
        pass
    return jsonify(doc)

# Chat endpoint – integrated with Ollama local LLM for 'Resolver Problema' and 'Sugerir Plan'
@app.route('/api/chat/message', methods=['POST'])
def chat_message():
    payload = request.json or {}
    content = payload.get('content', '')
    action = payload.get('action', 'generic')
    # Build prompt based on action
    if action == 'resolve':
        prompt = f"Resolver el problema: {content}"
    elif action == 'plan':
        prompt = f"Sugerir un plan de acción para: {content}"
    else:
        prompt = content
    # Call Ollama API (assumes Ollama running at http://localhost:11434)
    import requests, json
    try:
        resp = requests.post('http://localhost:11434/api/generate', json={
            'model': 'llama3.2',
            'prompt': prompt,
            'stream': False
        }, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            reply = data.get('response', '').strip()
        else:
            # Fallback mock response when Ollama is unavailable
            reply = "Respuesta simulada: el modelo está fuera de línea."
    except Exception as e:
        reply = f"Exception during Ollama request: {e}"
    return jsonify({"session_id": "ollama-session", "response": reply})

# Global search mock – returns empty list
@app.route('/api/search', methods=['POST'])
def global_search():
    query = request.json.get('query', '') if request.json else ''
    # In a real system we would search; here we just return empty results
    return jsonify({"query": query, "results": []})

# Menu generation based on role – used by sidebar if needed
@app.route('/api/menu', methods=['GET'])
def menu():
    role = request.args.get('role', 'employee')
    mapping = {
        "superadmin": ["dashboard", "area", "workspace", "employee", "users", "areas", "integrations", "settings", "billing"],
        "ceo":        ["dashboard", "area", "workspace", "employee", "users", "areas", "integrations", "settings", "billing"],
        "admin":      ["dashboard", "area", "workspace", "employee", "users", "areas", "integrations", "settings"],
        "leader":     ["area", "workspace", "employee", "users"],
        "employee":   ["employee"]
    }
    return jsonify(mapping.get(role, ["employee"]))

# Billing endpoint - returns mock billing data
@app.route('/api/billing', methods=['GET'])
def get_billing():
    return jsonify(BILLING_MOCK)

# Dashboard insights endpoint - aggregates key mock data
@app.route('/api/dashboard/insights', methods=['GET'])
def dashboard_insights():
    insights = {
        "alerts": AI_ALERTS_MOCK[:3],
        "roi": ROI_MOCK,
        "usage_by_area": BILLING_MOCK["usage_by_area"],
        "activity": ACTIVITY_MOCK[:5]
    }
    return jsonify(insights)

# Organization tree endpoint - provides org structure and dependencies
@app.route('/api/org/tree', methods=['GET'])
def get_org_tree():
    org_tree_data = {
        "areas": [
            {"id": "rrhh", "name": "RRHH", "metrics": {"tickets_open": 12, "resolution_rate": 0.92}},
            {"id": "ventas", "name": "Ventas", "metrics": {"tickets_open": 34, "resolution_rate": 0.65}},
            {"id": "operaciones", "name": "Operaciones", "metrics": {"tickets_open": 28, "resolution_rate": 0.78}},
            {"id": "finanzas", "name": "Finanzas", "metrics": {"tickets_open": 8, "resolution_rate": 0.88}},
            {"id": "tecnologia", "name": "Tecnología", "metrics": {"tickets_open": 19, "resolution_rate": 0.76}},
            {"id": "soporte", "name": "Soporte", "metrics": {"tickets_open": 45, "resolution_rate": 0.82}},
            {"id": "gerencia", "name": "Gerencia", "metrics": {"tickets_open": 3, "resolution_rate": 0.95}}
        ],
        "projects": [
            {"id": "proj-1", "name": "Automatización de Pedidos", "status": "En Progreso", "progress": 80, "area": "operaciones"},
            {"id": "proj-2", "name": "Inteligencia de Ventas", "status": "En Progreso", "progress": 65, "area": "ventas"},
            {"id": "proj-3", "name": "Onboarding IA", "status": "Completado", "progress": 100, "area": "rrhh"},
            {"id": "proj-4", "name": "Portal de Finanzas", "status": "Planejado", "progress": 25, "area": "finanzas"},
            {"id": "proj-5", "name": "Soporte Predictivo", "status": "En Progreso", "progress": 55, "area": "soporte"}
        ],
        "cross_area_relationships": [
            {"from": "ventas", "to": "operaciones", "type": "depends_on", "description": "Órdenes → Procesamiento"},
            {"from": "operaciones", "to": "finanzas", "type": "depends_on", "description": "Facturación → Contabilidad"},
            {"from": "soporte", "to": "tecnologia", "type": "escalation", "description": "Tickets técnicos"},
            {"from": "rrhh", "to": "ventas", "type": "supports", "description": "Capacitación de personal"}
        ],
        "dependencies": [
            {"from_area": "ventas", "to_area": "operaciones", "severity": "critical", "description": "34 órdenes pendientes de procesamiento"},
            {"from_area": "soporte", "to_area": "tecnologia", "severity": "high", "description": "Documentación desactualizada (60+ días)"},
            {"from_area": "ventas", "to_area": "finanzas", "severity": "medium", "description": "Tasa de resolución automática baja (65%)"}
        ]
    }
    return jsonify(org_tree_data)

# Fallback for any undefined route – return 404 JSON
@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Endpoint not implemented"}), 404

if __name__ == '__main__':
    print("Iniciando backend en http://localhost:5000")
    app.run(debug=True, port=5000)
