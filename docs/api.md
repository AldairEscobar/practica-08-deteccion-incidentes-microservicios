# API — Práctica 8

## 1. Convenciones

- **Formato:** JSON UTF-8, salvo las imágenes (`image/jpeg`) y el stream MJPEG.
- **Borde público:** `http://127.0.0.1:8000` (gateway).
- **Servicios internos:** `http://127.0.0.1:8001` (cámaras),
  `http://127.0.0.1:8002` (inferencia), `http://127.0.0.1:8003` (alertas).
- **Swagger:** `/docs` en cada servicio y en el gateway.
- **Autenticación del borde:** si `CENTRAL_API_KEY` no está vacío, las rutas
  proxificadas y el WebSocket exigen `X-API-Key: <clave>` o
  `?api_key=<clave>`. `GET /health` y `GET /api/v1/info` quedan abiertos para
  diagnóstico. Las rutas internas no exigen la clave.
- **Token interno:** si `INTERNAL_TOKEN` no está vacío, `POST /internal/eventos`
  exige `X-Internal-Token`.
- **Errores:** el cuerpo sigue el formato de FastAPI:
  `{"detail": "mensaje"}` o la lista de errores de validación (422).

## 2. Gateway (8000)

### 2.1 Salud

`GET /health`

```json
{
  "status": "ok",
  "gateway": "ok",
  "servicios": {
    "camaras": {"status": "ok", "service": "camaras", "camaras": 1, "en_vivo": 1},
    "inferencia": {
      "status": "ok",
      "service": "inferencia",
      "model": "yolo11n.pt",
      "verifier": "ollama/qwen2.5vl:7b",
      "ollama": false,
      "model_loaded": true,
      "camaras": 1
    },
    "alertas": {"status": "ok", "service": "alertas", "alertas": 3}
  }
}
```

`status` es `degradado` si algún servicio no responde o devuelve un estado
distinto de `ok`. `ollama: false` no marca el servicio como caído: la
inferencia y la preview siguen funcionando.

### 2.2 Información

`GET /api/v1/info`

```json
{
  "service": "gateway",
  "version": "2.0.0",
  "pwa": "/",
  "docs": "/docs",
  "servicios": {
    "camaras": "http://127.0.0.1:8001",
    "inferencia": "http://127.0.0.1:8002",
    "alertas": "http://127.0.0.1:8003"
  }
}
```

### 2.3 Cámaras

Todas las rutas se proxifican al servicio de cámaras. La respuesta usa
`CameraOut` y **enmascara la contraseña** de la URL.

#### `POST /api/v1/camaras`

```json
{
  "name": "Entrada",
  "source": "rtsp://usuario:clave@10.0.0.5:554/stream1",
  "address": "Piso 1",
  "lat": -12.05,
  "lon": -77.04,
  "enabled": true
}
```

Respuesta `201`:

```json
{
  "id": "cam-1a2b3c4d",
  "name": "Entrada",
  "source": "rtsp://usuario:***@10.0.0.5:554/stream1",
  "address": "Piso 1",
  "lat": -12.05,
  "lon": -77.04,
  "enabled": true,
  "created_at": "2026-11-16T23:39:46+00:00",
  "state": "conectando",
  "running": true,
  "last_error": "",
  "last_event": "",
  "started_at": "2026-11-16T23:39:46+00:00"
}
```

Si `enabled` es `false`, la cámara se registra pero no arranca.

#### `GET /api/v1/camaras`

Lista de `CameraOut`. El campo `state` puede ser:

| Estado | Significado |
|---|---|
| `conectando` | Abriendo la fuente |
| `en_vivo` | Recibiendo frames |
| `sin_senal` | La fuente en vivo no entrega imagen |
| `reconectando` | Reintentando la conexión |
| `detenida` | Sin pipeline activo |
| `error` | El pipeline terminó con error |

#### `PATCH /api/v1/camaras/{id}`

Cambios parciales; solo se aplican los campos enviados.

```json
{"name": "Entrada principal", "enabled": false}
```

Si cambia `source` o se deshabilita, el supervisor detiene el pipeline y limpia
el estado heurístico en inferencia. Si se vuelve a habilitar, arranca de nuevo.

#### `DELETE /api/v1/camaras/{id}`

```json
{"removed": "cam-1a2b3c4d"}
```

Detiene el pipeline, borra el registro y limpia el estado de inferencia.

#### `POST /api/v1/camaras/{id}/iniciar` y `/detener`

Devuelven `CameraOut` con el estado resultante. `404` si la cámara no existe.

#### `GET /api/v1/camaras/{id}/snapshot`

Devuelve el último frame anotado como `image/jpeg`, o `404` si todavía no hay
imagen. Funciona aunque no haya un espectador conectado al stream.

#### `GET /api/v1/camaras/{id}/stream`

Stream MJPEG:

```
Content-Type: multipart/x-mixed-replace; boundary=frame
Cache-Control: no-store
X-Accel-Buffering: no
```

El gateway lo reenvía sin bufferizar. `404` si la cámara no está en marcha.

### 2.4 Alertas

#### `POST /api/v1/alertas`

Lo usa el servicio de cámaras (y cualquier cliente/edge autorizado).

```json
{
  "client_id": "cam-1a2b3c4d",
  "type": "PELEA",
  "confidence": 0.93,
  "label": "pelea",
  "reason": "forcejeo entre dos personas",
  "timestamp": "2026-11-16T23:40:00+00:00",
  "location": {"address": "Piso 1", "lat": -12.05, "lon": -77.04},
  "snapshot_b64": "/9j/4AAQSkZJRgABAQ..."
}
```

Respuesta `201` (`AlertAck`):

```json
{
  "ticket": "ALT-20261116-0001",
  "status": "registrada",
  "priority": "ALTA",
  "eta_min": 5,
  "units": ["patrulla", "supervisor"],
  "message": "Ayuda en camino: patrulla, supervisor (ETA ~5 min). Ticket en camino. Ticket ALT-20261116-0001.",
  "received_at": "2026-11-16T23:40:00+00:00"
}
```

- `snapshot_b64` es opcional; si viene, se guarda como JPEG y se calcula su
  SHA-256.
- El servicio de alertas publica `alerta` en el gateway.

#### `GET /api/v1/alertas?limit=100`

Lista de `AlertRecord`, más recientes primero. `limit` va de 1 a 1000.

```json
[
  {
    "ticket": "ALT-20261116-0001",
    "status": "registrada",
    "priority": "ALTA",
    "eta_min": 5,
    "units": ["patrulla", "ambulancia"],
    "received_at": "2026-11-16T23:40:00+00:00",
    "alert": {
      "client_id": "cam-1a2b3c4d",
      "type": "PELEA",
      "confidence": 0.93,
      "label": "pelea",
      "reason": "forcejeo entre dos personas",
      "timestamp": "2026-11-16T23:40:00+00:00",
      "location": {"address": "Piso 1", "lat": -12.05, "lon": -77.04},
      "snapshot_b64": null
    },
    "has_snapshot": true,
    "snapshot_url": "/api/v1/alertas/ALT-20261116-0001/captura",
    "snapshot_sha256": "9f2c..."
  }
]
```

#### `GET /api/v1/alertas/{ticket}`

Devuelve un `AlertRecord` o `404`.

#### `GET /api/v1/alertas/{ticket}/json`

El mismo registro como descarga:

```
Content-Disposition: attachment; filename="ALT-20261116-0001.json"
```

#### `GET /api/v1/alertas/{ticket}/captura`

Devuelve la evidencia JPEG (`image/jpeg`) o `404` si el ticket no tiene captura.

#### `PATCH /api/v1/alertas/{ticket}`

```json
{"status": "atendida"}
```

Devuelve el `AlertRecord` actualizado y publica `alerta_actualizada`. Estados
usados por la PWA: `registrada`, `despachada`, `atendida`.

### 2.5 WebSocket

`WS /ws/eventos?api_key=<clave>`

Si `CENTRAL_API_KEY` está definida y la clave no coincide, el gateway cierra
con código `1008`. Al conectar envía:

```json
{"kind": "hola", "data": {"version": "2.0.0", "camaras": 2, "alertas": 5}}
```

Después difunde los eventos internos y un `ping` cada 20 s:

```json
{"kind": "estado", "data": {"id": "cam-1a2b3c4d", "state": "en_vivo"}}
{"kind": "bitacora", "data": {"camera_id": "cam-1a2b3c4d", "camera_name": "Entrada", "level": "info", "text": "Fuente conectada", "at": "2026-11-16T23:40:00+00:00"}}
{"kind": "alerta", "data": {"ticket": "ALT-20261116-0001"}}
{"kind": "alerta_actualizada", "data": {"ticket": "ALT-20261116-0001", "status": "atendida"}}
{"kind": "ping", "data": null}
```

La PWA se resincroniza por REST al reconectar; no hay replay de eventos.

### 2.6 Eventos internos

`POST /internal/eventos`

```json
{"kind": "alerta", "data": {"ticket": "ALT-20261116-0001"}}
```

- `200` → `{"ok": true}`.
- Si `INTERNAL_TOKEN` está definido, exige `X-Internal-Token`.
- `401` si el token no coincide.

## 3. Servicio de cámaras (8001)

Expone las mismas rutas de cámaras que el gateway, sin API key:

- `GET /health`
- `GET /api/v1/camaras`
- `POST /api/v1/camaras`
- `PATCH /api/v1/camaras/{id}`
- `DELETE /api/v1/camaras/{id}`
- `POST /api/v1/camaras/{id}/iniciar|detener`
- `GET /api/v1/camaras/{id}/snapshot|stream`
- `GET /api/v1/info`

`GET /health`:

```json
{"status": "ok", "service": "camaras", "camaras": 2, "en_vivo": 1}
```

## 4. Servicio de inferencia (8002)

### 4.1 `POST /v1/analizar`

```json
{
  "camera_id": "cam-1a2b3c4d",
  "image_b64": "/9j/4AAQSkZJRgABAQ..."
}
```

Respuesta `200`:

```json
{
  "annotated_b64": "/9j/4AAQSkZJRgABAQ...",
  "incidents": [
    {"type": "PELEA", "confidence": 0.82, "label": "grupo"}
  ],
  "alert": false
}
```

- `incidents` es la propuesta de YOLO + heurísticas; `alert` queda en `false`
  en el modo sospecha (la alarma solo la decide la etapa 2).
- `400` si `image_b64` no es base64 válido o no es un JPEG.
- `503` si el modelo no está cargado o YOLO falla.

### 4.2 `POST /v1/verificar`

```json
{
  "suspicion": {
    "frame": 120,
    "time_sec": 4.0,
    "type": "PELEA",
    "label": "grupo",
    "heuristic_confidence": 0.82,
    "timestamp": "2026-11-16T23:40:00+00:00"
  },
  "image_b64": "/9j/4AAQSkZJRgABAQ..."
}
```

Respuesta `200`:

```json
{
  "violent": true,
  "confidence": 0.9,
  "type": "PELEA",
  "reason": "dos personas forcejean",
  "verifier": "ollama/qwen2.5vl:7b"
}
```

El modelo se configura con `INCIDENTES_OLLAMA_MODEL`. Si Ollama falla, el
servicio devuelve `200` con `violent: false` y un `reason` que empieza por
`verifier-remoto-error` (fail-closed). `400` si el base64 es inválido.

### 4.3 `DELETE /v1/camaras/{id}`

```json
{"removed": "cam-1a2b3c4d", "was_loaded": true}
```

Limpia el estado heurístico. Lo llaman el servicio de cámaras al detener o
eliminar una cámara.

### 4.4 `GET /health`

```json
{
  "status": "ok",
  "service": "inferencia",
  "model": "yolo11n.pt",
  "verifier": "ollama/qwen2.5vl:7b",
  "ollama": true,
  "model_loaded": true,
  "camaras": 2
}
```

Puede tardar hasta ~5 s si Ollama no responde, porque comprueba su
disponibilidad.

## 5. Servicio de alertas (8003)

Las mismas rutas de alertas que el gateway, sin API key:

- `GET /health`
- `GET /api/v1/alertas?limit=100`
- `POST /api/v1/alertas`
- `GET /api/v1/alertas/{ticket}`
- `GET /api/v1/alertas/{ticket}/json`
- `GET /api/v1/alertas/{ticket}/captura`
- `PATCH /api/v1/alertas/{ticket}`
- `GET /api/v1/info`

`GET /health`:

```json
{"status": "ok", "service": "alertas", "alertas": 3}
```

## 6. Códigos de error

| Código | Cuándo |
|---|---|
| `400` | Base64/JPEG inválido en inferencia |
| `401` | API key del gateway o token interno incorrecto |
| `404` | Cámara, ticket o captura inexistente |
| `422` | Validación de Pydantic (campo faltante, rango, etc.) |
| `502` | El gateway no pudo contactar al servicio interno |
| `503` | Inferencia no disponible o modelo no cargado |

## 7. Ejemplos

```bash
# Alta de cámara
curl -X POST http://127.0.0.1:8000/api/v1/camaras \
  -H "Content-Type: application/json" \
  -d '{"name":"Entrada","source":"rtsp://10.0.0.5:554/stream1"}'

# Listado y snapshot
curl http://127.0.0.1:8000/api/v1/camaras
curl -o preview.jpg http://127.0.0.1:8000/api/v1/camaras/cam-1a2b3c4d/snapshot

# Alertas
curl "http://127.0.0.1:8000/api/v1/alertas?limit=20"
curl -X PATCH http://127.0.0.1:8000/api/v1/alertas/ALT-20261116-0001 \
  -H "Content-Type: application/json" \
  -d '{"status":"atendida"}'

# Con API key
curl -H "X-API-Key: mi-clave" http://127.0.0.1:8000/api/v1/camaras

# Evento interno (si hay token)
curl -X POST http://127.0.0.1:8000/internal/eventos \
  -H "Content-Type: application/json" \
  -H "X-Internal-Token: mi-token" \
  -d '{"kind":"ping","data":null}'
```

Para el WebSocket, desde la consola del navegador:

```js
const ws = new WebSocket("ws://127.0.0.1:8000/ws/eventos?api_key=mi-clave");
ws.onmessage = (event) => console.log(JSON.parse(event.data));
```
