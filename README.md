# Wiimote MVP (Bluetooth + HID + uinput)

Proyecto mínimo viable para leer controles de un mando Wii conectado por Bluetooth en Linux.
El comando `read` usa backend `auto`: intenta HID primero y, si no aparece, cae a `/dev/input/event*`.
La salida es JSON estructurado por frame.
El comando `control` traduce esos frames a teclado/raton virtual mediante `uinput`.
El cursor usa IR de forma prioritaria (estilo Wiimote) si `mouse_from_ir.enabled=true`.

## Requisitos

- Linux con BlueZ (`bluetoothctl`)
- Python 3.11+
- Librería de sistema para `hidapi` (normalmente `libhidapi-hidraw0` o equivalente)
- Soporte kernel `uinput` para modo control (`/dev/uinput`)

## Instalación

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

En Windows PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Si PowerShell bloquea `Activate.ps1` con un error de execution policy, habilitalo solo para la sesion actual:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

Alternativa sin activar el entorno:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m src.main --help
```

## Uso

### 1) Escanear dispositivos Bluetooth

```bash
python -m src.main scan --seconds 8
```

### 2) Emparejar y conectar el Wiimote

```bash
python -m src.main pair-connect AA:BB:CC:DD:EE:FF
```

Si ya estaba emparejado:

```bash
python -m src.main connect AA:BB:CC:DD:EE:FF
```

### 3) Leer eventos

```bash
python -m src.main read --mac AA:BB:CC:DD:EE:FF
```

Si no detecta HID en tu modelo, prueba:

```bash
python -m src.main read --mac AA:BB:CC:DD:EE:FF --product-id 0x0330
```

Si quieres forzar backend Linux input:

```bash
python -m src.main read --backend input
```

Listar dispositivos HID compatibles:

```bash
python -m src.main list-devices
```

### 4) Calibrar IR (recomendado antes de `control`)

```bash
python -m src.main calibrate-ir --backend input
```

Flujo:
- apunta a cada esquina cuando lo pida (TL, TR, BR, BL)
- pulsa `A` para capturar cada punto
- se guardan bounds en `config/mapping.json`
- por defecto se abre una vista grafica con los puntos IR en tiempo real; usa `--no-gui` si prefieres solo consola

### 5) Modo control (teclado/raton virtual)

Dry-run (muestra acciones sin inyectar):

```bash
python -m src.main control --backend input --dry-run
```

uinput real:

```bash
python -m src.main control --backend input
```

Mapping por defecto: `config/mapping.json` (puedes pasar `--mapping /ruta/a/otro.json`).
Parámetros IR clave: `mouse_from_ir.enabled`, `mode`, `smoothing_alpha`, `calibration`, `screen_edge_trim`, `recalibrate_button`, `capture_button`.
Si desactivas IR (`mouse_from_ir.enabled=false`), vuelve el control por gyro (`mouse_from_gyro`).

### Uso en Windows

- el emparejado del Wiimote se hace manualmente desde la configuracion Bluetooth de Windows
- `scan`, `pair-connect` y `connect` no estan soportados en Windows
- usa `list-devices` para localizar el `device path` HID y luego `read` o `control`
- el backend recomendado es `--backend windows-hid` o `--backend auto`
- en el MVP actual Windows soporta botones, clicks y movimiento basado en `mouse_from_accel`
- `calibrate-ir` no esta soportado en Windows porque el backend HID actual no expone IR util

Ejemplos:

```bash
python -m src.main list-devices
python -m src.main read --backend windows-hid --device-path "<device-path>"
python -m src.main control --backend windows-hid --device-path "<device-path>" --dry-run
```

Ejemplo completo en PowerShell usando el `venv` sin depender de la activacion:

```powershell
.\.venv\Scripts\python.exe -m src.main list-devices
.\.venv\Scripts\python.exe -m src.main read --backend windows-hid --device-path "<device-path>"
.\.venv\Scripts\python.exe -m src.main control --backend windows-hid --device-path "<device-path>" --dry-run
```

Salida esperada:

```json
{
  "ts": 1710000000.123,
  "buttons": {"A": 1, "B": 0, "ONE": 0, "TWO": 0, "PLUS": 0, "MINUS": 0, "HOME": 0, "UP": 0, "DOWN": 0, "LEFT": 0, "RIGHT": 0},
  "accel": {"x": -41, "y": -73, "z": 70},
  "gyro": {"x": 12, "y": -3, "z": 5},
  "ir": [
    {"x": 512, "y": 384, "size": null},
    {"x": null, "y": null, "size": null},
    {"x": null, "y": null, "size": null},
    {"x": null, "y": null, "size": null}
  ]
}
```

## Docker

Build de la imagen:

```bash
docker compose build
```

Validar el entorno con tests:

```bash
docker compose run --rm wiimote python -m pytest -q
```

Mostrar la ayuda del CLI:

```bash
docker compose run --rm wiimote
```

Ejecutar un comando del proyecto dentro del contenedor:

```bash
docker compose run --rm wiimote python -m src.main scan --seconds 8
```

Perfil para host Linux con Bluetooth y dispositivos de entrada reales:

```bash
docker compose --profile linux-hw run --rm wiimote-hw python -m src.main scan --seconds 8
docker compose --profile linux-hw run --rm wiimote-hw python -m src.main read --backend input
docker compose --profile linux-hw run --rm wiimote-hw python -m src.main control --backend input --dry-run
```

Notas importantes para uso real con Wiimote:

- el proyecto depende de Linux, BlueZ y acceso a Bluetooth del host
- para `scan`, `pair-connect`, `connect`, `read` o `control` hace falta un host Linux con `bluetoothctl` operativo
- para `read --backend input` y `control` tambien hace falta exponer `/dev/input`, `/dev/uinput` y normalmente ejecutar el contenedor con privilegios elevados
- en Docker Desktop sobre Windows o macOS puedes construir la imagen y ejecutar tests, pero no es un entorno valido para acceder al Wiimote del host
- el servicio `wiimote-hw` del compose esta pensado para Docker Engine en Linux; en Windows no te dara acceso al Bluetooth nativo del sistema

## Tests

```bash
pytest -q
```

## Notas del MVP

- Incluye botones, acelerómetro, Motion Plus (gyro) e IR si el kernel expone esos nodos.
- No incluye todavía Nunchuk/Classic Controller.
- En algunos sistemas Linux puede hacer falta acceso a `/dev/hidraw*`, `/dev/input/event*` o `/dev/uinput`.
