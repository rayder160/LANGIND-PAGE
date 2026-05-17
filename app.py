from flask import Flask, send_from_directory, jsonify, request
import os

app = Flask(__name__, static_url_path='', static_folder='.')

# Base de datos simulada en memoria
db = {
    "users": [
        {"id": 1, "name": "Admin CEO", "email": "ceo@empresa.com", "role": "ceo", "area": "Gerencia", "status": "Activo", "last_access": "Reciente"},
        {"id": 2, "name": "Maria Rodriguez", "email": "maria@empresa.com", "role": "admin", "area": "RRHH", "status": "Activo", "last_access": "Hace 2 min"},
        {"id": 3, "name": "Carlos Andrade", "email": "carlos@empresa.com", "role": "leader", "area": "Tecnologia", "status": "Activo", "last_access": "Hace 15 min"},
        {"id": 4, "name": "Juan Torres", "email": "juan@empresa.com", "role": "employee", "area": "Ventas", "status": "Inactivo", "last_access": "Hace 3 horas"}
    ],
    "areas": [
        "RRHH", "Ventas", "Operaciones", "Finanzas", "Tecnologia", "Soporte", "Gerencia"
    ]
}

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.json
    email = data.get('email', '')
    
    # Simple mock login - buscar usuario por email
    user = next((u for u in db["users"] if u["email"] == email), None)
    
    if user:
        return jsonify({"success": True, "user": user})
    
    # Si no existe, creamos un mock dependiendo del correo (para pruebas rápidas)
    if "ceo" in email: role, area = "ceo", "Gerencia"
    elif "admin" in email: role, area = "admin", "Operaciones"
    elif "leader" in email: role, area = "leader", "Ventas"
    else: role, area = "employee", "Soporte"
        
    mock_user = {"id": len(db["users"])+1, "name": email.split('@')[0].capitalize(), "email": email, "role": role, "area": area, "status": "Activo"}
    return jsonify({"success": True, "user": mock_user})

@app.route('/api/users', methods=['GET'])
def get_users():
    return jsonify(db["users"])

@app.route('/api/areas', methods=['GET'])
def get_areas():
    return jsonify(db["areas"])

if __name__ == '__main__':
    print("Iniciando backend en http://localhost:5000")
    app.run(debug=True, port=5000)
