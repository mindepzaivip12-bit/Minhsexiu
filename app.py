import os
import math
import traceback
from datetime import datetime
from collections import defaultdict
import requests
from flask import Flask, render_template_string, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

API_URL_MD5 = "https://wtxmd52.macminim6.online/v1/txmd5/lite-sessions?cp=R&cl=R&pf=web&at=bd651d261174ed4a619dfde6c5432fdd"

HISTORY_CACHE = {}
PREDICTION_CACHE = {}
MAX_PREDICTION_CACHE = 2000
RESET_ID = 0
BOOTSTRAP_SESSIONS = []


# =====================================================================
# UTILS
# =====================================================================
def _opp(v):
    return "Xỉu" if v == "Tài" else "Tài"


def get_groups(results):
    if not results:
        return []
    groups = []
    current_val = results[0]
    count = 1
    for r in results[1:]:
        if r == current_val:
            count += 1
        else:
            groups.append(count)
            current_val = r
            count = 1
    groups.append(count)
    return groups


# =====================================================================
# HỆ THỐNG CẦU 5 PHIÊN (NỘI BỘ - KHÔNG HIỂN THỊ RA UI)
# =====================================================================
def analyze_5session_pattern(results):
    if len(results) < 5:
        return None

    seq = results[:5]
    seq_old_to_new = seq[::-1]

    alternations = sum(1 for i in range(4) if seq[i] != seq[i + 1])
    streak = 1
    for r in seq[1:]:
        if r == seq[0]:
            streak += 1
        else:
            break

    pattern_type = "unknown"
    pred = None
    base_conf = 50

    if alternations >= 4:
        pattern_type = "1-1"
        pred = _opp(seq[0])
        base_conf = 72
    elif streak >= 5:
        pattern_type = "Thuan5"
        pred = seq[0]
        base_conf = 65
    elif streak == 4:
        pattern_type = "Thuan4"
        pred = seq[0]
        base_conf = 60
    elif streak == 3:
        pattern_type = "Thuan3"
        pred = seq[0]
        base_conf = 56
    elif alternations == 3:
        pattern_type = "Nghich3"
        pred = _opp(seq[0])
        base_conf = 58
    elif alternations == 2:
        groups = []
        i = 0
        while i < len(seq_old_to_new):
            j = i
            while j + 1 < len(seq_old_to_new) and seq_old_to_new[j] == seq_old_to_new[j + 1]:
                j += 1
            groups.append((seq_old_to_new[i], j - i + 1))
            i = j + 1

        lens = [g[1] for g in groups]
        if lens in ([1, 2, 1, 1], [1, 1, 2, 1], [2, 1, 1, 1], [1, 1, 1, 2]):
            pattern_type = "Nghich_zigzag"
            pred = _opp(seq[0])
            base_conf = 55
        elif lens in ([2, 2, 1], [1, 2, 2], [2, 1, 2]):
            pattern_type = "Dao_212"
            pred = _opp(seq[0])
            base_conf = 57
        else:
            pattern_type = "Honhop"
            pred = seq[0]
            base_conf = 52
    else:
        pattern_type = "Honhop"
        pred = seq[0]
        base_conf = 52

    if pred == seq[0]:
        base_conf -= 2
    else:
        base_conf += 1

    conf = int(max(50, min(88, base_conf)))

    return {
        "pattern_type": pattern_type,
        "prediction": pred,
        "confidence": conf,
        "alternations": alternations,
        "streak": streak,
        "sequence": "".join("T" if r == "Tài" else "X" for r in seq_old_to_new),
    }


# =====================================================================
# PREDICTOR CHÍNH
# =====================================================================
def predict_5session(results):
    n = len(results)

    if n == 0:
        return "Tài", 50, "empty"
    if n == 1:
        return _opp(results[0]), 52, "1_opp"
    if n == 2:
        if results[0] == results[1]:
            return _opp(results[0]), 55, "2_opp"
        return results[0], 52, "2_momentum"
    if n == 3:
        if results[0] != results[1] and results[1] != results[2]:
            return _opp(results[0]), 58, "3_alt"
        if results[0] == results[1] == results[2]:
            return results[0], 56, "3_streak"
        return results[0], 53, "3_mixed"
    if n == 4:
        alt = sum(1 for i in range(3) if results[i] != results[i + 1])
        if alt >= 3:
            return _opp(results[0]), 62, "4_alt"
        if results[0] == results[1] == results[2] == results[3]:
            return results[0], 58, "4_streak"
        return results[0], 54, "4_mixed"

    analysis = analyze_5session_pattern(results)
    if analysis:
        return analysis["prediction"], analysis["confidence"], analysis["pattern_type"]

    return results[0], 52, "fallback"


def bootstrap_predict(results):
    pred, conf, detail = predict_5session(results)
    return pred, conf, detail


# =====================================================================
# AUTO-RECOVERY
# =====================================================================
def apply_recovery(base_pred, conf, loss_streak):
    if loss_streak < 2:
        return base_pred, conf, False, None
    if loss_streak >= 5:
        return _opp(base_pred), min(90, conf + 15), True, "fl
