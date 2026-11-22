import os
import math
import time
import hashlib
import traceback
from datetime import datetime
from collections import defaultdict, Counter
import requests
from flask import Flask, render_template_string, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# ========== API MỚI (đã thay) ==========
API_URL_MD5 = "https://wtxmd52.macminim6.online/v1/txmd5/lite-sessions?cp=R&cl=R&pf=web&at=bd651d261174ed4a619dfde6c5432fdd"

HISTORY_CACHE = {}
PREDICTION_CACHE = {}
MAX_PREDICTION_CACHE = 3000
RESET_ID = 0
BOOTSTRAP_SESSIONS = []
LAST_FETCH_OK_TS = 0
FETCH_RETRY = 2
FETCH_TIMEOUT = 6

# =====================================================================
# CẤU HÌNH THUẬT TOÁN LC79 MD5 DEEP PRO v3
# =====================================================================
WINDOW_SIZE = 5
FINGERPRINT_MIN_MATCH = 3
FINGERPRINT_STRONG = 0.72
TRANSITION_MIN_SAMPLES = 5
MOMENTUM_MIN_STREAK = 3
ENTROPY_HIGH = 0.98
ENTROPY_LOW = 0.55

PATTERN_MIN_OCCURRENCES = 3
BAYES_PRIOR_ALPHA = 1.0
MARKOV_ORDER = 2
DRIFT_WINDOW = 30
CONFIDENCE_FLOOR = 50
CONFIDENCE_CEIL = 95

FUZZY_MATCH_RATIO = 0.75
CLUSTER_MIN_SAMPLES = 3
CYCLE_MIN_PERIOD = 3
CYCLE_MAX_PERIOD = 14
CYCLE_MIN_CONFIDENCE = 0.60
MULTI_WINDOW_LIST = (4, 5, 8, 12, 20)

# ========== CẤU HÌNH FIX GÃY CẦU 2-3 TAY ==========
BREAK_STREAK_THRESHOLD = 2      # Bắt đầu bẻ khi thua 2 tay
BREAK_STREAK_STRONG = 3         # Bẻ mạnh khi thua 3 tay
TREND_REVERSE_WINDOW = 8        # Window phát hiện đảo chiều
ALT_1_1_DETECT = 6              # Phát hiện cầu 1-1


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
    p = t / len(seq)
    q = 1 - p
    e = 0.0
    if p > 0:
        e -= p * math.log2(p)
    if q > 0:
        e -= q * math.log2(q)
    return e


def _to_bits(results):
    return "".join("1" if r == "Tài" else "0" for r in results)


def _md5(s):
    return hashlib.md5(s.encode()).hexdigest()


def _safe_div(a, b, default=0.5):
    return a / b if b > 0 else default


def _hamming(a, b):
    if len(a) != len(b):
        n = min(len(a), len(b))
        a, b = a[:n], b[:n]
    return sum(1 for x, y in zip(a, b) if x != y)


def _weighted_count(seq, base_pred, decay=0.9):
    w = 0.0
    for i, v in enumerate(seq):
        weight = decay ** i
        if v == base_pred:
            w += weight
    return w


def _detect_alt_pattern(results, max_len=10):
    """Phát hiện cầu 1-1 (alternating)"""
    if len(results) < 4:
        return None
    check_len = min(len(results), max_len)
    seq = results[:check_len]
    alt_count = 0
    for i in range(len(seq) - 1):
        if seq[i] != seq[i + 1]:
            alt_count += 1
        else:
            break
    if alt_count >= ALT_1_1_DETECT - 1:
        return {'type': 'alt_1_1', 'length': alt_count + 1,
                'next': _opp(seq[0])}
    return None


def _detect_trend(results, window=8):
    """Phát hiện xu hướng đảo nghịch / thuận"""
    if len(results) < window + 2:
        return None
    recent = results[:window]
    older = results[window:window * 2]
    if not older:
        return None
    r_tai = recent.count("Tài") / len(recent)
    o_tai = older.count("Tài") / len(older)
    drift = r_tai - o_tai
    if abs(drift) >= 0.30:
        return {
            'type': 'trend_reverse' if (r_tai > 0.5) != (o_tai > 0.5) else 'trend_follow',
            'recent_tai': round(r_tai, 3),
            'older_tai': round(o_tai, 3),
            'drift': round(drift, 3),
        }
    return None


# =====================================================================
# TẦNG 1: DEEP MD5 FINGERPRINT (MULTI-WINDOW + FUZZY + DEEP HASH)
# =====================================================================
def lc79_fingerprint(results):
    if len(results) < 10:
        return None

    bits = _to_bits(results)
    best = None

    for ws in (8, 7, 6, 5, 4, 3):
        if len(bits) < ws + 5:
            continue
        cur_fp = bits[:ws]
        tai_next = xiu_next = 0.0
        total = 0.0
        fuzzy_hits = []
        deep_hits = 0

        # Tạo deep hash từ đoạn hiện tại + 2 bit context
        cur_deep = _md5(cur_fp + bits[:2])[:4]

        for i in range(1, len(bits) - ws):
            segment = bits[i:i + ws]
            if segment == cur_fp:
                nxt = bits[i - 1]
                if nxt == "1":
                    tai_next += 1
                else:
                    xiu_next += 1
                total += 1
                # Deep hash match
                deep_seg = _md5(segment + bits[i:i + 2])[:4]
                if deep_seg == cur_deep:
                    deep_hits += 1
            else:
                ham = _hamming(segment, cur_fp)
                similarity = 1.0 - ham / ws
                if similarity >= FUZZY_MATCH_RATIO:
                    weight = similarity
                    nxt = bits[i - 1]
                    if nxt == "1":
                        tai_next += weight
                    else:
                        xiu_next += weight
                    total += weight
                    fuzzy_hits.append(similarity)

        if total < FINGERPRINT_MIN_MATCH:
            continue

        p_tai = tai_next / total
        ratio = max(p_tai, 1 - p_tai)
        if ratio < 0.60:
            continue

        fuzzy_bonus = 1.0 + (len(fuzzy_hits) / max(total, 1)) * 0.35
        deep_bonus = 1.0 + min(0.5, deep_hits * 0.05)
        score = ratio * math.log2(total + 1) * (ws / 5.0) * fuzzy_bonus * deep_bonus

        if best is None or score > best['score']:
            pred = "Tài" if p_tai >= 0.5 else "Xỉu"
            best = {
                'pred': pred, 'ratio': ratio, 'samples': total,
                'window': ws, 'score': score,
                'tai_next': tai_next, 'xiu_next': xiu_next,
                'fuzzy_count': len(fuzzy_hits),
                'exact_count': int(total - len(fuzzy_hits)),
                'deep_hits': deep_hits,
            }

    if not best:
        return None

    sample_bonus = min(8, int(math.log2(best['samples'] + 1) * 2.5))
    window_bonus = min(4, max(0, best['window'] - 4))
    fuzzy_bonus = min(4, best['fuzzy_count'] // 3)
    deep_bonus = min(3, best['deep_hits'] // 2)
    weight = 11 + sample_bonus + window_bonus + fuzzy_bonus + deep_bonus + int((best['ratio'] - 0.5) * 30)
    weight = max(11, min(30, weight))

    return (best['pred'], weight, {
        "fp": _md5(bits[:best['window']])[:8],
        "samples": round(best['samples'], 2),
        "ratio": round(best['ratio'], 3),
        "window": best['window'],
        "exact": best['exact_count'],
        "fuzzy": best['fuzzy_count'],
        "deep_hits": best['deep_hits'],
    })


# =====================================================================
# TẦNG 2: DEEP MD5 TRANSITION (ORDER 2-6)
# =====================================================================
def lc79_transition_md5(results):
    if len(results) < 20:
        return None

    bits = _to_bits(results)
    signals = []

    for order in (2, 3, 4, 5, 6):
        if len(bits) < order + 10:
            continue
        cur_key = _md5(bits[:order])
        tr = defaultdict(lambda: {"Tài": 0, "Xỉu": 0})

        for i in range(1, len(bits) - order):
            k = _md5(bits[i:i + order])
            nxt = bits[i - 1]
            tr[k][("Tài" if nxt == "1" else "Xỉu")] += 1

        d = tr.get(cur_key)
        if not d:
            continue
        tot = d["Tài"] + d["Xỉu"]
        if tot < TRANSITION_MIN_SAMPLES:
            continue

        p_tai = (d["Tài"] + BAYES_PRIOR_ALPHA) / (tot + 2 * BAYES_PRIOR_ALPHA)
        ratio = max(p_tai, 1 - p_tai)
        if ratio < 0.60:
            continue

        signals.append({
            'pred': "Tài" if p_tai >= 0.5 else "Xỉu",
            'ratio': ratio, 'samples': tot, 'order': order,
        })

    if not signals:
        return None

    st = sx = 0.0
    for s in signals:
        w = s['ratio'] * math.log2(s['samples'] + 1) * (s['order'] ** 0.6)
        if s['pred'] == "Tài":
            st += w
        else:
            sx += w

    if st == sx:
        return None

    pred = "Tài" if st > sx else "Xỉu"
    ratio = max(st, sx) / (st + sx)
    if ratio < 0.56:
        return None

    weight = 9 + int((ratio - 0.6) * 50) + len(signals) * 2
    weight = max(9, min(26, weight))

    return (pred, weight, {
        "samples": sum(s['samples'] for s in signals),
        "ratio": round(ratio, 3),
        "orders": [s['order'] for s in signals],
        "n_signals": len(signals),
    })


# =====================================================================
# TẦNG 3: MOMENTUM + ENTROPY + TREND REVERSE
# =====================================================================
def lc79_momentum_entropy(results):
    if len(results) < 10:
        return None

    streak = 1
    for r in results[1:]:
        if r == results[0]:
            streak += 1
        else:
            break

    e = _entropy(results[:20]) if len(results) >= 20 else _entropy(results)

    # Cầu 1-1 alternating
    alt = _detect_alt_pattern(results)
    if alt:
        return (_opp(results[0]), 14, {"type": "alt_1_1", "length": alt['length'], "entropy": round(e, 3)})

    if len(results) >= 8:
        pairs = [(results[i], results[i + 1]) for i in range(0, 8, 2)]
        if all(p[0] == p[1] for p in pairs):
            pair_vals = [p[0] for p in pairs]
            if all(pair_vals[i] != pair_vals[i + 1] for i in range(len(pair_vals) - 1)):
                return (_opp(pair_vals[-1]), 13, {"type": "alt_2_2", "entropy": round(e, 3)})

    if len(results) >= 9:
        triples = [(results[i], results[i + 1], results[i + 2]) for i in range(0, 9, 3)]
        if all(t[0] == t[1] == t[2] for t in triples):
            triple_vals = [t[0] for t in triples]
            if all(triple_vals[i] != triple_vals[i + 1] for i in range(len(triple_vals) - 1)):
                return (_opp(triple_vals[-1]), 13, {"type": "alt_3_3", "entropy": round(e, 3)})

    # Momentum thuận / nghịch
    if MOMENTUM_MIN_STREAK <= streak <= 9:
        window = results[:12]
        w_tai = _weighted_count(window, "Tài", decay=0.85)
        w_xiu = _weighted_count(window, "Xỉu", decay=0.85)
        total_w = w_tai + w_xiu
        if total_w > 0:
            p_tai = w_tai / total_w
            p_xiu = w_xiu / total_w
            dominant = "Tài" if p_tai > p_xiu else "Xỉu"
            ratio = max(p_tai, p_xiu)

            if results[0] == dominant and ratio >= 0.58:
                w = 14 if e < 0.85 else 11
                return (dominant, w, {
                    "type": "momentum_hold", "streak": streak,
                    "ratio": round(ratio, 3), "entropy": round(e, 3),
                })
            if results[0] != dominant and ratio >= 0.58:
                w = 13 if e < 0.85 else 10
                return (dominant, w, {
                    "type": "momentum_break", "streak": streak,
                    "ratio": round(ratio, 3), "entropy": round(e, 3),
                })

    # Xu hướng đảo nghịch
    trend = _detect_trend(results, TREND_REVERSE_WINDOW)
    if trend:
        if trend['type'] == 'trend_reverse':
            pred = "Tài" if trend['recent_tai'] < 0.5 else "Xỉu"
            return (pred, 13, {"type": "trend_reverse", **trend})
        else:
            pred = "Tài" if trend['recent_tai'] > 0.5 else "Xỉu"
            return (pred, 11, {"type": "trend_follow", **trend})

    if e >= ENTROPY_HIGH:
        return (_opp(results[0]), 10, {"type": "entropy_balanced_reverse", "entropy": round(e, 3)})

    if e <= ENTROPY_LOW and len(results) >= 20:
        w = results[:20]
        tc = w.count("Tài")
        if tc >= 15:
            return ("Xỉu", 13, {"type": "pressure_tai", "tai_count": tc})
        if tc <= 5:
            return ("Tài", 13, {"type": "pressure_xiu", "tai_count": tc})

    return None


# =====================================================================
# TẦNG 4: PATTERN N-GRAM (FORWARD + BACKWARD + SKIPGRAM)
# =====================================================================
def lc79_pattern_ngram(results):
    if len(results) < 20:
        return None

    bits = _to_bits(results)
    signals = []

    # Forward
    for order in (3, 4, 5, 6, 7):
        if len(bits) < order + 10:
            continue
        cur = bits[:order]
        tai_next = xiu_next = 0
        for i in range(1, len(bits) - order):
            if bits[i:i + order] == cur:
                if bits[i - 1] == "1":
                    tai_next += 1
                else:
                    xiu_next += 1
        total = tai_next + xiu_next
        if total < PATTERN_MIN_OCCURRENCES:
            continue
        p_tai = (tai_next + BAYES_PRIOR_ALPHA) / (total + 2 * BAYES_PRIOR_ALPHA)
        ratio = max(p_tai, 1 - p_tai)
        if ratio < 0.60:
            continue
        signals.append({
            'pred': "Tài" if p_tai >= 0.5 else "Xỉu",
            'ratio': ratio, 'samples': total, 'order': order, 'type': 'forward',
        })

    # Backward
    for order in (3, 4, 5, 6):
        if len(bits) < order + 10:
            continue
        cur = bits[:order][::-1]
        tai_next = xiu_next = 0
        for i in range(1, len(bits) - order):
            if bits[i:i + order][::-1] == cur:
                if bits[i - 1] == "1":
                    tai_next += 1
                else:
                    xiu_next += 1
        total = tai_next + xiu_next
        if total < PATTERN_MIN_OCCURRENCES:
            continue
        p_tai = (tai_next + BAYES_PRIOR_ALPHA) / (total + 2 * BAYES_PRIOR_ALPHA)
        ratio = max(p_tai, 1 - p_tai)
        if ratio < 0.63:
            continue
        signals.append({
            'pred': "Tài" if p_tai >= 0.5 else "Xỉu",
            'ratio': ratio, 'samples': total, 'order': order, 'type': 'backward',
        })

    # Skip-gram
    for order in (4, 5, 6):
        if len(bits) < order + 12:
            continue
        for skip_pos in range(1, order - 1):
            pattern = bits[:skip_pos] + bits[skip_pos + 1:order]
            tai_next = xiu_next = 0
            for i in range(1, len(bits) - order):
                seg = bits[i:i + order]
                if seg[:skip_pos] + seg[skip_pos + 1:] == pattern:
                    if bits[i - 1] == "1":
                        tai_next += 1
                    else:
                        xiu_next += 1
            total = tai_next + xiu_next
            if total < PATTERN_MIN_OCCURRENCES + 1:
                continue
            p_tai = (tai_next + BAYES_PRIOR_ALPHA) / (total + 2 * BAYES_PRIOR_ALPHA)
            ratio = max(p_tai, 1 - p_tai)
            if ratio < 0.65:
                continue
            signals.append({
                'pred': "Tài" if p_tai >= 0.5 else "Xỉu",
                'ratio': ratio, 'samples': total, 'order': order, 'type': 'skipgram',
            })

    if not signals:
        return None

    st = sx = 0.0
    for s in signals:
        type_bonus = {'forward': 1.0, 'backward': 1.35, 'skipgram': 1.45}.get(s['type'], 1.0)
        w = s['ratio'] * math.log2(s['samples'] + 1) * (s['order'] - 1) * type_bonus
        if s['pred'] == "Tài":
            st += w
        else:
            sx += w

    if st == sx:
        return None

    pred = "Tài" if st > sx else "Xỉu"
    ratio = max(st, sx) / (st + sx)
    weight = 10 + int((ratio - 0.6) * 45) + len(signals) * 2
    weight = max(10, min(27, weight))

    return (pred, weight, {
        "ratio": round(ratio, 3),
        "n_signals": len(signals),
        "types": list(set(s['type'] for s in signals)),
        "total_samples": sum(s['samples'] for s in signals),
    })


# =====================================================================
# TẦNG 5: MARKOV (ORDER 2+3+4 ENSEMBLE)
# =====================================================================
def lc79_markov2(results):
    if len(results) < 25:
        return None

    bits = _to_bits(results)
    if len(bits) < 3:
        return None

    signals = []
    for order in (2, 3, 4):
        if len(bits) < order + 10:
            continue
        cur_state = bits[:order]
        tr = defaultdict(lambda: {"Tài": 0, "Xỉu": 0})
        for i in range(1, len(bits) - order):
            state = bits[i:i + order]
            nxt = bits[i - 1]
            tr[state][("Tài" if nxt == "1" else "Xỉu")] += 1

        d = tr.get(cur_state)
        if not d:
            continue
        tot = d["Tài"] + d["Xỉu"]
        if tot < 4:
            continue

        p_tai = (d["Tài"] + BAYES_PRIOR_ALPHA) / (tot + 2 * BAYES_PRIOR_ALPHA)
        ratio = max(p_tai, 1 - p_tai)
        if ratio < 0.58:
            continue
        signals.append({
            'pred': "Tài" if p_tai >= 0.5 else "Xỉu",
            'ratio': ratio, 'samples': tot, 'order': order,
        })

    if not signals:
        return None

    st = sx = 0.0
    for s in signals:
        w = s['ratio'] * math.log2(s['samples'] + 1) * s['order']
        if s['pred'] == "Tài":
            st += w
        else:
            sx += w

    if st == sx:
        return None

    pred = "Tài" if st > sx else "Xỉu"
    ratio = max(st, sx) / (st + sx)
    weight = 8 + int((ratio - 0.55) * 45) + len(signals) * 2
    weight = max(8, min(23, weight))

    return (pred, weight, {
        "ratio": round(ratio, 3),
        "orders": [s['order'] for s in signals],
        "samples": sum(s['samples'] for s in signals),
    })


# =====================================================================
# TẦNG 6: BAYESIAN RECENCY
# =====================================================================
def lc79_bayesian_recency(results):
    if len(results) < 15:
        return None

    n = min(len(results), 25)
    weighted_tai = weighted_xiu = 0.0
    for i, r in enumerate(results[:n]):
        w = math.exp(-i / 8.0)
        if r == "Tài":
            weighted_tai += w
        else:
            weighted_xiu += w

    total = weighted_tai + weighted_xiu
    if total == 0:
        return None

    p_tai = weighted_tai / total
    ratio = max(p_tai, 1 - p_tai)
    if ratio < 0.60:
        return None

    pred = "Tài" if p_tai >= 0.5 else "Xỉu"
    weight = 7 + int((ratio - 0.6) * 40)
    weight = max(7, min(19, weight))

    return (pred, weight, {
        "p_tai": round(p_tai, 3), "ratio": round(ratio, 3), "window": n,
    })


# =====================================================================
# TẦNG 7: STREAK PRESSURE
# =====================================================================
def lc79_streak_pressure(results):
    if len(results) < 20:
        return None

    streak = 1
    for r in results[1:]:
        if r == results[0]:
            streak += 1
        else:
            break

    groups = get_groups(results)
    if len(groups) < 5:
        return None

    past_groups = groups[1:min(len(groups), 20)]
    if not past_groups:
        return None
    avg_group = sum(past_groups) / len(past_groups)

    if streak >= 4 and streak >= avg_group * 1.6:
        w = 10 + min(6, int(streak / 2))
        return (_opp(results[0]), w, {
            "type": "streak_break", "streak": streak,
            "avg_group": round(avg_group, 2),
        })

    return None


# =====================================================================
# TẦNG 8: DICE ANALYSIS
# =====================================================================
def _parse_dice_history(valid_rows):
    sums = []
    for row in valid_rows:
        try:
            dice = row.get('dice', '')
            if '+ ' in dice or '+' in dice:
                parts = [p.strip() for p in dice.replace('+', ' + ').split('+') if p.strip()]
                if len(parts) >= 3:
                    s = sum(int(p) for p in parts[:3])
                    sums.append(s)
        except Exception:
            continue
    return sums


def lc79_dice_analysis(dice_sums):
    if not dice_sums or len(dice_sums) < 15:
        return None

    recent = dice_sums[:20]
    avg = sum(recent) / len(recent)
    deviation = avg - 10.5

    if abs(deviation) < 1.2:
        return None

    if deviation > 0:
        pred = "Xỉu"
        strength = min(1.0, deviation / 3.0)
    else:
        pred = "Tài"
        strength = min(1.0, abs(deviation) / 3.0)

    weight = 7 + int(strength * 10)
    return (pred, weight, {
        "type": "dice_bias",
        "avg_dice": round(avg, 2),
        "deviation": round(deviation, 2),
        "samples": len(recent),
    })


# =====================================================================
# TẦNG 9: DEEP MD5 HASH CLUSTER
# =====================================================================
def lc79_md5_cluster(results):
    if len(results) < 25:
        return None

    bits = _to_bits(results)
    cur_prefix = _md5(bits[:5])[:3]
    cur_deep = _md5(bits[:5] + bits[:2])[:4]

    cluster = []
    deep_cluster = []
    for i in range(1, len(bits) - 5):
        h = _md5(bits[i:i + 5])[:3]
        if h == cur_prefix:
            cluster.append(bits[i - 1])
            deep_seg = _md5(bits[i:i + 5] + bits[i:i + 2])[:4]
            if deep_seg == cur_deep:
                deep_cluster.append(bits[i - 1])

    target = deep_cluster if len(deep_cluster) >= CLUSTER_MIN_SAMPLES else cluster
    if len(target) < CLUSTER_MIN_SAMPLES:
        return None

    tai = target.count("1")
    xiu = target.count("0")
    total = len(target)
    p_tai = (tai + BAYES_PRIOR_ALPHA) / (total + 2 * BAYES_PRIOR_ALPHA)
    ratio = max(p_tai, 1 - p_tai)
    if ratio < 0.62:
        return None

    pred = "Tài" if p_tai >= 0.5 else "Xỉu"
    weight = 9 + int((ratio - 0.6) * 45) + min(4, total // 5)
    weight = max(9, min(23, weight))

    return (pred, weight, {
        "cluster_prefix": cur_prefix,
        "cluster_size": total,
        "deep_hit": len(deep_cluster) >= CLUSTER_MIN_SAMPLES,
        "tai": tai, "xiu": xiu,
        "ratio": round(ratio, 3),
    })


# =====================================================================
# TẦNG 10: CYCLE DETECTOR
# =====================================================================
def lc79_cycle_detector(results):
    if len(results) < 18:
        return None

    best = None
    for period in range(CYCLE_MIN_PERIOD, CYCLE_MAX_PERIOD + 1):
        if len(results) < period * 2 + 2:
            continue

        matches = 0
        checks = 0
        for offset in range(1, period * 2 + 1):
            if offset >= len(results) or period >= len(results):
                continue
            checks += 1
            if results[offset - 1] == results[offset - 1 + period]:
                matches += 1

        if checks < period:
            continue
        confidence = matches / checks
        if confidence < CYCLE_MIN_CONFIDENCE:
            continue

        if period < len(results):
            pred = results[period - 1]
            score = confidence * math.log2(checks + 1) / period
            if best is None or score > best['score']:
                best = {'pred': pred, 'period': period,
                        'confidence': confidence, 'score': score}

    if not best:
        return None

    weight = 8 + int(best['confidence'] * 17)
    weight = max(8, min(22, weight))

    return (best['pred'], weight, {
        "type": "cycle",
        "period": best['period'],
        "confidence": round(best['confidence'], 3),
    })


# =====================================================================
# TẦNG 11: BAYESIAN DRIFT
# =====================================================================
def lc79_bayesian_drift(results):
    if len(results) < 30:
        return None

    old_window = results[15:30]
    new_window = results[:15]

    old_tai_rate = old_window.count("Tài") / len(old_window)
    new_tai_rate = new_window.count("Tài") / len(new_window)
    drift = new_tai_rate - old_tai_rate

    if abs(drift) < 0.22:
        return None

    if new_tai_rate > 0.63:
        pred = "Tài"
        strength = (new_tai_rate - 0.5) * 2
    elif new_tai_rate < 0.37:
        pred = "Xỉu"
        strength = (0.5 - new_tai_rate) * 2
    else:
        return None

    weight = 7 + int(strength * 9)
    weight = max(7, min(16, weight))

    return (pred, weight, {
        "type": "drift",
        "old_tai_rate": round(old_tai_rate, 3),
        "new_tai_rate": round(new_tai_rate, 3),
        "drift": round(drift, 3),
    })


# =====================================================================
# TẦNG 12: MULTI-WINDOW CONSENSUS
# =====================================================================
def lc79_multi_window_consensus(results):
    if len(results) < 20:
        return None

    votes = []
    for ws in MULTI_WINDOW_LIST:
        if len(results) < ws:
            continue
        window = results[:ws]
        tai = window.count("Tài")
        p_tai = tai / ws

        if ws <= 5:
            if p_tai >= 0.6:
                votes.append(("Tài", 1.0))
            elif p_tai <= 0.4:
                votes.append(("Xỉu", 1.0))
        elif ws <= 8:
            if p_tai >= 0.625:
                votes.append(("Tài", 0.9))
            elif p_tai <= 0.375:
                votes.append(("Xỉu", 0.9))
        else:
            if p_tai >= 0.70:
                votes.append(("Xỉu", 0.8))
            elif p_tai <= 0.30:
                votes.append(("Tài", 0.8))

    if len(votes) < 2:
        return None

    st = sum(w for p, w in votes if p == "Tài")
    sx = sum(w for p, w in votes if p == "Xỉu")

    if st == sx:
        return None

    pred = "Tài" if st > sx else "Xỉu"
    ratio = max(st, sx) / (st + sx)
    if ratio < 0.58:
        return None

    weight = 7 + int((ratio - 0.6) * 35) + len(votes) * 2
    weight = max(7, min(20, weight))

    return (pred, weight, {
        "type": "multi_window",
        "votes": [{"pred": p, "w": w} for p, w in votes],
        "ratio": round(ratio, 3),
    })


# =====================================================================
# TỔNG HỢP (12 TẦNG)
# =====================================================================
def lc79_predict(results, dice_sums=None):
    n = len(results)
    if n == 0:
        return "Tài", 50, {"mode": "empty"}

    if n == 1:
        return _opp(results[0]), 54, {"mode": "warmup_1"}
    if n == 2:
        if results[0] == results[1]:
            return _opp(results[0]), 57, {"mode": "warmup_2_reverse"}
        return results[0], 53, {"mode": "warmup_2_hold"}
    if n == 3:
        if results[0] != results[1] and results[1] != results[2]:
            return _opp(results[0]), 60, {"mode": "warmup_3_zigzag"}
        if results[0] == results[1] == results[2]:
            return results[0], 58, {"mode": "warmup_3_streak"}
        return results[0], 54, {"mode": "warmup_3_mixed"}
    if n < WINDOW_SIZE + 5:
        streak = 1
        for r in results[1:]:
            if r == results[0]:
                streak += 1
            else:
                break
        if streak >= 3:
            return results[0], 57, {"mode": "warmup_momentum"}
        return _opp(results[0]), 55, {"mode": "warmup_alt"}

    layers = []
    layer_names = []

    layer_funcs = [
        ("fingerprint_md5",  lc79_fingerprint),
        ("transition_md5",   lc79_transition_md5),
        ("momentum_entropy", lc79_momentum_entropy),
        ("pattern_ngram",    lc79_pattern_ngram),
        ("markov2",          lc79_markov2),
        ("bayesian_recency", lc79_bayesian_recency),
        ("streak_pressure",  lc79_streak_pressure),
        ("md5_cluster",      lc79_md5_cluster),
        ("cycle_detector",   lc79_cycle_detector),
        ("bayesian_drift",   lc79_bayesian_drift),
        ("multi_window",     lc79_multi_window_consensus),
    ]

    for name, fn in layer_funcs:
        r = fn(results)
        if r:
            layers.append(r)
            layer_names.append(name)

    if dice_sums:
        r = lc79_dice_analysis(dice_sums)
        if r:
            layers.append(r)
            layer_names.append("dice_bias")

    if not layers:
        return _opp(results[0]), 54, {"mode": "fallback_alt"}

    st = sx = 0.0
    for pred, w, _ in layers:
        if pred == "Tài":
            st += w
        else:
            sx += w

    total_w = st + sx
    if total_w == 0:
        return _opp(results[0]), 54, {"mode": "no_signal"}

    if st > sx:
        pred = "Tài"; ratio = st / total_w
    elif sx > st:
        pred = "Xỉu"; ratio = sx / total_w
    else:
        return _opp(results[0]), 55, {"mode": "tie"}

    agree_layers = sum(1 for p, _, _ in layers if p == pred)
    n_layers = len(layers)

    base_conf = 52
    base_conf += int((ratio - 0.5) * 60)
    if n_layers >= 2:
        agreement_pct = agree_layers / n_layers
        base_conf += int(agreement_pct * 14)
        if agree_layers == n_layers and n_layers >= 3:
            base_conf += 4
    base_conf += min(5, max(0, n_layers - 2))

    if n_layers == 1:
        base_conf -= 8
    elif n_layers == 2:
        base_conf -= 3

    conf = max(CONFIDENCE_FLOOR, min(CONFIDENCE_CEIL, base_conf))

    meta = {
        "mode": "lc79_md5_deep_pro_v3",
        "n_layers": n_layers,
        "agree": agree_layers,
        "ratio": round(ratio, 3),
        "layers": [
            {"name": name, "pred": p, "w": w, "info": info}
            for name, (p, w, info) in zip(layer_names, layers)
        ],
    }
    return pred, conf, meta


# =====================================================================
# ADAPTIVE RECOVERY - FIX GÃY CẦU 2-3 TAY + BẺ CẦU ĐẢO NGHỊCH
# =====================================================================
def apply_recovery(base_pred, conf, loss_streak, results=None):
    """
    Fix gãy cầu 2-3 tay:
    - Thua 2 tay: bẻ nhẹ nếu conf thấp, giữ nếu conf cao
    - Thua 3 tay: bẻ mạnh + tăng conf
    - Thua 4+ tay: bẻ hoàn toàn
    - Phát hiện xu hướng đảo nghịch để tăng độ chính xác
    """
    # Chưa thua gì → giữ nguyên
    if loss_streak < BREAK_STREAK_THRESHOLD:
        return base_pred, conf, False, None

    # Phân tích xu hướng để quyết định bẻ thông minh
    trend_hint = None
    if results and len(results) >= 10:
        trend = _detect_trend(results, TREND_REVERSE_WINDOW)
        alt = _detect_alt_pattern(results)
        if trend:
            trend_hint = trend['type']
        if alt:
            # Nếu đang cầu 1-1 mà thua → đảo ngay
            return _opp(base_pred), min(92, conf + 12), True, "alt_1_1_flip"

    # Thua 2 tay
    if loss_streak == 2:
        if conf < 72:
            return _opp(base_pred), min(88, conf + 8), True, "flip_2_low_conf"
        # Conf cao → có thể cầu sắp về, giảm conf + giữ
        return base_pred, max(50, conf - 8), False, "hold_2_high_conf"

    # Thua 3 tay → bẻ mạnh
    if loss_streak == 3:
        # Nếu xu hướng đảo nghịch → bẻ tự tin hơn
        if trend_hint == 'trend_reverse':
            return _opp(base_pred), min(92, conf + 14), True, "flip_3_trend_reverse"
        return _opp(base_pred), min(90, conf + 10), True, "flip_3"

    # Thua 4 tay
    if loss_streak == 4:
        return _opp(base_pred), min(91, conf + 13), True, "flip_4"

    # Thua 5+ tay → bẻ hoàn toàn
    if loss_streak >= 5:
        return _opp(base_pred), min(93, conf + 15), True, "flip_5plus"

    return base_pred, conf, False, None


# =====================================================================
# DATA FETCHER
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
    pred, conf, _ = lc79_predict(hist)
    if pred == "-":
        result = ("-", 50, None)
    else:
        is_correct = (pred == valid[i]['result'])
        result = (pred, conf, is_correct)
    if len(PREDICTION_CACHE) > MAX_PREDICTION_CACHE:
        PREDICTION_CACHE.clear()
    PREDICTION_CACHE[key] = result
    return result


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
# HTML TEMPLATE — GIỮ NGUYÊN HOÀN TOÀN
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
        --win-color: #22c55e; --win-bg: rgba(34, 197, 94, 0.15);
        --lose-color: #ef4444; --lose-bg: rgba(239, 68, 68, 0.15);
    }
    * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Plus Jakarta Sans', sans-serif; }
    body { background: linear-gradient(135deg, #0f172a 0%, #1e1b4b 100%); color: var(--text-main); min-height: 100vh; padding-bottom: 40px; }
    .header { background: rgba(30, 41, 59, 0.85); backdrop-filter: blur(12px); border-bottom: 1px solid var(--border-color); padding: 16px 24px; display: flex; justify-content: space-between; align-items: center; position: sticky; top: 0; z-index: 100; }
    .header-title { font-size: 22px; font-weight: 800; color: #38bdf8; display: flex; align-items: center; gap: 10px; text-shadow: 0 0 20px rgba(56, 189, 248, 0.4); }
    .header-actions { display: flex; gap: 12px; align-items: center; }
    .time-update { font-size: 12px; color: var(--text-muted); font-weight: 600; }
    .btn-refresh { background: var(--bg-card); color: var(--text-main); border: 1px solid var(--border-color); width: 38px; height: 38px; border-radius: 10px; font-weight: 700; font-size: 16px; cursor: pointer; display: flex; align-items: center; justify-content: center; transition: 0.2s; }
    .btn-refresh:hover { background: var(--border-color); transform: rotate(180deg); }
    .btn-reset { background: rgba(248, 113, 113, 0.2); color: #f87171; border: 1px solid #f87171; padding: 8px 16px; border-radius: 10px; font-weight: 700; font-size: 13px; cursor: pointer; transition: 0.2s; }
    .btn-reset:hover { background: #f87171; color: #fff; box-shadow: 0 0 15px rgba(248, 113, 113, 0.4); }

    .stats-bar { max-width: 800px; margin: 18px auto 0; padding: 0 16px; display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; }
    .stat-card { background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 12px; padding: 12px; text-align: center; }
    .stat-label { font-size: 11px; color: var(--text-muted); font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; }
    .stat-value { font-size: 22px; font-weight: 800; margin-top: 4px; }
    .stat-value.win { color: var(--win-color); }
    .stat-value.lose { color: var(--lose-color); }
    .stat-value.rate { color: #38bdf8; }
    .stat-value.total { color: var(--text-main); }

    .table-wrapper { max-width: 800px; margin: 18px auto; padding: 0 16px; }
    .table-container { background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 16px; box-shadow: 0 10px 30px rgba(0,0,0,0.3); max-height: 72vh; overflow-y: auto; }
    .data-table { width: 100%; border-collapse: separate; border-spacing: 0; }
    .data-table th { background: #111827; color: var(--text-muted); font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; padding: 14px 10px; text-align: center; border-bottom: 1px solid var(--border-color); position: sticky; top: 0; z-index: 10; }
    .data-table td { padding: 12px 10px; text-align: center; border-bottom: 1px solid var(--border-color); font-size: 14px; font-weight: 600; }
    .data-table tr:last-child td { border-bottom: none; }
    .data-table tr:hover { background: rgba(255, 255, 255, 0.02); }
    .badge { display: inline-block; padding: 6px 12px; border-radius: 20px; font-size: 12px; font-weight: 700; min-width: 66px; text-align: center; }
    .badge-tai { background: var(--tai-bg); color: var(--tai-color); border: 1px solid rgba(56, 189, 248, 0.3); }
    .badge-xiu { background: var(--xiu-bg); color: var(--xiu-color); border: 1px solid rgba(244, 114, 182, 0.3); }
    .badge-win { background: var(--win-bg); color: var(--win-color); border: 1px solid rgba(34, 197, 94, 0.35); }
    .badge-lose { background: var(--lose-bg); color: var(--lose-color); border: 1px solid rgba(239, 68, 68, 0.35); }
    .badge-none { background: rgba(148, 163, 184, 0.12); color: var(--text-muted); border: 1px solid rgba(148, 163, 184, 0.25); }
    .dice-text { font-size: 11px; color: var(--text-muted); display: block; margin-top: 4px; font-weight: 500; }
    .conf-text { font-size: 11px; color: var(--text-muted); display: block; margin-top: 2px; font-weight: 500; }
    .loading { text-align: center; padding: 40px; color: var(--tai-color); font-weight: 600; }

    @media (max-width: 600px) {
        .stats-bar { grid-template-columns: repeat(2, 1fr); }
        .stat-value { font-size: 18px; }
        .data-table th, .data-table td { padding: 9px 4px; font-size: 11px; }
        .badge { padding: 4px 7px; font-size: 10px; min-width: 44px; }
        .header-title { font-size: 18px; }
        .time-update { display: none; }
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
    <div class="stat-card">
        <div class="stat-label">Tổng phiên</div>
        <div class="stat-value total" id="stat-total">0</div>
    </div>
    <div class="stat-card">
        <div class="stat-label">Thắng</div>
        <div class="stat-value win" id="stat-win">0</div>
    </div>
    <div class="stat-card">
        <div class="stat-label">Thua</div>
        <div class="stat-value lose" id="stat-lose">0</div>
    </div>
    <div class="stat-card">
        <div class="stat-label">Tỷ lệ</div>
        <div class="stat-value rate" id="stat-rate">0%</div>
    </div>
</div>

<div class="table-wrapper">
    <div class="table-container">
        <table class="data-table">
            <thead>
                <tr>
                    <th>Phiên</th>
                    <th>Dự đoán</th>
                    <th>Kết quả</th>
                    <th>Nhận định</th>
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

async function fetchData() {
    if (isResetting) return;
    try {
        const r = await fetch('/api/history', { cache: 'no-store' });
        const data = await r.json();
        const now = new Date();
        document.getElementById('time-update').innerText = now.toLocaleTimeString('vi-VN');
        renderStats(data.stats || {});
        renderTable(data.rows || []);
    } catch (e) { console.error("Lỗi tải dữ liệu:", e); }
}

function renderStats(stats) {
    document.getElementById('stat-total').innerText = stats.total || 0;
    document.getElementById('stat-win').innerText = stats.win || 0;
    document.getElementById('stat-lose').innerText = stats.lose || 0;
    document.getElementById('stat-rate').innerText = (stats.rate || 0) + '%';
}

function renderTable(rows) {
    const tbody = document.getElementById('table-body');
    if (!rows.length) {
        tbody.innerHTML = '<tr><td colspan="4" class="loading">Không có dữ liệu. Hãy đợi phiên mới.</td></tr>';
        return;
    }
    let html = '';
    rows.forEach((row) => {
        let resultHtml = `<span class="badge badge-tai">TÀI</span>`;
        if (row.result === "Xỉu") resultHtml = `<span class="badge badge-xiu">XỈU</span>`;
        let diceDisplay = "";
        if (row.dice && row.dice !== "? + ? + ?") {
            const p = row.dice.split(" + ");
            const sum = parseInt(p[0]) + parseInt(p[1]) + parseInt(p[2]);
            diceDisplay = `<span class="dice-text">${row.dice} (${sum})</span>`;
        }
        let resultFull = `<div style="display:flex; flex-direction:column; align-items:center;">${resultHtml}${diceDisplay}</div>`;

        let predHtml = `<span class="badge badge-none">—</span>`;
        let confHtml = "";
        if (row.prediction === "Tài") {
            predHtml = `<span class="badge badge-tai">TÀI</span>`;
            confHtml = `<span class="conf-text">${row.confidence}%</span>`;
        } else if (row.prediction === "Xỉu") {
            predHtml = `<span class="badge badge-xiu">XỈU</span>`;
            confHtml = `<span class="conf-text">${row.confidence}%</span>`;
        }
        let predFull = `<div style="display:flex; flex-direction:column; align-items:center;">${predHtml}${confHtml}</div>`;

        let judgeHtml = `<span class="badge badge-none">—</span>`;
        if (row.is_correct === true) judgeHtml = `<span class="badge badge-win">✓ ĐÚNG</span>`;
        else if (row.is_correct === false) judgeHtml = `<span class="badge badge-lose">✗ SAI</span>`;

        html += `<tr>
            <td>#${row.phien}</td>
            <td>${predFull}</td>
            <td>${resultFull}</td>
            <td>${judgeHtml}</td>
        </tr>`;
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
            return jsonify({'rows': [], 'stats': {'total': 0, 'win': 0, 'lose': 0, 'rate': 0}})

        out = []
        win = lose = 0
        scan_limit = min(len(valid), 200)
        for i in range(len(valid) - 1, len(valid) - scan_limit - 1, -1):
            if i < 0:
                break
            s = valid[i]
            pred, conf, is_correct = get_prediction_for(valid, i)

            if is_correct is True:
                win += 1
            elif is_correct is False:
                lose += 1

            out.append({
                'phien': s['phien'],
                'result': s['result'],
                'dice': s['dice'],
                'prediction': pred if pred != "-" else None,
                'confidence': conf,
                'is_correct': is_correct,
            })

        total = win + lose
        rate = round(win / total * 100, 1) if total > 0 else 0
        stats = {'total': total, 'win': win, 'lose': lose, 'rate': rate}

        return jsonify({'rows': out, 'stats': stats})
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
        dice_sums = _parse_dice_history(valid[::-1])

        loss_streak = compute_loss_streak(valid)
        base_pred, base_conf, meta = lc79_predict(results, dice_sums)
        pred, conf, flipped, reason = apply_recovery(base_pred, base_conf, loss_streak, results)

        return jsonify({
            'algorithm': 'LC79_MD5_DEEP_PRO_V3',
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
            'meta': meta,
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
        dice_sums = _parse_dice_history(valid[::-1])

        loss_streak = compute_loss_streak(valid)
        base_pred, base_conf, meta = lc79_predict(results, dice_sums)
        pred, conf, flipped, reason = apply_recovery(base_pred, base_conf, loss_streak, results)

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
            'algorithm': 'LC79_DEEP_V3',
            'phien': str(next_phien),
            'ket_qua': ket_qua_str,
            'du_doan': pred,
            'do_tin_cay': conf_str,
            'lich_su': lich_su,
            'base_du_doan': base_pred if flipped else None,
            'auto_flip': flipped,
            'chuoi_thua': loss_streak,
            'meta': meta,
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
                    'algorithm': 'LC79_MD5_DEEP_PRO_V3',
                    'message': 'Đã reset - sẵn sàng dự đoán ngay phiên kế tiếp'
                })
        return jsonify({'status': 'error', 'message': 'Lỗi mạng, không thể xác nhận mốc Reset'}), 400
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
