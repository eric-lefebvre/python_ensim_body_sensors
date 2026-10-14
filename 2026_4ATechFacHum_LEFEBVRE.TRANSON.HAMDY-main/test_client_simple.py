"""
Client de test WebSocket pour acquisition_simple.py (1+ capteur).
Lance acquisition_simple.py d'abord, puis ce script dans un autre terminal.

    python test_client_simple.py
"""

import asyncio
import json
import websockets

URI = "ws://localhost:8765"


def _bar(value: float, width: int = 20, fill="█", empty="░") -> str:
    n = round(max(0.0, min(1.0, value)) * width)
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
                    bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
                    print(f"\r  [{bar}] {msg['elapsed_sec']:2d}s / {msg['total_sec']}s  {pct:3d}%",
                          end="", flush=True)

                elif t == "calibration_complete":
                    print("\n")
                    print("╔══════════════════════════════════════╗")
                    print("║         CALIBRATION TERMINÉE         ║")
                    print("╠══════════════════════════════════════╣")
                    for ch, b in msg["baselines"].items():
                        rate = b.get("rate", 0.0)
                        amp  = b.get("amp",  0.0)
                        print(f"║  {ch:<6}  repos : {rate:4.1f} cyc/min  amp={amp:<6.0f} ║")
                    print("╠══════════════════════════════════════╣")
                    print("║         ► Prêt à jouer ◄             ║")
                    print("╚══════════════════════════════════════╝")
                    print()

                elif t == "data":
                    channels = [k for k in msg if k != "type" and not k.endswith(("_rate", "_force"))]
                    lines = []
                    for ch in channels:
                        rate  = msg.get(f"{ch}_rate",  0.0)
                        force = msg.get(f"{ch}_force", 0.0)
                        bar   = _bar(force)
                        lines.append(f"{ch}: {rate:5.1f} cyc/min  force=[{bar}] {force:.2f}")
                    print(f"\r  {'  |  '.join(lines)}  ", end="", flush=True)

    except ConnectionRefusedError:
        print("[TestClient] Impossible de se connecter.")
        print("  → Lance d'abord acquisition_simple.py dans un autre terminal.")
    except KeyboardInterrupt:
        print("\n[TestClient] Arrêt.")


if __name__ == "__main__":
    asyncio.run(main())
