# Mini-Golf Adaptatif par Biofeedback Physiologique

Jeu de mini-golf Unity dont la difficulté s'adapte en temps réel à l'état physiologique du joueur, mesuré via une carte BITalino.

## Principe

Les capteurs mesurent le stress du joueur → un score [0–1] est calculé → Unity adapte le vent, le tremblement de balle et le chrono.

## Prérequis

- Python **3.13 64-bit** (Windows)
- BITalino Core BT couplé en Bluetooth (`98:D3:91:FD:69:DD`)
- Unity (projet séparé)

## GIT UNITY

https://github.com/mayahamdy/2026_4ATechFacHum_LEFEBVRE.TRANSON.HAMDY

## Installation

```powershell
python -m venv mon_env
.\mon_env\Scripts\Activate.ps1
pip install matplotlib websockets
```

> Le module `plux` est un binaire précompilé dans `Win64_313/` — il ne s'installe pas via pip.

## Lancement

```powershell
.\mon_env\Scripts\Activate.ps1
python acquisition.py
```

Arrêt propre : **Ctrl+C**

Unity se connecte automatiquement sur `ws://localhost:8765`.

## Capteurs (ports A1–A6)

| Port | Capteur | Mesure |
|------|---------|--------|
| A1   | RESP    | Fréquence respiratoire |
| A2   | EMG     | Déclenchement du tir |
| A3   | EDA     | Conductance cutanée (stress) |
| A4   | PPG     | Fréquence cardiaque, HRV |
| A5   | ACC X   | Tremblement |
| A6   | ACC Z   | Tremblement |

## Structure

```
acquisition.py        # Point d'entrée
signal_processor.py   # Traitement du signal (calibration + score de stress)
websocket_bridge.py   # Serveur WebSocket → Unity
Win64_313/plux.pyd    # Extension C PLUX (ne pas modifier)
documentation/        # Rapport LNCS + bibliographie
```

## Auteurs

Transon Paul, Hamdy Maya, Lefebvre Eric — ENSIM Le Mans, 2026
