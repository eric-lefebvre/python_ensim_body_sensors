"""
Client de test WebSocket pour acquisition.py (tous capteurs).
Lance acquisition.py d'abord, puis ce script dans un autre terminal.

    python test_client.py
"""

import asyncio
import json
import websockets

URI = "ws://localhost:8765"


def _bar(value: float, width: int = 20, fill="█", empty="░") -> str:
    n = round(value * width)
    return fill * n + empty * (width - n)


async def main():
    print(f"[TestClient] Connexion à {URI} ...")
    try:
        async with websockets.connect(URI) as ws:
            print("[TestClient] Connecté.\n")
            async for raw in ws:
                msg = json.loads(raw)
                t   = msg.get("type", "data")

                if t == "calibration_progress":
                    pct = int(msg["progress"] * 100)
                    bar = _bar(msg["progress"])
                    print(f"\r  [{bar}] {msg['elapsed_sec']:2d}s / {msg['total_sec']}s  {pct:3d}%",
                          end="", flush=True)

                elif t == "calibration_complete":
                    print("\n")
                    print("╔════════════════════════════════════════════╗")
                    print("║           CALIBRATION TERMINÉE             ║")
                    print("╠════════════════════════════════════════════╣")
                    print(f"║  FC au repos   : {msg['hr_rest']:5.1f} bpm  ({msg['hr_label']:<14})║")
                    print(f"║  HRV au repos  : {msg.get('hrv_rest', 0.0):5.1f} ms                  ║")
                    print(f"║  Resp au repos : {msg['resp_rest']:5.1f} bpm  ({msg['resp_label']:<14})║")
                    print(f"║  EDA baseline  : {msg['eda_baseline']:5.1f}                       ║")
                    print("╠════════════════════════════════════════════╣")
                    print("║           ► Prêt à jouer ◄                 ║")
                    print("╚════════════════════════════════════════════╝")
                    print()

                elif t == "data":
                    if msg["shot_start"]:
                        print(f"\n  ► CONTRACTION")
                    elif msg["shot_end"]:
                        print(f"\n  ★ TIR !  stress={msg['stress']:.2f}")
                    else:
                        stress_bar = _bar(msg["stress"])
                        print(
                            f"\r  stress=[{stress_bar}] {msg['stress']:.2f}"
                            f"  FC={msg['heart_rate']:5.1f}bpm"
                            f"  HRV={msg.get('heart_rate_variability', 0.0):5.1f}ms"
                            f"  resp={msg['breath_rate']:4.1f}bpm"
                            f"  EDA={msg.get('eda_level', 0.0):6.1f}  ",
                            end="", flush=True,
                        )

    except ConnectionRefusedError:
        print("[TestClient] Impossible de se connecter.")
        print("  → Lance d'abord acquisition.py dans un autre terminal.")
    except KeyboardInterrupt:
        print("\n[TestClient] Arrêt.")


if __name__ == "__main__":
    asyncio.run(main())
