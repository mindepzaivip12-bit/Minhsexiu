import os
import math
import time
import traceback
from datetime import datetime
from collections import defaultdict
import requests
from flask import Flask, render_template_string, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

API_URL_MD5 = "https://wtx.tele68.com/v1/tx/lite-sessions?cp=R&cl=R&pf=web&at=85e48cf010cfd2ac6b4ee4a16a30a9f0"

HISTORY_CACHE = {}
PREDICTION_CACHE = {}
MAX_PREDICTION_CACHE = 3000
RESET_ID = 0
BOOTSTRAP_SESSIONS = []
LAST_FETCH_OK_TS = 0
FETCH_RETRY = 2
FETCH_TIMEOUT = 6


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


def _entropy(seq):
    if not seq:
        return 0.0
    t = seq.count("Tài")
    x = len(seq) - t
    p = t / len(seq)
    q = x / len(seq)
    e = 0.0
    if p > 0:
        e -= p * math.log2(p)
    if q > 0:
        e -= q * math.log2(q)
    return e  # 0..1


# =====================================================================
# HỆ THỐNG CẦU 5 PHIÊN (NỘI BỘ)
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
        base_conf = 74
    elif streak >= 5:
        pattern_type = "Thuan5"
        pred = seq[0]
        base_conf = 66
    elif streak == 4:
        pattern_type = "Thuan4"
        pred = seq[0]
        base_conf = 61
    elif streak == 3:
        pattern_type = "Thuan3"
        pred = seq[0]
        base_conf = 57
    elif alternations == 3:
        pattern_type = "Nghich3"
        pred = _opp(seq[0])
        base_conf = 59
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
            base_conf = 56
        elif lens in ([2, 2, 1], [1, 2, 2], [2, 1, 2]):
            pattern_type = "Dao_212"
            pred = _opp(seq[0])
            base_conf = 58
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

    conf = int(max(50, min(90, base_conf)))

    return {
        "pattern_type": pattern_type,
        "prediction": pred,
        "confidence": conf,
        "alternations": alternations,
        "streak": streak,
        "sequence": "".join("T" if r == "Tài" else "X" for r in seq_old_to_new),
    }


# =====================================================================
# THUẬT TOÁN BỔ TRỢ (CHẠY NGẦM)
# =====================================================================
def algo_markov1_soft(results):
    if len(results) < 15:
        return None
    tr = {"Tài": {"Tài": 0, "Xỉu": 0}, "Xỉu": {"Tài": 0, "Xỉu": 0}}
    for i in range(min(len(results), 40) - 1):
        tr[results[i + 1]][results[i]] += 1
    last = results[0]
    tot = sum(tr[last].values())
    if tot < 5:
        return None
    p = tr[last]["Tài"] / tot
    if p >= 0.68:
        return ("Tài", 12, "markov1")
    if p <= 0.32:
        return ("Xỉu", 12, "markov1")
    return None


def algo_markov2_soft(results):
    if len(results) < 20:
        return None
    key = (results[1], results[0])
    tr = defaultdict(lambda: {"Tài": 0, "Xỉu": 0})
    for i in range(min(len(results), 50) - 2):
        k = (results[i + 2], results[i + 1])
        tr[k][results[i]] += 1
    d = tr.get(key)
    if not d:
        return None
    tot = d["Tài"] + d["Xỉu"]
    if tot < 5:
        return None
    p = d["Tài"] / tot
    if p >= 0.73:
        return ("Tài", 14, "markov2")
    if p <= 0.27:
        return ("Xỉu", 14, "markov2")
    return None


def algo_pressure_balance(results):
    if len(results) < 15:
        return None
    w = results[:15]
    tc = w.count("Tài")
    if tc >= 13:
        return ("Xỉu", 11, "pressure_tai")
    if tc <= 2:
        return ("Tài", 11, "pressure_xiu")
    return None


def algo_momentum_controlled(results):
    if len(results) < 10:
        return None
    streak = 1
    for r in results[1:]:
        if r == results[0]:
            streak += 1
        else:
            break
    if streak < 3 or streak > 7:
        return None
    ratio = results[:10].count(results[0]) / 10
    if ratio >= 0.65:
        return (results[0], 10, "momentum_hold")
    if ratio <= 0.35:
        return (_opp(results[0]), 10, "momentum_break")
    return None


def algo_pattern_3history(results):
    if len(results) < 25:
        return None
    key = tuple(results[:3])
    tai_next = xiu_next = 0
    for i in range(min(len(results), 60) - 3):
        if tuple(results[i:i + 3]) == key:
            nxt = results[i + 3]
            if nxt == "Tài":
                tai_next += 1
            else:
                xiu_next += 1
    tot = tai_next + xiu_next
    if tot < 3:
        return None
    p = tai_next / tot
    if p >= 0.72:
        return ("Tài", 13, "pattern3")
    if p <= 0.28:
        return ("Xỉu", 13, "pattern3")
    return None


def algo_pattern_4history(results):
    if len(results) < 30:
        return None
    key = tuple(results[:4])
    tai_next = xiu_next = 0
    for i in range(min(len(results), 60) - 4):
        if tuple(results[i:i + 4]) == key:
            nxt = results[i + 4]
            if nxt == "Tài":
                tai_next += 1
            else:
                xiu_next += 1
    tot = tai_next + xiu_next
    if tot < 3:
        return None
    p = tai_next / tot
    if p >= 0.75:
        return ("Tài", 15, "pattern4")
    if p <= 0.25:
        return ("Xỉu", 15, "pattern4")
    return None


def algo_alternation_long(results):
    if len(results) < 6:
        return None
    alt = 0
    for i in range(min(len(results) - 1, 14)):
        if results[i] != results[i + 1]:
            alt += 1
        else:
            break
    if alt >= 7:
        return (_opp(results[0]), 16, "alt_7plus")
    if alt >= 6:
        return (_opp(results[0]), 14, "alt_6")
    if alt >= 5:
        return (_opp(results[0]), 11, "alt_5")
    return None


def algo_entropy_filter(results):
    """Bộ lọc nhiễu: entropy thấp (1 chiều) -> bám xu hướng, entropy cao -> giảm tin cậy."""
    if len(results) < 20:
        return None
    e = _entropy(results[:20])
    if e <= 0.55:
        # quá 1 chiều -> đảo
        t = results[:20].count("Tài")
        return (_opp("Tài" if t > 10 else "Xỉu"), 9, "entropy_skew")
    if e >= 0.98:
        # cân bằng tuyệt đối -> theo nhịp gần nhất
        return (results[0], 8, "entropy_balanced")
    return None


def algo_reversal_pressure(results):
    """Cầu đảo khi có 3-4 phiên liên tiếp cùng cửa nhưng tổng 12 phiên lệch mạnh."""
    if len(results) < 12:
        return None
    streak = 1
    for r in results[1:]:
        if r == results[0]:
            streak += 1
        else:
            break
    if streak not in (3, 4):
        return None
    w = results[:12]
    tc = w.count("Tài")
    if tc >= 9 and results[0] == "Tài":
        return ("Xỉu", 12, "reversal_tai")
    if tc <= 3 and results[0] == "Xỉu":
        return ("Tài", 12, "reversal_xiu")
    return None


# =====================================================================
# TỔNG HỢP — CẦU 5 PHIÊN + BỔ TRỢ
# =====================================================================
def _aggregate_support(support):
    st = sx = 0.0
    for p, w, _ in support:
        if p == "Tài":
            st += w
        else:
            sx += w
    total = st + sx
    if total <= 0:
        return None, 0.0
    if st > sx:
        return "Tài", st / total
    if sx > st:
        return "Xỉu", sx / total
    return None, 0.5


def predict_combined(results):
    n = len(results)
    if n == 0:
        return "Tài", 50
    if n == 1:
        return _opp(results[0]), 53
    if n == 2:
        if results[0] == results[1]:
            return _opp(results[0]), 56
        return results[0], 52
    if n == 3:
        if results[0] != results[1] and results[1] != results[2]:
            return _opp(results[0]), 59
        if results[0] == results[1] == results[2]:
            return results[0], 57
        return results[0], 53
    if n == 4:
        alt = sum(1 for i in range(3) if results[i] != results[i + 1])
        if alt >= 3:
            return _opp(results[0]), 61
        if results[0] == results[1] == results[2] == results[3]:
            return results[0], 57
        return results[0], 53

    analysis = analyze_5session_pattern(results)
    base_pred = analysis["prediction"]
    base_conf = analysis["confidence"]

    support = []
    for fn in (algo_markov1_soft, algo_markov2_soft,
               algo_pressure_balance, algo_momentum_controlled,
               algo_pattern_3history, algo_pattern_4history,
               algo_alternation_long, algo_entropy_filter,
               algo_reversal_pressure):
        s = fn(results)
        if s:
            support.append(s)

    # Nếu base đã rất chắc -> giữ, chỉ tăng nhẹ nếu đồng thuận
    if base_conf >= 66:
        if support:
            sp, ratio = _aggregate_support(support)
            if sp == base_pred and ratio >= 0.70:
                return base_pred, min(88, base_conf + 4)
            if sp and sp != base_pred and ratio >= 0.85:
                # tín hiệu bổ trợ phản đối rất mạnh -> giảm nhẹ tin cậy
                return base_pred, max(50, base_conf - 5)
        return base_pred, base_conf

    if not support:
        return base_pred, base_conf

    support_pred, support_ratio = _aggregate_support(support)
    if support_pred is None:
        return base_pred, base_conf

    # Đồng thuận mạnh
    if support_pred == base_pred and support_ratio >= 0.70:
        bonus = int((support_ratio - 0.5) * 40)
        # nếu có >=3 tín hiệu đồng thuận -> boost mạnh
        if len(support) >= 3:
            bonus += 4
        return base_pred, min(86, base_conf + bonus)

    # Bổ trợ phản đối cực mạnh -> giảm tin cậy
    if support_pred != base_pred and support_ratio >= 0.82:
        return base_pred, max(50, base_conf - 8)

    # Bổ trợ phản đối vừa + base yếu -> chuyển hướng
    if support_pred != base_pred and base_conf < 58 and support_ratio >= 0.72:
        return support_pred, min(76, 55 + int((support_ratio - 0.5) * 30))

    return base_pred, base_conf


def bootstrap_predict(results):
    pred, conf = predict_combined(results)
    return pred, conf, None


# =====================================================================
# AUTO-RECOVERY
# =====================================================================
def apply_recovery(base_pred, conf, loss_streak):
    if loss_streak < 2:
        return base_pred, conf, False, None
    # Recovery cần kết hợp cả conf + streak
    if loss_streak >= 5:
        return _opp(base_pred), min(92, conf + 16), True, "flip_5plus"
    if loss_streak == 4:
        return _opp(base_pred), min(90, conf + 13), True, "flip_4"
    if loss_streak == 3:
        return _opp(base_pred), min(88, conf + 10), True, "flip_3"
    if loss_streak == 2:
        if conf < 70:
            return _opp(base_pred), min(87, conf + 7), True, "flip_2"
        return base_pred, max(50, conf - 10), False, "penalty_2"
    return base_pred, conf, False, None


# =====================================================================
# DATA FETCHER (ỔN ĐỊNH, RETRY, FALLBACK)
# =====================================================================
def _parse_list(data):
    rows = []
    if not isinstance(data, dict):
        return rows
    lst = data.get('list') or data.get('data') or data.get('sessions')
    if not lst or not isinstance(lst, list):
        return rows
    for item in lst:
        try:
            if not isinstance(item, dict):
                continue
            raw = item.get('resultTruyenThong') or item.get('result') or ''
            res = "Tài" if str(raw).upper() == "TAI" else "Xỉu"
            dices = item.get('dices') or item.get('dice') or [0, 0, 0]
            if not isinstance(dices, list) or len(dices) < 3:
                dices = [0, 0, 0]
            dice_str = f"{dices[0]} + {dices[1]} + {dices[2]}"
            total = item.get('point')
            if total is None:
                try:
                    total = int(dices[0]) + int(dices[1]) + int(dices[2])
                except Exception:
                    total = 0
            phien = item.get('id') or item.get('phien')
            if phien is None:
                continue
            rows.append({
                'phien': int(phien),
                'result': res,
                'dice': dice_str,
                'total': total
            })
        except Exception:
            continue
    return rows


def fetch_sessions():
    global LAST_FETCH_OK_TS
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'vi,en;q=0.9',
        'Referer': 'https://wtx.tele68.com/',
    }
    last_err = None
    for attempt in range(FETCH_RETRY):
        try:
            r = requests.get(API_URL_MD5, timeout=FETCH_TIMEOUT, headers=headers)
            if r.status_code == 200:
                try:
                    data = r.json()
                except Exception:
                    # thử parse text
                    import json as _json
                    data = _json.loads(r.text)
                rows = _parse_list(data)
                if rows:
                    LAST_FETCH_OK_TS = time.time()
                    return rows, True
            last_err = f"HTTP {r.status_code}"
        except Exception as e:
            last_err = str(e)
            time.sleep(0.4 * (attempt + 1))
    print(f"[API ERROR]: {last_err}")
    return [], False


# =====================================================================
# PREDICTION CACHE
# =====================================================================
def get_prediction_for(valid, i):
    key = valid[i]['phien']
    if key in PREDICTION_CACHE:
        return PREDICTION_CACHE[key]
    hist = [v['result'] for v in valid[:i]][::-1]
    pred, conf, _ = bootstrap_predict(hist)
    if pred == "-":
        result = ("-", 50, None)
    else:
        is_correct = (pred == valid[i]['result'])
        result = (pred, conf, is_correct)
    if len(PREDICTION_CACHE) > MAX_PREDICTION_CACHE:
        PREDICTION_CACHE.clear()
    PREDICTION_CACHE[key] = result
    return result


# =====================================================================
# LOSS STREAK
# =====================================================================
def compute_loss_streak(valid, max_scan=25):
    if len(valid) < 3:
        return 0
    streak = 0
    end = len(valid) - 1
    start = max(0, end - max_scan)
    for i in range(end, start - 1, -1):
        pred, conf, is_correct = get_prediction_for(valid, i)
        if is_correct is None:
            break
        if is_correct:
            break
        streak += 1
    return streak


# =====================================================================
# HTML TEMPLATE
# =====================================================================
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>LEMINH</title>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/sweetalert2@11"></script>
<style>
    :root {
        --bg-page: #0f172a; --bg-card: #1e293b; --text-main: #f8fafc;
        --text-muted: #94a3b8; --border-color: #334155;
        --tai-color: #38bdf8; --tai-bg: rgba(56, 189, 248, 0.15);
        --xiu-color: #f472b6; --xiu-bg: rgba(244, 114, 182, 0.15);
        --win-color: #34d399; --win-bg: rgba(52, 211, 153, 0.15);
        --lose-color: #f87171; --lose-bg: rgba(248, 113, 113, 0.15);
        --wait-color: #fbbf24; --wait-bg: rgba(251, 191, 36, 0.15);
        --recovery-color: #fb923c; --recovery-bg: rgba(251, 146, 60, 0.15);
    }
    * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Plus Jakarta Sans', sans-serif; }
    body { background: linear-gradient(135deg, #0f172a 0%, #1e1b4b 100%); color: var(--text-main); min-height: 100vh; padding-bottom: 40px; }
    .header { background: rgba(30, 41, 59, 0.85); backdrop-filter: blur(12px); border-bottom: 1px solid var(--border-color); padding: 16px 24px; display: flex; justify-content: space-between; align-items: center; position: sticky; top: 0; z-index: 100; }
    .header-title { font-size: 22px; font-weight: 800; color: #38bdf8; display: flex; align-items: center; gap: 10px; text-shadow: 0 0 20px rgba(56, 189, 248, 0.4); }
    .header-actions { display: flex; gap: 12px; align-items: center; }
    .time-update { font-size: 12px; color: var(--text-muted); font-weight: 600; }
    .btn-refresh { background: var(--bg-card); color: var(--text-main); border: 1px solid var(--border-color); width: 38px; height: 38px; border-radius: 10px; font-weight: 700; font-size: 16px; cursor: pointer; display: flex; align-items: center; justify-content: center; transition: 0.2s; }
    .btn-refresh:hover { background: var(--border-color); transform: rotate(180deg); }
    .btn-reset { background: rgba(248, 113, 113, 0.2); color: var(--lose-color); border: 1px solid var(--lose-color); padding: 8px 16px; border-radius: 10px; font-weight: 700; font-size: 13px; cursor: pointer; transition: 0.2s; }
    .btn-reset:hover { background: var(--lose-color); color: #fff; box-shadow: 0 0 15px rgba(248, 113, 113, 0.4); }
    .stats-bar { max-width: 800px; margin: 24px auto 16px; padding: 0 16px; display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
    .stat-item { background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 14px; padding: 16px 10px; text-align: center; box-shadow: 0 4px 20px rgba(0,0,0,0.2); }
    .stat-label { font-size: 11px; color: var(--text-muted); text-transform: uppercase; font-weight: 700; margin-bottom: 6px; letter-spacing: 0.3px; }
    .stat-value { font-size: 22px; font-weight: 800; color: var(--text-main); }
    .stat-value.win { color: var(--win-color); text-shadow: 0 0 15px rgba(52, 211, 153, 0.4); }
    .stat-value.loss { color: var(--lose-color); text-shadow: 0 0 15px rgba(248, 113, 113, 0.4); }
    .stat-value.rate { color: var(--tai-color); text-shadow: 0 0 15px rgba(56, 189, 248, 0.4); }
    .table-wrapper { max-width: 800px; margin: 0 auto; padding: 0 16px; }
    .table-container { background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 16px; box-shadow: 0 10px 30px rgba(0,0,0,0.3); max-height: 70vh; overflow-y: auto; }
    .data-table { width: 100%; border-collapse: separate; border-spacing: 0; }
    .data-table th { background: #111827; color: var(--text-muted); font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; padding: 16px 12px; text-align: center; border-bottom: 1px solid var(--border-color); position: sticky; top: 0; z-index: 10; }
    .data-table td { padding: 14px 12px; text-align: center; border-bottom: 1px solid var(--border-color); font-size: 14px; font-weight: 600; }
    .data-table tr:last-child td { border-bottom: none; }
    .data-table tr:hover { background: rgba(255, 255, 255, 0.02); }
    .row-next { background: rgba(14, 165, 233, 0.08); border-left: 4px solid var(--tai-color); }
    .row-next.recovery { background: rgba(251, 146, 60, 0.1); border-left: 4px solid var(--recovery-color); }
    .badge { display: inline-block; padding: 6px 14px; border-radius: 20px; font-size: 12px; font-weight: 700; min-width: 70px; text-align: center; }
    .badge-tai { background: var(--tai-bg); color: var(--tai-color); border: 1px solid rgba(56, 189, 248, 0.3); box-shadow: 0 0 10px rgba(56, 189, 248, 0.2); }
    .badge-xiu { background: var(--xiu-bg); color: var(--xiu-color); border: 1px solid rgba(244, 114, 182, 0.3); box-shadow: 0 0 10px rgba(244, 114, 182, 0.2); }
    .badge-empty { background: rgba(255, 255, 255, 0.05); color: var(--text-muted); }
    .recovery-badge { display: inline-block; margin-left: 6px; padding: 2px 8px; border-radius: 8px; font-size: 10px; font-weight: 800; background: var(--recovery-bg); color: var(--recovery-color); border: 1px solid var(--recovery-color); box-shadow: 0 0 10px rgba(251, 146, 60, 0.3); animation: pulse 1.5s infinite; letter-spacing: 0.5px; }
    .status { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; font-weight: 700; padding: 6px 14px; border-radius: 20px; }
    .st-win { background: var(--win-bg); color: var(--win-color); border: 1px solid rgba(52, 211, 153, 0.3); }
    .st-lose { background: var(--lose-bg); color: var(--lose-color); border: 1px solid rgba(248, 113, 113, 0.3); }
    .st-wait { background: var(--wait-bg); color: var(--wait-color); border: 1px solid rgba(251, 191, 36, 0.3); animation: pulse 1.5s infinite; }
    .st-recover { background: var(--recovery-bg); color: var(--recovery-color); border: 1px solid var(--recovery-color); animation: pulse 1.5s infinite; }
    @keyframes pulse { 0% { opacity: 1; } 50% { opacity: 0.5; } 100% { opacity: 1; } }
    .dice-text { font-size: 11px; color: var(--text-muted); display: block; margin-top: 4px; font-weight: 500; }
    .conf-badge { font-size: 11px; color: #38bdf8; background: rgba(56, 189, 248, 0.1); padding: 2px 8px; border-radius: 10px; margin-top: 4px; display: inline-block; font-weight: 700; }
    .conf-badge.high { color: #34d399; background: rgba(52, 211, 153, 0.15); box-shadow: 0 0 10px rgba(52, 211, 153, 0.2); }
    .loading { text-align: center; padding: 40px; color: var(--tai-color); font-weight: 600; }
    @media (max-width: 500px) {
        .data-table th, .data-table td { padding: 10px 6px; font-size: 12px; }
        .badge { padding: 4px 8px; font-size: 11px; min-width: 50px; }
        .header-title { font-size: 18px; }
        .time-update { display: none; }
        .stats-bar { grid-template-columns: repeat(2, 1fr); }
        .stat-value { font-size: 18px; }
    }
</style>
</head>
<body>

<div class="header">
    <div class="header-title">LEMINH</div>
    <div class="header-actions">
        <div class="time-update" id="time-update">--:--:--</div>
        <button class="btn-refresh" onclick="fetchData()">↻</button>
        <button class="btn-reset" onclick="confirmReset()">Reset</button>
    </div>
</div>

<div class="stats-bar">
    <div class="stat-item"><div class="stat-label">Tổng</div><div class="stat-value" id="stat-total">0</div></div>
    <div class="stat-item"><div class="stat-label">Thắng</div><div class="stat-value win" id="stat-win">0</div></div>
    <div class="stat-item"><div class="stat-label">Thua</div><div class="stat-value loss" id="stat-lose">0</div></div>
    <div class="stat-item"><div class="stat-label">Tỷ lệ thắng</div><div class="stat-value rate" id="stat-rate">0%</div></div>
</div>

<div class="table-wrapper">
    <div class="table-container">
        <table class="data-table">
            <thead>
                <tr>
                    <th>Phiên</th>
                    <th>Dự đoán</th>
                    <th>Kết quả</th>
                    <th>Trạng thái</th>
                </tr>
            </thead>
            <tbody id="table-body">
                <tr><td colspan="4" class="loading">Đang kết nối Server...</td></tr>
            </tbody>
        </table>
    </div>
</div>

<script>
let isResetting = false;

function animateNumber(el, target, suffix = '') {
    const start = parseInt(el.innerText.replace(/[^0-9]/g, '')) || 0;
    if (start === target) { el.innerText = target + suffix; return; }
    const duration = 500;
    const startTime = performance.now();
    function update(t) {
        const p = Math.min((t - startTime) / duration, 1);
        const ease = 1 - Math.pow(1 - p, 3);
        el.innerText = Math.floor(start + (target - start) * ease) + suffix;
        if (p < 1) requestAnimationFrame(update);
    }
    requestAnimationFrame(update);
}

async function fetchData() {
    if (isResetting) return;
    try {
        const r = await fetch('/api/history', { cache: 'no-store' });
        const data = await r.json();
        const now = new Date();
        document.getElementById('time-update').innerText = now.toLocaleTimeString('vi-VN');
        renderTable(data.rows || [], data.stats || {});
    } catch (e) { console.error("Lỗi tải dữ liệu:", e); }
}

function renderTable(rows, stats) {
    const tbody = document.getElementById('table-body');
    const total = stats.total || 0;
    const correct = stats.correct || 0;
    const wrong = total - correct;
    animateNumber(document.getElementById('stat-total'), total);
    animateNumber(document.getElementById('stat-win'), correct);
    animateNumber(document.getElementById('stat-lose'), wrong);
    const rate = total > 0 ? (correct / total) * 100 : 0;
    animateNumber(document.getElementById('stat-rate'), Math.round(rate), '%');

    if (!rows.length) {
        tbody.innerHTML = '<tr><td colspan="4" class="loading">Không có dữ liệu. Hãy đợi phiên mới.</td></tr>';
        return;
    }
    let html = '';
    rows.forEach((row) => {
        let trClass = '';
        if (row.is_next) trClass = row.recovery_flipped ? 'class="row-next recovery"' : 'class="row-next"';
        let predHtml = `<span class="badge badge-empty">-</span>`;
        if (row.prediction === "Tài") predHtml = `<span class="badge badge-tai">TÀI</span>`;
        if (row.prediction === "Xỉu") predHtml = `<span class="badge badge-xiu">XỈU</span>`;
        let recBadge = "";
        if (row.recovery_flipped) {
            recBadge = `<span class="recovery-badge" title="Auto-flip do thua ${row.loss_streak || 0} lần">⚡</span>`;
        }
        let confHtml = "";
        if (row.conf && row.prediction !== "-") {
            const cls = row.conf >= 70 ? 'conf-badge high' : 'conf-badge';
            confHtml = `<div class="${cls}">${row.conf}%</div>`;
        }
        let predFull = `<div style="display:flex; flex-direction:column; align-items:center; gap:2px;">${predHtml}${confHtml}${recBadge}</div>`;
        let resultHtml = `<span class="badge badge-empty">-</span>`;
        if (row.result === "Tài") resultHtml = `<span class="badge badge-tai">TÀI</span>`;
        if (row.result === "Xỉu") resultHtml = `<span class="badge badge-xiu">XỈU</span>`;
        let diceDisplay = "";
        if (row.dice !== "? + ? + ?") {
            const p = row.dice.split(" + ");
            const sum = parseInt(p[0]) + parseInt(p[1]) + parseInt(p[2]);
            diceDisplay = `<span class="dice-text">${row.dice} (${sum})</span>`;
        }
        let resultFull = `<div style="display:flex; flex-direction:column; align-items:center;">${resultHtml}${diceDisplay}</div>`;
        let statusHtml = '';
        if (row.is_next) {
            statusHtml = row.recovery_flipped
                ? `<span class="status st-recover">⚡ Đảo chiều</span>`
                : `<span class="status st-wait">Chờ kết quả</span>`;
        } else if (row.is_correct === true) {
            statusHtml = `<span class="status st-win">✔ THẮNG</span>`;
        } else if (row.is_correct === false) {
            statusHtml = `<span class="status st-lose">✖ THUA</span>`;
        } else {
            statusHtml = `<span class="status" style="color:var(--text-muted)">-</span>`;
        }
        html += `<tr ${trClass}><td>#${row.phien}</td><td>${predFull}</td><td>${resultFull}</td><td>${statusHtml}</td></tr>`;
    });
    tbody.innerHTML = html;
}

async function confirmReset() {
    Swal.fire({
        title: 'Xác nhận Reset?', text: "Toàn bộ lịch sử hiển thị sẽ bị xoá và bắt đầu lại từ đầu!",
        icon: 'warning', background: '#1e293b', color: '#f8fafc',
        showCancelButton: true, confirmButtonColor: '#0ea5e9', cancelButtonColor: '#64748b',
        confirmButtonText: 'Đồng ý', cancelButtonText: 'Hủy'
    }).then(async (result) => {
        if (result.isConfirmed) {
            isResetting = true;
            Swal.fire({ title: 'Đang Reset...', allowOutsideClick: false, background: '#1e293b', color: '#f8fafc', didOpen: () => { Swal.showLoading() } });
            try {
                const res = await fetch('/api/reset', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}) });
                const data = await res.json();
                if (data.status === 'success') {
                    Swal.fire({ title: 'Thành công!', text: 'Đã xoá lịch sử và bắt đầu lại.', icon: 'success', background: '#1e293b', color: '#f8fafc', confirmButtonColor: '#0ea5e9' });
                    isResetting = false; fetchData();
                } else { throw new Error(data.message); }
            } catch (error) {
                Swal.fire({title: 'Lỗi!', text: 'Không thể reset: ' + error.message, icon: 'error', background: '#1e293b', color: '#f8fafc'});
                isResetting = false;
            }
        }
    });
}

fetchData();
setInterval(fetchData, 5000);
</script>
</body>
</html>
"""


# =====================================================================
# ROUTES
# =====================================================================
@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)


def _sync_history(raw_rows):
    for row in raw_rows:
        HISTORY_CACHE[row['phien']] = row
    for k in [k for k in HISTORY_CACHE if k < RESET_ID]:
        del HISTORY_CACHE[k]
    return sorted(HISTORY_CACHE.values(), key=lambda x: x['phien'])


@app.route('/api/history')
def history():
    global RESET_ID
    try:
        raw_rows, success = fetch_sessions()
        if not success and not HISTORY_CACHE:
            return jsonify({'error': 'Không thể kết nối API'}), 500
        valid = _sync_history(raw_rows)
        if not valid:
            return jsonify({'rows': [], 'stats': {'total': 0, 'correct': 0}})

        loss_streak = compute_loss_streak(valid)

        out = []
        correct_count = 0
        total_past = 0
        latest_session = valid[-1]
        next_phien = latest_session['phien'] + 1
        history_for_next = [v['result'] for v in valid][::-1]

        base_pred, base_conf, _ = bootstrap_predict(history_for_next)
        next_pred, next_conf, flipped, reason = apply_recovery(base_pred, base_conf, loss_streak)

        out.append({
            'phien': next_phien, 'is_next': True,
            'prediction': next_pred, 'result': 'Chờ...',
            'dice': "? + ? + ?", 'is_correct': None,
            'conf': next_conf,
            'recovery_flipped': flipped,
            'recovery_reason': reason,
            'loss_streak': loss_streak,
            'base_prediction': base_pred if flipped else None,
        })

        scan_limit = min(len(valid), 200)
        for i in range(len(valid) - 1, len(valid) - scan_limit - 1, -1):
            if i < 0:
                break
            current_session = valid[i]
            pred, conf, is_correct = get_prediction_for(valid, i)
            if is_correct is not None:
                total_past += 1
            if is_correct:
                correct_count += 1
            out.append({
                'phien': current_session['phien'], 'is_next': False,
                'prediction': pred, 'result': current_session['result'],
                'dice': current_session['dice'], 'is_correct': is_correct,
                'conf': conf, 'recovery_flipped': False,
            })

        return jsonify({
            'rows': out,
            'stats': {'total': total_past, 'correct': correct_count},
            'loss_streak': loss_streak,
            'recovery_active': flipped,
            'recovery_reason': reason,
            'base_prediction': base_pred,
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/predict')
def predict():
    global RESET_ID
    try:
        raw_rows, success = fetch_sessions()
        if not success and not HISTORY_CACHE:
            return jsonify({'error': 'Không thể kết nối API'}), 500
        valid = _sync_history(raw_rows)
        if not valid:
            return jsonify({'error': 'Chưa có dữ liệu'}), 400
        results = [v['result'] for v in valid][::-1]

        loss_streak = compute_loss_streak(valid)
        base_pred, base_conf, _ = bootstrap_predict(results)
        pred, conf, flipped, reason = apply_recovery(base_pred, base_conf, loss_streak)

        return jsonify({
            'last_phien': valid[-1]['phien'],
            'next_phien': valid[-1]['phien'] + 1,
            'last_result': valid[-1]['result'],
            'last_dice': valid[-1]['dice'],
            'prediction': pred,
            'confidence': conf,
            'base_prediction': base_pred,
            'base_confidence': base_conf,
            'recovery_flipped': flipped,
            'recovery_reason': reason,
            'loss_streak': loss_streak,
            'total_sessions': len(valid),
            'last_5': results[:5],
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/lc79md5')
def betvipmd5():
    global RESET_ID
    try:
        raw_rows, success = fetch_sessions()
        if not success and not HISTORY_CACHE:
            return jsonify({'error': 'Không thể kết nối API'}), 500
        valid = _sync_history(raw_rows)
        if not valid:
            return jsonify({'error': 'Chưa có dữ liệu'}), 400

        results = [v['result'] for v in valid][::-1]

        loss_streak = compute_loss_streak(valid)
        base_pred, base_conf, _ = bootstrap_predict(results)
        pred, conf, flipped, reason = apply_recovery(base_pred, base_conf, loss_streak)

        conf_str = f"{conf:.1f}".replace(".", ",") + "%"

        last = valid[-1]
        dice_parts = last['dice'].split(" + ")
        dice_short = "-".join(dice_parts) if len(dice_parts) == 3 else last['dice']
        ket_qua_str = f"{last['result'].lower()} {dice_short}"

        groups = get_groups(results)
        lich_su = "-" + "-".join(str(g) for g in groups[:4])

        next_phien = last['phien'] + 1

        return jsonify({
            'id': '@cskhleminh',
            'phien': str(next_phien),
            'ket_qua': ket_qua_str,
            'du_doan': pred,
            'do_tin_cay': conf_str,
            'lich_su': lich_su,
            'base_du_doan': base_pred if flipped else None,
            'auto_flip': flipped,
            'chuoi_thua': loss_streak,
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/reset', methods=['POST'])
def reset_api():
    global RESET_ID, HISTORY_CACHE, PREDICTION_CACHE, BOOTSTRAP_SESSIONS
    try:
        raw_rows, success = fetch_sessions()
        if success and raw_rows:
            valid = sorted(raw_rows, key=lambda x: x['phien'])
            if valid:
                RESET_ID = valid[-1]['phien']
                last_session = valid[-1]
                HISTORY_CACHE = {last_session['phien']: last_session}
                BOOTSTRAP_SESSIONS = [last_session]
                PREDICTION_CACHE = {}
                return jsonify({
                    'status': 'success',
                    'reset_to': RESET_ID,
                    'bootstrap': True,
                    'message': 'Đã reset - sẵn sàng dự đoán ngay phiên kế tiếp'
                })
        return jsonify({'status': 'error', 'message': 'Lỗi mạng, không thể xác nhận mốc Reset'}), 400
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
