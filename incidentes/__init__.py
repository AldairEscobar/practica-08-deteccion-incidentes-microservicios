"""Microservicios de detección de incidentes violentos con verificación por IA.

La práctica 8 parte el backend de la práctica 7 en cuatro servicios:

- **gateway:** PWA, proxy REST y WebSocket.
- **cámaras:** registro, captura, heurísticas y vista previa MJPEG.
- **inferencia:** YOLO (sospecha) y Ollama (confirmación).
- **alertas:** tickets, despacho y evidencia.

El dominio y el caso de uso son los mismos de la práctica anterior; cambian los
adaptadores y el despliegue.
"""

__version__ = "2.0.0"
