"""
org.py — Rutas de contexto organizacional para ProxDeep
Endpoints:
  GET /api/org/tree          → árbol completo de la empresa
  GET /api/org/areas         → lista de áreas con métricas
  GET /api/org/projects      → proyectos y áreas asociadas
  GET /api/org/dependencies  → dependencias entre ramas
  GET /api/org/context       → contexto completo para IA (user_id + view)

Datos: mock inteligente listo para reemplazar con DB real.
TODO BACKEND: reemplazar ORG_MOCK con queries a modelos SQLAlchemy.
"""

from fastapi import APIRouter, Depends, Query
from app.routes.auth import get_current_user
from app.models import User

router = APIRouter(prefix="/org", tags=["org"])

# ============================================================
# MOCK ORGANIZACIONAL — reemplazar con DB real
# ============================================================
ORG_MOCK = {
    "company": {
        "id": "proxdeep-corp",
        "name": "ProxDeep Corp",
        "industry": "IA Empresarial",
        "size": "50-200",
    },
    "areas": [
        {
            "id": "rrhh",
            "name": "Recursos Humanos",
            "leader": "María Rodríguez",
            "parent_id": None,
            "subareas": ["rrhh-seleccion", "rrhh-bienestar"],
            "metrics": {
                "tickets_open": 12,
                "resolution_rate": 0.87,
                "docs_indexed": 34,
                "satisfaction": 4.2,
            },
            "documents": [
                {"id": "doc-rrhh-1", "name": "Política de Vacaciones 2026", "shared_with": []},
                {"id": "doc-rrhh-2", "name": "Manual de Onboarding", "shared_with": ["tecnologia"]},
                {"id": "doc-rrhh-3", "name": "Reglamento Interno", "shared_with": []},
            ],
            "resources": ["Sistema HRIS", "Portal de Empleados"],
        },
        {
            "id": "tecnologia",
            "name": "Tecnología",
            "leader": "Carlos Andrade",
            "parent_id": None,
            "subareas": ["tecnologia-dev", "tecnologia-infra"],
            "metrics": {
                "tickets_open": 28,
                "resolution_rate": 0.72,
                "docs_indexed": 67,
                "satisfaction": 3.9,
            },
            "documents": [
                {"id": "doc-tec-1", "name": "Arquitectura del Sistema", "shared_with": []},
                {"id": "doc-tec-2", "name": "Guía de Onboarding Técnico", "shared_with": ["rrhh"]},
                {"id": "doc-tec-3", "name": "Runbook de Incidencias", "shared_with": ["soporte"]},
            ],
            "resources": ["GitHub", "AWS", "Jira"],
        },
    ],
}

# Continúa el mock
ORG_MOCK["areas"] += [
    {
        "id": "ventas",
        "name": "Ventas",
        "leader": "Juan Torres",
        "parent_id": None,
        "subareas": ["ventas-comercial", "ventas-postventa"],
        "metrics": {
            "tickets_open": 19,
            "resolution_rate": 0.65,
            "docs_indexed": 22,
            "satisfaction": 3.7,
        },
        "documents": [
            {"id": "doc-ven-1", "name": "Playbook de Ventas Q3", "shared_with": ["operaciones"]},
            {"id": "doc-ven-2", "name": "Guía de Objeciones", "shared_with": []},
        ],
        "resources": ["Salesforce", "HubSpot"],
    },
    {
        "id": "operaciones",
        "name": "Operaciones",
        "leader": "Ana Gómez",
        "parent_id": None,
        "subareas": ["operaciones-logistica", "operaciones-qa"],
        "metrics": {
            "tickets_open": 34,
            "resolution_rate": 0.78,
            "docs_indexed": 45,
            "satisfaction": 4.0,
        },
        "documents": [
            {"id": "doc-ops-1", "name": "Manual de QA 2026", "shared_with": ["tecnologia"]},
            {"id": "doc-ops-2", "name": "Flujo de Pedidos", "shared_with": ["ventas"]},
        ],
        "resources": ["ERP", "Tableau"],
    },
    {
        "id": "soporte",
        "name": "Soporte Interno",
        "leader": "Pedro Díaz",
        "parent_id": None,
        "subareas": [],
        "metrics": {
            "tickets_open": 56,
            "resolution_rate": 0.91,
            "docs_indexed": 18,
            "satisfaction": 4.5,
        },
        "documents": [
            {"id": "doc-sop-1", "name": "FAQ Soporte Nivel 1", "shared_with": []},
            {"id": "doc-sop-2", "name": "Escalamiento a Tecnología", "shared_with": ["tecnologia"]},
        ],
        "resources": ["Zendesk", "Slack"],
    },
    {
        "id": "finanzas",
        "name": "Finanzas",
        "leader": "Laura Peña",
        "parent_id": None,
        "subareas": ["finanzas-contabilidad", "finanzas-tesoreria"],
        "metrics": {
            "tickets_open": 8,
            "resolution_rate": 0.95,
            "docs_indexed": 29,
            "satisfaction": 4.3,
        },
        "documents": [
            {"id": "doc-fin-1", "name": "Presupuesto Anual 2026", "shared_with": ["gerencia"]},
            {"id": "doc-fin-2", "name": "Política de Gastos", "shared_with": []},
        ],
        "resources": ["SAP", "Excel Corporativo"],
    },
    {
        "id": "gerencia",
        "name": "Gerencia",
        "leader": "Roberto Silva",
        "parent_id": None,
        "subareas": [],
        "metrics": {
            "tickets_open": 3,
            "resolution_rate": 1.0,
            "docs_indexed": 12,
            "satisfaction": 4.8,
        },
        "documents": [
            {"id": "doc-ger-1", "name": "Plan Estratégico 2026", "shared_with": ["finanzas", "rrhh"]},
        ],
        "resources": ["Power BI", "Notion"],
    },
]

# Proyectos transversales (multi-área)
ORG_MOCK["projects"] = [
    {
        "id": "proj-onboarding-ia",
        "name": "Onboarding IA",
        "status": "active",
        "priority": "high",
        "areas": ["rrhh", "tecnologia"],
        "description": "Automatización del proceso de onboarding con IA. RRHH define el flujo, Tecnología implementa la integración.",
        "documents": ["doc-rrhh-2", "doc-tec-2"],
        "metrics": {"progress": 0.65, "blockers": 1},
        "dependencies": ["proj-infra-base"],
    },
    {
        "id": "proj-automatizacion-pedidos",
        "name": "Automatización de Pedidos",
        "status": "active",
        "priority": "high",
        "areas": ["ventas", "operaciones"],
        "description": "Flujo automático desde cotización hasta despacho. Ventas genera la orden, Operaciones la ejecuta.",
        "documents": ["doc-ven-1", "doc-ops-2"],
        "metrics": {"progress": 0.40, "blockers": 2},
        "dependencies": [],
    },
    {
        "id": "proj-incidencias-core",
        "name": "Gestión de Incidencias CORE",
        "status": "active",
        "priority": "critical",
        "areas": ["soporte", "tecnologia"],
        "description": "Sistema unificado de tickets para incidencias del sistema CORE. Soporte recibe, Tecnología resuelve.",
        "documents": ["doc-tec-3", "doc-sop-2"],
        "metrics": {"progress": 0.80, "blockers": 0},
        "dependencies": [],
    },
    {
        "id": "proj-control-presupuestal",
        "name": "Control Presupuestal 2026",
        "status": "active",
        "priority": "medium",
        "areas": ["finanzas", "gerencia"],
        "description": "Seguimiento y control del presupuesto anual. Finanzas reporta, Gerencia aprueba desviaciones.",
        "documents": ["doc-fin-1", "doc-ger-1"],
        "metrics": {"progress": 0.55, "blockers": 0},
        "dependencies": [],
    },
    {
        "id": "proj-infra-base",
        "name": "Infraestructura Base IA",
        "status": "active",
        "priority": "high",
        "areas": ["tecnologia"],
        "description": "Despliegue de la infraestructura base para todos los proyectos de IA.",
        "documents": ["doc-tec-1"],
        "metrics": {"progress": 0.90, "blockers": 0},
        "dependencies": [],
    },
]

# Dependencias entre áreas (grafo dirigido)
ORG_MOCK["dependencies"] = [
    {
        "id": "dep-1",
        "from_area": "ventas",
        "to_area": "operaciones",
        "type": "operational",
        "description": "Ventas genera órdenes que Operaciones debe ejecutar. Un retraso en Operaciones bloquea el cierre de Ventas.",
        "severity": "high",
        "project": "proj-automatizacion-pedidos",
    },
    {
        "id": "dep-2",
        "from_area": "soporte",
        "to_area": "tecnologia",
        "type": "escalation",
        "description": "Soporte escala tickets de nivel 2+ a Tecnología. Alta carga en Soporte genera backlog en Tecnología.",
        "severity": "critical",
        "project": "proj-incidencias-core",
    },
    {
        "id": "dep-3",
        "from_area": "rrhh",
        "to_area": "tecnologia",
        "type": "collaboration",
        "description": "RRHH depende de Tecnología para activar accesos y herramientas en el onboarding.",
        "severity": "medium",
        "project": "proj-onboarding-ia",
    },
    {
        "id": "dep-4",
        "from_area": "finanzas",
        "to_area": "gerencia",
        "type": "reporting",
        "description": "Finanzas reporta desviaciones presupuestales a Gerencia para aprobación.",
        "severity": "medium",
        "project": "proj-control-presupuestal",
    },
    {
        "id": "dep-5",
        "from_area": "operaciones",
        "to_area": "tecnologia",
        "type": "technical",
        "description": "Operaciones depende de Tecnología para el ERP y sistemas de logística.",
        "severity": "high",
        "project": None,
    },
]

# Relaciones cruzadas (trabajo compartido detectado)
ORG_MOCK["cross_area_relationships"] = [
    {
        "areas": ["rrhh", "tecnologia"],
        "type": "shared_project",
        "project": "proj-onboarding-ia",
        "shared_docs": ["doc-rrhh-2", "doc-tec-2"],
        "insight": "Ambas áreas trabajan en el mismo proceso de onboarding. Un cambio en el flujo de RRHH impacta la implementación técnica.",
    },
    {
        "areas": ["ventas", "operaciones"],
        "type": "shared_project",
        "project": "proj-automatizacion-pedidos",
        "shared_docs": ["doc-ven-1", "doc-ops-2"],
        "insight": "El pipeline de ventas está directamente conectado con la capacidad operativa. Cuellos de botella en Operaciones reducen el cierre de Ventas.",
    },
    {
        "areas": ["soporte", "tecnologia"],
        "type": "shared_project",
        "project": "proj-incidencias-core",
        "shared_docs": ["doc-tec-3", "doc-sop-2"],
        "insight": "Los tickets de Soporte sobre el sistema CORE son resueltos por Tecnología. Alta correlación entre satisfacción de Soporte y velocidad de Tecnología.",
    },
    {
        "areas": ["finanzas", "gerencia"],
        "type": "shared_project",
        "project": "proj-control-presupuestal",
        "shared_docs": ["doc-fin-1", "doc-ger-1"],
        "insight": "El control presupuestal requiere alineación constante entre Finanzas y Gerencia.",
    },
    {
        "areas": ["operaciones", "tecnologia"],
        "type": "technical_dependency",
        "project": None,
        "shared_docs": ["doc-ops-1"],
        "insight": "Operaciones depende del ERP gestionado por Tecnología. Incidencias técnicas impactan directamente la logística.",
    },
]


# ============================================================
# HELPERS
# ============================================================

def _get_area_by_name(area_name: str) -> dict | None:
    """Busca un área por nombre (case-insensitive)."""
    if not area_name:
        return None
    name_lower = area_name.lower()
    for area in ORG_MOCK["areas"]:
        if area["name"].lower() == name_lower or area["id"] in name_lower or name_lower in area["name"].lower():
            return area
    return None


def _get_projects_for_area(area_id: str) -> list:
    return [p for p in ORG_MOCK["projects"] if area_id in p["areas"]]


def _get_dependencies_for_area(area_id: str) -> list:
    return [d for d in ORG_MOCK["dependencies"]
            if d["from_area"] == area_id or d["to_area"] == area_id]


def _get_cross_relationships_for_area(area_id: str) -> list:
    return [r for r in ORG_MOCK["cross_area_relationships"] if area_id in r["areas"]]


def _get_connected_areas(area_id: str) -> list:
    """Devuelve IDs de áreas conectadas por proyectos o dependencias."""
    connected = set()
    for p in _get_projects_for_area(area_id):
        for a in p["areas"]:
            if a != area_id:
                connected.add(a)
    for d in _get_dependencies_for_area(area_id):
        connected.add(d["from_area"])
        connected.add(d["to_area"])
    connected.discard(area_id)
    return list(connected)


def _build_org_tree() -> dict:
    """Construye el árbol organizacional completo."""
    areas_map = {a["id"]: dict(a) for a in ORG_MOCK["areas"]}
    # Agregar proyectos y dependencias a cada área
    for area_id, area in areas_map.items():
        area["projects"] = _get_projects_for_area(area_id)
        area["dependencies"] = _get_dependencies_for_area(area_id)
        area["cross_relationships"] = _get_cross_relationships_for_area(area_id)
        area["connected_areas"] = _get_connected_areas(area_id)
    return {
        "company": ORG_MOCK["company"],
        "areas": list(areas_map.values()),
        "projects": ORG_MOCK["projects"],
        "dependencies": ORG_MOCK["dependencies"],
        "cross_area_relationships": ORG_MOCK["cross_area_relationships"],
        "total_areas": len(ORG_MOCK["areas"]),
        "total_projects": len(ORG_MOCK["projects"]),
        "total_dependencies": len(ORG_MOCK["dependencies"]),
    }


def get_org_prompt_context(user, view: str, area_name: str | None) -> str:
    """
    Genera una descripción detallada en texto (markdown) de toda la estructura organizacional,
    para inyectarla en el system prompt del asistente de IA.
    """
    area_data = _get_area_by_name(area_name) if area_name else None
    area_id = area_data["id"] if area_data else None

    related_projects = _get_projects_for_area(area_id) if area_id else []
    connected_area_ids = _get_connected_areas(area_id) if area_id else []
    connected_areas = [a for a in ORG_MOCK["areas"] if a["id"] in connected_area_ids]
    dependencies = _get_dependencies_for_area(area_id) if area_id else []
    cross_rels = _get_cross_relationships_for_area(area_id) if area_id else []

    lines = []
    lines.append("=== CONTEXTO ORGANIZACIONAL GLOBAL (CONOCIMIENTO TRANSVERSAL DE LA EMPRESA) ===")
    lines.append(f"Compañía: {ORG_MOCK['company']['name']} ({ORG_MOCK['company']['industry']})")
    lines.append(f"Usuario actual: {user.name} | Rol: {user.role} | Vista activa: {view}")
    if area_name:
        lines.append(f"Área del usuario: {area_name}")
        if area_data and area_data.get("leader"):
            lines.append(f"  Líder del área: {area_data['leader']}")
    else:
        lines.append("Área del usuario: Sin área asignada")

    lines.append("\n1. MAPA DE ÁREAS Y JERARQUÍA:")
    for a in ORG_MOCK["areas"]:
        subareas_str = ", ".join(a["subareas"]) if a["subareas"] else "Ninguna"
        metrics_str = ", ".join(f"{k}: {v}" for k, v in a["metrics"].items())
        lines.append(f"- {a['name']} (ID: {a['id']}):")
        lines.append(f"  * Líder: {a['leader']}")
        lines.append(f"  * Subáreas: {subareas_str}")
        lines.append(f"  * Métricas operativas: {metrics_str}")
        lines.append(f"  * Recursos clave: {', '.join(a['resources'])}")
        docs_names = [d["name"] for d in a["documents"]]
        lines.append(f"  * Documentos indexados: {', '.join(docs_names) if docs_names else 'Ninguno'}")

    lines.append("\n2. PROYECTOS TRANSVERSALES ACTIVOS (COLABORACIÓN INTER-ÁREAS):")
    for p in ORG_MOCK["projects"]:
        areas_participantes = [next((a["name"] for a in ORG_MOCK["areas"] if a["id"] == aid), aid) for aid in p["areas"]]
        lines.append(f"- Proyecto: {p['name']} (ID: {p['id']})")
        lines.append(f"  * Descripción: {p['description']}")
        lines.append(f"  * Estado: {p['status']} | Prioridad: {p['priority']}")
        lines.append(f"  * Áreas involucradas: {', '.join(areas_participantes)}")
        lines.append(f"  * Avance: {int(p['metrics']['progress']*100)}% | Bloqueadores activos: {p['metrics']['blockers']}")
        if p.get("dependencies"):
            lines.append(f"  * Depende de proyectos: {', '.join(p['dependencies'])}")

    lines.append("\n3. DEPENDENCIAS OPERATIVAS Y BLOQUEOS ENTRE EQUIPOS:")
    for d in ORG_MOCK["dependencies"]:
        from_name = next((a["name"] for a in ORG_MOCK["areas"] if a["id"] == d["from_area"]), d["from_area"])
        to_name = next((a["name"] for a in ORG_MOCK["areas"] if a["id"] == d["to_area"]), d["to_area"])
        proj_str = f" en el proyecto '{d['project']}'" if d.get("project") else ""
        lines.append(f"- [{d['severity'].upper()}] {from_name} depende de {to_name}{proj_str}:")
        lines.append(f"  * Tipo: {d['type']}")
        lines.append(f"  * Descripción: {d['description']}")

    lines.append("\n4. RELACIONES CRUZADAS E INSIGHTS DE COLABORACIÓN:")
    for r in ORG_MOCK["cross_area_relationships"]:
        rel_areas = [next((a["name"] for a in ORG_MOCK["areas"] if a["id"] == aid), aid) for aid in r["areas"]]
        proj_info = f" en el proyecto '{r['project']}'" if r.get("project") else ""
        lines.append(f"- Relación entre {', '.join(rel_areas)}{proj_info} (Tipo: {r['type']}):")
        lines.append(f"  * Insight de impacto: {r['insight']}")
        if r.get("shared_docs"):
            lines.append(f"  * Documentación compartida: {', '.join(r['shared_docs'])}")

    if area_data:
        lines.append(f"\n5. DETALLES ESPECÍFICOS DE TU ÁREA ({area_name.upper()}):")
        lines.append(f"- Proyectos en los que participa tu área: {', '.join([p['name'] for p in related_projects]) if related_projects else 'Ninguno'}")
        lines.append(f"- Áreas con las que colaboras directamente: {', '.join([a['name'] for a in connected_areas]) if connected_areas else 'Ninguna'}")
        
        dept_deps = [d for d in dependencies if d["from_area"] == area_id]
        other_deps = [d for d in dependencies if d["to_area"] == area_id]
        if dept_deps:
            lines.append("  * Tareas/flujos que bloquean a tu equipo (dependés de otros):")
            for d in dept_deps:
                to_name = next((a["name"] for a in ORG_MOCK["areas"] if a["id"] == d["to_area"]), d["to_area"])
                lines.append(f"    - Bloqueado por {to_name}: {d['description']} (Severidad: {d['severity']})")
        if other_deps:
            lines.append("  * Tareas/flujos donde tu equipo puede bloquear a otros (otros dependen de vos):")
            for d in other_deps:
                from_name = next((a["name"] for a in ORG_MOCK["areas"] if a["id"] == d["from_area"]), d["from_area"])
                lines.append(f"    - {from_name} depende de tu equipo: {d['description']} (Severidad: {d['severity']})")

    lines.append("\n=== REGLA CRÍTICA PARA LA IA ===")
    lines.append("Usá este mapa organizacional completo para responder de manera transversal.")
    lines.append("Si el usuario pregunta sobre otras áreas, proyectos compartidos, dependencias, o impactos entre métricas (por ejemplo, por qué cayó una métrica si otra va bien), debés usar estas relaciones para dar respuestas precisas y cruzadas.")
    lines.append("No respondas como si cada departamento existiera de manera aislada. Explicá cómo se afectan mutuamente según el mapa organizacional provisto.")
    lines.append("=========================================================================")

    return "\n".join(lines)



# ============================================================
# ENDPOINTS
# ============================================================

@router.get("/tree")
async def get_org_tree(_: User = Depends(get_current_user)):
    """Árbol organizacional completo con relaciones."""
    return _build_org_tree()


@router.get("/areas")
async def get_areas(_: User = Depends(get_current_user)):
    """Lista de áreas con métricas y conexiones."""
    result = []
    for area in ORG_MOCK["areas"]:
        a = dict(area)
        a["connected_areas"] = _get_connected_areas(area["id"])
        a["active_projects"] = len(_get_projects_for_area(area["id"]))
        result.append(a)
    return result


@router.get("/projects")
async def get_projects(_: User = Depends(get_current_user)):
    """Proyectos con áreas participantes y estado."""
    return ORG_MOCK["projects"]


@router.get("/dependencies")
async def get_dependencies(_: User = Depends(get_current_user)):
    """Dependencias entre ramas con severidad."""
    return ORG_MOCK["dependencies"]


@router.get("/context")
async def get_org_context(
    view: str = Query("employee"),
    user: User = Depends(get_current_user),
):
    """
    Contexto organizacional completo para la IA.
    Combina: user_context + area_context + org_tree + relaciones.
    """
    from sqlalchemy import select
    from app.database import AsyncSessionLocal
    from app.models import Area as AreaModel

    # Obtener area_name del usuario
    area_name = None
    async with AsyncSessionLocal() as db:
        if user.area_id:
            result = await db.execute(
                select(AreaModel).where(AreaModel.id == user.area_id)
            )
            area_obj = result.scalar_one_or_none()
            area_name = area_obj.name if area_obj else None

    # Buscar área en el mock
    area_data = _get_area_by_name(area_name) if area_name else None
    area_id = area_data["id"] if area_data else None

    # Construir contexto
    user_context = {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "role": user.role,
        "area_id": user.area_id,
        "area_name": area_name,
        "tenant_id": user.tenant_id,
        "current_view": view,
        "source": f"dashboard_{view}",
    }

    area_context = area_data or {
        "id": None,
        "name": area_name or "Sin área",
        "note": "Área no encontrada en el mapa organizacional mock",
    }

    related_projects = _get_projects_for_area(area_id) if area_id else []
    connected_area_ids = _get_connected_areas(area_id) if area_id else []
    connected_areas = [a for a in ORG_MOCK["areas"] if a["id"] in connected_area_ids]
    dependencies = _get_dependencies_for_area(area_id) if area_id else []
    cross_rels = _get_cross_relationships_for_area(area_id) if area_id else []

    # Métricas relevantes: del área + de áreas conectadas
    relevant_metrics = []
    if area_data:
        relevant_metrics.append({"area": area_data["name"], "metrics": area_data["metrics"]})
    for ca in connected_areas:
        relevant_metrics.append({"area": ca["name"], "metrics": ca["metrics"]})

    # Recursos compartidos
    shared_resources = []
    for rel in cross_rels:
        shared_resources.extend(rel.get("shared_docs", []))

    return {
        "user_context": user_context,
        "area_context": area_context,
        "org_tree": {
            "company": ORG_MOCK["company"],
            "total_areas": len(ORG_MOCK["areas"]),
            "total_projects": len(ORG_MOCK["projects"]),
            "areas_summary": [
                {"id": a["id"], "name": a["name"], "leader": a["leader"]}
                for a in ORG_MOCK["areas"]
            ],
        },
        "related_projects": related_projects,
        "connected_areas": connected_areas,
        "dependencies": dependencies,
        "cross_area_relationships": cross_rels,
        "relevant_metrics": relevant_metrics,
        "shared_resources": shared_resources,
        "current_view": view,
        "source": f"dashboard_{view}",
    }
