# django001

Django development environment for this workspace.

## Setup

```bash
source venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
python manage.py migrate
python manage.py runserver
```

Open http://127.0.0.1:8000/ after starting the server.

## PoC API

Base URL:

```text
http://127.0.0.1:8000/api/v1
```

Initial user:

```text
login_id: admin
password: admin123
role: system_admin
```

Login:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"login_id":"admin","password":"admin123"}'
```

Use the returned token for authenticated requests:

```bash
curl http://127.0.0.1:8000/api/v1/companies \
  -H 'Authorization: Bearer <token>'
```

Implemented PoC endpoints:

```text
POST   /api/v1/data
POST   /api/v1/auth/login
POST   /api/v1/auth/logout
POST   /api/v1/auth/password-reset/request
POST   /api/v1/auth/password-reset/execute
GET    /api/v1/dashboard/summary
GET    /api/v1/masters/companies
GET    /api/v1/masters/sites
GET    /api/v1/masters/devices
GET    /api/v1/companies
POST   /api/v1/companies
PUT    /api/v1/companies/{id}
POST   /api/v1/companies/{id}/disable
GET    /api/v1/sites
POST   /api/v1/sites
PUT    /api/v1/sites/{id}
POST   /api/v1/sites/{id}/disable
GET    /api/v1/users
POST   /api/v1/users
PUT    /api/v1/users/{id}
POST   /api/v1/users/{id}/reset-password
GET    /api/v1/devices
GET    /api/v1/devices/latest
GET    /api/v1/devices/{id}
POST   /api/v1/devices
PUT    /api/v1/devices/{id}
POST   /api/v1/devices/{id}/disable
GET    /api/v1/devices/{id}/columns
GET    /api/v1/devices/{id}/graph
GET    /api/v1/thresholds
POST   /api/v1/thresholds
PUT    /api/v1/thresholds/{id}
DELETE /api/v1/thresholds/{id}
GET    /api/v1/audit-logs
```

Development device data receive example:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/data \
  -u 'device-auth-id:device-password' \
  -H 'Content-Type: application/json' \
  -d '{
    "device_id": "DEVICE001",
    "timestamp": "2026-06-12T10:00:00+09:00",
    "values": {"temp": 25.1, "humidity": 60}
  }'
```

The development implementation stores received raw and parsed data in SQLite and
updates the latest values and device communication status synchronously. The
production AWS Lambda and TimescaleDB implementation is pending.

## Common Commands

```bash
python manage.py test
python manage.py createsuperuser
```
