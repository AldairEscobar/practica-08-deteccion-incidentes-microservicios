# 🚨 Práctica 8 — Microservicios de detección de incidentes

<p align="center">
  <strong>La PWA de la práctica 7, con el backend partido en cuatro servicios.</strong><br>
  Gateway + cámaras + inferencia + alertas, con contratos REST y eventos en tiempo real.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/FastAPI-microservicios-009688?logo=fastapi&logoColor=white" alt="FastAPI">
  <img src="https://img.shields.io/badge/YOLO11-Ultralytics-111F68" alt="YOLO11">
  <img src="https://img.shields.io/badge/Ollama-IA%20local-black?logo=ollama&logoColor=white" alt="Ollama">
  <img src="https://img.shields.io/badge/PWA-instalable-5A0FC8" alt="PWA">
  <img src="https://img.shields.io/badge/tests-80%20passed-2ea44f?logo=pytest&logoColor=white" alt="80 tests">
  <img src="https://img.shields.io/badge/lint-ruff-261230?logo=ruff&logoColor=white" alt="Ruff">
</p>

La práctica 7 convirtió la central en una **PWA instalable**. Esta práctica
conserva esa PWA y divide el backend monolítico en cuatro microservicios con
responsabilidades y datos separados:

| Servicio | Puerto | Responsabilidad | Datos |
|---|---:|---|---|
| **gateway** | 8000 | PWA, proxy REST, WebSocket y eventos internos | — |
| **cámaras** | 8001 | Registro de cámaras, captura, vista previa y heurísticas | `data/cameras.db` |
| **inferencia** | 8002 | YOLO (sospecha) y Ollama (confirmación) | estado en memoria por cámara |
| **alertas** | 8003 | Tickets, prioridad, ETA y evidencia | `data/alerts.db` |

El navegador solo habla con el gateway. Los servicios internos se comunican por
REST y publican eventos HTTP que el gateway reenvía a la PWA por WebSocket.

## Qué demuestra

- **Descomposición por capacidad de negocio**, no por capa técnica: cada
  servicio es dueño de su estado y de sus datos.
- **Contratos REST explícitos** entre servicios, con modelos Pydantic y
  validación en los bordes.
- **API Gateway / BFF**: un único origen para la PWA, proxy de cámaras y
  alertas, y fan-out de eventos.
- **Aislamiento de fallos**: si la inferencia cae, la captura sigue; si Ollama
  no confirma, no se alarma; si las alertas fallan, la cámara no se detiene.
- **PWA sin cambios funcionales**: la interfaz de la práctica 7 se reutiliza
  tal cual.
- **Calidad**: 80 tests, Ruff limpio y una prueba de humo del stack.

## Arquitectura

```mermaid
flowchart LR
  PWA["PWA<br/>(navegador)"]
  GW["gateway<br/>:8000"]
  CAM["cámaras<br/>:8001"]
  INF["inferencia<br/>:8002"]
  ALE["alertas<br/>:8003"]
  CAMDB[("cameras.db")]
  ALEDB[("alerts.db")]
  OLLAMA["Ollama<br/>qwen2.5vl:7b"]

  PWA -->|REST + WebSocket| GW
  GW -->|proxy /api/v1/camaras| CAM
  GW -->|proxy /api/v1/alertas| ALE
  CAM -->|POST /v1/analizar| INF
  CAM -->|POST /v1/verificar| INF
  INF -->|/api/chat| OLLAMA
  CAM -->|POST /api/v1/alertas| ALE
  CAM -. eventos .-> GW
  ALE -. eventos .-> GW
  CAM --- CAMDB
  ALE --- ALEDB
```

### Comunicación

| Tipo | Ruta | Uso |
|---|---|---|
| REST (borde) | PWA → gateway | Cámaras, alertas, preview y WebSocket |
| REST (interno) | gateway → cámaras / alertas | Proxy de las rutas de la PWA |
| REST (interno) | cámaras → inferencia | Analizar un frame y verificar una sospecha |
| REST (interno) | cámaras → alertas | Registrar un ticket con evidencia |
| Eventos | cámaras / alertas → gateway | `POST /internal/eventos` |
| WebSocket | gateway → PWA | `estado`, `bitacora`, `alerta`, `alerta_actualizada` |
| Salud | gateway → servicios | `/health` agregado y arranque ordenado |

No se usa un broker (Redis, RabbitMQ, Kafka) porque el entorno de la práctica
no tiene Docker ni Redis instalados. El adaptador `HttpEventPublisher` envía
cada evento al gateway, que es el único punto de fan-out. El contrato
`{kind, data}` es el mismo que usaría un broker; para producción se puede
cambiar el transporte sin tocar el dominio ni la PWA.

**Tradeoff:** los eventos son *at-most-once*: si el gateway está caído, el
evento se pierde, aunque la PWA se resincroniza por REST al reconectar. No hay
replay ni outbox. Es aceptable para una demo local; en producción se usaría
Redis Streams o NATS y un outbox en cada servicio.

## Servicios en detalle

### gateway (:8000)

- Sirve la PWA desde `incidentes/services/gateway/web/`.
- Proxifica `/api/v1/camaras` y `/api/v1/alertas`.
- Mantiene el `EventBus` en memoria y el WebSocket `/ws/eventos`.
- Valida la API key opcional (`CENTRAL_API_KEY`) en las rutas proxificadas.
- Agrega `/health` de los tres servicios internos.
- Recibe `POST /internal/eventos` con token interno opcional.

### cámaras (:8001)

- CRUD de cámaras y arranque/parada de sus hilos de captura.
- Lee RTSP/HTTP o archivos locales; mantiene la conexión con la cámara.
- Llama a inferencia por cada frame y recibe el frame anotado.
- Publica `estado` y `bitacora` en el gateway.
- Expone snapshot JPEG y stream MJPEG para la PWA.
- Envía la alerta confirmada al servicio de alertas.
- Al detener/eliminar una cámara, limpia su estado heurístico en inferencia.

### inferencia (:8002)

- Carga **un** modelo YOLO compartido al arrancar y lo calienta.
- Mantiene un `YoloIncidentDetector` por cámara (estado de heurísticas).
- `POST /v1/analizar`: frame base64 → frame anotado + sospechas.
- `POST /v1/verificar`: JPEG + sospecha → veredicto de Ollama.
- `DELETE /v1/camaras/{id}`: olvida el estado de esa cámara.
- Un solo worker: el estado de las heurísticas vive en memoria.
- Serializa la inferencia con un lock global porque el modelo YOLO no
  garantiza ser thread-safe.

### alertas (:8003)

- Recibe `AlertIn`, calcula prioridad/ETA/unidades con `plan_dispatch`.
- Persiste el ticket y la captura en SQLite.
- Publica `alerta` y `alerta_actualizada` en el gateway.
- Expone el historial, el JSON descargable y la captura como JPEG.
- Permite cambiar el estado del ticket (`registrada`, `despachada`,
  `atendida`, ...).

## Flujo de una alerta

1. El servicio de cámaras captura un frame y lo envía a
   `POST /v1/analizar`.
2. Inferencia ejecuta YOLO + heurísticas (pelea, arma, caída, robo,
   violencia) y devuelve el frame anotado y las sospechas.
3. Si hay una sospecha, el caso de uso la envía a `POST /v1/verificar`.
4. Ollama confirma o descarta. Si confirma, se construye el `AlertPayload`
   con la captura JPEG.
5. `HttpAlertDispatcher` envía la alerta al servicio de alertas, que genera el
   ticket y persiste la evidencia.
6. Alertas publica `alerta` al gateway; el gateway lo difunde por WebSocket y
   la PWA lo muestra en vivo.

**Modos de fallo:**

- Inferencia caída → el detector remoto es **fail-open**: no hay sospechas,
  pero la cámara y la preview siguen.
- Ollama caído → el verificador remoto es **fail-closed**: no se confirma
  nada y no se generan falsos positivos.
- Alertas caídas → el dispatcher falla de forma controlada; el pipeline de
  captura no se detiene.

## Estructura del proyecto

```
incidentes/
  domain/                 # entidades y puertos (núcleo)
  application/            # DetectIncidentsUseCase
  infrastructure/         # YOLO, Ollama, SQLite, video
  shared/                 # kernel compartido: modelos, stores, supervisor, eventos
  services/
    gateway/app.py        # PWA, proxy, WebSocket, eventos internos
    gateway/web/          # PWA (HTML, CSS, JS, manifest, service worker)
    cameras/
      app.py              # API de cámaras y preview
      factory.py          # composición del pipeline por cámara
      remote.py           # detector/verificador remotos
    inference/app.py      # YOLO + Ollama
    alerts/app.py         # tickets y evidencia
  cli/
    stack.py              # arranca los cuatro uvicorn en orden
    smoke_test.py         # comprueba el stack en ejecución
tests/                    # 80 pruebas con dobles
docs/
  practica-08.md          # enunciado, decisiones y evidencia
  arquitectura.md         # detalle de la arquitectura
  api.md                  # contratos REST y WebSocket
```

`incidentes/shared` es el *shared kernel* de la práctica: modelos de contrato,
acceso a SQLite, supervisor de cámaras y bus de eventos. En un despliegue real
se publicaría como paquete versionado o se generarían los clientes desde
OpenAPI; aquí se mantiene en el monorepo para no duplicar contratos.

## Requisitos

- **Python 3.11+** (probado con 3.14 en Windows).
- **Ollama** con el modelo `qwen2.5vl:7b` para la confirmación por IA.
- Pesos de YOLO (`yolo11n.pt`): Ultralytics los descarga en el primer uso; si
  no hay red, coloca el archivo en la raíz.
- No se necesita Docker ni Redis para esta versión local.
- La instalación de la PWA requiere `localhost` o HTTPS (restricción de los
  service workers; `http://127.0.0.1:8000` funciona).

## Instalación

```bash
git clone https://github.com/AldairEscobar/practica-08-deteccion-incidentes-microservicios.git
cd practica-08-deteccion-incidentes-microservicios

python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/macOS
source .venv/bin/activate

pip install -e ".[central,dev]"
ollama pull qwen2.5vl:7b
```

Si no tienes Ollama, el sistema arranca igual: la preview y la detección de
YOLO funcionan, pero `/health` mostrará `ollama: false` y no se confirmarán
alertas.

## Arranque

```bash
python -m incidentes.cli.stack
```

o, si instalaste los scripts:

```bash
incidentes-stack
```

El lanzador:

1. arranca **inferencia** y espera su `/health` (YOLO tarda ~20 s en cargar y
   calentar);
2. arranca **cámaras** y **alertas** y espera sus `/health`;
3. arranca el **gateway** al final, para que la PWA encuentre todo listo;
4. deja los cuatro procesos en primer plano y los detiene con `Ctrl+C`.

Salida esperada:

```
Arrancando microservicios...
  inferencia http://127.0.0.1:8002
  ...
PWA:  http://127.0.0.1:8000
Docs: http://127.0.0.1:8000/docs
```

En otra terminal, prueba de humo:

```bash
python -m incidentes.cli.smoke_test
```

Comprueba el gateway, los tres servicios y la versión. Si algún servicio está
caído, devuelve código de salida 1.

### Arranque manual (cuatro terminales)

El stack es la forma recomendada, pero cada servicio se puede arrancar por
separado:

```bash
# Terminal 1 — inferencia
uvicorn incidentes.services.inference.app:app --host 127.0.0.1 --port 8002

# Terminal 2 — cámaras
uvicorn incidentes.services.cameras.app:app --host 127.0.0.1 --port 8001

# Terminal 3 — alertas
uvicorn incidentes.services.alerts.app:app --host 127.0.0.1 --port 8003

# Terminal 4 — gateway
uvicorn incidentes.services.gateway.app:app --host 127.0.0.1 --port 8000
```

El stack rellena `GATEWAY_URL`, `CAMERAS_URL`, `INFERENCE_URL` y `ALERTS_URL`.
Si arrancas a mano, exporta esas variables (o copia `.env.example` a `.env` y
usa tu cargador de entorno habitual).

## Uso de la PWA

1. Abre <http://127.0.0.1:8000>.
2. Registra una cámara con una URL RTSP/HTTP o un archivo local.
3. La tarjeta muestra el estado (`conectando`, `en_vivo`, `detenida`) y la
   preview MJPEG.
4. Las alertas confirmadas aparecen en la lista y en la bitácora en vivo.
5. Si definiste `CENTRAL_API_KEY`, introdúcela en **Ajustes**; la PWA la envía
   como `X-API-Key` y como `?api_key=` en el WebSocket.

La PWA es la misma de la práctica 7: no se reescribió la interfaz, solo se
movió a `incidentes/services/gateway/web/`.

## API

### Gateway (borde)

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/` | PWA instalable |
| GET | `/health` | Salud agregada de los tres servicios |
| GET | `/api/v1/info` | Versión y URLs internas |
| GET | `/api/v1/camaras` | Lista de cámaras con su estado |
| POST | `/api/v1/camaras` | Alta de cámara |
| PATCH | `/api/v1/camaras/{id}` | Cambios parciales |
| DELETE | `/api/v1/camaras/{id}` | Baja |
| POST | `/api/v1/camaras/{id}/iniciar` | Arranca el análisis |
| POST | `/api/v1/camaras/{id}/detener` | Detiene el análisis |
| GET | `/api/v1/camaras/{id}/snapshot` | Último frame JPEG |
| GET | `/api/v1/camaras/{id}/stream` | Stream MJPEG |
| GET | `/api/v1/alertas` | Historial (`?limit=100`) |
| POST | `/api/v1/alertas` | Registra una alerta (clientes/edge) |
| GET | `/api/v1/alertas/{ticket}` | Detalle |
| GET | `/api/v1/alertas/{ticket}/json` | JSON descargable |
| GET | `/api/v1/alertas/{ticket}/captura` | Evidencia JPEG |
| PATCH | `/api/v1/alertas/{ticket}` | Cambia el estado |
| WS | `/ws/eventos` | Eventos en vivo |
| POST | `/internal/eventos` | Eventos de servicios internos |

Ejemplo:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/camaras \
  -H "Content-Type: application/json" \
  -d '{"name":"Entrada","source":"rtsp://usuario:clave@10.0.0.5:554/stream1","address":"Piso 1"}'
```

### Servicios internos

| Servicio | Endpoint | Descripción |
|---|---|---|
| inferencia | `POST /v1/analizar` | `{camera_id, image_b64}` → frame anotado + sospechas |
| inferencia | `POST /v1/verificar` | `{suspicion, image_b64}` → veredicto |
| inferencia | `DELETE /v1/camaras/{id}` | Limpia el estado heurístico |
| cámaras | `/api/v1/camaras...` | CRUD, preview y stream |
| alertas | `/api/v1/alertas...` | Tickets y evidencia |
| todos | `GET /health` | Estado del servicio |

Cada servicio expone su Swagger en `http://127.0.0.1:<puerto>/docs`. Los
contratos completos, ejemplos y códigos de error están en
[`docs/api.md`](docs/api.md).

### Mensajes WebSocket

```json
{"kind": "hola", "data": {"version": "2.0.0", "camaras": 2, "alertas": 5}}
{"kind": "estado", "data": {"id": "cam-...", "state": "en_vivo"}}
{"kind": "bitacora", "data": {"camera_id": "cam-...", "level": "info", "text": "..."}}
{"kind": "alerta", "data": {"ticket": "ALT-...", "priority": "ALTA"}}
{"kind": "alerta_actualizada", "data": {"ticket": "ALT-...", "status": "atendida"}}
{"kind": "ping", "data": null}
```

## Configuración

Copia `.env.example` y ajusta lo necesario. El stack fija las URLs y los
puertos; las variables de datos, timeouts y modelos son las que más se tocan.

| Variable | Default | Uso |
|---|---|---|
| `GATEWAY_URL` | `http://127.0.0.1:8000` | URL que usan los servicios para publicar eventos |
| `CAMERAS_URL` | `http://127.0.0.1:8001` | Gateway y alertas |
| `INFERENCE_URL` | `http://127.0.0.1:8002` | Gateway y cámaras |
| `ALERTS_URL` | `http://127.0.0.1:8003` | Gateway y cámaras |
| `CENTRAL_API_KEY` | vacío | API key opcional del gateway |
| `INTERNAL_TOKEN` | vacío | Token opcional para `/internal/eventos` |
| `CAMERAS_DB_PATH` | `data/cameras.db` | SQLite del servicio de cámaras |
| `ALERTS_DB_PATH` | `data/alerts.db` | SQLite del servicio de alertas |
| `CAMERAS_AUTOSTART` | `1` | Arranca las cámaras guardadas al iniciar |
| `DETECT_TIMEOUT_SEC` | `8` | Timeout de `/v1/analizar` |
| `VERIFY_TIMEOUT_SEC` | `130` | Timeout de Ollama |
| `EVENT_TIMEOUT_SEC` | `2` | Timeout al publicar un evento |
| `HTTP_TIMEOUT_SEC` | `5` | Timeout de las llamadas REST internas |
| `INCIDENTES_MODEL_PATH` | `yolo11n.pt` | Pesos de YOLO |
| `INCIDENTES_OLLAMA_MODEL` | `qwen2.5vl:7b` | Modelo de visión |
| `INCIDENTES_OLLAMA_URL` | `http://localhost:11434` | Ollama |
| `DISPATCH_WEBHOOK_URL` | vacío | Webhook opcional de despacho |

## Pruebas y verificación

```bash
python -m pytest
python -m ruff check incidentes tests
```

Resultado local:

- **80 tests pasan**.
- **Ruff**: `All checks passed!`.
- La suite no descarga pesos ni necesita Ollama: usa dobles para el detector,
  el verificador, el publicador y los stores.

Prueba manual realizada con el stack completo:

| Comprobación | Resultado |
|---|---|
| `/health` del gateway | `ok`; cámaras, inferencia y alertas `ok` |
| PWA en `/` | HTTP 200, HTML con `<title>` |
| Alta/listado/baja de cámara | 201 / 200 / 200 |
| `snapshot` de una cámara con archivo MP4 | JPEG de ~6 KB |
| `stream` MJPEG | `multipart/x-mixed-replace; boundary=frame` |
| `POST /v1/analizar` | 200, frame anotado y `incidents` |
| Ollama | `false` en el entorno de prueba (no estaba arrancado) |

El pipeline de preview y detección se verificó con un video local. La
confirmación de alertas requiere Ollama en ejecución con el modelo descargado.

## Decisiones y límites

- **Sin broker:** eventos HTTP al gateway. Simple y suficiente para la
  práctica; *at-most-once*, sin replay, y el gateway es un punto único de
  fallo para el tiempo real. En producción: Redis Streams/NATS + outbox.
- **Inferencia con estado:** las heurísticas viven por cámara en memoria. Un
  reinicio las pierde y el servicio debe correr con un solo worker. Para
  escalar, mover el estado a Redis o usar *sticky sessions*.
- **SQLite por servicio:** cada servicio es dueño de su base. No hay
  transacciones distribuidas ni consistencia fuerte entre cámaras y alertas.
- **Despacho *at-most-once*:** si el servicio de alertas no está, la alerta se
  pierde; no hay reintentos ni outbox.
- **Red de cámaras:** el servicio de cámaras debe alcanzar las RTSP. En un
  despliegue cloud hace falta VPN/túnel o un agente edge; no se resuelve aquí.
- **Seguridad:** API key en el gateway y token interno para eventos; los
  puertos 8001-8003 son internos y no deben exponerse. No hay TLS ni
  autenticación entre servicios; es una práctica local.
- **Ollama en CPU:** la verificación puede tardar; el timeout por defecto es
  de 130 s. La primera carga de YOLO tarda ~20 s.
- **PWA:** la instalación y el service worker necesitan `localhost` o HTTPS.

## Roadmap

- Redis/NATS para eventos con replay y *consumer groups*.
- Docker Compose con los cuatro servicios y un broker.
- Postgres para tickets/evidencia y un patrón outbox.
- Autenticación servicio-a-servicio (mTLS o JWT) y TLS en el gateway.
- Métricas (Prometheus), trazas (OpenTelemetry) y *health checks* reales.
- Tests de integración con testcontainers y un video de muestra.

## Práctica 8 — checklist

- [x] Backend dividido en cuatro servicios con puertos y responsabilidades
      separadas.
- [x] Gateway como único punto de entrada para la PWA.
- [x] Contratos REST entre servicios y proxy del gateway.
- [x] Eventos de cámaras y alertas hacia el gateway por HTTP.
- [x] WebSocket para la PWA, conservando la interfaz de la práctica 7.
- [x] Datos separados por servicio (`cameras.db`, `alerts.db`).
- [x] Modos de fallo explícitos (fail-open en detección, fail-closed en
      verificación).
- [x] Arranque local con un comando y prueba de humo.
- [x] Tests, lint y documentación de arquitectura, API y evidencia.

Más detalle en [`docs/practica-08.md`](docs/practica-08.md),
[`docs/arquitectura.md`](docs/arquitectura.md) y [`docs/api.md`](docs/api.md).
