import os
import traceback
import time
import json
import hashlib
import math
import threading
import random
from datetime import datetime
from collections import Counter, defaultdict, deque
import requests
from flask import Flask, render_template_string, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

API_URL_MD5 = (
    "https://wtx.tele68.com/v1/tx/lite-sessions"
    "?cp=R&cl=R&pf=web&at=1d5d150f5b59bf7b20e283c525b4a6b5"
)

HISTORY_CACHE = {}
RESET_ID = 0
PREDICTION_LOG = {}
MAX_LOG_SIZE = 3000
LOG_FILE = "prediction_log.json"

_FETCH_LOCK = threading.Lock()
_LAST_FETCH_TIME = 0.0
_LAST_FETCH_RESULT = ([], False)
_FETCH_CACHE_TTL = 10.0

SIGNAL_NAMES = {
    'api_main': 'API Chính',
    'api_invert': 'API Đảo',
    'api_adaptive': 'API Thích Ứng',
    'api_momentum': 'API Động Lượng',
    'math_cau': 'Toán Cầu/Xu Hướng',
    'math_markov': 'Toán Markov',
    'math_markov3': 'Toán Markov Bậc 3',
    'math_streak': 'Toán Chuỗi Thắng/Thua',
    'math_balance': 'Toán Cân Bằng',
    'math_antimartingale': 'Toán Anti-Martingale',
    'math_regime': 'Toán Chế Độ Thị Trường',
    'math_recovery': 'Toán Phục Hồi',
    'math_pattern': 'Toán Mẫu Hình',
    'math_bayes': 'Toán Bayes',
    'math_entropy': 'Toán Entropy',
}

# ========== HELPER ==========
def _runs(seq):
    if not seq: return []
    out, cur, n = [], seq[0], 1
    for x in seq[1:]:
        if x == cur: n += 1
        else: out.append((cur, n)); cur, n = x, 1
    out.append((cur, n))
    return out

def _generate_dice(prediction, confidence, seed_str=None):
    if seed_str is None: seed_str = str(time.time())
    h = int(hashlib.md5(seed_str.encode()).hexdigest(), 16)
    rng = random.Random(h)
    if prediction == 'Tài':
        if confidence >= 85: target = rng.randint(14, 17)
        elif confidence >= 75: target = rng.randint(13, 16)
        elif confidence >= 65: target = rng.randint(12, 15)
        else: target = rng.randint(11, 13)
    else:
        if confidence >= 85: target = rng.randint(4, 7)
        elif confidence >= 75: target = rng.randint(5, 8)
        elif confidence >= 65: target = rng.randint(6, 9)
        else: target = rng.randint(7, 10)
    for _ in range(80):
        d1 = rng.randint(1, 6); d2 = rng.randint(1, 6)
        d3 = target - d1 - d2
        if 1 <= d3 <= 6: return [d1, d2, d3]
    return [5, 6, 4] if prediction == 'Tài' else [2, 3, 4]


# ========== PREDICTOR V21 - AUTO-ADAPTIVE % ==========
class MultiStrategyPredictor:
    """
    V21: Auto-Adaptive Confidence
    - Ép %: 50-60 (Thấp), 70-80 (Vừa), 80-90 (Cao - không quá 90%)
    - Tự động thay đổi dựa trên đồng thuận thuật toán
    """

    @staticmethod
    def _scale_confidence(raw_conf, agreement, fail_streak):
        """
        Ép % theo 3 khoảng:
        - Thấp: 50-60% (khi đồng thuận yếu)
        - Vừa: 70-80% (khi đồng thuận vừa)
        - Cao: 80-90% (khi đồng thuận mạnh, tối đa 90%)
        """
        # Base random để tạo sự tự nhiên (có thấp, có cao)
        rng = random.Random(int(time.time() * 1000) % 10000)
        
        if agreement >= 0.75 or fail_streak >= 3:
            # Cao: 80 - 90% (không quá 90)
            base = 80 + int(agreement * 10)
            if fail_streak >= 3:
                base += 2 # Boost nhẹ khi recovery
            return min(base + rng.randint(0, 2), 90)
        elif agreement >= 0.60:
            # Vừa: 70 - 80%
            base = 70 + int(agreement * 10)
            return min(base + rng.randint(0, 3), 80)
        else:
            # Thấp: 50 - 60%
            base = 50 + int(agreement * 15)
            return min(max(base + rng.randint(0, 2), 50), 60)

    # --- 1. API MAIN ---
    @staticmethod
    def _algo_api_main(valid):
        if len(valid) < 12: return (None, 0, 0)
        seq = [v['result'] for v in valid]
        scores = {'Tài': 0.0, 'Xỉu': 0.0}
        weights = 0.0
        for w_size, w in [(15, 1.0), (25, 0.7), (40, 0.4)]:
            if len(seq) < w_size: continue
            recent = seq[-w_size:]
            tais = sum(1 for r in recent if r == 'Tài')
            ratio = tais / len(recent)
            if ratio >= 0.72: scores['Xỉu'] += w * (ratio - 0.5) * 2
            elif ratio <= 0.28: scores['Tài'] += w * (0.5 - ratio) * 2
            weights += w
        if weights == 0: return (None, 0, 0)
        diff = scores['Tài'] - scores['Xỉu']
        if abs(diff) < 0.25: return (None, 0, 0)
        pred = 'Tài' if diff > 0 else 'Xỉu'
        return (pred, min(int(55 + abs(diff) * 40), 82), 1.3)

    # --- 2. API INVERT ---
    @staticmethod
    def _algo_api_invert(log):
        if not log: return (None, 0, 0)
        evaluated = [e for p, e in sorted(log.items(), reverse=True) if e.get('actual') is not None][:10]
        if len(evaluated) < 2: return (None, 0, 0)
        fail_streak = 0
        for e in evaluated:
            if not e.get('correct'): fail_streak += 1
            else: break
        win_streak = 0
        for e in evaluated:
            if e.get('correct'): win_streak += 1
            else: break
        last_pred = evaluated[0]['prediction']
        inv = 'Xỉu' if last_pred == 'Tài' else 'Tài'
        if fail_streak >= 5: return (inv, 90, 2.5)
        if fail_streak >= 4: return (inv, 85, 2.3)
        if fail_streak >= 3: return (inv, 80, 2.0)
        if fail_streak >= 2: return (inv, 74, 1.7)
        if win_streak >= 6: return (last_pred, 72, 1.5)
        if win_streak >= 4: return (last_pred, 66, 1.3)
        return (None, 0, 0)

    # --- 3. API ADAPTIVE ---
    @staticmethod
    def _algo_api_adaptive(valid):
        if len(valid) < 20: return (None, 0, 0)
        seq = [v['result'] for v in valid]
        recent10 = seq[-10:]
        older10 = seq[-20:-10]
        tais_r = sum(1 for r in recent10 if r == 'Tài')
        tais_o = sum(1 for r in older10 if r == 'Tài')
        ratio_r = tais_r / 10
        ratio_o = tais_o / 10
        if ratio_o >= 0.7 and ratio_r <= 0.3: return ('Xỉu', 72, 1.6)
        if ratio_o <= 0.3 and ratio_r >= 0.7: return ('Tài', 72, 1.6)
        if ratio_r >= 0.7 and ratio_o >= 0.6: return ('Tài', 65, 1.3)
        if ratio_r <= 0.3 and ratio_o <= 0.4: return ('Xỉu', 65, 1.3)
        return (None, 0, 0)

    # --- 4. API MOMENTUM ---
    @staticmethod
    def _algo_api_momentum(valid):
        if len(valid) < 15: return (None, 0, 0)
        seq = [v['result'] for v in valid]
        encoded = [1 if s == 'Tài' else 0 for s in seq]
        m5 = sum(encoded[-5:]) - sum(encoded[-10:-5])
        m3 = sum(encoded[-3:]) - sum(encoded[-6:-3])
        if m5 >= 3 and m3 >= 1: return ('Tài', min(60 + m5 * 4, 78), 1.5)
        if m5 <= -3 and m3 <= -1: return ('Xỉu', min(60 + abs(m5) * 4, 78), 1.5)
        if m5 >= 3 and m3 <= -2: return ('Xỉu', 68, 1.4)
        if m5 <= -3 and m3 >= 2: return ('Tài', 68, 1.4)
        return (None, 0, 0)

    # --- 5. CẦU ---
    @staticmethod
    def _algo_math_cau(seq):
        if len(seq) < 8: return (None, 0, 0)
        runs = _runs(seq)
        if not runs: return (None, 0, 0)
        cur_val, cur_len = runs[-1]
        opp = 'Xỉu' if cur_val == 'Tài' else 'Tài'
        last6 = [l for _, l in runs[-6:]]
        if len(last6) >= 4 and all(l == 1 for l in last6[-4:]): return (opp, 74, 1.7)
        if len(last6) >= 3 and all(l == 2 for l in last6[-3:]): return (cur_val if cur_len == 1 else opp, 72, 1.6)
        if len(last6) >= 2 and all(l == 3 for l in last6[-2:]): return (cur_val if cur_len < 3 else opp, 70, 1.5)
        if cur_len >= 8: return (opp, 90, 2.4)
        if cur_len >= 7: return (opp, 84, 2.1)
        if cur_len >= 6: return (opp, 78, 1.9)
        if 3 <= cur_len <= 5: return (cur_val, min(60 + cur_len * 3, 76), 1.4)
        if len(last6) >= 2 and last6[-2] == 1 and last6[-1] == 1: return (opp, 64, 1.2)
        return (None, 0, 0)

    # --- 6. MARKOV ---
    @staticmethod
    def _algo_math_markov(seq):
        if len(seq) < 12: return (None, 0, 0)
        trans = defaultdict(lambda: {'Tài': 0, 'Xỉu': 0})
        for i in range(len(seq) - 1): trans[seq[i]][seq[i+1]] += 1
        last = seq[-1]
        counts = trans[last]
        total = counts['Tài'] + counts['Xỉu']
        if total < 5: return (None, 0, 0)
        p_tai = counts['Tài'] / total
        if p_tai >= 0.72: return ('Tài', min(int(60 + p_tai * 25), 85), 1.6)
        if p_tai <= 0.28: return ('Xỉu', min(int(60 + (1 - p_tai) * 25), 85), 1.6)
        if len(seq) >= 14:
            pair = seq[-2] + '|' + seq[-1]
            trans2 = defaultdict(lambda: {'Tài': 0, 'Xỉu': 0})
            for i in range(len(seq) - 2):
                k = seq[i] + '|' + seq[i+1]
                trans2[k][seq[i+2]] += 1
            c2 = trans2[pair]
            t2 = c2['Tài'] + c2['Xỉu']
            if t2 >= 3:
                p2 = c2['Tài'] / t2
                if p2 >= 0.75: return ('Tài', 74, 1.7)
                if p2 <= 0.25: return ('Xỉu', 74, 1.7)
        return (None, 0, 0)

    # --- 7. MARKOV 3 ---
    @staticmethod
    def _algo_math_markov3(seq):
        if len(seq) < 20: return (None, 0, 0)
        trans3 = defaultdict(lambda: {'Tài': 0, 'Xỉu': 0})
        for i in range(len(seq) - 3):
            k = seq[i] + '|' + seq[i+1] + '|' + seq[i+2]
            trans3[k][seq[i+3]] += 1
        if len(seq) < 4: return (None, 0, 0)
        triple = seq[-3] + '|' + seq[-2] + '|' + seq[-1]
        c = trans3[triple]
        t = c['Tài'] + c['Xỉu']
        if t < 2: return (None, 0, 0)
        p_tai = c['Tài'] / t
        if p_tai >= 0.80: return ('Tài', 80, 1.9)
        if p_tai <= 0.20: return ('Xỉu', 80, 1.9)
        if p_tai >= 0.70: return ('Tài', 68, 1.5)
        if p_tai <= 0.30: return ('Xỉu', 68, 1.5)
        return (None, 0, 0)

    # --- 8. STREAK ---
    @staticmethod
    def _algo_math_streak(log):
        if not log: return (None, 0, 0)
        evaluated = [e for p, e in sorted(log.items(), reverse=True) if e.get('actual') is not None][:12]
        if len(evaluated) < 4: return (None, 0, 0)
        results = [e.get('correct') for e in evaluated]
        fail_streak = 0
        for r in results:
            if r is False: fail_streak += 1
            else: break
        if fail_streak >= 4:
            last_pred = evaluated[0]['prediction']
            return ('Xỉu' if last_pred == 'Tài' else 'Tài', 82, 2.2)
        if fail_streak >= 3:
            last_pred = evaluated[0]['prediction']
            return ('Xỉu' if last_pred == 'Tài' else 'Tài', 76, 1.9)
        if fail_streak >= 2:
            last_pred = evaluated[0]['prediction']
            return ('Xỉu' if last_pred == 'Tài' else 'Tài', 70, 1.6)
        win_streak = 0
        for r in results:
            if r is True: win_streak += 1
            else: break
        if win_streak >= 5:
            last_pred = evaluated[0]['prediction']
            return (last_pred, 72, 1.5)
        if win_streak >= 3:
            last_pred = evaluated[0]['prediction']
            return (last_pred, 64, 1.2)
        return (None, 0, 0)

    # --- 9. BALANCE ---
    @staticmethod
    def _algo_math_balance(valid):
        if len(valid) < 10: return (None, 0, 0)
        recent = valid[-15:]
        totals = [v['total'] for v in recent if v.get('total')]
        if len(totals) < 8: return (None, 0, 0)
        avg = sum(totals) / len(totals)
        deviation = avg - 10.5
        if deviation >= 2.2: return ('Xỉu', 72, 1.6)
        if deviation >= 1.8: return ('Xỉu', 66, 1.4)
        if deviation <= -2.2: return ('Tài', 72, 1.6)
        if deviation <= -1.8: return ('Tài', 66, 1.4)
        evens = sum(1 for t in totals if t % 2 == 0)
        r_even = evens / len(totals)
        if r_even >= 0.85: return ('Tài', 66, 1.4)
        if r_even <= 0.15: return ('Xỉu', 66, 1.4)
        return (None, 0, 0)

    # --- 10. ANTI-MARTINGALE ---
    @staticmethod
    def _algo_math_antimartingale(log):
        if not log: return (None, 0, 0)
        evaluated = [e for p, e in sorted(log.items(), reverse=True) if e.get('actual') is not None][:6]
        if len(evaluated) < 3: return (None, 0, 0)
        recent = [e.get('correct') for e in evaluated[:3]]
        correct = sum(1 for r in recent if r)
        wrong = 3 - correct
        last_pred = evaluated[0]['prediction']
        if wrong >= 2: return ('Xỉu' if last_pred == 'Tài' else 'Tài', 74, 1.7)
        if correct >= 2: return (last_pred, 62, 1.2)
        return (None, 0, 0)

    # --- 11. REGIME ---
    @staticmethod
    def _algo_math_regime(valid):
        if len(valid) < 20: return (None, 0, 0)
        seq = [v['result'] for v in valid[-25:]]
        runs = _runs(seq)
        if len(runs) < 4: return (None, 0, 0)
        avg_len = sum(l for _, l in runs) / len(runs)
        last_val, last_len = runs[-1]
        opp = 'Xỉu' if last_val == 'Tài' else 'Tài'
        if avg_len >= 2.5:
            if last_len <= avg_len: return (last_val, 65 + int(last_len * 3), 1.5)
            return (opp, 70, 1.6)
        if avg_len <= 1.6: return (opp, 68, 1.5)
        if last_len <= 2: return (last_val, 58, 1.1)
        return (opp, 62, 1.2)

    # --- 12. RECOVERY ---
    @staticmethod
    def _algo_math_recovery(log, valid):
        if not log: return (None, 0, 0)
        evaluated = [e for p, e in sorted(log.items(), reverse=True) if e.get('actual') is not None][:10]
        if len(evaluated) < 2: return (None, 0, 0)
        fail_streak = 0
        for e in evaluated:
            if not e.get('correct'): fail_streak += 1
            else: break
        if fail_streak < 2: return (None, 0, 0)
        last_pred = evaluated[0]['prediction']
        inv = 'Xỉu' if last_pred == 'Tài' else 'Tài'
        if valid and len(valid) >= 10:
            seq = [v['result'] for v in valid[-10:]]
            runs = _runs(seq)
            if runs:
                _, cur_len = runs[-1]
                if cur_len >= 5: return (inv, 92, 2.6)
                if cur_len >= 4: return (inv, 86, 2.3)
                if cur_len <= 2: return (inv, 80, 2.0)
        if fail_streak >= 5: return (inv, 90, 2.5)
        if fail_streak >= 4: return (inv, 86, 2.3)
        if fail_streak >= 3: return (inv, 82, 2.1)
        return (inv, 76, 1.8)

    # --- 13. PATTERN ---
    @staticmethod
    def _algo_math_pattern(seq):
        if len(seq) < 12: return (None, 0, 0)
        encoded = [1 if s == 'Tài' else 0 for s in seq]
        for plen in [3, 4, 5]:
            if len(encoded) < plen * 3: continue
            last_pat = tuple(encoded[-plen:])
            matches = 0
            nexts = []
            for i in range(len(encoded) - plen):
                if tuple(encoded[i:i+plen]) == last_pat:
                    matches += 1
                    if i + plen < len(encoded):
                        nexts.append(encoded[i + plen])
            if matches >= 2 and nexts:
                avg = sum(nexts) / len(nexts)
                if avg >= 0.75: return ('Tài', min(62 + matches * 6, 85), 1.6)
                if avg <= 0.25: return ('Xỉu', min(62 + matches * 6, 85), 1.6)
        if len(encoded) >= 6:
            l6 = encoded[-6:]
            if l6 == [1,0,1,0,1,0]: return ('Xỉu', 72, 1.6)
            if l6 == [0,1,0,1,0,1]: return ('Tài', 72, 1.6)
        return (None, 0, 0)

    # --- 14. BAYES ---
    @staticmethod
    def _algo_math_bayes(seq):
        if len(seq) < 15: return (None, 0, 0)
        encoded = [1 if s == 'Tài' else 0 for s in seq]
        last3 = encoded[-3:]
        counts = {'Tài': 0, 'Xỉu': 0}
        total = 0
        for i in range(len(encoded) - 3):
            if encoded[i:i+3] == last3 and i + 3 < len(encoded):
                if encoded[i+3] == 1: counts['Tài'] += 1
                else: counts['Xỉu'] += 1
                total += 1
        if total < 3: return (None, 0, 0)
        lik_tai = counts['Tài'] / total
        lik_xiu = counts['Xỉu'] / total
        post_tai = 0.5 * lik_tai
        post_xiu = 0.5 * lik_xiu
        s = post_tai + post_xiu
        if s == 0: return (None, 0, 0)
        post_tai /= s
        post_xiu /= s
        if post_tai >= 0.72: return ('Tài', min(int(60 + post_tai * 25), 85), 1.6)
        if post_xiu >= 0.72: return ('Xỉu', min(int(60 + post_xiu * 25), 85), 1.6)
        return (None, 0, 0)

    # --- 15. ENTROPY ---
    @staticmethod
    def _algo_math_entropy(valid):
        if len(valid) < 20: return (None, 0, 0)
        seq = [v['result'] for v in valid[-20:]]
        tais = sum(1 for s in seq if s == 'Tài')
        p_tai = tais / 20
        p_xiu = 1 - p_tai
        if p_tai == 0 or p_xiu == 0: entropy = 0
        else: entropy = -(p_tai * math.log2(p_tai) + p_xiu * math.log2(p_xiu))
        if entropy < 0.6:
            if p_tai > 0.65: return ('Xỉu', 70, 1.5)
            if p_xiu > 0.65: return ('Tài', 70, 1.5)
        runs = _runs(seq)
        if len(runs) >= 8:
            avg_run = sum(l for _, l in runs) / len(runs)
            last_val, last_len = runs[-1]
            if avg_run <= 1.4 and last_len == 1: return ('Xỉu' if last_val == 'Tài' else 'Tài', 68, 1.4)
            if avg_run >= 2.8 and last_len >= 2: return (last_val, 64, 1.3)
        return (None, 0, 0)

    @classmethod
    def predict(cls, valid, log=None):
        if log is None: log = PREDICTION_LOG
        if not valid: return cls._fallback()

        seq = [v['result'] for v in valid]
        if len(seq) < 5:
            run_val, run_len = _runs(seq)[-1]
            opp = 'Xỉu' if run_val == 'Tài' else 'Tài'
            conf = cls._scale_confidence(0.5, 0.5, 0)
            return cls._build_result(opp, conf, [], 'Khởi động', False, False, 0)

        fail_streak = 0
        if log:
            evaluated = [e for p, e in sorted(log.items(), reverse=True) if e.get('actual') is not None][:6]
            for e in evaluated:
                if not e.get('correct'): fail_streak += 1
                else: break

        s_api = cls._algo_api_main(valid)
        s_invert = cls._algo_api_invert(log)
        s_adaptive = cls._algo_api_adaptive(valid)
        s_momentum = cls._algo_api_momentum(valid)
        s_cau = cls._algo_math_cau(seq)
        s_markov = cls._algo_math_markov(seq)
        s_markov3 = cls._algo_math_markov3(seq)
        s_streak = cls._algo_math_streak(log)
        s_balance = cls._algo_math_balance(valid)
        s_anti = cls._algo_math_antimartingale(log)
        s_regime = cls._algo_math_regime(valid)
        s_recovery = cls._algo_math_recovery(log, valid)
        s_pattern = cls._algo_math_pattern(seq)
        s_bayes = cls._algo_math_bayes(seq)
        s_entropy = cls._algo_math_entropy(valid)

        raw_signals = [
            ('api_main', s_api, 1.3), ('api_invert', s_invert, 1.6),
            ('api_adaptive', s_adaptive, 1.4), ('api_momentum', s_momentum, 1.3),
            ('math_cau', s_cau, 1.5), ('math_markov', s_markov, 1.4),
            ('math_markov3', s_markov3, 1.5), ('math_streak', s_streak, 1.5),
            ('math_balance', s_balance, 1.2), ('math_antimartingale', s_anti, 1.4),
            ('math_regime', s_regime, 1.3), ('math_recovery', s_recovery, 2.2),
            ('math_pattern', s_pattern, 1.4), ('math_bayes', s_bayes, 1.5),
            ('math_entropy', s_entropy, 1.3),
        ]

        signals = []
        log_odds = 0.0
        total_w = 0.0
        recovery_boost = 1.0
        if fail_streak >= 4: recovery_boost = 2.5
        elif fail_streak >= 3: recovery_boost = 2.0
        elif fail_streak >= 2: recovery_boost = 1.5

        for name, (pred, conf, weight), base_w in raw_signals:
            if not pred or conf <= 0: continue
            weight *= base_w
            if name in ('math_recovery', 'api_invert', 'math_streak') and fail_streak >= 2:
                weight *= recovery_boost
            p_norm = max(0.51, min(conf / 100.0, 0.95))
            lr = p_norm / (1 - p_norm)
            if pred == 'Xỉu': lr = 1.0 / lr
            log_odds += weight * (1.0 if pred == 'Tài' else -1.0) * (lr - 1.0)
            total_w += weight
            signals.append({'name': name, 'prediction': pred, 'confidence': conf, 'weight': round(weight, 2)})

        if not signals or total_w == 0:
            return cls._fallback()

        scaled = log_odds / max(total_w, 1.0) * 2.4
        p_tai = 1.0 / (1.0 + math.exp(-scaled))
        pred = 'Tài' if p_tai >= 0.5 else 'Xỉu'

        votes_tai = sum(1 for s in signals if s['prediction'] == 'Tài')
        votes_xiu = len(signals) - votes_tai
        agreement = max(votes_tai, votes_xiu) / len(signals)

        # Ép % tự động vào 3 khoảng
        final_conf = cls._scale_confidence(0, agreement, fail_streak)

        recovery_active = False
        flipped = False
        
        if fail_streak >= 2 and log:
            evaluated_last = [e for p, e in sorted(log.items(), reverse=True) if e.get('actual') is not None]
            if evaluated_last:
                last_pred = evaluated_last[0]['prediction']
                if pred == last_pred:
                    pred = 'Xỉu' if pred == 'Tài' else 'Tài'
                    flipped = True
                    recovery_active = True
                    # Đảm bảo đảo luôn ở mức cao
                    final_conf = max(final_conf, random.randint(80, 88))
                else:
                    recovery_active = True

        signals.sort(key=lambda s: s['weight'] * s['confidence'], reverse=True)
        top = [SIGNAL_NAMES.get(s['name'], s['name']) for s in signals[:3]]

        return {
            'prediction': pred, 'confidence': final_conf,
            'method': ' + '.join(top) if top else 'Tổng hợp',
            'signals': signals, 'top_signals': top,
            'reason': 'V21: Tự động cập nhật %',
            'flipped': flipped, 'cau': 'math', 'cau_label': '',
            'recovery_active': recovery_active, 'fail_streak': fail_streak,
        }
    
    @staticmethod
    def _fallback():
        return {
            'prediction': 'Tài', 'confidence': 55,
            'method': 'Fallback', 'signals': [], 'top_signals': [],
            'reason': '', 'flipped': False, 'cau': 'unknown', 'cau_label': '',
            'recovery_active': False, 'fail_streak': 0,
        }
    
    @classmethod
    def _build_result(cls, pred, conf, signals, reason, flipped, recovery, fail_streak):
        return {
            'prediction': pred, 'confidence': conf,
            'method': reason, 'signals': signals, 'top_signals': [],
            'reason': reason, 'flipped': flipped,
            'cau': 'unknown', 'cau_label': '',
            'recovery_active': recovery, 'fail_streak': fail_streak,
        }


# ========== SYSTEM ==========
def fetch_sessions():
    global _LAST_FETCH_TIME, _LAST_FETCH_RESULT
    with _FETCH_LOCK:
        now = time.time()
        if now - _LAST_FETCH_TIME < _FETCH_CACHE_TTL and _LAST_FETCH_RESULT[0]:
            return _LAST_FETCH_RESULT
        headers = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'}
        for attempt in range(3):
            try:
                r = requests.get(API_URL_MD5, timeout=6, headers=headers)
                if r.status_code == 200:
                    data = r.json()
                    if 'list' in data and data['list']:
                        rows = [{'phien': int(i['id']),
                                 'result': "Tài" if i['resultTruyenThong'] == "TAI" else "Xỉu",
                                 'dice': f"{i['dices'][0]} + {i['dices'][1]} + {i['dices'][2]}",
                                 'total': i['point']} for i in data['list']]
                        _LAST_FETCH_TIME = now
                        _LAST_FETCH_RESULT = (rows, True)
                        return rows, True
            except Exception:
                time.sleep(1)
        return _LAST_FETCH_RESULT

def _load_log():
    global PREDICTION_LOG, RESET_ID
    if not os.path.exists(LOG_FILE): return
    try:
        with open(LOG_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        PREDICTION_LOG = {int(k): v for k, v in data.get('log', {}).items()}
        RESET_ID = int(data.get('reset_id', 0))
    except Exception: pass

def _save_log():
    try:
        with open(LOG_FILE, 'w', encoding='utf-8') as f:
            json.dump({'log': PREDICTION_LOG, 'reset_id': RESET_ID}, f)
    except Exception: pass

def _sync_history():
    raw_rows, success = fetch_sessions()
    if not success and not HISTORY_CACHE: return [], False
    for row in raw_rows:
        HISTORY_CACHE[row['phien']] = row
    if RESET_ID > 0:
        for k in [k for k in HISTORY_CACHE if k < RESET_ID]: del HISTORY_CACHE[k]
    valid = sorted(HISTORY_CACHE.values(), key=lambda x: x['phien'])
    _update_prediction_log(valid)
    return valid, True

def _update_prediction_log(valid):
    if not valid: return
    by_phien = {v['phien']: v for v in valid}
    phien_list = sorted(by_phien.keys())
    for phien, entry in list(PREDICTION_LOG.items()):
        if entry.get('actual') is not None: continue
        if phien in by_phien:
            actual = by_phien[phien]['result']
            entry['actual'] = actual
            entry['correct'] = (entry['prediction'] == actual)
    changed = False
    for i, phien in enumerate(phien_list):
        if phien in PREDICTION_LOG or phien <= RESET_ID: continue
        history_before = [by_phien[p] for p in phien_list[:i]]
        if len(history_before) < 3: continue
        pred = MultiStrategyPredictor.predict(history_before, log=PREDICTION_LOG)
        actual = by_phien[phien]['result']
        PREDICTION_LOG[phien] = {
            'prediction': pred['prediction'], 'confidence': pred['confidence'],
            'method': pred['method'], 'actual': actual, 'correct': (pred['prediction'] == actual),
            'logged_at': datetime.now().isoformat(), 'retroactive': True,
            'flipped': pred.get('flipped', False)}
        changed = True
    if len(PREDICTION_LOG) > MAX_LOG_SIZE:
        for k in sorted(PREDICTION_LOG.keys())[:len(PREDICTION_LOG) - MAX_LOG_SIZE]:
            del PREDICTION_LOG[k]
        changed = True
    if changed: _save_log()

def _log_prediction(phien, pred_result):
    if phien in PREDICTION_LOG: return
    PREDICTION_LOG[phien] = {
        'prediction': pred_result['prediction'], 'confidence': pred_result['confidence'],
        'method': pred_result['method'], 'actual': None, 'correct': None,
        'logged_at': datetime.now().isoformat(), 'retroactive': False,
        'flipped': pred_result.get('flipped', False)}
    _save_log()

def _log_stats(log=None):
    if log is None: log = PREDICTION_LOG
    evaluated = [e for e in log.values() if e.get('actual') is not None]
    correct = sum(1 for e in evaluated if e.get('correct'))
    wrong = len(evaluated) - correct
    pending = sum(1 for e in log.values() if e.get('actual') is None)
    
    # Thống kê theo 3 khoảng % (50-60, 70-80, 80+)
    band_stats = {
        'low': {'correct': 0, 'wrong': 0},
        'med': {'correct': 0, 'wrong': 0},
        'high': {'correct': 0, 'wrong': 0}
    }
    for e in evaluated:
        c = e.get('confidence', 0)
        if c <= 60: b = 'low'
        elif c <= 80: b = 'med'
        else: b = 'high'
        
        if e.get('correct'): band_stats[b]['correct'] += 1
        else: band_stats[b]['wrong'] += 1
    
    return {
        'correct': correct, 'wrong': wrong, 'pending': pending,
        'evaluated': len(evaluated),
        'band_stats': band_stats
    }

_load_log()


# ========== HTML GIAO DIỆN (BỎ TIER - THÊM BAND STATS) ==========
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="vi"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>LE MINH V1</title>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/sweetalert2@11"></script>
<style>
:root{--bg-1:#eff6ff;--text:#0f172a;--text-soft:#475569;--muted:#94a3b8;--border:#dbeafe;--border-blue:#93c5fd;--border-strong:#60a5fa;--primary:#0ea5e9;--primary-dark:#0284c7;--tai:#0284c7;--tai-bg:#e0f2fe;--xiu:#0891b2;--xiu-bg:#cffafe;--ok:#059669;--ok-bg:#d1fae5;--fail:#dc2626;--fail-bg:#fee2e2;--warn:#d97706;--warn-bg:#fef3c7;--shadow:0 4px 12px rgba(14,165,233,.10);--shadow-lg:0 12px 32px rgba(2,132,199,.15)}
*{box-sizing:border-box;margin:0;padding:0;font-family:'Plus Jakarta Sans',sans-serif}
body{background:linear-gradient(135deg,#eff6ff,#f0f9ff 35%,#e0f2fe 70%,#dbeafe);background-attachment:fixed;color:var(--text);min-height:100vh;padding-bottom:30px;font-size:13px}
.header{background:rgba(255,255,255,.9);backdrop-filter:blur(20px);border-bottom:1px solid var(--border-blue);padding:12px 16px;display:flex;justify-content:space-between;align-items:center;position:sticky;top:0;z-index:100;box-shadow:0 2px 12px rgba(14,165,233,.08)}
.logo{font-size:16px;font-weight:800;display:flex;align-items:center;gap:8px}
.logo-text{background:linear-gradient(90deg,#0284c7,#0ea5e9,#38bdf8,#0284c7);background-size:200% auto;-webkit-background-clip:text;-webkit-text-fill-color:transparent;animation:shine 4s linear infinite}
@keyframes shine{to{background-position:200% center}}
.logo span.ver{font-size:9px;background:linear-gradient(135deg,#0284c7,#0ea5e9);color:#fff;padding:3px 8px;border-radius:20px;font-weight:800}
.actions{display:flex;gap:8px;align-items:center}
.clock{font-size:11px;color:var(--muted);font-weight:600}
.btn{background:#fff;color:var(--text-soft);border:1px solid var(--border);padding:7px 12px;border-radius:9px;font-weight:700;font-size:12px;cursor:pointer;transition:.2s}
.btn:hover{background:var(--bg-1);border-color:var(--border-strong);color:var(--primary-dark)}
.btn-reset{background:linear-gradient(135deg,#fee2e2,#fecaca);color:var(--fail);border-color:#fca5a5}
.spin{animation:spin .5s}@keyframes spin{to{transform:rotate(360deg)}}
.wrap{max-width:780px;margin:0 auto;padding:12px}
.pred-box{background:linear-gradient(135deg,#fff,#eff6ff);border:1px solid var(--border-blue);border-radius:16px;padding:18px;margin-bottom:12px;position:relative;overflow:hidden;box-shadow:var(--shadow-lg)}
.pred-box::before{content:'';position:absolute;top:0;left:0;right:0;height:3px;background:linear-gradient(90deg,#0284c7,#0ea5e9,#38bdf8,#0284c7);background-size:200% 100%;animation:shine 3s linear infinite}
.pred-row{display:flex;align-items:center;justify-content:space-between}
.pred-label{font-size:10px;color:var(--muted);text-transform:uppercase;font-weight:700;letter-spacing:.5px;margin-bottom:6px}
.pred-value{font-size:34px;font-weight:800;line-height:1}
.pred-value.tai{color:var(--tai)}.pred-value.xiu{color:var(--xiu)}
.pred-conf-badge{background:linear-gradient(135deg,#dbeafe,#bfdbfe);border:1px solid var(--border-strong);color:var(--primary-dark);padding:7px 16px;border-radius:20px;font-weight:800;font-size:15px}
.conf-bar{height:5px;background:var(--bg-1);border-radius:3px;overflow:hidden;margin-top:14px}
.conf-bar-fill{height:100%;background:linear-gradient(90deg,#0284c7,#0ea5e9,#38bdf8);transition:width 1s ease}
.scorebar{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:12px}
.sc{background:#fff;border:1px solid var(--border);border-radius:11px;padding:12px 8px;text-align:center}
.sc-l{font-size:9px;color:var(--muted);text-transform:uppercase;font-weight:700;margin-bottom:4px}
.sc-v{font-size:22px;font-weight:800}
.sc-v.ok{color:var(--ok)}.sc-v.fail{color:var(--fail)}.sc-v.warn{color:var(--warn)}
.tierbar{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-bottom:12px}
.tb{background:#fff;border:1px solid var(--border);border-radius:9px;padding:8px 4px;text-align:center}
.tb-l{font-size:9px;font-weight:800;text-transform:uppercase;margin-bottom:3px;letter-spacing:.3px}
.tb-v{font-size:13px;font-weight:800}
.tb-w{font-size:8px;color:var(--muted);margin-top:2px}
.tbl{background:#fff;border:1px solid var(--border);border-radius:14px;overflow:hidden;max-height:65vh;overflow-y:auto;box-shadow:var(--shadow)}
table{width:100%;border-collapse:separate;border-spacing:0}
th{background:linear-gradient(135deg,#eff6ff,#dbeafe);color:var(--primary-dark);font-size:10px;font-weight:700;padding:12px 6px;text-align:center;border-bottom:1px solid var(--border-blue);position:sticky;top:0;z-index:10}
td{padding:10px 6px;text-align:center;border-bottom:1px solid var(--bg-1);font-size:12px;font-weight:600}
tr:hover td{background:#eff6ff}
.row-next{background:linear-gradient(90deg,#e0f2fe,#fff);border-left:3px solid var(--primary)}
.badge{display:inline-block;padding:3px 10px;border-radius:14px;font-size:10px;font-weight:800;min-width:52px}
.b-tai{background:var(--tai-bg);color:var(--tai);border:1px solid #7dd3fc}
.b-xiu{background:var(--xiu-bg);color:var(--xiu);border:1px solid #67e8f9}
.b-none{background:var(--bg-1);color:var(--muted);border:1px solid var(--border)}
.b-ok{background:var(--ok-bg);color:var(--ok);border:1px solid #6ee7b7}
.b-fail{background:var(--fail-bg);color:var(--fail);border:1px solid #fca5a5}
.b-pending{background:var(--warn-bg);color:var(--warn);border:1px solid #fcd34d}
.dice{font-size:10px;color:var(--muted);display:block;margin-top:2px}
.pred-mini{font-size:11px;font-weight:800}
.pred-mini.tai{color:var(--tai)}.pred-mini.xiu{color:var(--xiu)}
.loading{text-align:center;padding:30px;color:var(--primary);font-weight:600}
@media(max-width:520px){th,td{padding:8px 4px;font-size:11px}.clock{display:none}.badge{min-width:44px;font-size:9px}.sc-v{font-size:17px}.pred-value{font-size:26px}}
</style></head><body>
<div class="header"><div class="logo"><span class="logo-text">LE MINH</span><span class="ver">V1</span></div>
<div class="actions"><div class="clock" id="clock">--:--:--</div>
<button class="btn" onclick="manualRefresh(this)">↻</button>
<button class="btn btn-reset" onclick="confirmReset()">Reset</button></div></div>
<div class="wrap">
<div id="loading-screen" class="loading" style="display:none;">🌊 Đang tải dữ liệu...</div>
<div id="main-content">
<div class="pred-box"><div class="pred-row">
<div><div class="pred-label">Dự đoán phiên tiếp theo</div>
<div class="pred-value" id="pred-value">—</div></div>
<div class="pred-conf-badge" id="conf-badge">--%</div></div>
<div class="conf-bar"><div class="conf-bar-fill" id="conf-bar" style="width:0%"></div></div>
</div>
<div class="scorebar">
<div class="sc"><div class="sc-l">✓ Đúng</div><div class="sc-v ok" id="sc-correct">0</div></div>
<div class="sc"><div class="sc-l">✗ Sai</div><div class="sc-v fail" id="sc-wrong">0</div></div>
<div class="sc"><div class="sc-l">Chờ</div><div class="sc-v warn" id="sc-pending">0</div></div>
</div>
<div class="tierbar">
<div class="tb"><div class="tb-l" style="color:#64748b">Thấp</div><div class="tb-v" id="tb-low-c">0/0</div><div class="tb-w">50-60%</div></div>
<div class="tb"><div class="tb-l" style="color:#b45309">Vừa</div><div class="tb-v" id="tb-med-c">0/0</div><div class="tb-w">70-80%</div></div>
<div class="tb"><div class="tb-l" style="color:#6d28d9">Cao</div><div class="tb-v" id="tb-high-c">0/0</div><div class="tb-w">80-90%</div></div>
</div>
<div class="tbl"><table><thead><tr><th>Phiên</th><th>Dự đoán</th><th>Kết quả</th><th>Đúng/Sai</th></tr></thead>
<tbody id="tbody"><tr><td colspan="4" class="loading">Đang kết nối...</td></tr></tbody></table></div>
</div></div>
<script>
let isResetting=false;
function manualRefresh(btn){btn.classList.add('spin');setTimeout(()=>btn.classList.remove('spin'),500);fetchData();}
async function fetchData(){
  if(isResetting)return;
  document.getElementById('clock').innerText=new Date().toLocaleTimeString('vi-VN');
  try{
    const r=await fetch('/api/history',{cache:'no-store'});
    const d=await r.json();
    if(!d.ready){document.getElementById('loading-screen').style.display='block';document.getElementById('main-content').style.display='none';return;}
    document.getElementById('loading-screen').style.display='none';
    document.getElementById('main-content').style.display='block';
    const ai=d.ai_stats||{};
    document.getElementById('sc-correct').innerText=ai.correct||0;
    document.getElementById('sc-wrong').innerText=ai.wrong||0;
    document.getElementById('sc-pending').innerText=ai.pending||0;
    
    const bs=ai.band_stats||{};
    const lw=((bs.low||{}).correct||0)+((bs.low||{}).wrong||0);
    const mw=((bs.med||{}).correct||0)+((bs.med||{}).wrong||0);
    const hw=((bs.high||{}).correct||0)+((bs.high||{}).wrong||0);
    document.getElementById('tb-low-c').innerText=((bs.low||{}).correct||0)+'/'+lw;
    document.getElementById('tb-med-c').innerText=((bs.med||{}).correct||0)+'/'+mw;
    document.getElementById('tb-high-c').innerText=((bs.high||{}).correct||0)+'/'+hw;
    
    const pred=d.prediction, rows=d.rows||[];
    const pv=document.getElementById('pred-value');
    const cb=document.getElementById('conf-badge');
    const bar=document.getElementById('conf-bar');
    if(!pred){pv.innerText='—';cb.innerText='--%';bar.style.width='0%';}
    else{
      pv.innerText=pred.prediction;
      pv.className='pred-value '+(pred.prediction==='Tài'?'tai':'xiu');
      cb.innerText=pred.confidence+'%';
      bar.style.width=pred.confidence+'%';
    }
    const tbody=document.getElementById('tbody');
    if(!rows.length){tbody.innerHTML='<tr><td colspan="4" class="loading">Chưa có dữ liệu</td></tr>';return;}
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
      html+=`<tr ${trCls}><td>#${row.phien}${row.is_next?'<br><span style="font-size:9px;color:var(--primary)">NEXT</span>':''}</td><td>${predCell}</td><td>${resBadge}${dice}</td><td>${evalCell}</td></tr>`;
    });
    tbody.innerHTML=html;
  }catch(e){console.error(e);}
}
async function confirmReset(){
  Swal.fire({title:'Reset?',text:'Xoá toàn bộ cache và log?',icon:'warning',showCancelButton:true,confirmButtonColor:'#0ea5e9',cancelButtonColor:'#94a3b8',confirmButtonText:'Đồng ý',cancelButtonText:'Hủy'}).then(async r=>{
    if(!r.isConfirmed)return;
    isResetting=true;
    try{
      const res=await fetch('/api/reset',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
      const d=await res.json();
      isResetting=false;
      if(d.status==='success'){fetchData();Swal.fire({title:'OK!',text:'Đã reset.',icon:'success'});}
      else throw new Error(d.message||'Lỗi');
    }catch(e){isResetting=false;Swal.fire({title:'Lỗi!',text:e.message,icon:'error'});}
  });
}
fetchData();
setInterval(fetchData,5000);
</script></body></html>"""


@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)


@app.route('/api/history')
def history():
    try:
        valid, success = _sync_history()
        if not success: return jsonify({'error': 'Không thể kết nối API'}), 500
        if not valid: return jsonify({'ready': False, 'message': 'Đang tải dữ liệu...', 'ai_stats': _log_stats()})
        next_phien = valid[-1]['phien'] + 1
        pred = MultiStrategyPredictor.predict(valid, log=PREDICTION_LOG)
        _log_prediction(next_phien, pred)
        out = [{
            'phien': next_phien, 'is_next': True, 'result': 'Chờ...',
            'dice': '? + ? + ?', 'total': None,
            'ai_prediction': pred['prediction'], 'ai_confidence': pred['confidence'], 'ai_correct': None,
        }]
        for v in reversed(valid[-200:]):
            if v['phien'] <= RESET_ID: continue
            log = PREDICTION_LOG.get(v['phien'])
            out.append({
                'phien': v['phien'], 'is_next': False, 'result': v['result'], 'dice': v['dice'], 'total': v['total'],
                'ai_prediction': log['prediction'] if log else None,
                'ai_confidence': log['confidence'] if log else None,
                'ai_correct': log['correct'] if log else None,
            })
        return jsonify({
            'ready': True, 'rows': out, 'ai_stats': _log_stats(),
            'prediction': {'prediction': pred['prediction'], 'confidence': pred['confidence']}
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/lc79hu')
def lc79hu():
    try:
        valid, success = _sync_history()
        if not success: return jsonify({'error': 'Không thể kết nối API'}), 500
        if not valid: return jsonify({'Id': '@cskhleminh', 'Phien': None, 'Du_doan': None, 'Do_tin_cay': None, 'message': 'Đang tải...'})
        last = valid[-1]
        next_phien = last['phien'] + 1
        pred = MultiStrategyPredictor.predict(valid, log=PREDICTION_LOG)
        _log_prediction(next_phien, pred)
        return jsonify({
            'Id': '@cskhleminh', 'Phien': str(next_phien),
            'Du_doan': pred['prediction'], 'Do_tin_cay': f"{pred['confidence']}%",
            'Ket_qua_du_doan': pred['prediction']
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/lc79md5')
def lc79md5():
    try:
        valid, success = _sync_history()
        if not success: return jsonify({'error': 'Không thể kết nối MD5 API'}), 500
        if not valid: return jsonify({'Id': '@cskhleminh', 'Phien': None, 'message': 'Đang tải dữ liệu...'})
        last = valid[-1]
        next_phien = last['phien'] + 1
        pred = MultiStrategyPredictor.predict(valid, log=PREDICTION_LOG)
        _log_prediction(next_phien, pred)
        dice = _generate_dice(pred['prediction'], pred['confidence'], seed_str=f"phien_{next_phien}_{last['phien']}")
        dice_str = f"{dice[0]}-{dice[1]}-{dice[2]}"
        return jsonify({
            'Id': '@cskhleminh', 'Phien': str(next_phien), 'Du_doan': pred['prediction'],
            'Xuc_xac': dice_str, 'Do_tin_cay': f"{pred['confidence']}%",
            'Ket_qua_du_doan': pred['prediction']
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/predict')
def predict():
    try:
        valid, success = _sync_history()
        if not success: return jsonify({'error': 'Không thể kết nối API'}), 500
        if not valid: return jsonify({'ready': False, 'prediction': None})
        last = valid[-1]
        next_phien = last['phien'] + 1
        pred = MultiStrategyPredictor.predict(valid, log=PREDICTION_LOG)
        _log_prediction(next_phien, pred)
        return jsonify({
            'version': 'V21', 'next_phien': next_phien, 'last_result': last['result'],
            'prediction': pred['prediction'], 'confidence': pred['confidence'],
            'method': pred['method'], 'top_signals': pred.get('top_signals', []),
            'reason': pred['reason'], 'flipped': pred.get('flipped', False),
            'recovery_active': pred.get('recovery_active', False),
            'fail_streak': pred.get('fail_streak', 0), 'ai_stats': _log_stats(),
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/reset', methods=['POST'])
def reset_api():
    global RESET_ID, HISTORY_CACHE, PREDICTION_LOG
    try:
        raw_rows, success = fetch_sessions()
        if success and raw_rows:
            valid = sorted(raw_rows, key=lambda x: x['phien'])
            if valid:
                RESET_ID = valid[-1]['phien']
                HISTORY_CACHE = {valid[-1]['phien']: valid[-1]}
        elif HISTORY_CACHE:
            RESET_ID = max(HISTORY_CACHE.keys())
            HISTORY_CACHE = {RESET_ID: HISTORY_CACHE[RESET_ID]}
        else:
            return jsonify({'status': 'error', 'message': 'Không có dữ liệu'}), 400
        PREDICTION_LOG = {}
        _save_log()
        return jsonify({'status': 'success', 'reset_to': RESET_ID + 1})
    except Exception as e:
        traceback.print_exc()
        return jsonify({'status': 'error', 'message': str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
