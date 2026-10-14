import platform
import sys
import csv
import signal
import threading
from datetime import datetime
import matplotlib.pyplot as plt
from signal_processor import SignalProcessor
from websocket_bridge import WebSocketBridge

sys.path.append(f"Win{platform.architecture()[0][:2]}_{''.join(platform.python_version().split('.')[:2])}")
import plux

# ── Configuration ──────────────────────────────────────────────────────
DEVICE_ADDRESS  = "98:D3:91:FD:69:DD"
FREQUENCY       = 100   # Hz
CALIBRATION_SEC = 30    # secondes de repos pour mesurer les baselines
ACTIVE_PORTS    = [1, 2, 3, 4, 5, 6]
CHANNEL_NAMES  = ["resp", "emg", "eda", "ppg", "acc_x", "acc_z"]
WINDOW_SAMPLES = FREQUENCY * 5   # fenêtre glissante : 5 dernières secondes
PLOT_INTERVAL  = 0.2              # rafraîchissement du graphique (secondes)
# ───────────────────────────────────────────────────────────────────────


class Acquisition(plux.SignalsDev):
    """Tourne dans un thread séparé — ne touche jamais à matplotlib."""

    def __init__(self, address, bridge: WebSocketBridge = None):
        plux.SignalsDev.__init__(address)
        self.frequency = FREQUENCY
        self.data      = [[] for _ in ACTIVE_PORTS]
        self.processor = SignalProcessor(frequency=FREQUENCY, calibration_sec=CALIBRATION_SEC)
        self.bridge    = bridge
        self.running   = True

    def onRawFrame(self, nSeq, data):
        for i in range(len(ACTIVE_PORTS)):
            self.data[i].append(data[i])

        frame  = {name: data[i] for i, name in enumerate(CHANNEL_NAMES)}
        result = self.processor.update(frame)
        if result is None:
            return not self.running

        if self.bridge:
            self.bridge.send(result)

        msg_type = result.get("type", "data")

        if msg_type == "calibration_progress":
            print(f"[Calibration] {result['elapsed_sec']}s / {result['total_sec']}s"
                  f"  ({int(result['progress'] * 100)}%)")

        elif msg_type == "calibration_complete":
            print(f"[Calibration OK] "
                  f"FC repos={result['hr_rest']:.0f}bpm ({result['hr_label']})  "
                  f"Resp repos={result['resp_rest']:.1f}bpm ({result['resp_label']})  "
                  f"EDA baseline={result['eda_baseline']:.1f}")

        elif msg_type == "data":
            if result["shot_start"]:
                print("[TIR DEBUT]")
            elif result["shot_end"]:
                print(f"[TIR FIN]  stress={result['stress']:.2f}")
            elif nSeq % (self.frequency * 2) == 0:
                print(f"[Live] stress={result['stress']:.2f}  "
                      f"FC={result['heart_rate']:.0f}bpm  "
                      f"resp={result['breath_rate']:.1f}bpm  "
                      f"HRV={result['heart_rate_variability']:.0f}ms  "
                      f"EDA={result['eda_level']:.0f}  "
                      f"mvt={result['movement']:.2f}")

        if nSeq % (self.frequency * 10) == 0 and nSeq > 0:
            total = nSeq + 1
            print(f"[Acquisition] {total} échantillons ({total // self.frequency}s)")

        return not self.running


def acquérir_et_afficher():
    bridge = WebSocketBridge()
    bridge.start()

    print(f"[Connexion] Tentative de connexion à {DEVICE_ADDRESS}...")
    try:
        device = Acquisition(DEVICE_ADDRESS, bridge=bridge)
    except RuntimeError as e:
        print(f"[Erreur] Impossible de se connecter : {e}")
        print("[Erreur] Vérifiez que le BITalino est allumé et couplé en Bluetooth.")
        return

    print(f"[Connexion] Connecté. Démarrage de l'acquisition sur ports {ACTIVE_PORTS}.")
    print("[Acquisition] Appuyez sur Ctrl+C pour arrêter.\n")

    n = len(ACTIVE_PORTS)
    fig, axes = plt.subplots(n, 1, figsize=(10, 3 * n))
    axes  = [axes] if n == 1 else list(axes)
    lines = []
    for ax, name in zip(axes, CHANNEL_NAMES):
        line, = ax.plot([], [])
        ax.set_title(name)
        ax.set_ylabel("Valeur brute")
        lines.append(line)
    axes[-1].set_xlabel("Échantillon")
    plt.tight_layout()
    plt.ion()
    plt.show()

    def run_acquisition():
        try:
            device.start(FREQUENCY, ACTIVE_PORTS, 16)
            device.loop()
        except Exception as e:
            if device.running:
                print(f"\n[Erreur] Problème pendant l'acquisition : {e}")
        finally:
            try:
                device.stop()
                device.close()
            except Exception:
                pass
            print("[Connexion] Déconnecté du BITalino.")

    acq_thread = threading.Thread(target=run_acquisition, daemon=True)
    acq_thread.start()

    stop_event = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop_event.set())

    while acq_thread.is_alive() and not stop_event.is_set():
        for i, (line, ax) in enumerate(zip(lines, axes)):
            y = device.data[i][-WINDOW_SAMPLES:]
            line.set_data(range(len(y)), y)
            ax.relim()
            ax.autoscale_view()
        fig.canvas.draw()
        fig.canvas.flush_events()
        stop_event.wait(PLOT_INTERVAL)

    if stop_event.is_set():
        print("\n[Acquisition] Arrêt demandé.")
        device.running = False
        acq_thread.join(timeout=3)

    n_samples = len(device.data[0])
    if n_samples == 0:
        print("[Export] Aucune donnée reçue, pas de fichier créé.")
        return

    print(f"[Export] {n_samples} échantillons ({n_samples // FREQUENCY}s) — sauvegarde en cours...")
    filename = f"data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    with open(filename, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["index"] + CHANNEL_NAMES)
        for i, row in enumerate(zip(*device.data), start=1):
            writer.writerow([i] + list(row))
    print(f"[Export] Fichier créé : {filename}")

    bridge.stop()
    plt.ioff()
    plt.show()


if __name__ == "__main__":
    acquérir_et_afficher()
