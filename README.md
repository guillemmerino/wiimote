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

### 4) Calibrar IR (recomendado antes de `control`)

```bash
python -m src.main calibrate-ir --backend input
```

Flujo:
- apunta a cada esquina cuando lo pida (TL, TR, BR, BL)
- pulsa `A` para capturar cada punto
- se guardan bounds en `config/mapping.json`

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
Parámetros IR clave: `mouse_from_ir.enabled`, `mode`, `smoothing_alpha`, `calibration`, `recalibrate_button`, `capture_button`.
Si desactivas IR (`mouse_from_ir.enabled=false`), vuelve el control por gyro (`mouse_from_gyro`).

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

## Tests

```bash
pytest -q
```

## Notas del MVP

- Incluye botones, acelerómetro, Motion Plus (gyro) e IR si el kernel expone esos nodos.
- No incluye todavía Nunchuk/Classic Controller.
- En algunos sistemas Linux puede hacer falta acceso a `/dev/hidraw*`, `/dev/input/event*` o `/dev/uinput`.
