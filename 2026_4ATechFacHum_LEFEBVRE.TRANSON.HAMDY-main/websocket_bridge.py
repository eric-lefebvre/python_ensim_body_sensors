"""
Bridge WebSocket — envoie les données physiologiques traitées à Unity.

Usage :
    bridge = WebSocketBridge(send_rate_hz=10)
    bridge.start()
    bridge.send(result)   # thread-safe, appelé depuis onRawFrame
    bridge.stop()

Unity se connecte sur ws://localhost:8765 et reçoit des JSON :
    {
        "shot_triggered": false,
        "shot_power":     0.0,
        "aim_angle":      -5.3,
        "stress":         0.15,
        "heart_rate":     72.0,
        "breath_rate":    14.2
    }

Dépendance : pip install websockets
"""

import asyncio
import json
import threading
import time
import websockets

# Plages pour les valeurs DÉRIVÉES uniquement (formules pouvant déborder).
# heart_rate et breath_rate sont déjà validés par les filtres d'intervalle
# du signal_processor — les filtrer ici provoquerait un gel sur les défauts.
DEFAULT_BOUNDS = {
    "stress":    (0,    1),   # normalisé
    "shot_power":(0,    1),   # normalisé
    "aim_angle": (-180, 180), # degrés
}


class WebSocketBridge:

    def __init__(
        self,
        host: str = "localhost",
        port: int = 8765,
        send_rate_hz: float = 10.0,
        bounds: dict = None,
    ):
        self._host = host
        self._port = port
        self._send_interval = 1.0 / send_rate_hz   # secondes entre deux envois
        self._bounds = bounds if bounds is not None else DEFAULT_BOUNDS

        self._clients: set = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None

        self._last_send_time = 0.0
        self._last_valid: dict = {}     # dernières valeurs dans les bornes
        self._rate_lock = threading.Lock()

    # ── Démarrage / arrêt ──────────────────────────────────────────────

    def start(self):
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True,
                                        name="WebSocketBridge")
        self._thread.start()

    def stop(self):
        if self._loop and hasattr(self, '_stop_event'):
            self._loop.call_soon_threadsafe(self._stop_event.set)

    # ── Envoi thread-safe ──────────────────────────────────────────────

    def send(self, data: dict):
        if not self._clients or self._loop is None:
            return

        msg_type = data.get("type", "data")
        shot     = data.get("shot_start", False) or data.get("shot_end", False)

        # Limite de fréquence — bypass pour les messages de calibration et les tirs
        now = time.monotonic()
        with self._rate_lock:
            bypass = (msg_type != "data") or shot
            if not bypass and (now - self._last_send_time) < self._send_interval:
                return
            self._last_send_time = now

        filtered = self._filter(data)
        asyncio.run_coroutine_threadsafe(self._broadcast(filtered), self._loop)

    # ── Filtrage des valeurs aberrantes ───────────────────────────────

    def _filter(self, data: dict) -> dict:
        out = {}
        for key, value in data.items():
            if key in self._bounds:
                lo, hi = self._bounds[key]
                if lo <= value <= hi:
                    self._last_valid[key] = value
                    out[key] = value
                else:
                    # Valeur hors plage → on garde la dernière valeur valide
                    out[key] = self._last_valid.get(key, value)
            else:
                out[key] = value     # shot_triggered, shot_power brut, etc.
        return out

    # ── Boucle asyncio (thread dédié) ─────────────────────────────────

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._stop_event = asyncio.Event()
        self._loop.run_until_complete(self._serve())

    async def _serve(self):
        async with websockets.serve(self._handler, self._host, self._port):
            print(f"[WebSocket] Serveur démarré → ws://{self._host}:{self._port}  "
                  f"({1/self._send_interval:.0f} Hz)")
            await self._stop_event.wait()  # attend jusqu'à stop()

    async def _handler(self, websocket):
        self._clients.add(websocket)
        addr = getattr(websocket, "remote_address", "?")
        print(f"[WebSocket] Unity connecté ({addr})")
        try:
            await websocket.wait_closed()
        finally:
            self._clients.discard(websocket)
            print(f"[WebSocket] Unity déconnecté ({addr})")

    async def _broadcast(self, data: dict):
        if not self._clients:
            return
        message = json.dumps(data)
        await asyncio.gather(
            *(client.send(message) for client in list(self._clients)),
            return_exceptions=True,
        )
