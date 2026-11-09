import os
import traceback
import time
import json
import hashlib
import math
from datetime import datetime
from collections import Counter, defaultdict, deque
import requests
from flask import Flask, render_template_string, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

API_URL_MD5 = (
    "https://wtxmd52.tele68.com/v1/txmd5/lite-sessions"
    "?cp=R&cl=R&pf=web&at=d35795e07a393f91815436933a4852df"
)

HISTORY_CACHE = {}
RESET_ID = 0
PREDICTION_LOG = {}
MAX_LOG_SIZE = 3000
LOG_FILE = "prediction_log.json"

# ==== V9.0: Pattern memory ====
PATTERN_MEMORY = deque(maxlen=200)  # top winning patterns
STRATEGY_BLACKLIST = set()  # strategies too weak

SIGNAL_NAMES = {
    'markov3':      'Markov bậc 3',
    'markov2':      'Markov bậc 2',
    'markov1':      'Markov bậc 1',
    'streak':       'Bẻ chuỗi',
    'pattern':      'Khớp mẫu',
    'weighted':     'Tần suất',
    'dice':         'Lệch xúc xắc',
    'alternation':  'Cầu 1-1',
    'momentum':     'Đà',
    'recency':      'Gần đây',
    'invert':       'Đảo cầu',
    'trend':        'Xu hướng',
    'bias_run':     'Lệch 12 phiên',
    'follow':       'Theo cầu',
    'exhaust':      'Cầu kiệt',
    'dominance':    'Lệch cực đoan',
    'cycle_1_1':    'Chu kỳ 1-1',
    'cycle_2_2':    'Chu kỳ 2-2',
    'cycle_3_3':    'Chu kỳ 3-3',
    'cycle_1_2':    'Chu kỳ 1-2',
    'cycle_2_1':    'Chu kỳ 2-1',
    'cycle_1_3':    'Chu kỳ 1-3',
    'cycle_3_1':    'Chu kỳ 3-1',
    'cycle_2_3':    'Chu kỳ 2-3',
    'cycle_3_2':    'Chu kỳ 3-2',
    'cycle_123':    'Chu kỳ 1-2-3',
    'cycle_321':    'Chu kỳ 3-2-1',
    'cycle_346':    'Chu kỳ 3-4-6',
    'cycle_112':    'Chu kỳ 1-1-2',
    'cycle_212':    'Chu kỳ 2-1-2',
    'cycle_122':    'Chu kỳ 1-2-2',
    'cycle_221':    'Chu kỳ 2-2-1',
    'cycle_1234':   'Chu kỳ 1-2-3-4',
    'cycle_2341':   'Chu kỳ 2-3-4-1',
    'cycle_3456':   'Chu kỳ 3-4-5-6',
    'cycle_4_4':    'Chu kỳ 4-4',
    'auto_cycle':   'Chu kỳ tự động',
    'pattern_sim':  'Tương đồng mẫu',
    'regime':       'Chế độ thị trường',
    'flip_break':   'Bẻ cầu đảo',
    'run_break':    'Bẻ run dài',
    'ngram_2':      'N-gram 2',
    'ngram_3':      'N-gram 3',
    'ngram_4':      'N-gram 4',
    'ngram_5':      'N-gram 5',
    'mirror_pattern':   'Đối xứng đảo',
    'reverse_trend':    'Đảo xu hướng',
    'palindrome_check': 'Cầu đối xứng',
    'bayes_recent':     'Bayes gần đây',
    'chi_square':       'Kiểm định lệch',
    'runs_prediction':  'Chuỗi run-length',
    'transition_matrix': 'Ma trận chuyển',
    'double_break':     'Bẻ đôi',
    'signal_consensus': 'Đồng thuận tín hiệu',
    'signal_dissent':   'Phản biện tín hiệu',
    'entropy_adaptive': 'Entropy thích ứng',
    'streak_maturity':  'Độ chín streak',
    'time_pattern':     'Mẫu thời gian',
    'volatility':       'Biến động',
    'cusum':            'CUSUM',
    'kelly_optimal':    'Kelly tối ưu',
    'multi_window':     'Đa cửa sổ',
    'rsi_signal':       'RSI cầu',
    'fibonacci_break':  'Fibonacci bẻ',
    'zscore_extreme':   'Z-Score cực trị',
    'harmonic_pattern': 'Mẫu hài hòa',
    'wavelet_trend':    'Xu hướng sóng',
    'seasonal_cycle':   'Chu kỳ mùa',
    'markov_weighted':  'Markov trọng số',
    'ensemble_boost':   'Ensemble boost',
    'decision_tree':    'Cây quyết định',
    'streak_aware':     'Streak-aware',
    'tier2_boost':      'Tier-2 boost',
    'volatility_regime': 'Regime biến động',
    'cascade_pattern':  'Mẫu tầng',
    # ==== V9.0 NEW ====
    'regime_router':    'Định tuyến chế độ',
    'confidence_gate':  'Cổng tin cậy',
    'pattern_memory':   'Bộ nhớ mẫu',
    'anti_streak':      'Chống chuỗi dài',
    'safe_fallback':    'Fallback an toàn',
    'dice_parity':      'Chẵn lẻ xúc xắc',
}


# =====================================================================
# PERSISTENCE
# =====================================================================
def _load_log():
    global PREDICTION_LOG, RESET_ID
    if not os.path.exists(LOG_FILE):
        return
    try:
        with open(LOG_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        PREDICTION_LOG = {int(k): v for k, v in data.get('log', {}).items()}
        RESET_ID = int(data.get('reset_id', 0))
        print(f"[LOG] Loaded {len(PREDICTION_LOG)} predictions")
    except Exception as e:
        print(f"[LOG] Load failed: {e}")


def _save_log():
    try:
        with open(LOG_FILE, 'w', encoding='utf-8') as f:
            json.dump({'log': PREDICTION_LOG, 'reset_id': RESET_ID}, f)
    except Exception as e:
        print(f"[LOG] Save failed: {e}")


# =====================================================================
# UTILS
# =====================================================================
def _run_lengths(results):
    if not results:
        return []
    runs = [1]
    for i in range(1, len(results)):
        if results[i] == results[i - 1]:
            runs[-1] += 1
        else:
            runs.append(1)
    return runs


def _entropy(results):
    if len(results) < 5:
        return 0.0
    c = Counter(results)
    total = len(results)
    h = 0.0
    for v in c.values():
        p = v / total
        if p > 0:
            h -= p * math.log2(p)
    return h


def _detect_regime(results):
    """V9.0: Phân loại chế độ thị trường."""
    if len(results) < 12:
        return 'unknown'
    r = results[-20:] if len(results) >= 20 else results
    flips = sum(1 for i in range(1, len(r)) if r[i] != r[i - 1])
    flip_ratio = flips / (len(r) - 1)
    runs = _run_lengths(r)
    avg_run = sum(runs) / len(runs) if runs else 1.0

    if flip_ratio >= 0.65:
        return 'alternating'   # cầu 1-1
    if flip_ratio <= 0.30 or avg_run >= 2.5:
        return 'streaky'       # cầu bệt
    return 'mixed'             # hỗn hợp


# =====================================================================
# PREDICTOR V9.0 - MD5 ONLY
# =====================================================================
class MultiStrategyPredictor:
    _STRATEGY_TRACK = defaultdict(lambda: {'hit': 0, 'miss': 0, 'last_len': 0})
    _REGIME_TRACK = defaultdict(lambda: {'hit': 0, 'miss': 0})

    @staticmethod
    def _deterministic_choice(options, seed_str):
        h = int(hashlib.md5(seed_str.encode()).hexdigest(), 16)
        return options[h % len(options)]

    @staticmethod
    def _tail_run(seq):
        if not seq:
            return 0, None
        last = seq[-1]
        n = 1
        for i in range(len(seq) - 2, -1, -1):
            if seq[i] == last:
                n += 1
            else:
                break
        return n, last

    @staticmethod
    def _seg_ratio(seq, win):
        if len(seq) < win or win <= 0:
            return 0.5
        seg = seq[-win:]
        return sum(1 for x in seg if x == 'Tài') / len(seg)

    # ==================== CYCLE ====================
    @staticmethod
    def _detect_cycle(r, max_period=6, min_repeats=2):
        if len(r) < 8:
            return None
        best = None
        for period in range(2, min(max_period * 2, len(r) // min_repeats) + 1):
            needed = period * min_repeats
            if len(r) < needed:
                break
            tail = r[-needed:]
            pattern = tail[-period:]
            match = True
            for k in range(min_repeats):
                chunk = tail[k * period:(k + 1) * period]
                if chunk != pattern:
                    match = False
                    break
            if match:
                hits, total = 0, 0
                for i in range(len(r) - period):
                    if r[i] == pattern[i % period]:
                        hits += 1
                    total += 1
                if total >= 6 and hits / total >= 0.65:
                    phase = len(r) % period
                    score = hits / total
                    if best is None or score > best[0]:
                        best = (score, period, phase, pattern)
        if not best:
            return None
        _, period, phase, pattern = best
        return {'period': period, 'phase': phase, 'pattern': pattern,
                'next': pattern[phase % period]}

    @classmethod
    def _cycle_signal(cls, r, sizes_target=None):
        if sizes_target is None:
            return None, 0, 0
        period_len = sum(sizes_target)
        if len(r) < period_len * 2 + 2:
            return None, 0, 0
        runs = _run_lengths(r)
        if len(runs) < 2:
            return None, 0, 0
        n_cycles = min(len(runs) // len(sizes_target), 4)
        if n_cycles < 2:
            return None, 0, 0
        matched, total_checks = 0, 0
        for c in range(n_cycles):
            start = len(runs) - (c + 1) * len(sizes_target)
            end = start + len(sizes_target)
            if start < 0:
                break
            chunk = runs[start:end]
            if c == 0:
                for i in range(min(len(chunk) - 1, len(sizes_target) - 1)):
                    total_checks += 1
                    if chunk[i] == sizes_target[i]:
                        matched += 1
                    else:
                        break
            else:
                for i in range(len(chunk)):
                    total_checks += 1
                    if chunk[i] == sizes_target[i]:
                        matched += 1
                    else:
                        break
        if total_checks < 3:
            return None, 0, 0
        score = matched / total_checks
        if score < 0.75:
            return None, 0, 0
        cycle_pos = len(runs) % len(sizes_target)
        cur_run_len, cur_val = cls._tail_run(r)
        expected_size = sizes_target[(cycle_pos - 1) % len(sizes_target)]
        if cur_run_len < expected_size:
            conf = min(58 + int(score * 25) + (expected_size - cur_run_len) * 2, 82)
            weight = 1.4 + score * 0.4
            return cur_val, conf, min(weight, 1.9)
        else:
            next_val = 'Xỉu' if cur_val == 'Tài' else 'Tài'
            conf = min(58 + int(score * 25) + (cur_run_len - expected_size) * 2, 82)
            weight = 1.4 + score * 0.4
            return next_val, conf, min(weight, 1.9)

    @classmethod
    def _auto_cycle_signal(cls, r):
        if len(r) < 12:
            return None, 0, 0
        cyc = cls._detect_cycle(r, max_period=6, min_repeats=2)
        if not cyc:
            return None, 0, 0
        next_val = cyc['next']
        base = 60 if cyc['period'] <= 3 else 55
        conf = min(base + max(0, (6 - cyc['period'])) * 3, 78)
        weight = 1.3 + (6 - min(cyc['period'], 6)) * 0.08
        return next_val, conf, round(weight, 2)

    @classmethod
    def _pattern_similarity(cls, r):
        if len(r) < 15:
            return None, 0, 0
        for wlen in (8, 7, 6, 5, 4):
            if len(r) < wlen + 6:
                continue
            cur = r[-wlen:]
            matches = []
            for i in range(len(r) - wlen):
                window = r[i:i + wlen]
                sim = sum(1 for a, b in zip(cur, window) if a == b) / wlen
                if sim >= 0.85 and i + wlen < len(r):
                    matches.append((sim, r[i + wlen]))
            if len(matches) >= 3:
                weighted = {'Tài': 0.0, 'Xỉu': 0.0}
                for sim, nv in matches:
                    weighted[nv] += sim
                total = weighted['Tài'] + weighted['Xỉu']
                if total == 0:
                    continue
                p_tai = weighted['Tài'] / total
                if p_tai >= 0.62:
                    return 'Tài', min(int(p_tai * 100) + wlen, 85), 1.25
                if p_tai <= 0.38:
                    return 'Xỉu', min(int((1 - p_tai) * 100) + wlen, 85), 1.25
        return None, 0, 0

    @classmethod
    def _flip_break(cls, valid):
        if len(valid) < 10:
            return None, 0, 0
        r = [v['result'] for v in valid]
        cyc = cls._detect_cycle(r, max_period=5, min_repeats=2)
        if not cyc:
            return None, 0, 0
        if 2 <= cyc['period'] <= 4:
            recent = r[-8:]
            flips = sum(1 for i in range(1, len(recent))
                        if recent[i] != recent[i - 1])
            if flips >= 6:
                next_val = 'Xỉu' if cyc['next'] == 'Tài' else 'Tài'
                return next_val, 65, 1.05
        return None, 0, 0

    @classmethod
    def _run_break(cls, valid):
        if len(valid) < 8:
            return None, 0, 0
        r = [v['result'] for v in valid]
        cur_len, cur_val = cls._tail_run(r)
        if cur_len < 6 or cur_len > 10:
            return None, 0, 0
        runs = _run_lengths(r)
        breaks, totals = 0, 0
        idx = 0
        for rlen in runs[:-1]:
            if rlen >= 6:
                totals += 1
                if idx + rlen < len(r):
                    breaks += 1
            idx += rlen
        p = breaks / totals if totals > 0 else 0.5
        if p >= 0.55:
            next_val = 'Xỉu' if cur_val == 'Tài' else 'Tài'
            conf = min(56 + int(p * 20) + (cur_len - 6) * 2, 78)
            return next_val, conf, 1.15
        return None, 0, 0

    @classmethod
    def _regime_signal(cls, valid):
        if len(valid) < 20:
            return None, 0, 0
        r = [v['result'] for v in valid[-20:]]
        alt = sum(1 for i in range(1, len(r)) if r[i] != r[i - 1]) / (len(r) - 1)
        runs = _run_lengths(r)
        avg_run = sum(runs) / len(runs)
        p_tai = cls._seg_ratio(r, 20)
        if avg_run >= 2.2 and alt <= 0.45:
            if p_tai >= 0.55:
                return 'Tài', 60, 0.95
            if p_tai <= 0.45:
                return 'Xỉu', 60, 0.95
        if alt >= 0.70 and avg_run <= 1.6:
            return ('Xỉu' if r[-1] == 'Tài' else 'Tài'), 62, 1.10
        return None, 0, 0

    # ==================== V8.0 NEW ====================
    @classmethod
    def _streak_aware(cls, valid):
        if len(valid) < 15:
            return None, 0, 0
        r = [v['result'] for v in valid]
        cur_len, cur_val = cls._tail_run(r)
        if cur_len == 1:
            prev_run, prev_val = cls._tail_run(r[:-1])
            if prev_run >= 3:
                return cur_val, 62, 1.10
            return None, 0, 0
        if cur_len == 2:
            return cur_val, 58, 0.95
        if cur_len >= 7:
            opp = 'Xỉu' if cur_val == 'Tài' else 'Tài'
            return opp, 70, 1.20
        return None, 0, 0

    @classmethod
    def _tier2_boost(cls, valid):
        if len(valid) < 20:
            return None, 0, 0
        t2 = [
            cls._markov_order1(valid),
            cls._weighted_freq(valid),
            cls._recency_streak(valid),
            cls._momentum(valid),
        ]
        votes = {'Tài': 0.0, 'Xỉu': 0.0}
        count = 0
        for p, c, w in t2:
            if p:
                votes[p] += c * w
                count += 1
        if count < 3:
            return None, 0, 0
        total = votes['Tài'] + votes['Xỉu']
        if total == 0:
            return None, 0, 0
        if votes['Tài'] / total >= 0.68:
            return 'Tài', min(int(votes['Tài'] / total * 100), 76), 0.90
        if votes['Xỉu'] / total >= 0.68:
            return 'Xỉu', min(int(votes['Xỉu'] / total * 100), 76), 0.90
        return None, 0, 0

    @classmethod
    def _volatility_regime(cls, valid):
        if len(valid) < 25:
            return None, 0, 0
        r = [v['result'] for v in valid[-20:]]
        flips = sum(1 for i in range(1, len(r)) if r[i] != r[i - 1])
        flip_ratio = flips / (len(r) - 1)
        runs = _run_lengths(r)
        avg_run = sum(runs) / len(runs)
        p_tai = cls._seg_ratio(r, 20)
        if flip_ratio >= 0.75:
            return ('Xỉu' if r[-1] == 'Tài' else 'Tài'), 66, 1.15
        if flip_ratio <= 0.25:
            cur_len, cur_val = cls._tail_run(r)
            if cur_len <= 3:
                return cur_val, 60, 0.95
            if cur_len >= 5:
                opp = 'Xỉu' if cur_val == 'Tài' else 'Tài'
                return opp, 64, 1.10
        if 0.40 <= flip_ratio <= 0.60:
            if p_tai >= 0.60:
                return 'Tài', 58, 0.95
            if p_tai <= 0.40:
                return 'Xỉu', 58, 0.95
        return None, 0, 0

    @classmethod
    def _cascade_pattern(cls, valid):
        if len(valid) < 20:
            return None, 0, 0
        r = [v['result'] for v in valid]
        results = []
        for scale in (2, 3, 5, 8):
            if len(r) < scale + 5:
                continue
            pat = tuple(r[-scale:])
            matches = []
            for i in range(len(r) - scale):
                if tuple(r[i:i + scale]) == pat and i + scale < len(r):
                    matches.append(r[i + scale])
            if len(matches) >= 2:
                c = Counter(matches)
                top, cnt = c.most_common(1)[0]
                ratio = cnt / len(matches)
                if ratio >= 0.70:
                    results.append((scale, top, ratio))
        if not results:
            return None, 0, 0
        results.sort(key=lambda x: x[0], reverse=True)
        scale, top, ratio = results[0]
        conf = min(55 + int(ratio * 25) + scale * 2, 82)
        weight = 1.05 + (scale / 10)
        return top, conf, min(weight, 1.6)

    # ==================== V9.0 NEW ====================
    @classmethod
    def _regime_router(cls, valid):
        """Định tuyến theo chế độ thị trường."""
        if len(valid) < 20:
            return None, 0, 0
        r = [v['result'] for v in valid]
        regime = _detect_regime(r)

        if regime == 'alternating':
            # Cầu 1-1: dùng alternation + inverse
            p1, c1, w1 = cls._alternation(valid, window=14)
            p2, c2, w2 = cls._invert_cau(valid)
            if p1 and p2 and p1 == p2:
                return p1, min(c1 + 3, 82), 1.4
            if p1:
                return p1, c1, 1.15
            if p2:
                return p2, c2, 1.15

        elif regime == 'streaky':
            # Cầu bệt: follow streak ngắn, bẻ streak dài
            cur_len, cur_val = cls._tail_run(r)
            if cur_len <= 4:
                p, c, w = cls._follow_trend(valid)
                if p:
                    return p, c, 1.2
            if cur_len >= 6:
                p, c, w = cls._exhaustion(valid)
                if p:
                    return p, c, 1.25
            # fallback: markov2
            p, c, w = cls._markov_order2(valid)
            if p:
                return p, c, w * 0.9

        else:  # mixed
            p, c, w = cls._ensemble_boost(valid)
            if p:
                return p, c, w * 0.95

        return None, 0, 0

    @classmethod
    def _pattern_memory_signal(cls, valid):
        """V9.0: dựa vào pattern thắng gần đây trong bộ nhớ."""
        if len(PATTERN_MEMORY) < 5 or len(valid) < 15:
            return None, 0, 0
        r = [v['result'] for v in valid]
        # Tìm pattern trong memory khớp với đuôi hiện tại
        best = None
        for entry in list(PATTERN_MEMORY)[-80:]:
            pat = entry['pattern']
            plen = len(pat)
            if plen > len(r):
                continue
            # so khớp 3 phần tử cuối của pattern với đuôi
            if tuple(r[-min(plen, 5):]) == pat[-min(plen, 5):]:
                score = entry['hits'] * entry['ratio']
                if best is None or score > best[0]:
                    best = (score, entry['next'])
        if best and best[0] >= 1.5:
            conf = min(55 + int(best[0] * 4), 80)
            return best[1], conf, 1.15
        return None, 0, 0

    @classmethod
    def _anti_streak(cls, valid):
        """V9.0: chống lại streak quá dài đang tiếp diễn."""
        if len(valid) < 20:
            return None, 0, 0
        r = [v['result'] for v in valid]
        cur_len, cur_val = cls._tail_run(r)
        # Nếu streak hiện tại đã là streak dài nhất gần đây => có khả năng kết thúc
        runs = _run_lengths(r[-40:])
        if len(runs) < 4:
            return None, 0, 0
        # Bỏ run cuối, lấy max của các run trước
        prev_max = max(runs[:-1]) if len(runs) > 1 else cur_len
        if cur_len >= max(prev_max, 3):
            opp = 'Xỉu' if cur_val == 'Tài' else 'Tài'
            conf = min(58 + (cur_len - prev_max) * 3, 76)
            return opp, conf, 1.10
        return None, 0, 0

    @classmethod
    def _dice_parity(cls, valid):
        """V9.0: dùng tổng xúc xắc để dự đoán xu hướng chẵn/lẻ."""
        recent = [v for v in valid[-30:] if v.get('total') is not None]
        if len(recent) < 15:
            return None, 0, 0
        # Tổng chẵn thường đi với Xỉu, lẻ với Tài (heuristic)
        last_total = recent[-1].get('total', 0)
        last_result = recent[-1]['result']
        # Nếu 3 phiên gần tổng đều chẵn/lẻ và kết quả nghiêng 1 bên
        last_totals = [v.get('total', 0) for v in recent[-5:]]
        even_count = sum(1 for t in last_totals if t % 2 == 0)
        if even_count >= 4:
            return 'Xỉu', 56, 0.85
        if even_count <= 1:
            return 'Tài', 56, 0.85
        return None, 0, 0

    # ==================== HARMONIC / WAVELET ====================
    @classmethod
    def _harmonic_pattern(cls, r):
        if len(r) < 12:
            return None, 0, 0
        trans = defaultdict(lambda: {'Tài': 0.0, 'Xỉu': 0.0})
        for i in range(len(r) - 2):
            key = (r[i], r[i + 1])
            w = 1.0 + (i / len(r)) * 0.5
            trans[key][r[i + 2]] += w
        key = (r[-2], r[-1])
        counts = trans[key]
        total = counts['Tài'] + counts['Xỉu']
        if total < 3:
            return None, 0, 0
        p_tai = counts['Tài'] / total
        if p_tai >= 0.65:
            return 'Tài', min(int(p_tai * 100), 82), 1.20
        if p_tai <= 0.35:
            return 'Xỉu', min(int((1 - p_tai) * 100), 82), 1.20
        return None, 0, 0

    @classmethod
    def _wavelet_trend(cls, valid):
        if len(valid) < 30:
            return None, 0, 0
        r = [v['result'] for v in valid]
        p_short = cls._seg_ratio(r, 5)
        p_medium = cls._seg_ratio(r, 15)
        p_long = cls._seg_ratio(r, 30)
        if p_short > 0.65 and p_medium > 0.58 and p_long > 0.52:
            conf = min(60 + int((p_short - 0.6) * 100), 80)
            return 'Tài', conf, 1.15
        if p_short < 0.35 and p_medium < 0.42 and p_long < 0.48:
            conf = min(60 + int((0.4 - p_short) * 100), 80)
            return 'Xỉu', conf, 1.15
        if p_short > 0.7 and p_long < 0.45:
            return 'Xỉu', 63, 1.10
        if p_short < 0.3 and p_long > 0.55:
            return 'Tài', 63, 1.10
        return None, 0, 0

    @classmethod
    def _seasonal_cycle(cls, r):
        if len(r) < 40:
            return None, 0, 0
        for period in (12, 10, 14, 15, 11, 13):
            if len(r) < period * 3:
                continue
            matches = []
            target_pos = len(r) % period
            for i in range(len(r) - period):
                if i % period == target_pos and i < len(r) - 1:
                    matches.append(r[i])
            if len(matches) >= 3:
                c = Counter(matches)
                top, cnt = c.most_common(1)[0]
                ratio = cnt / len(matches)
                if ratio >= 0.70:
                    conf = min(55 + int(ratio * 20), 78)
                    return top, conf, 1.05
        return None, 0, 0

    @classmethod
    def _markov_weighted(cls, valid):
        if len(valid) < 25:
            return None, 0, 0
        r = [v['result'] for v in valid]
        trans = defaultdict(lambda: {'Tài': 0.0, 'Xỉu': 0.0})
        for i in range(len(r) - 2):
            key = (r[i], r[i + 1])
            w = 0.95 ** (len(r) - i - 2)
            trans[key][r[i + 2]] += w
        key = (r[-2], r[-1])
        counts = trans[key]
        total = counts['Tài'] + counts['Xỉu']
        if total < 2:
            return None, 0, 0
        p_tai = (counts['Tài'] + 0.5) / (total + 1.0)
        if p_tai >= 0.62:
            return 'Tài', min(int(p_tai * 100) + 2, 82), 1.25
        if p_tai <= 0.38:
            return 'Xỉu', min(int((1 - p_tai) * 100) + 2, 82), 1.25
        return None, 0, 0

    @classmethod
    def _ensemble_boost(cls, valid):
        if len(valid) < 20:
            return None, 0, 0
        stable_signals = [
            cls._markov_order2(valid),
            cls._trend(valid),
            cls._transition_matrix(valid),
        ]
        votes = {'Tài': 0.0, 'Xỉu': 0.0}
        count = 0
        for p, c, w in stable_signals:
            if p:
                votes[p] += c
                count += 1
        if count < 2:
            return None, 0, 0
        total = votes['Tài'] + votes['Xỉu']
        if total == 0:
            return None, 0, 0
        if votes['Tài'] >= 0.75 * total:
            return 'Tài', min(int(votes['Tài'] / total * 100), 85), 1.15
        if votes['Xỉu'] >= 0.75 * total:
            return 'Xỉu', min(int(votes['Xỉu'] / total * 100), 85), 1.15
        return None, 0, 0

    @classmethod
    def _decision_tree(cls, valid):
        if len(valid) < 20:
            return None, 0, 0
        r = [v['result'] for v in valid]
        tail_len, tail_val = cls._tail_run(r)
        if tail_len >= 5:
            opp = 'Xỉu' if tail_val == 'Tài' else 'Tài'
            return opp, 68, 1.15
        if 3 <= tail_len <= 4:
            p10 = cls._seg_ratio(r, 10)
            if p10 > 0.65 and tail_val == 'Tài':
                return tail_val, 62, 1.05
            if p10 < 0.35 and tail_val == 'Xỉu':
                return tail_val, 62, 1.05
            opp = 'Xỉu' if tail_val == 'Tài' else 'Tài'
            return opp, 58, 1.00
        if tail_len <= 2:
            p_alt = sum(1 for i in range(1, len(r[-10:]))
                        if r[-10:][i] != r[-10:][i - 1]) / 9
            if p_alt >= 0.65:
                opp = 'Xỉu' if tail_val == 'Tài' else 'Tài'
                return opp, 64, 1.10
        return None, 0, 0

    # ==================== N-GRAM ====================
    @classmethod
    def _ngram_signal(cls, r, n):
        if len(r) < n + 6:
            return None, 0, 0
        pattern = tuple(r[-n:])
        matches = []
        for i in range(len(r) - n):
            if tuple(r[i:i + n]) == pattern and i + n < len(r):
                matches.append(r[i + n])
        if len(matches) < 3:
            return None, 0, 0
        c = Counter(matches)
        top, count = c.most_common(1)[0]
        ratio = count / len(matches)
        if ratio < 0.60:
            return None, 0, 0
        base_conf = 55 + min(n * 3, 15) + int((ratio - 0.6) * 40)
        conf = min(base_conf, 85)
        weight = 1.1 + n * 0.10
        return top, conf, min(weight, 1.7)

    @classmethod
    def _mirror_pattern(cls, r):
        if len(r) < 10:
            return None, 0, 0
        n = 6
        if len(r) < n * 2:
            return None, 0, 0
        recent = r[-n:]
        mirrored = tuple('Xỉu' if x == 'Tài' else 'Tài' for x in reversed(recent))
        matches = []
        for i in range(len(r) - n):
            if tuple(r[i:i + n]) == mirrored and i + n < len(r):
                nxt = r[i + n]
                matches.append('Xỉu' if nxt == 'Tài' else 'Tài')
        if len(matches) >= 2:
            c = Counter(matches)
            top, count = c.most_common(1)[0]
            ratio = count / len(matches)
            if ratio >= 0.65:
                conf = min(55 + int(ratio * 30), 78)
                return top, conf, 1.10
        return None, 0, 0

    @classmethod
    def _reverse_trend(cls, valid):
        if len(valid) < 24:
            return None, 0, 0
        r = [v['result'] for v in valid]
        first = r[-20:-10]
        second = r[-10:]
        t_first = sum(1 for x in first if x == 'Tài') / 10
        t_second = sum(1 for x in second if x == 'Tài') / 10
        if t_first >= 0.7 and t_second <= 0.3:
            return 'Xỉu', 62, 1.15
        if t_first <= 0.3 and t_second >= 0.7:
            return 'Tài', 62, 1.15
        return None, 0, 0

    @classmethod
    def _palindrome_check(cls, r):
        if len(r) < 8:
            return None, 0, 0
        for plen in (5, 4, 3):
            if len(r) < plen * 2:
                continue
            tail = r[-plen:]
            if tail == tail[::-1]:
                for i in range(len(r) - plen * 2 - 1, -1, -1):
                    if r[i:i + plen] == tail and i + plen < len(r):
                        nxt = r[i + plen]
                        conf = 58 + plen * 2
                        return nxt, min(conf, 72), 1.05
        return None, 0, 0

    # ==================== STATISTICAL ====================
    @classmethod
    def _bayes_recent(cls, valid, prior_strength=4.0):
        if len(valid) < 10:
            return None, 0, 0
        recent = [v['result'] for v in valid[-25:]]
        tai = sum(1 for x in recent if x == 'Tài')
        n = len(recent)
        a = tai + prior_strength / 2
        b = (n - tai) + prior_strength / 2
        p_tai = a / (a + b)
        if p_tai >= 0.60:
            return 'Tài', min(int(p_tai * 100) + 3, 78), 1.05
        if p_tai <= 0.40:
            return 'Xỉu', min(int((1 - p_tai) * 100) + 3, 78), 1.05
        return None, 0, 0

    @classmethod
    def _chi_square(cls, valid):
        if len(valid) < 20:
            return None, 0, 0
        recent = [v['result'] for v in valid[-30:]]
        n = len(recent)
        tai = sum(1 for x in recent if x == 'Tài')
        xiu = n - tai
        expected = n / 2
        chi2 = ((tai - expected) ** 2 + (xiu - expected) ** 2) / expected
        if chi2 >= 3.84:
            if tai > xiu:
                return 'Xỉu', min(55 + int((chi2 - 3.84) * 3), 75), 0.85
            else:
                return 'Tài', min(55 + int((chi2 - 3.84) * 3), 75), 0.85
        return None, 0, 0

    @classmethod
    def _runs_prediction(cls, valid):
        if len(valid) < 15:
            return None, 0, 0
        r = [v['result'] for v in valid]
        runs = _run_lengths(r)
        if len(runs) < 5:
            return None, 0, 0
        cur_len, cur_val = cls._tail_run(r)
        run_dist = Counter(runs[:-1])
        total_runs = sum(run_dist.values())
        if total_runs < 5:
            return None, 0, 0
        end_count = sum(c for l, c in run_dist.items() if l <= cur_len)
        p_end = end_count / total_runs
        if p_end >= 0.65:
            opp = 'Xỉu' if cur_val == 'Tài' else 'Tài'
            return opp, min(55 + int(p_end * 25), 75), 1.10
        if p_end <= 0.35:
            return cur_val, min(55 + int((1 - p_end) * 25), 75), 1.00
        return None, 0, 0

    @classmethod
    def _transition_matrix(cls, valid, window=40):
        if len(valid) < 15:
            return None, 0, 0
        data = valid[-window:] if len(valid) > window else valid
        r = [v['result'] for v in data]
        trans = defaultdict(lambda: {'Tài': 0, 'Xỉu': 0})
        for i in range(len(r) - 2):
            key = (r[i], r[i + 1])
            trans[key][r[i + 2]] += 1
        key = (r[-2], r[-1])
        counts = trans[key]
        total = counts['Tài'] + counts['Xỉu']
        if total < 4:
            return None, 0, 0
        p_tai = (counts['Tài'] + 1) / (total + 2)
        if p_tai >= 0.62:
            return 'Tài', min(int(p_tai * 100) + 3, 80), 1.15
        if p_tai <= 0.38:
            return 'Xỉu', min(int((1 - p_tai) * 100) + 3, 80), 1.15
        return None, 0, 0

    # ==================== META ====================
    @classmethod
    def _signal_consensus(cls, valid):
        if len(valid) < 10:
            return None, 0, 0
        key_signals = [
            cls._markov_order2(valid),
            cls._streak_break(valid),
            cls._trend(valid),
            cls._invert_cau(valid),
            cls._alternation(valid),
            cls._momentum(valid),
        ]
        votes = {'Tài': 0, 'Xỉu': 0}
        count = 0
        for p, c, w in key_signals:
            if p:
                votes[p] += c * w
                count += 1
        if count < 4:
            return None, 0, 0
        if votes['Tài'] > votes['Xỉu'] * 1.3:
            return 'Tài', min(int(votes['Tài'] / (votes['Tài'] + votes['Xỉu']) * 100), 80), 1.10
        if votes['Xỉu'] > votes['Tài'] * 1.3:
            return 'Xỉu', min(int(votes['Xỉu'] / (votes['Tài'] + votes['Xỉu']) * 100), 80), 1.10
        return None, 0, 0

    @classmethod
    def _signal_dissent(cls, valid):
        if len(valid) < 15:
            return None, 0, 0
        key_signals = [
            cls._markov_order2(valid),
            cls._streak_break(valid),
            cls._trend(valid),
            cls._invert_cau(valid),
            cls._alternation(valid),
        ]
        votes = {'Tài': 0, 'Xỉu': 0}
        for p, c, w in key_signals:
            if p:
                votes[p] += 1
        total = votes['Tài'] + votes['Xỉu']
        if total < 4:
            return None, 0, 0
        if abs(votes['Tài'] - votes['Xỉu']) <= 1:
            r = [v['result'] for v in valid[-8:]]
            t_count = sum(1 for x in r if x == 'Tài')
            if t_count >= 5:
                return 'Tài', 55, 0.85
            if t_count <= 3:
                return 'Xỉu', 55, 0.85
        return None, 0, 0

    @classmethod
    def _entropy_adaptive(cls, valid):
        if len(valid) < 20:
            return None, 0, 0
        r = [v['result'] for v in valid[-20:]]
        entropy = _entropy(r)
        p_tai = cls._seg_ratio(r, 20)
        if entropy < 0.7:
            if p_tai >= 0.6:
                return 'Tài', 62, 1.05
            if p_tai <= 0.4:
                return 'Xỉu', 62, 1.05
        elif entropy > 0.95:
            return ('Xỉu' if r[-1] == 'Tài' else 'Tài'), 58, 0.95
        return None, 0, 0

    @classmethod
    def _streak_maturity(cls, valid):
        if len(valid) < 12:
            return None, 0, 0
        r = [v['result'] for v in valid]
        cur_len, cur_val = cls._tail_run(r)
        if cur_len < 3:
            return None, 0, 0
        runs = _run_lengths(r)
        max_run = max(runs)
        if max_run < 3:
            return None, 0, 0
        maturity = cur_len / max_run
        if maturity >= 0.8 and cur_len >= 4:
            opp = 'Xỉu' if cur_val == 'Tài' else 'Tài'
            conf = min(55 + int(maturity * 20), 75)
            return opp, conf, 1.10
        return None, 0, 0

    # ==================== ORIGINAL ====================
    @staticmethod
    def _markov_order3(valid):
        if len(valid) < 25:
            return None, 0, 0
        trans = defaultdict(lambda: {'Tài': 0, 'Xỉu': 0})
        for i in range(len(valid) - 3):
            key = (valid[i]['result'], valid[i + 1]['result'], valid[i + 2]['result'])
            trans[key][valid[i + 3]['result']] += 1
        key = (valid[-3]['result'], valid[-2]['result'], valid[-1]['result'])
        counts = trans[key]
        total = counts['Tài'] + counts['Xỉu']
        if total < 3:
            return None, 0, 0
        p_tai = (counts['Tài'] + 1) / (total + 2)
        if p_tai > 0.65:
            return 'Tài', int(p_tai * 100), 1.45
        if p_tai < 0.35:
            return 'Xỉu', int((1 - p_tai) * 100), 1.45
        return None, 0, 0

    @staticmethod
    def _markov_order2(valid, window=None):
        if len(valid) < 15:
            return None, 0, 0
        data = valid[-window:] if window and len(valid) > window else valid
        trans = defaultdict(lambda: {'Tài': 0, 'Xỉu': 0})
        for i in range(len(data) - 2):
            key = (data[i]['result'], data[i + 1]['result'])
            trans[key][data[i + 2]['result']] += 1
        key = (data[-2]['result'], data[-1]['result'])
        counts = trans[key]
        total = counts['Tài'] + counts['Xỉu']
        if total < 3:
            return None, 0, 0
        p_tai = (counts['Tài'] + 1) / (total + 2)
        if p_tai > 0.58:
            return 'Tài', int(p_tai * 100), 1.30
        if p_tai < 0.42:
            return 'Xỉu', int((1 - p_tai) * 100), 1.30
        return None, 0, 0

    @staticmethod
    def _markov_order1(valid, window=None):
        if len(valid) < 8:
            return None, 0, 0
        data = valid[-window:] if window and len(valid) > window else valid
        trans = defaultdict(lambda: {'Tài': 0, 'Xỉu': 0})
        for i in range(len(data) - 1):
            trans[data[i]['result']][data[i + 1]['result']] += 1
        counts = trans[data[-1]['result']]
        total = counts['Tài'] + counts['Xỉu']
        if total < 4:
            return None, 0, 0
        p_tai = (counts['Tài'] + 1) / (total + 2)
        if p_tai > 0.56:
            return 'Tài', int(p_tai * 100), 1.15
        if p_tai < 0.44:
            return 'Xỉu', int((1 - p_tai) * 100), 1.15
        return None, 0, 0

    @staticmethod
    def _streak_break(valid, window=None):
        if len(valid) < 4:
            return None, 0, 0
        r = [v['result'] for v in valid]
        streak, last = MultiStrategyPredictor._tail_run(r)
        opp = 'Xỉu' if last == 'Tài' else 'Tài'
        if streak == 1 and len(r) >= 6:
            prev_run, _ = MultiStrategyPredictor._tail_run(r[:-1])
            if prev_run >= 4:
                return last, 62, 1.10
        if streak < 3 or streak >= 8:
            return None, 0, 0
        breaks, continues = 0, 0
        run = 1
        for i in range(1, len(r)):
            if r[i] == r[i - 1]:
                run += 1
            else:
                if run >= 3:
                    breaks += 1
                run = 1
        if run >= 3:
            continues += 1
        total_obs = breaks + continues
        if streak <= 4:
            if total_obs >= 3:
                p_break = breaks / total_obs
                p_break = 0.5 * p_break + 0.5 * min(0.52 + streak * 0.03, 0.65)
            else:
                p_break = min(0.55 + streak * 0.02, 0.65)
            target, conf = opp, int(p_break * 100)
        elif streak <= 7:
            if total_obs < 2:
                return None, 0, 0
            p_break = breaks / total_obs
            if p_break >= 0.55:
                target, conf = opp, int(p_break * 100)
            elif p_break <= 0.45:
                target, conf = last, int((1 - p_break) * 100)
            else:
                return None, 0, 0
        else:
            return None, 0, 0
        if conf < 55:
            return None, 0, 0
        weight = 1.0 + (streak - 3) * 0.10
        return target, conf, min(weight, 1.6)

    @staticmethod
    def _exhaustion(valid):
        if len(valid) < 10:
            return None, 0, 0
        r = [v['result'] for v in valid]
        streak, last = MultiStrategyPredictor._tail_run(r)
        if streak < 8:
            return None, 0, 0
        opp = 'Xỉu' if last == 'Tài' else 'Tài'
        long_breaks, long_total = 0, 0
        run = 1
        for i in range(1, len(r) - 1):
            if r[i] == r[i - 1]:
                run += 1
            else:
                if run >= 8:
                    long_total += 1
                    long_breaks += 1
                run = 1
        if run >= 8:
            long_total += 1
        if streak <= 9:
            base = 55 + (streak - 8) * 2
        elif streak <= 12:
            base = 60 + (streak - 10) * 2
        else:
            base = 70 + min((streak - 13) * 2, 8)
        if long_total > 0:
            p_break = long_breaks / long_total
            base = int(base * 0.6 + p_break * 100 * 0.4)
        else:
            base -= 8
        conf = max(55, min(int(base), 80))
        weight = 1.1 + (streak - 8) * 0.08
        return opp, conf, min(weight, 1.5)

    @staticmethod
    def _dominance_flip(valid):
        if len(valid) < 20:
            return None, 0, 0
        r = [v['result'] for v in valid[-20:]]
        t = sum(1 for x in r if x == 'Tài') / 20
        if t >= 0.85:
            return 'Xỉu', max(min(int((t - 0.75) * 200), 75), 58), 0.90
        if t <= 0.15:
            return 'Tài', max(min(int((0.25 - t) * 200), 75), 58), 0.90
        return None, 0, 0

    @staticmethod
    def _follow_trend(valid):
        if len(valid) < 8:
            return None, 0, 0
        r = [v['result'] for v in valid]
        streak, last = MultiStrategyPredictor._tail_run(r)
        if streak >= 8:
            return None, 0, 0
        if streak >= 5:
            return last, min(58 + (streak - 5) * 4, 72), 1.20
        if len(r) >= 15:
            p15 = MultiStrategyPredictor._seg_ratio(r, 15)
            p5 = MultiStrategyPredictor._seg_ratio(r, 5)
            if 0.70 <= p15 <= 0.80 and p5 >= 0.60:
                return 'Tài', min(55 + int((p15 - 0.6) * 100), 72), 1.10
            if 0.20 <= p15 <= 0.30 and p5 <= 0.40:
                return 'Xỉu', min(55 + int((0.4 - p15) * 100), 72), 1.10
        return None, 0, 0

    @staticmethod
    def _invert_cau(valid):
        if len(valid) < 12:
            return None, 0, 0
        r = [v['result'] for v in valid[-16:]]
        alt = sum(1 for i in range(1, len(r)) if r[i] != r[i - 1])
        alt_ratio = alt / (len(r) - 1)
        if alt_ratio >= 0.65:
            nxt = 'Xỉu' if r[-1] == 'Tài' else 'Tài'
            return nxt, min(56 + int(alt_ratio * 30), 82), 1.30
        block2_hits, block2_total = 0, 0
        for i in range(2, len(r)):
            if r[i] == r[i - 2] and r[i] != r[i - 1]:
                block2_hits += 1
            if r[i] != r[i - 1]:
                block2_total += 1
        if block2_total >= 4 and block2_hits / block2_total >= 0.7:
            tail_run, _ = MultiStrategyPredictor._tail_run(r)
            nxt = r[-2] if tail_run == 1 else ('Xỉu' if r[-1] == 'Tài' else 'Tài')
            return nxt, 68, 1.25
        return None, 0, 0

    @staticmethod
    def _trend(valid):
        if len(valid) < 20:
            return None, 0, 0
        r = [v['result'] for v in valid]

        def tai_ratio(win):
            seg = r[-win:]
            return sum(1 for x in seg if x == 'Tài') / len(seg)

        p10 = tai_ratio(10)
        p20 = tai_ratio(20) if len(r) >= 20 else p10
        p40 = tai_ratio(40) if len(r) >= 40 else p20
        if p10 > 0.55 and p20 > 0.52 and p10 >= p20 - 0.05 and p10 - p40 >= 0.05:
            return 'Tài', min(int((p10 - 0.5) * 200 + 52), 78), 1.20
        if p10 < 0.45 and p20 < 0.48 and p10 <= p20 + 0.05 and p40 - p10 >= 0.05:
            return 'Xỉu', min(int((0.5 - p10) * 200 + 52), 78), 1.20
        return None, 0, 0

    @staticmethod
    def _bias_run(valid):
        if len(valid) < 24:
            return None, 0, 0
        r = [v['result'] for v in valid[-24:]]
        first, second = r[:12], r[12:]
        r_first = sum(1 for x in first if x == 'Tài') / 12
        r_second = sum(1 for x in second if x == 'Tài') / 12
        if r_second - r_first >= 0.25 and r_second >= 0.60:
            return 'Tài', min(int((r_second - 0.5) * 180 + 55), 80), 1.15
        if r_first - r_second >= 0.25 and (1 - r_second) >= 0.60:
            return 'Xỉu', min(int((0.5 - r_second) * -180 + 55), 80), 1.15
        if r_second >= 0.75:
            return 'Tài', 65, 1.00
        if r_second <= 0.25:
            return 'Xỉu', 65, 1.00
        return None, 0, 0

    @staticmethod
    def _pattern_match(valid, window=None):
        if len(valid) < 15:
            return None, 0, 0
        results = [v['result'] for v in valid]
        best_hit, best_conf, best_len = None, 0, 0
        for plen in (7, 6, 5, 4, 3):
            if len(results) < plen + 5:
                continue
            pattern = tuple(results[-plen:])
            matches = []
            for i in range(len(results) - plen):
                if tuple(results[i:i + plen]) == pattern and i + plen < len(results):
                    matches.append(results[i + plen])
            if len(matches) >= 3:
                c = Counter(matches)
                top, count = c.most_common(1)[0]
                conf = int(count / len(matches) * 100)
                conf_adj = min(conf + min(plen * 2, 10), 88)
                if conf_adj > best_conf and conf >= 55:
                    best_hit, best_conf, best_len = top, conf_adj, plen
        if best_hit:
            weight = 1.2 + best_len * 0.08
            return best_hit, best_conf, min(weight, 1.8)
        return None, 0, 0

    @staticmethod
    def _weighted_freq(valid, window=30):
        recent = valid[-window:]
        if len(recent) < 8:
            return None, 0, 0
        tai, xiu = 0.0, 0.0
        for i, v in enumerate(recent):
            w = ((i + 1) / len(recent)) ** 1.5
            if v['result'] == 'Tài':
                tai += w
            else:
                xiu += w
        total = tai + xiu
        if total == 0:
            return None, 0, 0
        p = tai / total
        if p > 0.58:
            return 'Tài', int(p * 100), 0.85
        if p < 0.42:
            return 'Xỉu', int((1 - p) * 100), 0.85
        return None, 0, 0

    @staticmethod
    def _dice_bias(valid, window=50):
        recent = [v for v in valid[-window:] if v.get('total')]
        if len(recent) < 10:
            return None, 0, 0
        tais = sum(1 for v in recent if v['result'] == 'Tài')
        ratio = tais / len(recent)
        if ratio > 0.62:
            return 'Xỉu', max(int(min((ratio - 0.5) * 200, 75)), 55), 0.65
        if ratio < 0.38:
            return 'Tài', max(int(min((0.5 - ratio) * 200, 75)), 55), 0.65
        return None, 0, 0

    @staticmethod
    def _alternation(valid, window=12):
        recent = [v['result'] for v in valid[-window:]]
        if len(recent) < 6:
            return None, 0, 0
        alt = sum(1 for i in range(1, len(recent)) if recent[i] != recent[i - 1])
        alt_ratio = alt / (len(recent) - 1)
        if alt_ratio >= 0.7:
            nxt = 'Xỉu' if recent[-1] == 'Tài' else 'Tài'
            return nxt, min(int(alt_ratio * 100) + 5, 85), 1.45
        return None, 0, 0

    @staticmethod
    def _momentum(valid, window=10):
        recent = [v['result'] for v in valid[-window:]]
        if len(recent) < 6:
            return None, 0, 0
        tai = sum(1 for r in recent if r == 'Tài')
        p = tai / len(recent)
        if p >= 0.7:
            return 'Tài', int(p * 100), 0.95
        if p <= 0.3:
            return 'Xỉu', int((1 - p) * 100), 0.95
        return None, 0, 0

    @staticmethod
    def _recency_streak(valid):
        if len(valid) < 10:
            return None, 0, 0
        weights = {'Tài': 0.0, 'Xỉu': 0.0}
        n = 10
        for i in range(len(valid) - n, len(valid)):
            w = ((i - (len(valid) - n) + 1) / n) ** 2
            weights[valid[i]['result']] += w
        total = weights['Tài'] + weights['Xỉu']
        if total == 0:
            return None, 0, 0
        p = weights['Tài'] / total
        if p > 0.6:
            return 'Tài', int(p * 100), 1.15
        if p < 0.4:
            return 'Xỉu', int((1 - p) * 100), 1.15
        return None, 0, 0

    @classmethod
    def _time_pattern(cls, valid):
        if len(valid) < 20:
            return None, 0, 0
        r = [v['result'] for v in valid]
        for period in (5, 6, 7, 8, 9, 10):
            if len(r) < period * 3:
                continue
            target_pos = (len(r) % period)
            matching_pos = [r[i] for i in range(len(r)) if i % period == target_pos and i < len(r) - 1]
            if len(matching_pos) >= 3:
                c = Counter(matching_pos)
                top, cnt = c.most_common(1)[0]
                ratio = cnt / len(matching_pos)
                if ratio >= 0.65:
                    conf = min(55 + int(ratio * 25), 75)
                    return top, conf, 1.05
        return None, 0, 0

    @classmethod
    def _volatility(cls, valid):
        if len(valid) < 20:
            return None, 0, 0
        r = [v['result'] for v in valid[-20:]]
        flips = sum(1 for i in range(1, len(r)) if r[i] != r[i - 1])
        flip_ratio = flips / (len(r) - 1)
        if flip_ratio >= 0.85:
            return ('Xỉu' if r[-1] == 'Tài' else 'Tài'), 64, 1.15
        if flip_ratio <= 0.15:
            cur_len, cur_val = cls._tail_run(r)
            if cur_len >= 4:
                return ('Xỉu' if cur_val == 'Tài' else 'Tài'), 62, 1.10
        return None, 0, 0

    @classmethod
    def _cusum(cls, valid):
        if len(valid) < 15:
            return None, 0, 0
        r = [v['result'] for v in valid[-20:]]
        vals = [1 if x == 'Tài' else -1 for x in r]
        mean = sum(vals) / len(vals)
        s_pos, s_neg = 0.0, 0.0
        threshold = 1.5
        for v in vals:
            s_pos = max(0, s_pos + (v - mean))
            s_neg = max(0, s_neg - (v - mean))
        if s_pos > threshold and mean > 0:
            return 'Xỉu', 62, 1.05
        if s_neg > threshold and mean < 0:
            return 'Tài', 62, 1.05
        return None, 0, 0

    @classmethod
    def _kelly_optimal(cls, valid):
        if len(valid) < 15:
            return None, 0, 0
        recent = [v['result'] for v in valid[-15:]]
        tai = sum(1 for x in recent if x == 'Tài')
        p = tai / len(recent)
        kelly = 2 * p - 1
        if kelly >= 0.30:
            return 'Tài', min(55 + int(kelly * 40), 80), 1.10
        if kelly <= -0.30:
            return 'Xỉu', min(55 + int(-kelly * 40), 80), 1.10
        return None, 0, 0

    @classmethod
    def _multi_window(cls, valid):
        if len(valid) < 25:
            return None, 0, 0
        r = [v['result'] for v in valid]
        windows = [5, 10, 15, 20]
        votes = []
        for w in windows:
            seg = r[-w:]
            t = sum(1 for x in seg if x == 'Tài') / w
            if t >= 0.60:
                votes.append('Tài')
            elif t <= 0.40:
                votes.append('Xỉu')
        if len(votes) >= 3:
            c = Counter(votes)
            top, cnt = c.most_common(1)[0]
            if cnt >= 3:
                conf = min(55 + cnt * 5, 75)
                return top, conf, 1.15
        return None, 0, 0

    @classmethod
    def _rsi_signal(cls, valid, period=14):
        if len(valid) < period + 5:
            return None, 0, 0
        r = [v['result'] for v in valid]
        changes = []
        for i in range(1, len(r)):
            if r[i] == r[i - 1]:
                changes.append(0)
            elif r[i] == 'Tài':
                changes.append(1)
            else:
                changes.append(-1)
        recent = changes[-period:]
        gains = sum(c for c in recent if c > 0)
        losses = sum(-c for c in recent if c < 0)
        if gains + losses == 0:
            return None, 0, 0
        rs = gains / (losses + 0.01)
        rsi = 100 - 100 / (1 + rs)
        if rsi >= 75:
            return 'Xỉu', min(55 + int((rsi - 70)), 78), 1.10
        if rsi <= 25:
            return 'Tài', min(55 + int((30 - rsi)), 78), 1.10
        return None, 0, 0

    @classmethod
    def _fibonacci_break(cls, valid):
        if len(valid) < 15:
            return None, 0, 0
        r = [v['result'] for v in valid]
        cur_len, cur_val = cls._tail_run(r)
        fibs = [3, 5, 8, 13]
        for fib in fibs:
            if cur_len == fib or cur_len == fib + 1:
                opp = 'Xỉu' if cur_val == 'Tài' else 'Tài'
                conf = min(58 + fib, 75)
                return opp, conf, 1.10
        return None, 0, 0

    @classmethod
    def _zscore_extreme(cls, valid):
        if len(valid) < 20:
            return None, 0, 0
        r = [v['result'] for v in valid[-20:]]
        vals = [1 if x == 'Tài' else 0 for x in r]
        mean = sum(vals) / len(vals)
        variance = sum((v - mean) ** 2 for v in vals) / len(vals)
        std = variance ** 0.5
        if std < 0.01:
            return None, 0, 0
        z = (vals[-1] - mean) / std
        if z >= 1.5:
            return 'Xỉu', 62, 0.95
        if z <= -1.5:
            return 'Tài', 62, 0.95
        return None, 0, 0

    # ==================== ADAPTIVE ====================
    @classmethod
    def _get_adaptive_weight(cls, name, base_weight):
        tr = cls._STRATEGY_TRACK.get(name)
        if not tr:
            return base_weight
        n = tr['hit'] + tr['miss']
        if n < 8:
            return base_weight
        acc = tr['hit'] / n
        factor = 0.55 + (acc - 0.5) * 2.8
        return base_weight * max(0.40, min(factor, 1.65))

    @classmethod
    def _update_track(cls, name, pred, actual):
        if pred is None:
            return
        tr = cls._STRATEGY_TRACK[name]
        if pred == actual:
            tr['hit'] += 1
        else:
            tr['miss'] += 1

    @classmethod
    def _sync_track_from_history(cls, valid):
        if not valid:
            return
        cur_len = len(valid)
        if cls._STRATEGY_TRACK.get('__len__', {}).get('last_len') == cur_len:
            return
        cls._STRATEGY_TRACK = defaultdict(lambda: {'hit': 0, 'miss': 0, 'last_len': 0})
        start = max(15, len(valid) - 80)
        for i in range(start, len(valid) - 1):
            history = valid[:i]
            actual = valid[i]['result']
            for fname, fn in cls._strategy_list():
                try:
                    p, c, w = fn(history)
                    if p is not None:
                        cls._update_track(fname, p, actual)
                except Exception:
                    pass
        cls._STRATEGY_TRACK['__len__'] = {'last_len': cur_len}

    @classmethod
    def _check_failure_streak(cls, log=None):
        if log is None:
            log = PREDICTION_LOG
        evaluated = [
            (p, e) for p, e in sorted(log.items(), reverse=True)
            if e.get('actual') is not None
        ]
        if len(evaluated) < 2:
            return 0
        return sum(1 for _, e in evaluated[:2] if e.get('correct') is False)

    @classmethod
    def _was_flipped_last(cls, log=None):
        if log is None:
            log = PREDICTION_LOG
        evaluated = [
            e for p, e in sorted(log.items(), reverse=True)
            if e.get('actual') is not None
        ]
        return evaluated[0].get('flipped', False) if evaluated else False

    @staticmethod
    def _calibrate_confidence(raw_conf, log=None):
        if log is None:
            log = PREDICTION_LOG
        evaluated = [e for e in log.values() if e.get('actual') is not None]
        if len(evaluated) < 20:
            return raw_conf

        recent30 = sorted(
            [(p, e) for p, e in log.items() if e.get('actual') is not None],
            reverse=True
        )[:30]
        if not recent30:
            return raw_conf
        acc30 = sum(1 for _, e in recent30 if e.get('correct')) / len(recent30)

        recent100 = sorted(
            [(p, e) for p, e in log.items() if e.get('actual') is not None],
            reverse=True
        )[:100]
        acc100 = sum(1 for _, e in recent100 if e.get('correct')) / len(recent100) if recent100 else acc30

        acc_blend = acc30 * 0.6 + acc100 * 0.4

        if acc_blend < 0.42:
            return max(50, raw_conf - 14)
        if acc_blend < 0.48:
            return max(50, raw_conf - 8)
        if acc_blend < 0.52:
            return max(50, raw_conf - 3)
        if acc_blend > 0.70:
            return min(84, raw_conf + 5)
        if acc_blend > 0.62:
            return min(84, raw_conf + 2)
        return raw_conf

    # ==================== STRATEGY LIST ====================
    @classmethod
    def _strategy_list(cls):
        return [
            ('markov3',      cls._markov_order3),
            ('markov2',      cls._markov_order2),
            ('markov1',      cls._markov_order1),
            ('streak',       cls._streak_break),
            ('pattern',      cls._pattern_match),
            ('weighted',     cls._weighted_freq),
            ('dice',         cls._dice_bias),
            ('alternation',  cls._alternation),
            ('momentum',     cls._momentum),
            ('recency',      cls._recency_streak),
            ('invert',       cls._invert_cau),
            ('trend',        cls._trend),
            ('bias_run',     cls._bias_run),
            ('follow',       cls._follow_trend),
            ('exhaust',      cls._exhaustion),
            ('dominance',    cls._dominance_flip),
            ('cycle_1_1',    lambda v: cls._cycle_signal([x['result'] for x in v], [1, 1])),
            ('cycle_2_2',    lambda v: cls._cycle_signal([x['result'] for x in v], [2, 2])),
            ('cycle_3_3',    lambda v: cls._cycle_signal([x['result'] for x in v], [3, 3])),
            ('cycle_1_2',    lambda v: cls._cycle_signal([x['result'] for x in v], [1, 2])),
            ('cycle_2_1',    lambda v: cls._cycle_signal([x['result'] for x in v], [2, 1])),
            ('cycle_1_3',    lambda v: cls._cycle_signal([x['result'] for x in v], [1, 3])),
            ('cycle_3_1',    lambda v: cls._cycle_signal([x['result'] for x in v], [3, 1])),
            ('cycle_2_3',    lambda v: cls._cycle_signal([x['result'] for x in v], [2, 3])),
            ('cycle_3_2',    lambda v: cls._cycle_signal([x['result'] for x in v], [3, 2])),
            ('cycle_123',    lambda v: cls._cycle_signal([x['result'] for x in v], [1, 2, 3])),
            ('cycle_321',    lambda v: cls._cycle_signal([x['result'] for x in v], [3, 2, 1])),
            ('cycle_346',    lambda v: cls._cycle_signal([x['result'] for x in v], [3, 4, 6])),
            ('cycle_112',    lambda v: cls._cycle_signal([x['result'] for x in v], [1, 1, 2])),
            ('cycle_212',    lambda v: cls._cycle_signal([x['result'] for x in v], [2, 1, 2])),
            ('cycle_122',    lambda v: cls._cycle_signal([x['result'] for x in v], [1, 2, 2])),
            ('cycle_221',    lambda v: cls._cycle_signal([x['result'] for x in v], [2, 2, 1])),
            ('cycle_1234',   lambda v: cls._cycle_signal([x['result'] for x in v], [1, 2, 3, 4])),
            ('cycle_2341',   lambda v: cls._cycle_signal([x['result'] for x in v], [2, 3, 4, 1])),
            ('cycle_3456',   lambda v: cls._cycle_signal([x['result'] for x in v], [3, 4, 5, 6])),
            ('cycle_4_4',    lambda v: cls._cycle_signal([x['result'] for x in v], [4, 4])),
            ('auto_cycle',   lambda v: cls._auto_cycle_signal([x['result'] for x in v])),
            ('pattern_sim',  lambda v: cls._pattern_similarity([x['result'] for x in v])),
            ('regime',       cls._regime_signal),
            ('flip_break',   cls._flip_break),
            ('run_break',    cls._run_break),
            ('ngram_2',      lambda v: cls._ngram_signal([x['result'] for x in v], 2)),
            ('ngram_3',      lambda v: cls._ngram_signal([x['result'] for x in v], 3)),
            ('ngram_4',      lambda v: cls._ngram_signal([x['result'] for x in v], 4)),
            ('ngram_5',      lambda v: cls._ngram_signal([x['result'] for x in v], 5)),
            ('mirror_pattern',  lambda v: cls._mirror_pattern([x['result'] for x in v])),
            ('reverse_trend',   cls._reverse_trend),
            ('palindrome_check', lambda v: cls._palindrome_check([x['result'] for x in v])),
            ('bayes_recent',      cls._bayes_recent),
            ('chi_square',        cls._chi_square),
            ('runs_prediction',   cls._runs_prediction),
            ('transition_matrix', cls._transition_matrix),
            ('double_break',      lambda v: cls._cycle_signal([x['result'] for x in v], [2, 2])),
            ('signal_consensus',  cls._signal_consensus),
            ('signal_dissent',    cls._signal_dissent),
            ('entropy_adaptive',  cls._entropy_adaptive),
            ('streak_maturity',   cls._streak_maturity),
            ('time_pattern',      cls._time_pattern),
            ('volatility',        cls._volatility),
            ('cusum',             cls._cusum),
            ('kelly_optimal',     cls._kelly_optimal),
            ('multi_window',      cls._multi_window),
            ('rsi_signal',        cls._rsi_signal),
            ('fibonacci_break',   cls._fibonacci_break),
            ('zscore_extreme',    cls._zscore_extreme),
            ('harmonic_pattern',  lambda v: cls._harmonic_pattern([x['result'] for x in v])),
            ('wavelet_trend',     cls._wavelet_trend),
            ('seasonal_cycle',    lambda v: cls._seasonal_cycle([x['result'] for x in v])),
            ('markov_weighted',   cls._markov_weighted),
            ('ensemble_boost',    cls._ensemble_boost),
            ('decision_tree',     cls._decision_tree),
            ('streak_aware',      cls._streak_aware),
            ('tier2_boost',       cls._tier2_boost),
            ('volatility_regime', cls._volatility_regime),
            ('cascade_pattern',   cls._cascade_pattern),
            # V9.0 NEW
            ('regime_router',     cls._regime_router),
            ('pattern_memory',    cls._pattern_memory_signal),
            ('anti_streak',       cls._anti_streak),
            ('dice_parity',       cls._dice_parity),
        ]

    # ==================== MAIN PREDICT V9.0 ====================
    @classmethod
    def predict(cls, valid, log=None):
        if log is None:
            log = PREDICTION_LOG

        if not valid:
            seed = str(int(time.time()) // 60)
            return {
                'prediction': cls._deterministic_choice(['Tài', 'Xỉu'], seed),
                'confidence': 50, 'method': 'Chưa có DL',
                'signals': [], 'top_signals': ['Chưa có DL'],
                'signals_strength': [],
                'reason': 'Không có dữ liệu', 'flipped': False,
            }

        if len(valid) < 5:
            r = [v['result'] for v in valid]
            streak, last = cls._tail_run(r)
            opp = 'Xỉu' if last == 'Tài' else 'Tài'
            if streak == 1:
                method = f'Đảo {last}'
                reason = f'1 phiên {last}'
                conf = 52
            elif streak <= 3:
                method = f'Bẻ {last}×{streak}'
                reason = f'{streak} phiên {last}'
                conf = 52 + streak * 2
            else:
                method = f'Bẻ {last}×{streak}'
                reason = f'{streak} phiên {last}'
                conf = min(60 + (streak - 3) * 3, 70)
            return {
                'prediction': opp, 'confidence': conf,
                'method': method, 'signals': [],
                'top_signals': [method], 'signals_strength': [],
                'reason': reason, 'flipped': False,
            }

        cls._sync_track_from_history(valid)

        # === V9.0: phát hiện chế độ ===
        all_results = [v['result'] for v in valid]
        regime = _detect_regime(all_results)

        log_odds = 0.0
        signals = []
        total_weight = 0.0
        strong_signals = []  # V9.0: signals có conf >= 62

        for name, fn in cls._strategy_list():
            # V9.0: skip blacklisted signals
            if name in STRATEGY_BLACKLIST:
                continue
            try:
                p, conf, weight = fn(valid)
            except Exception:
                continue
            if p is None or weight == 0:
                continue
            weight = cls._get_adaptive_weight(name, weight)
            p_norm = max(0.51, min(conf / 100.0, 0.90))
            lr = p_norm / (1 - p_norm)
            if p == 'Xỉu':
                lr = 1.0 / lr
            contrib = weight * (1.0 if p == 'Tài' else -1.0) * (lr - 1.0)
            log_odds += contrib
            total_weight += weight
            sig = {'name': name, 'prediction': p,
                   'confidence': conf, 'weight': round(weight, 2)}
            signals.append(sig)
            if conf >= 62 and weight >= 1.1:
                strong_signals.append(sig)

        if not signals or total_weight == 0:
            r = [v['result'] for v in valid]
            cyc = cls._detect_cycle(r, max_period=6, min_repeats=2)
            if cyc:
                return {
                    'prediction': cyc['next'], 'confidence': 55,
                    'method': f"Chu kỳ {cyc['period']}",
                    'signals': [], 'top_signals': [f"Chu kỳ {cyc['period']}"],
                    'signals_strength': [],
                    'reason': f"Phát hiện chu kỳ {cyc['period']}",
                    'flipped': False,
                }
            streak, last = cls._tail_run(r)
            opp = 'Xỉu' if last == 'Tài' else 'Tài'
            return {
                'prediction': opp, 'confidence': 52,
                'method': f'Bẻ {last}×{streak}',
                'signals': [], 'top_signals': [f'Bẻ {last}×{streak}'],
                'signals_strength': [],
                'reason': f'Bẻ streak {streak}',
                'flipped': False,
            }

        scaled = log_odds / max(total_weight, 1.0) * 1.8
        p_tai = 1.0 / (1.0 + math.exp(-scaled))
        if p_tai >= 0.5:
            pred = 'Tài'
            raw_conf = int(p_tai * 100)
        else:
            pred = 'Xỉu'
            raw_conf = int((1 - p_tai) * 100)

        # === V9.0: Regime-based adjustment ===
        recent = [v['result'] for v in valid[-20:]]
        entropy = _entropy(recent)
        votes_tai = sum(1 for s in signals if s['prediction'] == 'Tài')
        votes_xiu = len(signals) - votes_tai
        agreement = max(votes_tai, votes_xiu) / len(signals)

        penalty = 0
        if entropy > 0.95:
            penalty += 10
        elif entropy > 0.85:
            penalty += 6
        elif entropy > 0.70:
            penalty += 3
        if agreement < 0.6:
            penalty += 8
        elif agreement < 0.75:
            penalty += 4
        bonus = 4 if agreement >= 0.9 and entropy < 0.6 else 0

        # V9.0: regime bonus
        if regime == 'alternating' and pred == ('Xỉu' if recent[-1] == 'Tài' else 'Tài'):
            bonus += 3
        elif regime == 'streaky':
            cur_len, cur_val = cls._tail_run(recent)
            if cur_len <= 4 and pred == cur_val:
                bonus += 2
            elif cur_len >= 6 and pred != cur_val:
                bonus += 2

        final_conf = max(50, min(raw_conf - penalty + bonus, 84))

        # === V9.0: Confidence Gate ===
        # Nếu confidence < 55 và có strong signals, dùng strong signal
        if final_conf < 55 and strong_signals:
            strong_votes = {'Tài': 0, 'Xỉu': 0}
            for s in strong_signals:
                strong_votes[s['prediction']] += s['confidence'] * s['weight']
            if strong_votes['Tài'] > strong_votes['Xỉu'] * 1.4:
                pred = 'Tài'
                final_conf = max(55, final_conf)
            elif strong_votes['Xỉu'] > strong_votes['Tài'] * 1.4:
                pred = 'Xỉu'
                final_conf = max(55, final_conf)
            # Nếu vẫn yếu, dùng fallback an toàn
            elif final_conf < 52:
                cur_len, cur_val = cls._tail_run(recent)
                if cur_len >= 3:
                    pred = 'Xỉu' if cur_val == 'Tài' else 'Tài'
                else:
                    pred = 'Xỉu' if recent[-1] == 'Tài' else 'Tài'
                final_conf = 54

        signals_sorted = sorted(
            signals,
            key=lambda s: s['weight'] * (s['confidence'] - 50),
            reverse=True
        )
        top_signals = [SIGNAL_NAMES.get(s['name'], s['name'])
                       for s in signals_sorted[:5]]

        result = {
            'prediction': pred, 'confidence': final_conf,
            'method': ' + '.join(top_signals[:3]) if top_signals else 'Tổng hợp',
            'signals': signals, 'top_signals': top_signals,
            'signals_strength': [
                {
                    'name': s['name'],
                    'display': SIGNAL_NAMES.get(s['name'], s['name']),
                    'prediction': s['prediction'],
                    'confidence': s['confidence'],
                    'weight': s['weight'],
                    'strength': round(s['weight'] * (s['confidence'] - 50), 1),
                }
                for s in signals_sorted[:8]
            ],
            'reason': (f"Regime={regime} · {len(signals)} tín hiệu · "
                       f"entropy={entropy:.2f} · đồng thuận={agreement:.0%}"),
            'flipped': False,
            'regime': regime,
        }

        # === Anti-failure flip ===
        fail_streak = cls._check_failure_streak(log)
        if fail_streak >= 2 and not cls._was_flipped_last(log):
            result['prediction'] = 'Xỉu' if result['prediction'] == 'Tài' else 'Tài'
            result['confidence'] = max(55, min(result['confidence'], 72))
            result['method'] = '⚠ ' + result['method']
            result['reason'] += f" · SAI {fail_streak} → ĐẢO"
            result['flipped'] = True

        result['confidence'] = cls._calibrate_confidence(result['confidence'], log)

        # === V9.0: update pattern memory ===
        try:
            cls._update_pattern_memory(valid)
        except Exception:
            pass

        return result

    @classmethod
    def _update_pattern_memory(cls, valid):
        """V9.0: cập nhật bộ nhớ pattern thắng."""
        global PATTERN_MEMORY
        if len(valid) < 10:
            return
        r = [v['result'] for v in valid]
        # Lấy pattern 3-5 phần tử cuối đã có kết quả
        for plen in (3, 4, 5):
            if len(r) < plen + 1:
                continue
            pat = tuple(r[-plen - 1:-1])  # pattern trước kết quả cuối
            nxt = r[-1]
            # Đếm số lần pattern này xuất hiện và đi với nxt
            hits = 0
            total = 0
            for i in range(len(r) - plen - 1):
                if tuple(r[i:i + plen]) == pat:
                    total += 1
                    if r[i + plen] == nxt:
                        hits += 1
            if total >= 2 and hits / total >= 0.6:
                PATTERN_MEMORY.append({
                    'pattern': pat,
                    'next': nxt,
                    'hits': hits,
                    'ratio': hits / total,
                })


# =====================================================================
# DATA FETCHERS
# =====================================================================
def fetch_sessions():
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'application/json',
    }
    for attempt in range(3):
        try:
            r = requests.get(API_URL_MD5, timeout=6, headers=headers)
            if r.status_code == 200:
                data = r.json()
                if 'list' in data and data['list']:
                    rows = []
                    for item in data['list']:
                        res = "Tài" if item['resultTruyenThong'] == "TAI" else "Xỉu"
                        dices = item.get('dices', [0, 0, 0])
                        rows.append({
                            'phien': int(item['id']),
                            'result': res,
                            'dice': f"{dices[0]} + {dices[1]} + {dices[2]}",
                            'total': item.get('point', sum(dices)),
                        })
                    return rows, True
        except Exception as e:
            print(f"[API ERROR] attempt {attempt + 1}: {e}")
            time.sleep(1)
    return [], False


def _sync_history():
    raw_rows, success = fetch_sessions()
    if not success and not HISTORY_CACHE:
        return [], False
    for row in raw_rows:
        HISTORY_CACHE[row['phien']] = row
    if RESET_ID > 0:
        for k in [k for k in HISTORY_CACHE if k < RESET_ID]:
            del HISTORY_CACHE[k]
    valid = sorted(HISTORY_CACHE.values(), key=lambda x: x['phien'])
    _update_prediction_log(valid)
    return valid, True


def _update_prediction_log(valid):
    if not valid:
        return
    by_phien = {v['phien']: v for v in valid}
    phien_list = sorted(by_phien.keys())
    for phien, entry in list(PREDICTION_LOG.items()):
        if entry.get('actual') is not None:
            continue
        if phien in by_phien:
            actual = by_phien[phien]['result']
            entry['actual'] = actual
            entry['correct'] = (entry['prediction'] == actual)
    changed = False
    for i, phien in enumerate(phien_list):
        if phien in PREDICTION_LOG or phien <= RESET_ID:
            continue
        history_before = [by_phien[p] for p in phien_list[:i]]
        if len(history_before) < 3:
            continue
        pred = MultiStrategyPredictor.predict(history_before, log=PREDICTION_LOG)
        actual = by_phien[phien]['result']
        PREDICTION_LOG[phien] = {
            'prediction': pred['prediction'],
            'confidence': pred['confidence'],
            'method': pred['method'],
            'actual': actual,
            'correct': (pred['prediction'] == actual),
            'logged_at': datetime.now().isoformat(),
            'retroactive': True,
            'flipped': pred.get('flipped', False),
        }
        changed = True
    if len(PREDICTION_LOG) > MAX_LOG_SIZE:
        keys = sorted(PREDICTION_LOG.keys())[:len(PREDICTION_LOG) - MAX_LOG_SIZE]
        for k in keys:
            del PREDICTION_LOG[k]
        changed = True
    if changed:
        _save_log()


def _log_prediction(phien, pred_result):
    if phien in PREDICTION_LOG:
        return
    PREDICTION_LOG[phien] = {
        'prediction': pred_result['prediction'],
        'confidence': pred_result['confidence'],
        'method': pred_result['method'],
        'actual': None, 'correct': None,
        'logged_at': datetime.now().isoformat(),
        'retroactive': False,
        'flipped': pred_result.get('flipped', False),
    }
    _save_log()


def _honest_stats(valid):
    total = len(valid)
    tai = sum(1 for v in valid if v['result'] == 'Tài')
    return {'total': total, 'tai': tai, 'xiu': total - tai,
            'ratio_tai': round(tai / total, 3) if total else 0}


def _log_stats(log=None):
    if log is None:
        log = PREDICTION_LOG
    evaluated = [e for e in log.values() if e.get('actual') is not None]
    correct = sum(1 for e in evaluated if e.get('correct'))
    wrong = len(evaluated) - correct
    pending = sum(1 for e in log.values() if e.get('actual') is None)
    return {'correct': correct, 'wrong': wrong, 'pending': pending,
            'evaluated': len(evaluated)}


_load_log()


# =====================================================================
# HTML - giữ nguyên giao diện
# =====================================================================
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>LE MINH MD5</title>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/sweetalert2@11"></script>
<style>
:root{
  --bg-1:#fdf2f8;--bg-2:#f0f9ff;
  --card:#ffffff;--card-soft:#f8fafc;
  --text:#1e293b;--text-soft:#475569;--muted:#94a3b8;
  --border:#e2e8f0;--border-pink:#fbcfe8;
  --tai:#0284c7;--tai-bg:#e0f2fe;
  --xiu:#db2777;--xiu-bg:#fce7f3;
  --ok:#059669;--ok-bg:#d1fae5;
  --fail:#dc2626;--fail-bg:#fee2e2;
  --warn:#d97706;--warn-bg:#fef3c7;
  --shadow-sm:0 1px 2px rgba(15,23,42,.04);
  --shadow:0 4px 12px rgba(15,23,42,.06);
  --shadow-lg:0 12px 32px rgba(219,39,119,.1);
}
*{box-sizing:border-box;margin:0;padding:0;font-family:'Plus Jakarta Sans',sans-serif}
body{
  background:linear-gradient(135deg,#fdf2f8 0%,#f0f9ff 50%,#fef3f9 100%);
  background-attachment:fixed;color:var(--text);
  min-height:100vh;padding-bottom:30px;font-size:13px;
}
body::before{
  content:'';position:fixed;inset:0;pointer-events:none;z-index:0;
  background:
    radial-gradient(circle at 15% 20%, rgba(251,207,232,.35), transparent 40%),
    radial-gradient(circle at 85% 80%, rgba(167,243,208,.28), transparent 40%);
}
.header{
  background:rgba(255,255,255,.85);backdrop-filter:blur(20px);
  -webkit-backdrop-filter:blur(20px);border-bottom:1px solid var(--border-pink);
  padding:12px 16px;display:flex;justify-content:space-between;align-items:center;
  position:sticky;top:0;z-index:100;box-shadow:0 2px 12px rgba(219,39,119,.06);
}
.logo{font-size:16px;font-weight:800;display:flex;align-items:center;gap:8px}
.logo-text{
  background:linear-gradient(90deg,#ec4899,#a855f7,#0ea5e9);
  background-size:200% auto;-webkit-background-clip:text;
  -webkit-text-fill-color:transparent;animation:shine 4s linear infinite;
}
@keyframes shine{to{background-position:200% center}}
.logo span.ver{
  font-size:9px;background:linear-gradient(135deg,#ec4899,#a855f7);
  color:#fff;padding:3px 8px;border-radius:20px;font-weight:800;letter-spacing:.5px;
  box-shadow:0 2px 6px rgba(168,85,247,.3);
}
.logo .md5-badge{
  font-size:9px;background:linear-gradient(135deg,#0ea5e9,#0284c7);
  color:#fff;padding:3px 8px;border-radius:20px;font-weight:800;letter-spacing:.5px;
  box-shadow:0 2px 6px rgba(2,132,199,.3);margin-left:4px;
}
.actions{display:flex;gap:8px;align-items:center}
.clock{font-size:11px;color:var(--muted);font-weight:600;font-variant-numeric:tabular-nums}
.btn{
  background:#fff;color:var(--text-soft);border:1px solid var(--border);
  padding:7px 12px;border-radius:9px;font-weight:700;font-size:12px;cursor:pointer;
  transition:.2s;box-shadow:var(--shadow-sm);
}
.btn:hover{background:var(--card-soft);transform:translateY(-1px);box-shadow:var(--shadow)}
.btn:active{transform:scale(.95)}
.btn-reset{background:linear-gradient(135deg,#fee2e2,#fce7f3);color:var(--fail);border-color:var(--border-pink)}
.spin{animation:spin .5s}
@keyframes spin{to{transform:rotate(360deg)}}

.wrap{max-width:780px;margin:0 auto;padding:12px;position:relative;z-index:1}

.pred-box{
  background:linear-gradient(135deg,#ffffff,#fdf2f8);
  border:1px solid var(--border-pink);border-radius:16px;padding:18px;
  margin-bottom:12px;position:relative;overflow:hidden;box-shadow:var(--shadow-lg);
}
.pred-box::before{
  content:'';position:absolute;inset:0;
  background:linear-gradient(45deg,transparent 30%,rgba(251,207,232,.4) 50%,transparent 70%);
  animation:sweep 4s linear infinite;
}
@keyframes sweep{0%{transform:translateX(-100%)}100%{transform:translateX(100%)}}
.pred-row{display:flex;align-items:center;justify-content:space-between;position:relative;z-index:1}
.pred-label{font-size:10px;color:var(--muted);text-transform:uppercase;font-weight:700;letter-spacing:.5px;margin-bottom:6px}
.pred-value{font-size:34px;font-weight:800;line-height:1;letter-spacing:-1px}
.pred-value.tai{color:var(--tai);text-shadow:0 2px 12px rgba(2,132,199,.2)}
.pred-value.xiu{color:var(--xiu);text-shadow:0 2px 12px rgba(219,39,119,.2)}
.pred-conf-badge{
  background:linear-gradient(135deg,#fce7f3,#fbcfe8);
  border:1px solid var(--border-pink);color:var(--xiu);
  padding:7px 16px;border-radius:20px;font-weight:800;font-size:15px;
  font-variant-numeric:tabular-nums;box-shadow:0 2px 8px rgba(236,72,153,.15);
}
.conf-bar{height:5px;background:var(--bg-1);border-radius:3px;overflow:hidden;margin-top:14px;position:relative;z-index:1}
.conf-bar-fill{
  height:100%;background:linear-gradient(90deg,#ec4899,#a855f7,#0ea5e9);
  border-radius:3px;transition:width 1s ease;
  box-shadow:0 0 10px rgba(236,72,153,.4);
}

.scorebar{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:12px}
.sc{
  background:#fff;border:1px solid var(--border);border-radius:11px;
  padding:12px 8px;text-align:center;transition:.2s;box-shadow:var(--shadow-sm);
  position:relative;overflow:hidden;
}
.sc::before{
  content:'';position:absolute;top:0;left:0;right:0;height:3px;
  background:linear-gradient(90deg,transparent,currentColor,transparent);opacity:.15;
}
.sc:hover{transform:translateY(-2px);box-shadow:var(--shadow);border-color:var(--border-pink)}
.sc-l{font-size:9px;color:var(--muted);text-transform:uppercase;font-weight:700;margin-bottom:4px;letter-spacing:.5px}
.sc-v{font-size:22px;font-weight:800;font-variant-numeric:tabular-nums;transition:.3s}
.sc-v.ok{color:var(--ok)}.sc-v.fail{color:var(--fail)}.sc-v.warn{color:var(--warn)}
.sc-v.pulse{animation:pulse .4s}
@keyframes pulse{0%,100%{transform:scale(1)}50%{transform:scale(1.15)}}

.tbl{background:#fff;border:1px solid var(--border);border-radius:14px;overflow:hidden;max-height:65vh;overflow-y:auto;box-shadow:var(--shadow)}
table{width:100%;border-collapse:separate;border-spacing:0}
th{
  background:linear-gradient(135deg,#fdf2f8,#f0f9ff);
  color:var(--text-soft);font-size:10px;font-weight:700;
  padding:12px 6px;text-align:center;border-bottom:1px solid var(--border-pink);
  position:sticky;top:0;z-index:10;text-transform:uppercase;letter-spacing:.3px;
}
td{padding:10px 6px;text-align:center;border-bottom:1px solid var(--bg-1);font-size:12px;font-weight:600;transition:.15s}
tr:hover td{background:#fdf2f8}
.row-next{background:linear-gradient(90deg,#fdf2f8,#fff);border-left:3px solid var(--xiu);animation:glow 2.5s ease infinite}
@keyframes glow{
  0%,100%{box-shadow:inset 0 0 20px rgba(236,72,153,.06)}
  50%{box-shadow:inset 0 0 25px rgba(236,72,153,.18)}
}
.badge{display:inline-block;padding:3px 10px;border-radius:14px;font-size:10px;font-weight:800;min-width:52px;text-align:center}
.b-tai{background:var(--tai-bg);color:var(--tai);border:1px solid #bae6fd}
.b-xiu{background:var(--xiu-bg);color:var(--xiu);border:1px solid var(--border-pink)}
.b-none{background:var(--bg-1);color:var(--muted);border:1px solid var(--border)}
.b-ok{background:var(--ok-bg);color:var(--ok);border:1px solid #6ee7b7;font-size:10px;padding:4px 10px;font-weight:800;animation:checkIn .3s}
@keyframes checkIn{from{transform:scale(0)}to{transform:scale(1)}}
.b-fail{background:var(--fail-bg);color:var(--fail);border:1px solid #fca5a5;font-size:10px;padding:4px 10px;font-weight:800}
.b-pending{background:var(--warn-bg);color:var(--warn);border:1px solid #fcd34d;font-size:10px;padding:4px 10px;font-weight:800;animation:blink 2s infinite}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.65}}
.dice{font-size:10px;color:var(--muted);display:block;margin-top:2px;font-variant-numeric:tabular-nums}
.pred-mini{font-size:11px;font-weight:800}
.pred-mini.tai{color:var(--tai)}.pred-mini.xiu{color:var(--xiu)}
.loading{text-align:center;padding:30px;color:var(--xiu);font-weight:600}

@media(max-width:520px){
  th,td{padding:8px 4px;font-size:11px}
  .clock{display:none}
  .badge{min-width:44px;font-size:9px}
  .sc-v{font-size:17px}
  .pred-value{font-size:26px}
}
</style>
</head>
<body>
<div class="header">
    <div class="logo">
        <span class="logo-text">LE MINH</span>
        <span class="ver">V8.0</span>
        <span class="md5-badge">MD5</span>
    </div>
    <div class="actions">
        <div class="clock" id="clock">--:--:--</div>
        <button class="btn" onclick="manualRefresh(this)">↻</button>
        <button class="btn btn-reset" onclick="confirmReset()">Reset</button>
    </div>
</div>
<div class="wrap">
    <div class="pred-box">
        <div class="pred-row">
            <div>
                <div class="pred-label">Dự đoán phiên tiếp theo</div>
                <div class="pred-value" id="pred-value">Đang tải...</div>
            </div>
            <div class="pred-conf-badge" id="conf-badge">--%</div>
        </div>
        <div class="conf-bar">
            <div class="conf-bar-fill" id="conf-bar" style="width:0%"></div>
        </div>
    </div>
    
    <div class="scorebar">
        <div class="sc"><div class="sc-l">✓ Đúng</div><div class="sc-v ok" id="sc-correct">0</div></div>
        <div class="sc"><div class="sc-l">✗ Sai</div><div class="sc-v fail" id="sc-wrong">0</div></div>
        <div class="sc"><div class="sc-l">⏳ Chờ</div><div class="sc-v warn" id="sc-pending">0</div></div>
    </div>
    
    <div class="tbl">
        <table>
            <thead><tr><th>Phiên</th><th>Dự đoán</th><th>Kết quả</th><th>Đúng/Sai</th></tr></thead>
            <tbody id="tbody"><tr><td colspan="4" class="loading">Đang kết nối...</td></tr></tbody>
        </table>
    </div>
</div>
<script>
let isResetting=false;

function manualRefresh(btn){
    btn.classList.add('spin');
    setTimeout(()=>btn.classList.remove('spin'),500);
    fetchData();
}

async function fetchData(){
    if(isResetting)return;
    document.getElementById('clock').innerText=new Date().toLocaleTimeString('vi-VN');
    try{
        const r=await fetch('/api/history',{cache:'no-store'});
        const d=await r.json();
        renderScore(d.ai_stats||{});
        renderPred(d.prediction,d.rows||[]);
    }catch(e){console.error(e);}
}

function renderScore(ai){
    const cv=document.getElementById('sc-correct');
    const wv=document.getElementById('sc-wrong');
    const pv=document.getElementById('sc-pending');
    const newC=ai.correct||0, newW=ai.wrong||0, newP=ai.pending||0;
    if(parseInt(cv.innerText)!==newC && cv.innerText!=='0'){
        cv.classList.add('pulse');setTimeout(()=>cv.classList.remove('pulse'),400);
    }
    cv.innerText=newC;wv.innerText=newW;pv.innerText=newP;
}

function renderPred(pred, rows){
    const pv=document.getElementById('pred-value');
    const cb=document.getElementById('conf-badge');
    const bar=document.getElementById('conf-bar');
    if(!pred){
        pv.innerText='—';pv.className='pred-value';
        cb.innerText='--%';bar.style.width='0%';
    } else {
        const p=pred.prediction, c=pred.confidence||0;
        pv.innerText=p;
        pv.className='pred-value '+(p==='Tài'?'tai':'xiu');
        cb.innerText=c+'%';
        bar.style.width=c+'%';
    }
    renderTable(rows);
}

function renderTable(rows){
    const tb=document.getElementById('tbody');
    if(!rows.length){tb.innerHTML='<tr><td colspan="4" class="loading">Chưa có dữ liệu.</td></tr>';return;}
    let html='';
    rows.forEach(row=>{
        const trCls=row.is_next?'class="row-next"':'';
        let predCell='<span class="badge b-none">—</span>';
        if(row.ai_prediction){
            const cls=row.ai_prediction==='Tài'?'tai':'xiu';
            predCell=`<span class="pred-mini ${cls}">${row.ai_prediction}</span><br><span style="font-size:9px;color:var(--muted)">${row.ai_confidence||0}%</span>`;
        }
        let resBadge='<span class="badge b-none">—</span>';
        if(row.result==='Tài')resBadge='<span class="badge b-tai">TÀI</span>';
        if(row.result==='Xỉu')resBadge='<span class="badge b-xiu">XỈU</span>';
        let dice='';
        if(row.dice&&row.dice!=='? + ? + ?')dice=`<span class="dice">${row.dice} · ${row.total}</span>`;
        let evalCell='<span class="badge b-none">—</span>';
        if(row.ai_correct===true)evalCell='<span class="badge b-ok">✓ ĐÚNG</span>';
        else if(row.ai_correct===false)evalCell='<span class="badge b-fail">✗ SAI</span>';
        else if(row.ai_prediction&&row.ai_correct===null)evalCell='<span class="badge b-pending">⏳ CHỜ</span>';
        html+=`<tr ${trCls}><td>#${row.phien}${row.is_next?'<br><span style="font-size:9px;color:var(--xiu)">NEXT</span>':''}</td><td>${predCell}</td><td>${resBadge}${dice}</td><td>${evalCell}</td></tr>`;
    });
    tb.innerHTML=html;
}

async function confirmReset(){
    Swal.fire({
        title:'Reset?',text:'Xoá toàn bộ cache và log?',icon:'warning',
        background:'#fff',color:'#1e293b',showCancelButton:true,
        confirmButtonColor:'#ec4899',cancelButtonColor:'#94a3b8',
        confirmButtonText:'Đồng ý',cancelButtonText:'Hủy'
    }).then(async r=>{
        if(!r.isConfirmed)return;
        isResetting=true;
        try{
            const res=await fetch('/api/reset',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
            const d=await res.json();
            isResetting=false;
            if(d.status==='success'){
                fetchData();
                Swal.fire({title:'OK!',text:'Đã reset.',icon:'success',background:'#fff',color:'#1e293b',confirmButtonColor:'#ec4899'});
            } else throw new Error(d.message||'Lỗi');
        }catch(e){
            isResetting=false;
            Swal.fire({title:'Lỗi!',text:e.message,icon:'error',background:'#fff',color:'#1e293b'});
        }
    });
}

fetchData();
setInterval(fetchData,5000);
</script>
</body>
</html>"""


# =====================================================================
# ROUTES (giữ nguyên API contract)
# =====================================================================
@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)


@app.route('/api/history')
def history():
    try:
        valid, success = _sync_history()
        if not success:
            return jsonify({'error': 'Không thể kết nối API'}), 500
        if not valid:
            return jsonify({'rows': [], 'stats': {'total': 0, 'tai': 0, 'xiu': 0},
                            'ai_stats': _log_stats(), 'prediction': None})
        next_phien = valid[-1]['phien'] + 1
        pred = MultiStrategyPredictor.predict(valid, log=PREDICTION_LOG)
        _log_prediction(next_phien, pred)
        stats = _honest_stats(valid)
        ai_stats = _log_stats()
        out = [{
            'phien': next_phien, 'is_next': True, 'result': 'Chờ...',
            'dice': '? + ? + ?', 'total': None,
            'ai_prediction': pred['prediction'],
            'ai_confidence': pred['confidence'],
            'ai_correct': None,
        }]
        for v in reversed(valid[-200:]):
            if v['phien'] <= RESET_ID:
                continue
            log = PREDICTION_LOG.get(v['phien'])
            out.append({
                'phien': v['phien'], 'is_next': False,
                'result': v['result'], 'dice': v['dice'], 'total': v['total'],
                'ai_prediction': log['prediction'] if log else None,
                'ai_confidence': log['confidence'] if log else None,
                'ai_correct': log['correct'] if log else None,
            })
        return jsonify({
            'rows': out, 'stats': stats, 'ai_stats': ai_stats,
            'prediction': {
                'prediction': pred['prediction'],
                'confidence': pred['confidence'],
                'method': pred['method'],
            }
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/lc79md5')
def lc79md5():
    try:
        valid, success = _sync_history()
        if not success:
            return jsonify({'error': 'Không thể kết nối MD5 API'}), 500
        if not valid:
            return jsonify({'Id': 'LE MINH V8.0 MD5', 'Phien': None,
                            'Du_doan': None, 'message': 'Chưa có dữ liệu'})
        last = valid[-1]
        next_phien = last['phien'] + 1
        pred = MultiStrategyPredictor.predict(valid, log=PREDICTION_LOG)
        _log_prediction(next_phien, pred)
        stats = _honest_stats(valid)
        ai_stats = _log_stats(PREDICTION_LOG)
        return jsonify({
            'Id': 'LE MINH V8.0 MD5',
            'Phien': str(next_phien),
            'Ket_qua_truoc': last['result'].lower(),
            'Xuc_xac_truoc': last['dice'],
            'Diem_truoc': last['total'],
            'Du_doan': pred['prediction'],
            'Do_tin_cay': f"{pred['confidence']}%",
            'Phuong_phap': pred['method'],
            'Tin_hieu_chinh': pred.get('top_signals', []),
            'Ly_do': pred['reason'],
            'Da_dao': pred.get('flipped', False),
            'So_tin_hieu': len(pred['signals']),
            'Top_manh': pred.get('signals_strength', [])[:5],
            'Tong_phien': stats['total'],
            'So_Tai': stats['tai'],
            'So_Xiu': stats['xiu'],
            'AI_dung': ai_stats['correct'],
            'AI_sai': ai_stats['wrong'],
            'AI_cho': ai_stats['pending'],
            'Ti_le_AI': round(
                ai_stats['correct'] / max(ai_stats['evaluated'], 1) * 100, 1),
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/lc79')
def lc79():
    try:
        valid, success = _sync_history()
        if not success:
            return jsonify({'error': 'Không thể kết nối API'}), 500
        if not valid:
            return jsonify({'Id': 'LE MINH V8.0', 'Phien': None, 'Du_doan': None})
        last = valid[-1]
        next_phien = last['phien'] + 1
        pred = MultiStrategyPredictor.predict(valid, log=PREDICTION_LOG)
        _log_prediction(next_phien, pred)
        stats = _honest_stats(valid)
        ai_stats = _log_stats(PREDICTION_LOG)
        return jsonify({
            'Id': 'LE MINH V8.0',
            'Phien': str(next_phien),
            'Ket_qua_truoc': last['result'].lower(),
            'Du_doan': pred['prediction'],
            'Do_tin_cay': f"{pred['confidence']}%",
            'Phuong_phap': pred['method'],
            'Tin_hieu_chinh': pred.get('top_signals', []),
            'Ly_do': pred['reason'],
            'Da_dao': pred.get('flipped', False),
            'So_tin_hieu': len(pred['signals']),
            'Top_manh': pred.get('signals_strength', [])[:5],
            'Tong_phien': stats['total'],
            'So_Tai': stats['tai'],
            'So_Xiu': stats['xiu'],
            'AI_dung': ai_stats['correct'],
            'AI_sai': ai_stats['wrong'],
            'AI_cho': ai_stats['pending'],
            'Ti_le_AI': round(
                ai_stats['correct'] / max(ai_stats['evaluated'], 1) * 100, 1),
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/predict')
def predict():
    try:
        valid, success = _sync_history()
        if not success:
            return jsonify({'error': 'Không thể kết nối API'}), 500
        if not valid:
            return jsonify({'prediction': None, 'ai_stats': _log_stats()})
        last = valid[-1]
        next_phien = last['phien'] + 1
        pred = MultiStrategyPredictor.predict(valid, log=PREDICTION_LOG)
        _log_prediction(next_phien, pred)
        return jsonify({
            'version': 'V8.0',
            'last_phien': last['phien'],
            'next_phien': next_phien,
            'last_result': last['result'],
            'last_dice': last['dice'],
            'last_total': last['total'],
            'prediction': pred['prediction'],
            'confidence': pred['confidence'],
            'method': pred['method'],
            'top_signals': pred.get('top_signals', []),
            'signals_strength': pred.get('signals_strength', []),
            'reason': pred['reason'],
            'signals': pred['signals'],
            'signals_count': len(pred['signals']),
            'flipped': pred.get('flipped', False),
            'stats': _honest_stats(valid),
            'ai_stats': _log_stats(),
            'timestamp': datetime.now().isoformat(),
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/stats')
def stats_only():
    try:
        valid, success = _sync_history()
        if not success:
            return jsonify({'error': 'Không thể kết nối API'}), 500
        last = valid[-1] if valid else None
        return jsonify({
            'stats': _honest_stats(valid),
            'ai_stats': _log_stats(),
            'last_result': last['result'] if last else None,
            'last_phien': last['phien'] if last else None,
            'last_dice': last['dice'] if last else None,
            'timestamp': datetime.now().isoformat(),
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/algo_info')
def algo_info():
    try:
        valid, success = _sync_history()
        if not success:
            return jsonify({'error': 'Không thể kết nối API'}), 500
        if not valid:
            return jsonify({'error': 'Chưa có dữ liệu'}), 400
        pred = MultiStrategyPredictor.predict(valid, log=PREDICTION_LOG)
        results = [v['result'] for v in valid]
        cyc = MultiStrategyPredictor._detect_cycle(results, max_period=6, min_repeats=2)
        return jsonify({
            'version': 'V8.0',
            'prediction': pred['prediction'],
            'confidence': pred['confidence'],
            'method': pred['method'],
            'top_signals': pred.get('top_signals', []),
            'signals_strength': pred.get('signals_strength', []),
            'all_signals': pred['signals'],
            'signals_count': len(pred['signals']),
            'flipped': pred.get('flipped', False),
            'reason': pred['reason'],
            'regime': pred.get('regime'),
            'recent_runs': _run_lengths(results[-40:]),
            'entropy': round(_entropy(results[-20:]), 3),
            'detected_cycle': {
                'period': cyc['period'], 'pattern': cyc['pattern'], 'next': cyc['next'],
            } if cyc else None,
            'num_phien': len(valid),
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/prediction_log')
def prediction_log():
    try:
        _sync_history()
        items = sorted(PREDICTION_LOG.items(), key=lambda x: x[0], reverse=True)[:100]
        out = [{
            'phien': p, 'prediction': e['prediction'], 'confidence': e['confidence'],
            'method': e['method'], 'actual': e.get('actual'),
            'correct': e.get('correct'), 'retroactive': e.get('retroactive', False),
            'logged_at': e.get('logged_at'),
        } for p, e in items]
        return jsonify({'stats': _log_stats(), 'predictions': out})
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/reset', methods=['POST'])
def reset_api():
    global RESET_ID, HISTORY_CACHE, PREDICTION_LOG, PATTERN_MEMORY
    try:
        raw_rows, success = fetch_sessions()
        if success and raw_rows:
            valid = sorted(raw_rows, key=lambda x: x['phien'])
            if valid:
                RESET_ID = valid[-1]['phien']
                HISTORY_CACHE = {valid[-1]['phien']: valid[-1]}
                PREDICTION_LOG = {}
                PATTERN_MEMORY.clear()
                MultiStrategyPredictor._STRATEGY_TRACK = defaultdict(
                    lambda: {'hit': 0, 'miss': 0, 'last_len': 0})
                _save_log()
                return jsonify({'status': 'success', 'reset_to': RESET_ID + 1})
        elif HISTORY_CACHE:
            RESET_ID = max(HISTORY_CACHE.keys())
            HISTORY_CACHE = {RESET_ID: HISTORY_CACHE[RESET_ID]}
            PREDICTION_LOG = {}
            PATTERN_MEMORY.clear()
            MultiStrategyPredictor._STRATEGY_TRACK = defaultdict(
                lambda: {'hit': 0, 'miss': 0, 'last_len': 0})
            _save_log()
            return jsonify({'status': 'success', 'reset_to': RESET_ID + 1})
        return jsonify({'status': 'error', 'message': 'Không có dữ liệu để reset'}), 400
    except Exception as e:
        traceback.print_exc()
        return jsonify({'status': 'error', 'message': str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
