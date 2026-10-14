"""
Traitement du signal pour le projet mini-golf.

Entrée  : trames brutes du Bitalino (entiers ADC 0–65535)
Sortie  : messages typés pour Unity

    type = "calibration_progress"        (1×/seconde pendant la calibration repos)
        progress    0–1
        elapsed_sec int
        total_sec   int

    type = "calibration_complete"        (1× à la fin de la calibration repos)
        hr_rest     bpm
        hr_label    str     "basse" / "normale" / "élevée" / "très élevée"
        hrv_rest    ms      RMSSD au repos
        resp_rest   bpm
        resp_label  str     "lente" / "normale" / "rapide"
        eda_baseline float  range EDA au repos (p90-p10)

    type = "data"                        (chaque trame après la calibration)
        shot_start              bool    (True 1 frame au début de la contraction)
        shot_end                bool    (True 1 frame à la fin du relâchement)
        stress                  0–1
        heart_rate              bpm
        heart_rate_variability  ms (RMSSD)
        breath_rate             bpm
        breath_amp_min          amplitude min détectée (fenêtre glissante)
        breath_amp_max          amplitude max détectée (fenêtre glissante)
        eda_level               niveau EDA moyen (fenêtre 10s)

Fonctionnement :
    1. 60s au repos    → calibration_complete (seuil EMG calculé automatiquement)
    2. Données temps réel → data (100 Hz, limité à 10 Hz côté bridge)

Stress (basé sur WESAD, Schmidt et al. 2018) :
    stress = 0.35 * hr_stress + 0.30 * hrv_stress + 0.20 * eda_stress + 0.15 * resp_stress
"""

import math
from collections import deque

# ── Paramètres ─────────────────────────────────────────────────────────
FREQUENCY          = 100   # Hz
CALIBRATION_SEC    = 60    # secondes de repos pour établir les baselines


EMG_RELEASE_FRAMES    = 20   # frames sous le seuil de relâchement pour déclencher le tir (0.2s)
EMG_REFRACTORY_FRAMES = 100  # frames d'insensibilité après un tir (1s)

EDA_SLOW_SEC          = 60   # buffer pour supprimer le drift d'électrode
EDA_CHECK_SEC         = 5    # buffer pour détecter si branché
EDA_SCR_REFERENCE     = 50   # amplitude phasique (ADC) = stress maximal
EDA_NOT_CONNECTED_STD = 5    # std < seuil → capteur non branché
PPG_WINDOW_SEC     = 5
RESP_WINDOW_SEC    = 8
ACC_WINDOW_FRAMES  = 50   # 0.5s à 100Hz
ACC_MOVEMENT_MAX   = 1.7  # score normalisé → 1.0

# ── Classe principale ───────────────────────────────────────────────────

class SignalProcessor:

    def __init__(self, frequency=FREQUENCY, calibration_sec=CALIBRATION_SEC):
        self.freq = frequency
        self.calibrated = False
        self._cal_sec    = calibration_sec
        self._cal_count  = 0
        self._cal_target = frequency * calibration_sec

        # Buffers de calibration repos
        self._cal_emg  = []
        self._cal_eda  = []
        # self._cal_accx = []   # ACC désactivé
        # self._cal_accz = []

        # Baselines repos
        self._eda_baseline       = 1.0
        self._eda_baseline_range = 1.0
        # ── ACC (mouvement) ──────────────────────────────────────────────
        self._acc_buf_x = deque(maxlen=ACC_WINDOW_FRAMES)
        self._acc_buf_z = deque(maxlen=ACC_WINDOW_FRAMES)

        # Baselines FC / HRV / resp
        self._hr_rest   = 70.0
        self._hrv_rest  = 0.05
        self._resp_rest = 15.0

        self._cal_hr_samples = []

        # ── EMG ─────────────────────────────────────────────────────────
        self._emg_threshold         = 0.0   # calculé à la fin de la calibration
        self._emg_release_threshold = 0.0   # mean + moitié de la marge du seuil
        self._emg_contracting       = False
        self._emg_below_frames      = 0
        self._emg_refractory        = 0

        # ── EDA ─────────────────────────────────────────────────────────
        self._eda_short_buf  = deque(maxlen=frequency * EDA_CHECK_SEC)
        self._eda_slow_buf   = deque(maxlen=frequency * EDA_SLOW_SEC)
        self._eda_phasic_buf = deque(maxlen=frequency * EDA_CHECK_SEC)
        self._eda_scr_amp    = 0.0

        # ── PPG (pouls + HRV) ───────────────────────────────────────────
        self._ppg_buf                  = deque(maxlen=frequency * PPG_WINDOW_SEC)
        self._ppg_rising               = False
        self._ppg_peaks                = deque(maxlen=20)
        self._ppg_last_peak_frame      = 0
        self._heart_rate_bpm           = 70.0
        self._rr_intervals             = deque(maxlen=20)

        # ── RESP ─────────────────────────────────────────────────────────
        self._resp_buf             = deque(maxlen=frequency * RESP_WINDOW_SEC)
        self._resp_rising          = False
        self._resp_peaks           = deque(maxlen=10)
        self._resp_last_peak_frame = 0
        self._breath_rate          = 15.0
        self._resp_amp_min         = 0.0
        self._resp_amp_max         = 0.0

        self._frame_count = 0

    # ───────────────────────────────────────────────────────────────────
    def update(self, frame: dict) -> dict | None:
        self._frame_count += 1

        if not self.calibrated:
            return self._run_calibration(frame)

        shot_start, shot_end = self._process_emg(frame["emg"])
        movement       = self._process_acc(frame["acc_x"], frame["acc_z"])
        heart_rate     = self._process_ppg(frame["ppg"])
        breath_rate    = self._process_resp(frame["resp"])
        stress         = self._process_stress(frame["eda"], heart_rate, breath_rate)
        hrv_ms         = _rmssd(list(self._rr_intervals)) * 1000

        return {
            "type":       "data",
            "shot_start": shot_start,
            "shot_end":   shot_end,
            "movement":   round(movement, 3),
            # "shot_power":           supprimé — recalibrer d'abord
            # "aim_angle":            supprimé — ACC désactivé
            "stress":                 round(stress, 3),
            "heart_rate":             round(heart_rate, 1),
            "heart_rate_variability": round(hrv_ms, 1),
            "breath_rate":            round(breath_rate, 1),
            "breath_amp_min":         round(self._resp_amp_min, 1),
            "breath_amp_max":         round(self._resp_amp_max, 1),
            "eda_level":              round(_mean(self._eda_short_buf), 1),
            "eda_scr_amp":            round(self._eda_scr_amp, 1),
        }

    # ── Calibration repos ───────────────────────────────────────────────
    def _run_calibration(self, frame) -> dict | None:
        self._cal_emg.append(frame["emg"])
        self._cal_eda.append(frame["eda"])
        # self._cal_accx.append(frame["acc_x"])   # ACC désactivé
        # self._cal_accz.append(frame["acc_z"])
        self._cal_count += 1

        self._process_ppg(frame["ppg"])
        self._process_resp(frame["resp"])

        if self._cal_count % self.freq != 0:
            return None

        self._cal_hr_samples.append(self._heart_rate_bpm)

        elapsed  = self._cal_count // self.freq
        progress = self._cal_count / self._cal_target

        if self._cal_count >= self._cal_target:
            emg_mean = _mean(self._cal_emg)
            emg_std  = math.sqrt(sum((v - emg_mean)**2 for v in self._cal_emg) / len(self._cal_emg))
            self._emg_threshold         = emg_mean + 20 * emg_std
            self._emg_release_threshold = emg_mean + 0.5 * 20 * emg_std
            self._eda_baseline       = _mean(self._cal_eda) or 1.0
            self._eda_baseline_range = _range90(self._cal_eda) or 1.0
            for v in self._cal_eda:
                self._eda_slow_buf.append(v)
            # self._acc_x_neutral = _mean(self._cal_accx)   # ACC désactivé
            # self._acc_z_neutral = _mean(self._cal_accz)
            self._hr_rest   = _mean(self._cal_hr_samples) or 70.0
            self._hrv_rest  = _rmssd(list(self._rr_intervals)) or 0.05
            self._resp_rest = self._breath_rate
            self.calibrated = True

            print(
                f"[Calibration OK] "
                f"FC repos={self._hr_rest:.0f}bpm ({_hr_label(self._hr_rest)})  "
                f"HRV repos={self._hrv_rest*1000:.0f}ms  "
                f"Resp repos={self._resp_rest:.1f}bpm ({_resp_label(self._resp_rest)})  "
                f"EDA range={self._eda_baseline_range:.1f}  "
                f"EMG seuil tir={self._emg_threshold:.0f} (moy={emg_mean:.0f}, std={emg_std:.1f})"
            )
            return {
                "type":         "calibration_complete",
                "hr_rest":      round(self._hr_rest, 1),
                "hr_label":     _hr_label(self._hr_rest),
                "hrv_rest":     round(self._hrv_rest * 1000, 1),
                "resp_rest":    round(self._resp_rest, 1),
                "resp_label":   _resp_label(self._resp_rest),
                "eda_baseline": round(self._eda_baseline_range, 1),
            }

        print(f"[Calibration] {elapsed}s / {self._cal_sec}s  ({int(progress * 100)}%)")
        return {
            "type":        "calibration_progress",
            "progress":    round(progress, 2),
            "elapsed_sec": elapsed,
            "total_sec":   self._cal_sec,
        }

    # ── EMG → tir ───────────────────────────────────────────────────────
    def _process_emg(self, raw) -> tuple[bool, bool]:
        if self._emg_refractory > 0:
            self._emg_refractory -= 1
            return False, False

        shot_start = False
        shot_end   = False

        if raw > self._emg_threshold:
            if not self._emg_contracting:
                shot_start = True
            self._emg_contracting  = True
            self._emg_below_frames = 0
        elif self._emg_contracting and raw < self._emg_release_threshold:
            self._emg_below_frames += 1
            if self._emg_below_frames >= EMG_RELEASE_FRAMES:
                self._emg_contracting  = False
                self._emg_below_frames = 0
                self._emg_refractory   = EMG_REFRACTORY_FRAMES
                shot_end = True

        return shot_start, shot_end

    # ── ACC → intensité de mouvement ────────────────────────────────────
    def _process_acc(self, raw_x, raw_z) -> float:
        self._acc_buf_x.append(raw_x)
        self._acc_buf_z.append(raw_z)
        if len(self._acc_buf_x) < 2:
            return 0.0
        buf_x = list(self._acc_buf_x)
        buf_z = list(self._acc_buf_z)
        n  = len(buf_x)
        mx = sum(buf_x) / n
        mz = sum(buf_z) / n
        std_x = math.sqrt(sum((v - mx) ** 2 for v in buf_x) / n)
        std_z = math.sqrt(sum((v - mz) ** 2 for v in buf_z) / n)
        score = math.sqrt(std_x ** 2 + std_z ** 2)
        return min(1.0, score / ACC_MOVEMENT_MAX)

    # ── PPG → fréquence cardiaque + HRV ─────────────────────────────────
    def _process_ppg(self, raw):
        self._ppg_buf.append(raw)

        if len(self._ppg_buf) < 10:
            return self._heart_rate_bpm

        buf_list   = list(self._ppg_buf)
        buf_sorted = sorted(buf_list)
        n          = len(buf_sorted)
        median_ppg = buf_sorted[n // 2]
        amp_ppg    = buf_sorted[int(n * 0.9)] - buf_sorted[int(n * 0.1)]
        mean_ppg   = sum(buf_list) / n
        std_ppg    = math.sqrt(sum((v - mean_ppg) ** 2 for v in buf_list) / n)
        if std_ppg < 20:
            return 0.0
        peak_threshold = median_ppg + 0.4 * amp_ppg

        if raw > peak_threshold and not self._ppg_rising:
            self._ppg_rising = True
        elif raw < peak_threshold and self._ppg_rising:
            self._ppg_rising = False
            frames_since_last = self._frame_count - self._ppg_last_peak_frame
            if frames_since_last < self.freq * 0.35:
                return self._heart_rate_bpm
            self._ppg_last_peak_frame = self._frame_count
            t = self._frame_count / self.freq

            if self._ppg_peaks:
                interval = t - self._ppg_peaks[-1]
                if 0.4 < interval < 1.5:
                    self._ppg_peaks.append(t)
                    self._rr_intervals.append(interval)
                    if len(self._ppg_peaks) >= 2:
                        intervals = [self._ppg_peaks[i+1] - self._ppg_peaks[i]
                                     for i in range(len(self._ppg_peaks)-1)]
                        self._heart_rate_bpm = 60.0 / _mean(intervals)
            else:
                self._ppg_peaks.append(t)

        return self._heart_rate_bpm

    # ── RESP → fréquence respiratoire + amplitude ────────────────────────
    def _process_resp(self, raw):
        self._resp_buf.append(raw)

        if len(self._resp_buf) < 20:
            return self._breath_rate

        buf_list = list(self._resp_buf)
        n        = len(buf_list)
        mean_resp = sum(buf_list) / n
        std_resp  = math.sqrt(sum((v - mean_resp) ** 2 for v in buf_list) / n)
        amp_resp  = std_resp
        threshold = mean_resp + 0.8 * std_resp

        if self._frame_count % 100 == 0:
            print(f"[RESP debug] raw={raw}  mean={mean_resp:.0f}  std={std_resp:.1f}  thr={threshold:.0f}  rising={self._resp_rising}  rate={self._breath_rate:.1f}")

        if raw > threshold and not self._resp_rising:
            self._resp_rising = True
        elif raw < threshold and self._resp_rising:
            self._resp_rising = False
            frames_since_last = self._frame_count - self._resp_last_peak_frame
            if frames_since_last < self.freq * 1.0:
                return self._breath_rate
            self._resp_last_peak_frame = self._frame_count
            t = self._frame_count / self.freq

            if amp_resp > 0:
                self._resp_amp_max = max(self._resp_amp_max, amp_resp)
                self._resp_amp_min = amp_resp if self._resp_amp_min == 0.0 \
                                     else min(self._resp_amp_min, amp_resp)

            if self._resp_peaks:
                interval = t - self._resp_peaks[-1]
                if 1.0 < interval < 10.0:
                    self._resp_peaks.append(t)
                    if len(self._resp_peaks) >= 2:
                        intervals = [self._resp_peaks[i+1] - self._resp_peaks[i]
                                     for i in range(len(self._resp_peaks)-1)]
                        self._breath_rate = 60.0 / _mean(intervals)
            else:
                self._resp_peaks.append(t)

        return self._breath_rate

    # ── EDA → composante phasique (SCR) ─────────────────────────────────
    def _process_eda(self, raw) -> float:
        self._eda_short_buf.append(raw)
        self._eda_slow_buf.append(raw)

        # Capteur non branché : signal plat
        if len(self._eda_short_buf) == self._eda_short_buf.maxlen:
            short_list = list(self._eda_short_buf)
            mean_s = sum(short_list) / len(short_list)
            std_s  = math.sqrt(sum((v - mean_s) ** 2 for v in short_list) / len(short_list))
            if std_s < EDA_NOT_CONNECTED_STD:
                return 0.0

        slow_mean = sum(self._eda_slow_buf) / len(self._eda_slow_buf)
        phasic    = raw - slow_mean
        self._eda_phasic_buf.append(phasic)

        if len(self._eda_phasic_buf) < self.freq * 2:
            return 0.0

        self._eda_scr_amp = max(0.0, max(self._eda_phasic_buf))
        return min(1.0, self._eda_scr_amp / EDA_SCR_REFERENCE)

    # ── Stress combiné (WESAD-inspired) ─────────────────────────────────
    def _process_stress(self, raw_eda, heart_rate, breath_rate):
        eda_stress = self._process_eda(raw_eda)

        hr_stress = min(1.0, max(0.0,
            (heart_rate - self._hr_rest) / (self._hr_rest * 0.25)))

        hrv_now    = _rmssd(list(self._rr_intervals))
        hrv_stress = min(1.0, max(0.0,
            1.0 - hrv_now / (self._hrv_rest * 0.5))) if self._hrv_rest > 0 else 0.0

        resp_stress = min(1.0, max(0.0,
            (breath_rate - self._resp_rest) / (self._resp_rest * 0.25)))

        return (0.35 * hr_stress
              + 0.30 * hrv_stress
              + 0.20 * eda_stress
              + 0.15 * resp_stress)


# ── Fonctions utilitaires ───────────────────────────────────────────────

def _rms(values) -> float:
    if not values:
        return 0.0
    return math.sqrt(sum(v * v for v in values) / len(values))

def _rms_ac(values) -> float:
    """RMS centré (écart-type) — ignore l'offset DC du capteur."""
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))

def _mean(values) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)

def _range90(values) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    return s[int(n * 0.9)] - s[int(n * 0.1)]

def _rmssd(intervals) -> float:
    if len(intervals) < 2:
        return 0.0
    diffs = [intervals[i+1] - intervals[i] for i in range(len(intervals)-1)]
    return math.sqrt(sum(d * d for d in diffs) / len(diffs))

def _hr_label(bpm: float) -> str:
    if bpm < 55:  return "basse"
    if bpm < 80:  return "normale"
    if bpm < 100: return "élevée"
    return "très élevée"

def _resp_label(bpm: float) -> str:
    if bpm < 10:  return "lente"
    if bpm <= 20: return "normale"
    return "rapide"
