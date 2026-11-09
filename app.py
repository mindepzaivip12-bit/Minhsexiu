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

PATTERN_MEMORY = deque(maxlen=200)
STRATEGY_BLACKLIST = set()

SIGNAL_NAMES = {
    # Core
    'markov3': 'Markov bậc 3', 'markov2': 'Markov bậc 2', 'markov1': 'Markov bậc 1',
    'streak': 'Bẻ chuỗi', 'pattern': 'Khớp mẫu', 'weighted': 'Tần suất',
    'dice': 'Lệch xúc xắc', 'alternation': 'Cầu 1-1', 'momentum': 'Đà',
    'recency': 'Gần đây', 'invert': 'Đảo cầu', 'trend': 'Xu hướng',
    'bias_run': 'Lệch 12 phiên', 'follow': 'Theo cầu', 'exhaust': 'Cầu kiệt',
    'dominance': 'Lệch cực đoan', 'auto_cycle': 'Chu kỳ tự động',
    'pattern_sim': 'Tương đồng mẫu', 'regime': 'Chế độ thị trường',
    'flip_break': 'Bẻ cầu đảo', 'run_break': 'Bẻ run dài',
    'ngram_2': 'N-gram 2', 'ngram_3': 'N-gram 3', 'ngram_4': 'N-gram 4', 'ngram_5': 'N-gram 5',
    'mirror_pattern': 'Đối xứng đảo', 'reverse_trend': 'Đảo xu hướng',
    'palindrome_check': 'Cầu đối xứng', 'bayes_recent': 'Bayes gần đây',
    'chi_square': 'Kiểm định lệch', 'runs_prediction': 'Chuỗi run-length',
    'transition_matrix': 'Ma trận chuyển', 'signal_consensus': 'Đồng thuận tín hiệu',
    'signal_dissent': 'Phản biện tín hiệu', 'entropy_adaptive': 'Entropy thích ứng',
    'streak_maturity': 'Độ chín streak', 'time_pattern': 'Mẫu thời gian',
    'volatility': 'Biến động', 'cusum': 'CUSUM', 'kelly_optimal': 'Kelly tối ưu',
    'multi_window': 'Đa cửa sổ', 'rsi_signal': 'RSI cầu', 'fibonacci_break': 'Fibonacci bẻ',
    'zscore_extreme': 'Z-Score cực trị', 'harmonic_pattern': 'Mẫu hài hòa',
    'wavelet_trend': 'Xu hướng sóng', 'seasonal_cycle': 'Chu kỳ mùa',
    'markov_weighted': 'Markov trọng số', 'ensemble_boost': 'Ensemble boost',
    'decision_tree': 'Cây quyết định',
    # V9.3 NEW - XU HƯỚNG
    'macro_trend': 'Xu hướng vĩ mô',
    'drift_trend': 'Độ trôi xu hướng',
    'weighted_trend': 'Xu hướng trọng số',
    'momentum_trend': 'Đà xu hướng',
    'regime_trend': 'Chế độ xu hướng',
    'trend_alignment': 'Căn xu hướng',
    # V9.3 NEW - FULL CẦU
    'cau_1_2': 'Cầu 1-2',
    'cau_2_1': 'Cầu 2-1',
    'cau_3_2': 'Cầu 3-2',
    'cau_nghieng': 'Cầu nghiêng',
    'cau_bac_thang': 'Cầu bậc thang',
    'cau_ziczac': 'Cầu ziczac',
    'cau_hon_hop': 'Cầu hỗn hợp',
    'cau_detector': 'Nhận diện cầu',
    # V9.3 - bệt thông minh
    'follow_bet_early': 'Theo bệt sớm',
    'break_bet_late': 'Bẻ bệt muộn',
    'follow_dao': 'Theo cầu đảo',
    'follow_22': 'Theo cầu 2-2',
    'follow_33': 'Theo cầu 3-3',
    'cau_momentum': 'Đà cầu',
    'reversal_signal': 'Tín hiệu đảo chiều',
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


def _tai_ratio(seq, win):
    if not seq or win <= 0:
        return 0.5
    seg = seq[-win:] if len(seq) >= win else seq
    return sum(1 for x in seg if x == 'Tài') / len(seg)


def _linreg_slope(vals):
    """Độ dốc hồi quy tuyến tính của chuỗi 0/1."""
    n = len(vals)
    if n < 3:
        return 0.0
    x_mean = (n - 1) / 2.0
    y_mean = sum(vals) / n
    num = sum((i - x_mean) * (vals[i] - y_mean) for i in range(n))
    den = sum((i - x_mean) ** 2 for i in range(n))
    return num / den if den > 0 else 0.0


# =====================================================================
# PREDICTOR V9.3 - TREND + FULL CAU
# =====================================================================
class MultiStrategyPredictor:
    _STRATEGY_TRACK = defaultdict(lambda: {'hit': 0, 'miss': 0, 'last_len': 0})

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
        return _tai_ratio(seq, win)

    # ============ V9.3: NHẬN DIỆN CẦU ĐẦY ĐỦ ============
    @classmethod
    def _detect_cau_type(cls, r):
        """
        Phân loại cầu đầy đủ:
        - 'bet_short': bệt ngắn (2-3) - follow
        - 'bet_mid': bệt vừa (4-5) - vùng chuyển tiếp
        - 'bet_long': bệt dài (>=6) - ưu tiên bẻ
        - 'dao_1_1': cầu đảo 1-1
        - 'cau_2_2': cầu 2-2
        - 'cau_3_3': cầu 3-3
        - 'cau_1_2': cầu 1-2 (1 đơn, 2 đôi)
        - 'cau_2_1': cầu 2-1 (2 đôi, 1 đơn)
        - 'cau_3_2': cầu 3-2
        - 'nghieng': cầu nghiêng (thiên 1 phía)
        - 'bac_thang': cầu bậc thang
        - 'ziczac': cầu ziczac xen kẽ
        - 'hon_hop': hỗn hợp
        - 'unknown': chưa đủ DL
        """
        if len(r) < 12:
            return 'unknown', 0, 0.5

        cur_len, cur_val = cls._tail_run(r)
        recent25 = r[-25:]
        runs = _run_lengths(recent25)
        p_tai = _tai_ratio(r, min(20, len(r)))

        # --- Bệt ---
        if cur_len >= 6:
            return 'bet_long', cur_len, p_tai
        if cur_len >= 4:
            return 'bet_mid', cur_len, p_tai
        if cur_len == 3:
            # Có tiền lệ bệt >=3 trong lịch sử không?
            prev_long = sum(1 for x in runs[:-1] if x >= 3)
            if prev_long >= 1:
                return 'bet_short', cur_len, p_tai
            return 'bet_mid', cur_len, p_tai
        if cur_len == 2:
            prev_long = sum(1 for x in runs[:-1] if x >= 3)
            if prev_long >= 2:
                return 'bet_short', cur_len, p_tai

        # --- Đảo 1-1 ---
        recent14 = r[-14:]
        flips = sum(1 for i in range(1, len(recent14)) if recent14[i] != recent14[i - 1])
        flip_ratio = flips / (len(recent14) - 1) if len(recent14) > 1 else 0
        if flip_ratio >= 0.78:
            return 'dao_1_1', cur_len, p_tai

        # --- Cầu 1-2 / 2-1 / 3-2 ---
        if len(runs) >= 6:
            recent_runs = runs[-6:]
            # 1-2: pattern 1,2,1,2,1,2 hoặc 2,1,2,1,2,1
            ones = sum(1 for x in recent_runs if x == 1)
            twos = sum(1 for x in recent_runs if x == 2)
            threes = sum(1 for x in recent_runs if x == 3)
            # cầu 2-2
            if twos >= 4 and ones + threes <= 2:
                return 'cau_2_2', cur_len, p_tai
            # cầu 3-3
            if threes >= 4 and ones + twos <= 2:
                return 'cau_3_3', cur_len, p_tai
            # 1-2 pattern (xen kẽ 1 và 2)
            if ones >= 3 and twos >= 2 and len(recent_runs) >= 5:
                alt12 = all(
                    (recent_runs[i] in (1, 2)) for i in range(len(recent_runs))
                )
                if alt12:
                    return 'cau_1_2', cur_len, p_tai
            # 2-1 pattern (giống 1-2 nhưng khác pha, vẫn gộp chung logic)
            if ones >= 3 and twos >= 2:
                return 'cau_2_1', cur_len, p_tai
            # 3-2
            if threes >= 2 and twos >= 2 and ones <= 2:
                return 'cau_3_2', cur_len, p_tai

        # --- Cầu nghiêng (thiên 1 phía) ---
        if len(r) >= 15:
            p20 = _tai_ratio(r, 20)
            if p20 >= 0.70 or p20 <= 0.30:
                return 'nghieng', cur_len, p_tai

        # --- Bậc thang (runs tăng/giảm dần) ---
        if len(runs) >= 4:
            tail4 = runs[-4:]
            increasing = all(tail4[i] <= tail4[i + 1] for i in range(3))
            decreasing = all(tail4[i] >= tail4[i + 1] for i in range(3))
            if (increasing or decreasing) and max(tail4) - min(tail4) >= 2:
                return 'bac_thang', cur_len, p_tai

        # --- Ziczac (runs xen kẽ 1-2-1-2 hoặc 1-3-1-3) ---
        if len(runs) >= 5:
            tail5 = runs[-5:]
            if tail5[0] == tail5[2] == tail5[4] and tail5[1] == tail5[3]:
                if tail5[0] != tail5[1]:
                    return 'ziczac', cur_len, p_tai

        return 'hon_hop', cur_len, p_tai

    # ============ V9.3: XU HƯỚNG ============
    @classmethod
    def _macro_trend(cls, valid, win=40):
        """Xu hướng vĩ mô - bias dài hạn."""
        if len(valid) < 25:
            return None, 0, 0
        r = [v['result'] for v in valid]
        w = min(win, len(r))
        seg = r[-w:]
        p_tai = sum(1 for x in seg if x == 'Tài') / w

        # Xu hướng: dùng độ dốc của 3 cửa sổ
        p10 = _tai_ratio(r, 10)
        p20 = _tai_ratio(r, 20)
        drift = p10 - p20

        # Xu hướng mạnh Tài: bias + drift dương
        if p20 >= 0.58 and drift >= -0.05:
            conf = min(int((p20 - 0.5) * 180) + 54, 76)
            return 'Tài', conf, 1.15
        # Xu hướng mạnh Xỉu
        if p20 <= 0.42 and drift <= 0.05:
            conf = min(int((0.5 - p20) * 180) + 54, 76)
            return 'Xỉu', conf, 1.15
        # Bias lệch nhẹ nhưng ổn định
        if p20 >= 0.55 and p10 >= 0.55:
            return 'Tài', 58, 0.95
        if p20 <= 0.45 and p10 <= 0.45:
            return 'Xỉu', 58, 0.95
        return None, 0, 0

    @classmethod
    def _drift_trend(cls, valid):
        """Độ trôi xu hướng - slope hồi quy."""
        if len(valid) < 20:
            return None, 0, 0
        r = [v['result'] for v in valid]
        vals = [1 if x == 'Tài' else 0 for x in r[-20:]]
        slope = _linreg_slope(vals)
        # slope dương mạnh -> Tài
        if slope >= 0.06:
            conf = min(int(slope * 200) + 55, 75)
            return 'Tài', conf, 1.20
        if slope <= -0.06:
            conf = min(int(-slope * 200) + 55, 75)
            return 'Xỉu', conf, 1.20
        return None, 0, 0

    @classmethod
    def _weighted_trend(cls, valid):
        """Xu hướng trọng số theo exponential decay."""
        if len(valid) < 20:
            return None, 0, 0
        r = [v['result'] for v in valid]
        # 3 mức decay
        scores = {}
        for alpha in (0.85, 0.92, 0.97):
            s_tai, s_xiu = 0.0, 0.0
            for i, x in enumerate(r[-30:]):
                w = alpha ** (len(r[-30:]) - i - 1)
                if x == 'Tài':
                    s_tai += w
                else:
                    s_xiu += w
            total = s_tai + s_xiu
            if total > 0:
                scores[alpha] = s_tai / total
        if not scores:
            return None, 0, 0
        p_avg = sum(scores.values()) / len(scores)
        # Đồng thuận cả 3 mức
        if all(p >= 0.58 for p in scores.values()):
            return 'Tài', min(int(p_avg * 100) + 2, 76), 1.20
        if all(p <= 0.42 for p in scores.values()):
            return 'Xỉu', min(int((1 - p_avg) * 100) + 2, 76), 1.20
        return None, 0, 0

    @classmethod
    def _momentum_trend(cls, valid):
        """Đà xu hướng ngắn hạn."""
        if len(valid) < 12:
            return None, 0, 0
        r = [v['result'] for v in valid]
        p5 = _tai_ratio(r, 5)
        p10 = _tai_ratio(r, 10)
        p15 = _tai_ratio(r, 15) if len(r) >= 15 else p10
        # Tăng tốc Tài
        if p5 >= 0.70 and p10 >= 0.60 and p5 >= p10 >= p15 - 0.05:
            return 'Tài', min(int(p5 * 100), 75), 1.10
        # Tăng tốc Xỉu
        if p5 <= 0.30 and p10 <= 0.40 and p5 <= p10 <= p15 + 0.05:
            return 'Xỉu', min(int((1 - p5) * 100), 75), 1.10
        return None, 0, 0

    @classmethod
    def _regime_trend(cls, valid):
        """Chế độ xu hướng: strong_trend / weak_trend / chop."""
        if len(valid) < 25:
            return None, 0, 0
        r = [v['result'] for v in valid]
        p10 = _tai_ratio(r, 10)
        p20 = _tai_ratio(r, 20)
        # Chế độ trend rõ ràng
        if abs(p20 - 0.5) >= 0.10 and abs(p10 - p20) <= 0.15:
            if p20 >= 0.60:
                return 'Tài', 64, 1.15
            if p20 <= 0.40:
                return 'Xỉu', 64, 1.15
        return None, 0, 0

    @classmethod
    def _trend_alignment(cls, valid):
        """Căn xu hướng đa cửa sổ - chỉ fire khi đồng thuận."""
        if len(valid) < 25:
            return None, 0, 0
        r = [v['result'] for v in valid]
        votes = []
        for win in (8, 12, 16, 20, 25):
            p = _tai_ratio(r, win)
            if p >= 0.60:
                votes.append('Tài')
            elif p <= 0.40:
                votes.append('Xỉu')
        if len(votes) >= 4:
            c = Counter(votes)
            top, cnt = c.most_common(1)[0]
            if cnt >= 4:
                return top, min(55 + cnt * 4, 76), 1.25
        return None, 0, 0

    # ============ V9.3: CẦU SIGNALS THÔNG MINH ============
    @classmethod
    def _follow_bet_early(cls, valid):
        """Theo bệt SỚM - chỉ khi bệt 2-3 phiên và có tiền lệ."""
        if len(valid) < 20:
            return None, 0, 0
        r = [v['result'] for v in valid]
        cau, cur_len, p_tai = cls._detect_cau_type(r)
        if cau not in ('bet_short',):
            return None, 0, 0
        cur_val = r[-1]
        runs = _run_lengths(r[-40:])
        # Kiểm tra: trong 40 phiên gần, các run >= 3 có nhiều không?
        prev_long = sum(1 for x in runs[:-1] if x >= 3)
        if prev_long < 2:
            return None, 0, 0
        # Bệt đã được chứng minh là ổn định
        conf = min(58 + cur_len * 4 + prev_long * 2, 76)
        return cur_val, conf, 1.30

    @classmethod
    def _break_bet_late(cls, valid):
        """Bẻ bệt MUỘN - chỉ khi bệt >= 5 phiên."""
        if len(valid) < 20:
            return None, 0, 0
        r = [v['result'] for v in valid]
        cau, cur_len, p_tai = cls._detect_cau_type(r)
        if cau not in ('bet_long', 'bet_mid'):
            return None, 0, 0
        if cur_len < 5:
            return None, 0, 0
        cur_val = r[-1]
        opp = 'Xỉu' if cur_val == 'Tài' else 'Tài'
        runs = _run_lengths(r[-40:])
        max_prev = max(runs[:-1]) if len(runs) > 1 else 0
        # Bẻ khi:
        # 1. cur_len > max_prev (phá kỷ lục)
        # 2. HOẶC cur_len >= 6 (bệt quá dài)
        # 3. HOẶC Fibonacci
        conf, weight = 0, 0
        if cur_len > max_prev and cur_len >= 5:
            conf = min(62 + (cur_len - max_prev) * 3, 78)
            weight = 1.30
        if cur_len >= 6:
            conf = max(conf, min(64 + (cur_len - 6) * 2, 80))
            weight = max(weight, 1.40)
        if cur_len in (5, 8, 13):
            conf = max(conf, 66)
            weight = max(weight, 1.20)
        if conf > 0:
            return opp, conf, weight
        return None, 0, 0

    @classmethod
    def _follow_dao(cls, valid):
        """Theo cầu đảo 1-1."""
        if len(valid) < 14:
            return None, 0, 0
        r = [v['result'] for v in valid]
        cau, _, _ = cls._detect_cau_type(r)
        if cau != 'dao_1_1':
            return None, 0, 0
        recent = r[-14:]
        flips = sum(1 for i in range(1, len(recent)) if recent[i] != recent[i - 1])
        ratio = flips / (len(recent) - 1)
        nxt = 'Xỉu' if r[-1] == 'Tài' else 'Tài'
        conf = min(60 + int(ratio * 22), 82)
        weight = 1.35 + (ratio - 0.78) * 0.5
        return nxt, conf, min(weight, 1.85)

    @classmethod
    def _follow_22(cls, valid):
        """Theo cầu 2-2."""
        if len(valid) < 20:
            return None, 0, 0
        r = [v['result'] for v in valid]
        cau, cur_len, _ = cls._detect_cau_type(r)
        if cau != 'cau_2_2':
            return None, 0, 0
        cur_val = r[-1]
        if cur_len == 1:
            return cur_val, 62, 1.35
        if cur_len == 2:
            opp = 'Xỉu' if cur_val == 'Tài' else 'Tài'
            return opp, 64, 1.40
        return None, 0, 0

    @classmethod
    def _follow_33(cls, valid):
        """Theo cầu 3-3."""
        if len(valid) < 24:
            return None, 0, 0
        r = [v['result'] for v in valid]
        cau, cur_len, _ = cls._detect_cau_type(r)
        if cau != 'cau_3_3':
            return None, 0, 0
        cur_val = r[-1]
        opp = 'Xỉu' if cur_val == 'Tài' else 'Tài'
        if cur_len <= 2:
            return cur_val, 60, 1.25
        if cur_len == 3:
            return opp, 66, 1.40
        return None, 0, 0

    @classmethod
    def _cau_1_2(cls, valid):
        """Cầu 1-2 / 2-1: dựa trên run hiện tại."""
        if len(valid) < 20:
            return None, 0, 0
        r = [v['result'] for v in valid]
        cau, cur_len, _ = cls._detect_cau_type(r)
        if cau not in ('cau_1_2', 'cau_2_1'):
            return None, 0, 0
        cur_val = r[-1]
        opp = 'Xỉu' if cur_val == 'Tài' else 'Tài'
        # Nếu run hiện tại = 1 -> khả năng sang run khác = 2
        if cur_len == 1:
            return cur_val, 62, 1.25
        if cur_len == 2:
            return opp, 63, 1.30
        return None, 0, 0

    @classmethod
    def _cau_3_2(cls, valid):
        """Cầu 3-2."""
        if len(valid) < 24:
            return None, 0, 0
        r = [v['result'] for v in valid]
        cau, cur_len, _ = cls._detect_cau_type(r)
        if cau != 'cau_3_2':
            return None, 0, 0
        cur_val = r[-1]
        opp = 'Xỉu' if cur_val == 'Tài' else 'Tài'
        if cur_len <= 2:
            return cur_val, 60, 1.20
        if cur_len == 3:
            return opp, 63, 1.25
        return None, 0, 0

    @classmethod
    def _cau_nghieng(cls, valid):
        """Cầu nghiêng - thiên 1 phía rõ rệt."""
        if len(valid) < 20:
            return None, 0, 0
        r = [v['result'] for v in valid]
        cau, _, p_tai = cls._detect_cau_type(r)
        if cau != 'nghieng':
            return None, 0, 0
        # Cầu nghiêng: theo phe đang thắng
        if p_tai >= 0.68:
            return 'Tài', min(int(p_tai * 100), 76), 1.20
        if p_tai <= 0.32:
            return 'Xỉu', min(int((1 - p_tai) * 100), 76), 1.20
        return None, 0, 0

    @classmethod
    def _cau_bac_thang(cls, valid):
        """Cầu bậc thang - runs tăng/giảm dần."""
        if len(valid) < 24:
            return None, 0, 0
        r = [v['result'] for v in valid]
        cau, cur_len, _ = cls._detect_cau_type(r)
        if cau != 'bac_thang':
            return None, 0, 0
        runs = _run_lengths(r[-25:])
        tail = runs[-4:]
        cur_val = r[-1]
        opp = 'Xỉu' if cur_val == 'Tài' else 'Tài'
        # Nếu runs đang tăng -> sang run mới (đảo) với độ dài lớn hơn
        # Nếu đang giảm -> tiếp tục hiện tại
        increasing = all(tail[i] <= tail[i + 1] for i in range(len(tail) - 1))
        if increasing and cur_len >= tail[-1] - 1:
            return opp, 62, 1.15
        # Đang giảm -> sắp đảo
        decreasing = all(tail[i] >= tail[i + 1] for i in range(len(tail) - 1))
        if decreasing:
            return opp, 60, 1.10
        return None, 0, 0

    @classmethod
    def _cau_ziczac(cls, valid):
        """Cầu ziczac xen kẽ."""
        if len(valid) < 20:
            return None, 0, 0
        r = [v['result'] for v in valid]
        cau, cur_len, _ = cls._detect_cau_type(r)
        if cau != 'ziczac':
            return None, 0, 0
        runs = _run_lengths(r[-20:])
        if len(runs) < 5:
            return None, 0, 0
        # Ziczac 1-2-1-2: nếu run hiện tại = 1, tiếp theo là 2 (cùng value)
        # nếu run = 2, tiếp theo đảo
        tail5 = runs[-5:]
        if tail5[0] == tail5[2] == tail5[4] and tail5[1] == tail5[3]:
            pattern_len = tail5[0]
            cur_val = r[-1]
            opp = 'Xỉu' if cur_val == 'Tài' else 'Tài'
            if cur_len <= pattern_len:
                return cur_val, 62, 1.20
            return opp, 63, 1.25
        return None, 0, 0

    @classmethod
    def _cau_momentum(cls, valid):
        """Đà cầu."""
        if len(valid) < 15:
            return None, 0, 0
        r = [v['result'] for v in valid]
        cau, cur_len, _ = cls._detect_cau_type(r)
        if cau in ('bet_short', 'bet_mid') and cur_len >= 2:
            return r[-1], min(56 + cur_len * 3, 74), 1.05
        if cau == 'dao_1_1':
            return ('Xỉu' if r[-1] == 'Tài' else 'Tài'), 62, 1.20
        if cau == 'nghieng':
            p_tai = _tai_ratio(r, 20)
            return ('Tài' if p_tai >= 0.5 else 'Xỉu'), 60, 1.05
        return None, 0, 0

    @classmethod
    def _reversal_signal(cls, valid):
        """Tín hiệu đảo chiều."""
        if len(valid) < 25:
            return None, 0, 0
        r = [v['result'] for v in valid]
        cau, cur_len, _ = cls._detect_cau_type(r)
        if cau not in ('bet_mid', 'bet_long'):
            return None, 0, 0
        cur_val = r[-1]
        runs = _run_lengths(r[-40:])
        max_prev = max(runs[:-1]) if len(runs) > 1 else 0
        if cur_len >= max_prev and max_prev >= 3:
            opp = 'Xỉu' if cur_val == 'Tài' else 'Tài'
            conf = min(62 + (cur_len - max_prev) * 4, 78)
            return opp, conf, 1.15
        return None, 0, 0

    # ============ SIGNALS CỔ ĐIỂN (giữ nguyên) ============
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
        cau, _, _ = MultiStrategyPredictor._detect_cau_type(r)
        if cau in ('bet_short', 'bet_mid', 'bet_long'):
            return None, 0, 0
        if streak < 4 or streak > 9:
            return None, 0, 0
        breaks, continues = 0, 0
        run = 1
        for i in range(1, len(r)):
            if r[i] == r[i - 1]:
                run += 1
            else:
                if run >= 4:
                    breaks += 1
                run = 1
        if breaks < 2:
            return None, 0, 0
        p_break = breaks / (breaks + continues + 1)
        if p_break >= 0.55:
            conf = min(56 + int(p_break * 20) + (streak - 4) * 2, 74)
            return opp, conf, 1.1
        return None, 0, 0

    @staticmethod
    def _exhaustion(valid):
        if len(valid) < 10:
            return None, 0, 0
        r = [v['result'] for v in valid]
        streak, last = MultiStrategyPredictor._tail_run(r)
        cau, _, _ = MultiStrategyPredictor._detect_cau_type(r)
        if cau in ('bet_short', 'bet_mid', 'bet_long'):
            return None, 0, 0
        if streak < 7:
            return None, 0, 0
        opp = 'Xỉu' if last == 'Tài' else 'Tài'
        base = 60 + (streak - 7) * 3
        conf = max(55, min(int(base), 80))
        weight = 1.1 + (streak - 7) * 0.08
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
        cau, _, _ = MultiStrategyPredictor._detect_cau_type(r)
        if cau in ('bet_short', 'bet_mid', 'bet_long'):
            return last, min(58 + streak * 2, 70), 1.15
        if streak >= 5 or streak <= 1:
            return None, 0, 0
        if streak >= 3:
            return last, min(56 + (streak - 3) * 3, 64), 0.85
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
        return None, 0, 0

    @staticmethod
    def _trend(valid):
        if len(valid) < 20:
            return None, 0, 0
        r = [v['result'] for v in valid]

        def tai_ratio(win):
            return _tai_ratio(r, win)

        p10 = tai_ratio(10)
        p20 = tai_ratio(20)
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
        if p >= 0.75:
            return 'Tài', int(p * 100), 0.85
        if p <= 0.25:
            return 'Xỉu', int((1 - p) * 100), 0.85
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
        if p > 0.65:
            return 'Tài', int(p * 100), 1.00
        if p < 0.35:
            return 'Xỉu', int((1 - p) * 100), 1.00
        return None, 0, 0

    # ============ N-GRAM & PATTERN ============
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
        if len(r) < 16:
            return None, 0, 0
        n = 6
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

    # ============ STATISTICAL ============
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

    # ============ META ============
    @classmethod
    def _signal_consensus(cls, valid):
        if len(valid) < 10:
            return None, 0, 0
        key_signals = [
            cls._markov_order2(valid),
            cls._macro_trend(valid),
            cls._weighted_trend(valid),
            cls._break_bet_late(valid),
            cls._follow_dao(valid),
        ]
        votes = {'Tài': 0, 'Xỉu': 0}
        count = 0
        for p, c, w in key_signals:
            if p:
                votes[p] += c * w
                count += 1
        if count < 3:
            return None, 0, 0
        if votes['Tài'] > votes['Xỉu'] * 1.3:
            return 'Tài', min(int(votes['Tài'] / (votes['Tài'] + votes['Xỉu']) * 100), 82), 1.15
        if votes['Xỉu'] > votes['Tài'] * 1.3:
            return 'Xỉu', min(int(votes['Xỉu'] / (votes['Tài'] + votes['Xỉu']) * 100), 82), 1.15
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
        cau, _, _ = cls._detect_cau_type(r)
        if cau in ('bet_short', 'bet_mid', 'bet_long'):
            return None, 0, 0
        if cur_len < 4:
            return None, 0, 0
        runs = _run_lengths(r)
        max_run = max(runs)
        if max_run < 4:
            return None, 0, 0
        if cur_len >= max_run and cur_len >= 4:
            opp = 'Xỉu' if cur_val == 'Tài' else 'Tài'
            conf = min(56 + (cur_len - max_run) * 3, 74)
            return opp, conf, 1.10
        return None, 0, 0

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
            cls._macro_trend(valid),
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
        cau, cur_len, _ = cls._detect_cau_type(r)
        tail_len, tail_val = cls._tail_run(r)

        # V9.3: cây quyết định theo cầu + xu hướng
        if cau == 'bet_short':
            return tail_val, 64, 1.25
        if cau == 'bet_long':
            opp = 'Xỉu' if tail_val == 'Tài' else 'Tài'
            if tail_len >= 6:
                return opp, 66, 1.35
        if cau == 'dao_1_1':
            opp = 'Xỉu' if tail_val == 'Tài' else 'Tài'
            return opp, 66, 1.30
        if cau == 'cau_2_2':
            if tail_len == 1:
                return tail_val, 62, 1.20
            if tail_len == 2:
                opp = 'Xỉu' if tail_val == 'Tài' else 'Tài'
                return opp, 64, 1.30
        if cau == 'nghieng':
            p20 = _tai_ratio(r, 20)
            if p20 >= 0.68:
                return 'Tài', 62, 1.15
            if p20 <= 0.32:
                return 'Xỉu', 62, 1.15

        # Fallback: dùng trend alignment
        p10 = _tai_ratio(r, 10)
        p20 = _tai_ratio(r, 20)
        if p10 >= 0.62 and p20 >= 0.58:
            return 'Tài', 60, 1.10
        if p10 <= 0.38 and p20 <= 0.42:
            return 'Xỉu', 60, 1.10
        return None, 0, 0

    @classmethod
    def _auto_cycle_signal(cls, r):
        if len(r) < 12:
            return None, 0, 0
        cyc = cls._detect_cycle(r, max_period=6, min_repeats=2)
        if not cyc:
            return None, 0, 0
        return cyc['next'], min(60 + (6 - cyc['period']) * 3, 78), 1.3

    @classmethod
    def _detect_cycle(cls, r, max_period=6, min_repeats=2):
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
                phase = len(r) % period
                hits, total = 0, 0
                for i in range(len(r) - period):
                    if r[i] == pattern[i % period]:
                        hits += 1
                    total += 1
                if total >= 6 and hits / total >= 0.65:
                    score = hits / total
                    if best is None or score > best[0]:
                        best = (score, period, phase, pattern)
        if not best:
            return None
        _, period, phase, pattern = best
        return {'period': period, 'phase': phase, 'pattern': pattern,
                'next': pattern[phase % period]}

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
    def _regime_signal(cls, valid):
        if len(valid) < 20:
            return None, 0, 0
        r = [v['result'] for v in valid]
        cau, _, _ = cls._detect_cau_type(r)
        if cau in ('bet_short', 'bet_mid', 'bet_long'):
            return None, 0, 0
        if cau == 'dao_1_1':
            return ('Xỉu' if r[-1] == 'Tài' else 'Tài'), 62, 1.2
        if cau == 'nghieng':
            p20 = _tai_ratio(r, 20)
            if p20 >= 0.68:
                return 'Tài', 62, 1.10
            if p20 <= 0.32:
                return 'Xỉu', 62, 1.10
        return None, 0, 0

    @classmethod
    def _run_break(cls, valid):
        if len(valid) < 8:
            return None, 0, 0
        r = [v['result'] for v in valid]
        cau, _, _ = cls._detect_cau_type(r)
        if cau in ('bet_short', 'bet_mid', 'bet_long'):
            return None, 0, 0
        cur_len, cur_val = cls._tail_run(r)
        if cur_len < 5 or cur_len > 10:
            return None, 0, 0
        runs = _run_lengths(r)
        breaks, totals = 0, 0
        for rlen in runs[:-1]:
            if rlen >= 5:
                totals += 1
                breaks += 1
        p = breaks / totals if totals > 0 else 0.5
        if p >= 0.55:
            next_val = 'Xỉu' if cur_val == 'Tài' else 'Tài'
            conf = min(56 + int(p * 20) + (cur_len - 5) * 2, 78)
            return next_val, conf, 1.15
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
    def _fibonacci_break(cls, valid):
        if len(valid) < 15:
            return None, 0, 0
        r = [v['result'] for v in valid]
        cau, _, _ = cls._detect_cau_type(r)
        if cau in ('bet_short',):
            return None, 0, 0
        cur_len, cur_val = cls._tail_run(r)
        fibs = [3, 5, 8, 13]
        for fib in fibs:
            if cur_len == fib:
                opp = 'Xỉu' if cur_val == 'Tài' else 'Tài'
                conf = min(58 + fib, 75)
                return opp, conf, 1.10
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
        return None, 0, 0

    @classmethod
    def _dice_parity(cls, valid):
        recent = [v for v in valid[-30:] if v.get('total') is not None]
        if len(recent) < 15:
            return None, 0, 0
        last_totals = [v.get('total', 0) for v in recent[-5:]]
        even_count = sum(1 for t in last_totals if t % 2 == 0)
        if even_count >= 4:
            return 'Xỉu', 56, 0.85
        if even_count <= 1:
            return 'Tài', 56, 0.85
        return None, 0, 0

    @classmethod
    def _pattern_memory_signal(cls, valid):
        if len(PATTERN_MEMORY) < 5 or len(valid) < 15:
            return None, 0, 0
        r = [v['result'] for v in valid]
        best = None
        for entry in list(PATTERN_MEMORY)[-80:]:
            if entry.get('hits', 0) < 5:
                continue
            if entry.get('ratio', 0) < 0.7:
                continue
            pat = entry['pattern']
            plen = len(pat)
            if plen > len(r):
                continue
            if tuple(r[-min(plen, 5):]) == pat[-min(plen, 5):]:
                score = entry['hits'] * entry['ratio']
                if best is None or score > best[0]:
                    best = (score, entry['next'])
        if best and best[0] >= 3.5:
            conf = min(58 + int(best[0] * 3), 80)
            return best[1], conf, 1.15
        return None, 0, 0

    # ============ ADAPTIVE ============
    @classmethod
    def _get_adaptive_weight(cls, name, base_weight):
        tr = cls._STRATEGY_TRACK.get(name)
        if not tr:
            return base_weight
        n = tr['hit'] + tr['miss']
        if n < 8:
            return base_weight
        acc = tr['hit'] / n
        if acc < 0.45:
            return base_weight * 0.35
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
        start = max(15, len(valid) - 60)
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
            return max(50, raw_conf - 12)
        if acc_blend < 0.48:
            return max(50, raw_conf - 6)
        if acc_blend < 0.52:
            return max(50, raw_conf - 2)
        if acc_blend > 0.70:
            return min(84, raw_conf + 5)
        if acc_blend > 0.62:
            return min(84, raw_conf + 2)
        return raw_conf

    # ============ STRATEGY LIST V9.3 ============
    @classmethod
    def _strategy_list(cls):
        return [
            # === V9.3 XU HƯỚNG (ưu tiên cao) ===
            ('macro_trend',      cls._macro_trend),
            ('drift_trend',      cls._drift_trend),
            ('weighted_trend',   cls._weighted_trend),
            ('momentum_trend',   cls._momentum_trend),
            ('regime_trend',     cls._regime_trend),
            ('trend_alignment',  cls._trend_alignment),
            # === V9.3 FULL CẦU ===
            ('follow_bet_early', cls._follow_bet_early),
            ('break_bet_late',   cls._break_bet_late),
            ('follow_dao',       cls._follow_dao),
            ('follow_22',        cls._follow_22),
            ('follow_33',        cls._follow_33),
            ('cau_1_2',          cls._cau_1_2),
            ('cau_3_2',          cls._cau_3_2),
            ('cau_nghieng',      cls._cau_nghieng),
            ('cau_bac_thang',    cls._cau_bac_thang),
            ('cau_ziczac',       cls._cau_ziczac),
            ('cau_momentum',     cls._cau_momentum),
            ('reversal_signal',  cls._reversal_signal),
            # === Cổ điển ===
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
            ('auto_cycle',   lambda v: cls._auto_cycle_signal([x['result'] for x in v])),
            ('pattern_sim',  lambda v: cls._pattern_similarity([x['result'] for x in v])),
            ('regime',       cls._regime_signal),
            ('run_break',    cls._run_break),
            ('ngram_2',      lambda v: cls._ngram_signal([x['result'] for x in v], 2)),
            ('ngram_3',      lambda v: cls._ngram_signal([x['result'] for x in v], 3)),
            ('ngram_4',      lambda v: cls._ngram_signal([x['result'] for x in v], 4)),
            ('ngram_5',      lambda v: cls._ngram_signal([x['result'] for x in v], 5)),
            ('mirror_pattern',   lambda v: cls._mirror_pattern([x['result'] for x in v])),
            ('reverse_trend',    cls._reverse_trend),
            ('palindrome_check', lambda v: cls._palindrome_check([x['result'] for x in v])),
            ('bayes_recent',      cls._bayes_recent),
            ('chi_square',        cls._chi_square),
            ('runs_prediction',   cls._runs_prediction),
            ('transition_matrix', cls._transition_matrix),
            ('signal_consensus',  cls._signal_consensus),
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
            ('pattern_memory',    cls._pattern_memory_signal),
            ('dice_parity',       cls._dice_parity),
        ]

    # ============ MAIN PREDICT V9.3 ============
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
                'cau': 'unknown', 'cau_label': 'Chưa đủ DL',
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
                'cau': 'unknown', 'cau_label': 'Chưa đủ DL',
            }

        cls._sync_track_from_history(valid)

        all_results = [v['result'] for v in valid]
        cau_type, cur_streak_len, p_tai_20 = cls._detect_cau_type(all_results)
        cur_streak_val = all_results[-1]

        log_odds = 0.0
        signals = []
        total_weight = 0.0
        strong_signals = []

        # Track riêng cho cầu và xu hướng
        cau_follow_weight = 0.0
        cau_break_weight = 0.0
        trend_weight = 0.0
        trend_dir = None
        trend_votes = {'Tài': 0.0, 'Xỉu': 0.0}

        cau_follow_names = {'follow_bet_early', 'follow_dao', 'follow_22',
                            'follow_33', 'cau_1_2', 'cau_3_2', 'cau_momentum',
                            'cau_nghieng', 'cau_bac_thang', 'cau_ziczac'}
        cau_break_names = {'break_bet_late', 'reversal_signal'}
        trend_names = {'macro_trend', 'drift_trend', 'weighted_trend',
                       'momentum_trend', 'regime_trend', 'trend_alignment'}

        for name, fn in cls._strategy_list():
            if name in STRATEGY_BLACKLIST:
                continue
            try:
                p, conf, weight = fn(valid)
            except Exception:
                continue
            if p is None or weight == 0:
                continue
            weight = cls._get_adaptive_weight(name, weight)

            is_follow = (p == cur_streak_val)

            # === V9.3: ĐIỀU CHỈNH WEIGHT THEO CẦU ===
            if cau_type == 'bet_short':
                # Bệt ngắn: ưu tiên follow, giảm break
                if is_follow and name in cau_follow_names:
                    weight *= 1.20
                elif not is_follow and name in cau_break_names:
                    weight *= 0.55
            elif cau_type in ('bet_mid', 'bet_long'):
                # Bệt dài: ưu tiên break, giảm follow
                if is_follow and name in cau_follow_names:
                    weight *= 0.50
                elif not is_follow and name in cau_break_names:
                    weight *= 1.30
            elif cau_type == 'dao_1_1':
                # Cầu đảo: phạt follow, thưởng đảo
                if is_follow:
                    weight *= 0.45
                else:
                    weight *= 1.25
            elif cau_type == 'nghieng':
                # Cầu nghiêng: theo phe đang thắng
                if (p_tai_20 >= 0.5 and p == 'Tài') or (p_tai_20 < 0.5 and p == 'Xỉu'):
                    weight *= 1.15
                else:
                    weight *= 0.80

            # === V9.3: TREND ALIGNMENT ===
            if name in trend_names and p:
                trend_votes[p] += weight * (conf - 50)
                trend_weight += weight
                if trend_dir is None:
                    trend_dir = p

            p_norm = max(0.51, min(conf / 100.0, 0.90))
            lr = p_norm / (1 - p_norm)
            if p == 'Xỉu':
                lr = 1.0 / lr
            contrib = weight * (1.0 if p == 'Tài' else -1.0) * (lr - 1.0)
            log_odds += contrib
            total_weight += weight

            if name in cau_follow_names and is_follow:
                cau_follow_weight += weight
            if name in cau_break_names:
                cau_break_weight += weight

            sig = {'name': name, 'prediction': p,
                   'confidence': conf, 'weight': round(weight, 2)}
            signals.append(sig)
            if conf >= 62 and weight >= 1.05:
                strong_signals.append(sig)

        if not signals or total_weight == 0:
            streak, last = cls._tail_run(all_results)
            opp = 'Xỉu' if last == 'Tài' else 'Tài'
            return {
                'prediction': opp, 'confidence': 52,
                'method': f'Bẻ {last}×{streak}',
                'signals': [], 'top_signals': [f'Bẻ {last}×{streak}'],
                'signals_strength': [],
                'reason': f'Bẻ streak {streak}',
                'flipped': False, 'cau': cau_type,
                'cau_label': 'Cầu hỗn hợp',
            }

        scaled = log_odds / max(total_weight, 1.0) * 1.8
        p_tai = 1.0 / (1.0 + math.exp(-scaled))
        if p_tai >= 0.5:
            pred = 'Tài'
            raw_conf = int(p_tai * 100)
        else:
            pred = 'Xỉu'
            raw_conf = int((1 - p_tai) * 100)

        # === V9.3: XỬ LÝ CẦU + XU HƯỚNG ===
        # Xác định trend direction từ trend signals
        trend_dir_final = None
        if trend_votes['Tài'] > trend_votes['Xỉu'] * 1.4:
            trend_dir_final = 'Tài'
        elif trend_votes['Xỉu'] > trend_votes['Tài'] * 1.4:
            trend_dir_final = 'Xỉu'

        # Áp dụng theo loại cầu
        if cau_type == 'bet_short':
            # Bệt ngắn: follow nếu signals đồng thuận
            if cur_streak_len <= 3 and cau_follow_weight > cau_break_weight * 1.2:
                if pred != cur_streak_val:
                    pred = cur_streak_val
                    raw_conf = max(raw_conf - 2, 58)
        elif cau_type in ('bet_mid', 'bet_long'):
            # Bệt dài: ưu tiên bẻ nếu có signals break hoặc trend đảo
            opp = 'Xỉu' if cur_streak_val == 'Tài' else 'Tài'
            if cur_streak_len >= 6 and cau_break_weight > 0.3 and pred == cur_streak_val:
                pred = opp
                raw_conf = max(raw_conf - 3, 58)
            elif cur_streak_len >= 5 and trend_dir_final == opp and pred == cur_streak_val:
                pred = opp
                raw_conf = max(raw_conf - 2, 58)

        elif cau_type == 'dao_1_1':
            expected = 'Xỉu' if cur_streak_val == 'Tài' else 'Tài'
            if cau_follow_weight > cau_break_weight:
                pred = expected
                raw_conf = max(raw_conf, 64)

        # Trend override: nếu trend mạnh và pred ngược trend
        if trend_dir_final and trend_weight >= 3.5:
            if pred != trend_dir_final and raw_conf < 68:
                pred = trend_dir_final
                raw_conf = max(raw_conf, 60)

        # Safety: bẻ streak khi không phải bệt
        if cau_type not in ('bet_short', 'bet_mid', 'bet_long') and \
                cur_streak_len >= 6 and pred == cur_streak_val and raw_conf < 72:
            pred = 'Xỉu' if cur_streak_val == 'Tài' else 'Tài'
            raw_conf = max(raw_conf, 58)

        recent = [v['result'] for v in valid[-20:]]
        entropy = _entropy(recent)
        votes_tai = sum(1 for s in signals if s['prediction'] == 'Tài')
        votes_xiu = len(signals) - votes_tai
        agreement = max(votes_tai, votes_xiu) / len(signals)

        penalty = 0
        if entropy > 0.95:
            penalty += 8
        elif entropy > 0.85:
            penalty += 4
        if agreement < 0.6:
            penalty += 6
        elif agreement < 0.75:
            penalty += 3
        bonus = 4 if agreement >= 0.9 and entropy < 0.6 else 0

        # Bonus theo cầu + trend alignment
        if cau_type == 'bet_short' and pred == cur_streak_val:
            bonus += 3
        elif cau_type == 'dao_1_1' and pred != cur_streak_val:
            bonus += 3
        if trend_dir_final == pred and trend_weight >= 3.0:
            bonus += 3

        final_conf = max(50, min(raw_conf - penalty + bonus, 84))

        # Strong signals override khi conf thấp
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

        signals_sorted = sorted(
            signals,
            key=lambda s: s['weight'] * (s['confidence'] - 50),
            reverse=True
        )
        top_signals = [SIGNAL_NAMES.get(s['name'], s['name'])
                       for s in signals_sorted[:5]]

        # Label cầu V9.3
        cau_label_map = {
            'bet_short': f'Bệt ngắn {cur_streak_val}×{cur_streak_len}',
            'bet_mid': f'Bệt vừa {cur_streak_val}×{cur_streak_len}',
            'bet_long': f'Bệt dài {cur_streak_val}×{cur_streak_len}',
            'dao_1_1': 'Cầu đảo 1-1',
            'cau_2_2': 'Cầu 2-2',
            'cau_3_3': 'Cầu 3-3',
            'cau_1_2': 'Cầu 1-2',
            'cau_2_1': 'Cầu 2-1',
            'cau_3_2': 'Cầu 3-2',
            'nghieng': 'Cầu nghiêng',
            'bac_thang': 'Cầu bậc thang',
            'ziczac': 'Cầu ziczac',
            'hon_hop': 'Cầu hỗn hợp',
            'unknown': 'Chưa đủ DL',
        }
        cau_label = cau_label_map.get(cau_type, cau_type)
        if trend_dir_final:
            cau_label += f' · Trend {trend_dir_final}'

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
            'reason': (f"{cau_label} · {len(signals)} tín hiệu · "
                       f"entropy={entropy:.2f} · đồng thuận={agreement:.0%}"
                       + (f" · trend={trend_dir_final}" if trend_dir_final else "")),
            'flipped': False,
            'cau': cau_type,
            'cau_label': cau_label,
        }

        fail_streak = cls._check_failure_streak(log)
        if fail_streak >= 2 and not cls._was_flipped_last(log):
            result['prediction'] = 'Xỉu' if result['prediction'] == 'Tài' else 'Tài'
            result['confidence'] = max(55, min(result['confidence'], 72))
            result['method'] = '⚠ ' + result['method']
            result['reason'] += f" · SAI {fail_streak} → ĐẢO"
            result['flipped'] = True

        result['confidence'] = cls._calibrate_confidence(result['confidence'], log)

        try:
            cls._update_pattern_memory(valid)
        except Exception:
            pass

        return result

    @classmethod
    def _update_pattern_memory(cls, valid):
        global PATTERN_MEMORY
        if len(valid) < 10:
            return
        r = [v['result'] for v in valid]
        for plen in (3, 4, 5):
            if len(r) < plen + 1:
                continue
            pat = tuple(r[-plen - 1:-1])
            nxt = r[-1]
            hits = 0
            total = 0
            for i in range(len(r) - plen - 1):
                if tuple(r[i:i + plen]) == pat:
                    total += 1
                    if r[i + plen] == nxt:
                        hits += 1
            if total >= 3 and hits / total >= 0.65:
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
# HTML (giữ nguyên giao diện)
# =====================================================================
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>LE MINH MD5 V2</title>
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
body{background:linear-gradient(135deg,#fdf2f8 0%,#f0f9ff 50%,#fef3f9 100%);background-attachment:fixed;color:var(--text);min-height:100vh;padding-bottom:30px;font-size:13px}
body::before{content:'';position:fixed;inset:0;pointer-events:none;z-index:0;background:radial-gradient(circle at 15% 20%, rgba(251,207,232,.35), transparent 40%),radial-gradient(circle at 85% 80%, rgba(167,243,208,.28), transparent 40%)}
.header{background:rgba(255,255,255,.85);backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);border-bottom:1px solid var(--border-pink);padding:12px 16px;display:flex;justify-content:space-between;align-items:center;position:sticky;top:0;z-index:100;box-shadow:0 2px 12px rgba(219,39,119,.06)}
.logo{font-size:16px;font-weight:800;display:flex;align-items:center;gap:8px}
.logo-text{background:linear-gradient(90deg,#ec4899,#a855f7,#0ea5e9);background-size:200% auto;-webkit-background-clip:text;-webkit-text-fill-color:transparent;animation:shine 4s linear infinite}
@keyframes shine{to{background-position:200% center}}
.logo span.ver{font-size:9px;background:linear-gradient(135deg,#ec4899,#a855f7);color:#fff;padding:3px 8px;border-radius:20px;font-weight:800;letter-spacing:.5px;box-shadow:0 2px 6px rgba(168,85,247,.3)}
.logo .md5-badge{font-size:9px;background:linear-gradient(135deg,#0ea5e9,#0284c7);color:#fff;padding:3px 8px;border-radius:20px;font-weight:800;letter-spacing:.5px;box-shadow:0 2px 6px rgba(2,132,199,.3);margin-left:4px}
.actions{display:flex;gap:8px;align-items:center}
.clock{font-size:11px;color:var(--muted);font-weight:600;font-variant-numeric:tabular-nums}
.btn{background:#fff;color:var(--text-soft);border:1px solid var(--border);padding:7px 12px;border-radius:9px;font-weight:700;font-size:12px;cursor:pointer;transition:.2s;box-shadow:var(--shadow-sm)}
.btn:hover{background:var(--card-soft);transform:translateY(-1px);box-shadow:var(--shadow)}
.btn-reset{background:linear-gradient(135deg,#fee2e2,#fce7f3);color:var(--fail);border-color:var(--border-pink)}
.spin{animation:spin .5s}
@keyframes spin{to{transform:rotate(360deg)}}
.wrap{max-width:780px;margin:0 auto;padding:12px;position:relative;z-index:1}
.pred-box{background:linear-gradient(135deg,#ffffff,#fdf2f8);border:1px solid var(--border-pink);border-radius:16px;padding:18px;margin-bottom:12px;position:relative;overflow:hidden;box-shadow:var(--shadow-lg)}
.pred-row{display:flex;align-items:center;justify-content:space-between;position:relative;z-index:1}
.pred-label{font-size:10px;color:var(--muted);text-transform:uppercase;font-weight:700;letter-spacing:.5px;margin-bottom:6px}
.pred-value{font-size:34px;font-weight:800;line-height:1;letter-spacing:-1px}
.pred-value.tai{color:var(--tai);text-shadow:0 2px 12px rgba(2,132,199,.2)}
.pred-value.xiu{color:var(--xiu);text-shadow:0 2px 12px rgba(219,39,119,.2)}
.cau-badge{display:inline-block;font-size:10px;padding:3px 10px;border-radius:12px;background:linear-gradient(135deg,#fef3c7,#fde68a);color:#92400e;font-weight:700;margin-top:8px}
.pred-conf-badge{background:linear-gradient(135deg,#fce7f3,#fbcfe8);border:1px solid var(--border-pink);color:var(--xiu);padding:7px 16px;border-radius:20px;font-weight:800;font-size:15px;font-variant-numeric:tabular-nums;box-shadow:0 2px 8px rgba(236,72,153,.15)}
.conf-bar{height:5px;background:var(--bg-1);border-radius:3px;overflow:hidden;margin-top:14px;position:relative;z-index:1}
.conf-bar-fill{height:100%;background:linear-gradient(90deg,#ec4899,#a855f7,#0ea5e9);border-radius:3px;transition:width 1s ease;box-shadow:0 0 10px rgba(236,72,153,.4)}
.scorebar{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:12px}
.sc{background:#fff;border:1px solid var(--border);border-radius:11px;padding:12px 8px;text-align:center;transition:.2s;box-shadow:var(--shadow-sm)}
.sc:hover{transform:translateY(-2px);box-shadow:var(--shadow);border-color:var(--border-pink)}
.sc-l{font-size:9px;color:var(--muted);text-transform:uppercase;font-weight:700;margin-bottom:4px;letter-spacing:.5px}
.sc-v{font-size:22px;font-weight:800;font-variant-numeric:tabular-nums}
.sc-v.ok{color:var(--ok)}.sc-v.fail{color:var(--fail)}.sc-v.warn{color:var(--warn)}
.tbl{background:#fff;border:1px solid var(--border);border-radius:14px;overflow:hidden;max-height:65vh;overflow-y:auto;box-shadow:var(--shadow)}
table{width:100%;border-collapse:separate;border-spacing:0}
th{background:linear-gradient(135deg,#fdf2f8,#f0f9ff);color:var(--text-soft);font-size:10px;font-weight:700;padding:12px 6px;text-align:center;border-bottom:1px solid var(--border-pink);position:sticky;top:0;z-index:10;text-transform:uppercase;letter-spacing:.3px}
td{padding:10px 6px;text-align:center;border-bottom:1px solid var(--bg-1);font-size:12px;font-weight:600}
tr:hover td{background:#fdf2f8}
.row-next{background:linear-gradient(90deg,#fdf2f8,#fff);border-left:3px solid var(--xiu)}
.badge{display:inline-block;padding:3px 10px;border-radius:14px;font-size:10px;font-weight:800;min-width:52px;text-align:center}
.b-tai{background:var(--tai-bg);color:var(--tai);border:1px solid #bae6fd}
.b-xiu{background:var(--xiu-bg);color:var(--xiu);border:1px solid var(--border-pink)}
.b-none{background:var(--bg-1);color:var(--muted);border:1px solid var(--border)}
.b-ok{background:var(--ok-bg);color:var(--ok);border:1px solid #6ee7b7;font-size:10px;padding:4px 10px;font-weight:800}
.b-fail{background:var(--fail-bg);color:var(--fail);border:1px solid #fca5a5;font-size:10px;padding:4px 10px;font-weight:800}
.b-pending{background:var(--warn-bg);color:var(--warn);border:1px solid #fcd34d;font-size:10px;padding:4px 10px;font-weight:800;animation:blink 2s infinite}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.65}}
.dice{font-size:10px;color:var(--muted);display:block;margin-top:2px;font-variant-numeric:tabular-nums}
.pred-mini{font-size:11px;font-weight:800}
.pred-mini.tai{color:var(--tai)}.pred-mini.xiu{color:var(--xiu)}
.loading{text-align:center;padding:30px;color:var(--xiu);font-weight:600}
@media(max-width:520px){th,td{padding:8px 4px;font-size:11px}.clock{display:none}.badge{min-width:44px;font-size:9px}.sc-v{font-size:17px}.pred-value{font-size:26px}}
</style>
</head>
<body>
<div class="header">
    <div class="logo">
        <span class="logo-text">LE MINH</span>
        <span class="ver">V1</span>
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
                <div class="cau-badge" id="cau-badge">—</div>
            </div>
            <div class="pred-conf-badge" id="conf-badge">--%</div>
        </div>
        <div class="conf-bar"><div class="conf-bar-fill" id="conf-bar" style="width:0%"></div></div>
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
function manualRefresh(btn){btn.classList.add('spin');setTimeout(()=>btn.classList.remove('spin'),500);fetchData();}
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
    document.getElementById('sc-correct').innerText=ai.correct||0;
    document.getElementById('sc-wrong').innerText=ai.wrong||0;
    document.getElementById('sc-pending').innerText=ai.pending||0;
}
function renderPred(pred, rows){
    const pv=document.getElementById('pred-value');
    const cb=document.getElementById('conf-badge');
    const bar=document.getElementById('conf-bar');
    const cauB=document.getElementById('cau-badge');
    if(!pred){pv.innerText='—';pv.className='pred-value';cb.innerText='--%';bar.style.width='0%';cauB.innerText='—';}
    else{
        const p=pred.prediction, c=pred.confidence||0;
        pv.innerText=p;
        pv.className='pred-value '+(p==='Tài'?'tai':'xiu');
        cb.innerText=c+'%';
        bar.style.width=c+'%';
        cauB.innerText=pred.cau_label||'—';
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
    Swal.fire({title:'Reset?',text:'Xoá toàn bộ cache và log?',icon:'warning',background:'#fff',color:'#1e293b',showCancelButton:true,confirmButtonColor:'#ec4899',cancelButtonColor:'#94a3b8',confirmButtonText:'Đồng ý',cancelButtonText:'Hủy'}).then(async r=>{
        if(!r.isConfirmed)return;
        isResetting=true;
        try{
            const res=await fetch('/api/reset',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
            const d=await res.json();
            isResetting=false;
            if(d.status==='success'){fetchData();Swal.fire({title:'OK!',text:'Đã reset.',icon:'success',background:'#fff',color:'#1e293b',confirmButtonColor:'#ec4899'});}
            else throw new Error(d.message||'Lỗi');
        }catch(e){isResetting=false;Swal.fire({title:'Lỗi!',text:e.message,icon:'error',background:'#fff',color:'#1e293b'});}
    });
}
fetchData();
setInterval(fetchData,5000);
</script>
</body>
</html>"""


# =====================================================================
# ROUTES
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
                'cau_label': pred.get('cau_label', '—'),
                'cau': pred.get('cau', 'unknown'),
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
            return jsonify({'Id': 'LE MINH V9.3 MD5', 'Phien': None,
                            'Du_doan': None, 'message': 'Chưa có dữ liệu'})
        last = valid[-1]
        next_phien = last['phien'] + 1
        pred = MultiStrategyPredictor.predict(valid, log=PREDICTION_LOG)
        _log_prediction(next_phien, pred)
        stats = _honest_stats(valid)
        ai_stats = _log_stats(PREDICTION_LOG)
        return jsonify({
            'Id': 'LE MINH V9.3 MD5',
            'Phien': str(next_phien),
            'Ket_qua_truoc': last['result'].lower(),
            'Xuc_xac_truoc': last['dice'],
            'Diem_truoc': last['total'],
            'Du_doan': pred['prediction'],
            'Do_tin_cay': f"{pred['confidence']}%",
            'Phuong_phap': pred['method'],
            'Loai_cau': pred.get('cau_label', '—'),
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
            'Ti_le_AI': round(ai_stats['correct'] / max(ai_stats['evaluated'], 1) * 100, 1),
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
            return jsonify({'Id': 'LE MINH V9.3', 'Phien': None, 'Du_doan': None})
        last = valid[-1]
        next_phien = last['phien'] + 1
        pred = MultiStrategyPredictor.predict(valid, log=PREDICTION_LOG)
        _log_prediction(next_phien, pred)
        stats = _honest_stats(valid)
        ai_stats = _log_stats(PREDICTION_LOG)
        return jsonify({
            'Id': 'LE MINH V9.3',
            'Phien': str(next_phien),
            'Ket_qua_truoc': last['result'].lower(),
            'Du_doan': pred['prediction'],
            'Do_tin_cay': f"{pred['confidence']}%",
            'Phuong_phap': pred['method'],
            'Loai_cau': pred.get('cau_label', '—'),
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
            'Ti_le_AI': round(ai_stats['correct'] / max(ai_stats['evaluated'], 1) * 100, 1),
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
            'version': 'V9.3',
            'last_phien': last['phien'],
            'next_phien': next_phien,
            'last_result': last['result'],
            'last_dice': last['dice'],
            'last_total': last['total'],
            'prediction': pred['prediction'],
            'confidence': pred['confidence'],
            'method': pred['method'],
            'cau': pred.get('cau', 'unknown'),
            'cau_label': pred.get('cau_label', '—'),
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
        cau, cur_len, p_tai = MultiStrategyPredictor._detect_cau_type(results)
        return jsonify({
            'version': 'V9.3',
            'prediction': pred['prediction'],
            'confidence': pred['confidence'],
            'method': pred['method'],
            'cau': cau,
            'cau_label': pred.get('cau_label', '—'),
            'cau_streak_len': cur_len,
            'cau_tai_ratio': round(p_tai, 3),
            'top_signals': pred.get('top_signals', []),
            'signals_strength': pred.get('signals_strength', []),
            'all_signals': pred['signals'],
            'signals_count': len(pred['signals']),
            'flipped': pred.get('flipped', False),
            'reason': pred['reason'],
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
