import os
import math
import time
import hashlib
import random
import traceback
from datetime import datetime
from collections import defaultdict, Counter
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
# CẤU HÌNH LC79 RANDOM-5 (ĐẢO/THUẬN/NGHỊCH)
# =====================================================================
WINDOW_SIZE = 5
FINGERPRINT_MIN_MATCH = 3
TRANSITION_MIN_SAMPLES = 8
ENTROPY_HIGH = 0.98
ENTROPY_LOW = 0.55

# Random-5 config
RANDOM_ITERATIONS = 800           # Số lần Monte Carlo
FIVE_SESSION_WEIGHT = 1.5         # Hệ số boost tín hiệu 5 phiên
PATTERN_MIN_OCCURRENCES = 3
BAYES_PRIOR_ALPHA = 1.0
CONFIDENCE_FLOOR = 50
CONFIDENCE_CEIL = 96

# Trọng số 3 mode 5 phiên
WEIGHT_DAO = 1.4                  # Đảo: T-X-T-X-T
WEIGHT_THUAN = 1.2                # Thuận: T-T-T-T-T hoặc X-X-X-X-X
WEIGHT_NGHICH = 1.3               # Nghịch: đảo chiều xu hướng


# =====================================================================
# UTILS
# =====================================================================
def _opp(v):
    return "Xỉu" if v == "Tài" else "Tài"


def _to_bits(results):
    return "".join("1" if r == "Tài" else "0" for r in results)


def _md5(s):
    return hashlib.md5(s.encode()).hexdigest()


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
# 🎯 CORE: PHÂN TÍCH 5 PHIÊN ĐẢO/THUẬN/NGHỊCH
# =====================================================================

def analyze_5session_mode(results):
    """
    Phân tích 5 phiên gần nhất và phân loại thành 3 mode:
    - ĐẢO (alternating): T-X-T-X-T hoặc X-T-X-T-X
    - THUẬN (streak/trend): T-T-T-T-T, X-X-X-X-X, hoặc trend rõ ràng
    - NGHỊCH (reverse): có dấu hiệu đảo chiều xu hướng
    
    Trả về dict: {mode, confidence, next_pred, detail}
    """
    if len(results) < 5:
        return None
    
    last5 = results[:5]           # mới nhất -> cũ nhất
    last5_bits = _to_bits(last5)
    
    # ---------- PHÂN TÍCH ĐẢO (ALTERNATING) ----------
    alternations = sum(1 for i in range(4) if last5[i] != last5[i + 1])
    dao_score = 0
    if alternations == 4:
        dao_score = 1.0           # Hoàn hảo: T-X-T-X-T
    elif alternations == 3:
        dao_score = 0.7           # Gần đảo
    elif alternations == 2:
        dao_score = 0.35
    
    # ---------- PHÂN TÍCH THUẬN (STREAK/TREND) ----------
    # Streak liên tiếp ở đầu
    streak = 1
    for r in last5[1:]:
        if r == last5[0]:
            streak += 1
        else:
            break
    thuan_score = 0
    if streak == 5:
        thuan_score = 1.0         # T-T-T-T-T
    elif streak == 4:
        thuan_score = 0.75
    elif streak == 3:
        thuan_score = 0.45
    
    # Trend rõ ràng: 4/5 giống nhau
    cnt = Counter(last5)
    top_val, top_cnt = cnt.most_common(1)[0]
    if top_cnt == 4:
        thuan_score = max(thuan_score, 0.65)
    elif top_cnt == 5:
        thuan_score = max(thuan_score, 1.0)
    
    # ---------- PHÂN TÍCH NGHỊCH (REVERSE) ----------
    # Nghịch = xu hướng đảo chiều: 3 phiên đầu cùng A, 2 phiên cuối cùng B
    # hoặc 4 phiên đầu cùng A, phiên cuối B (đang đảo)
    nghich_score = 0
    if len(last5) == 5:
        # Pattern AAABB (3-2): đảo từ B về A? Hoặc đang đảo từ A sang B?
        if last5[0] == last5[1] == last5[2] and last5[3] == last5[4] and last5[0] != last5[3]:
            nghich_score = 0.85   # Pattern 3-2 rõ ràng
        # Pattern AABBB (2-3)
        elif last5[0] == last5[1] and last5[2] == last5[3] == last5[4] and last5[0] != last5[2]:
            nghich_score = 0.9    # Pattern 2-3 rõ ràng (đảo mạnh)
        # Pattern ABBBB (1-4): đang đảo về B
        elif last5[0] != last5[1] and last5[1] == last5[2] == last5[3] == last5[4]:
            nghich_score = 0.7
        # Pattern AABAB hoặc ABABB (zigzag phức tạp)
        elif alternations == 2 and top_cnt == 3:
            nghich_score = 0.5
    
    # ---------- XÁC ĐỊNH MODE CHIẾN THẮNG ----------
    scores = {
        'dao': dao_score,
        'thuan': thuan_score,
        'nghich': nghich_score,
    }
    mode = max(scores, key=scores.get)
    mode_score = scores[mode]
    
    if mode_score < 0.3:
        return None                # Không đủ tín hiệu
    
    # ---------- XÁC ĐỊNH DỰ ĐOÁN TIẾP THEO ----------
    last = last5[0]
    
    if mode == 'dao':
        # Đảo: phiên tiếp = ngược phiên cuối
        pred = _opp(last)
        weight = int(10 * WEIGHT_DAO * (1 + dao_score))
        reason = f"ĐẢO ({alternations}/4 đảo)"
    
    elif mode == 'thuan':
        # Thuận: phiên tiếp = tiếp tục xu hướng
        if streak >= 4:
            # Streak dài → có thể sắp đảo (cảnh báo)
            if streak >= 5:
                # Quá dài, 60% tiếp tục, 40% đảo
                pred = last if random.random() < 0.6 else _opp(last)
                reason = f"THUẬN dài ({streak}) - bias nhẹ"
            else:
                pred = last
                reason = f"THUẬN ({streak} streak)"
        else:
            pred = top_val
            reason = f"THUẬN trend ({top_cnt}/5)"
        weight = int(10 * WEIGHT_THUAN * (1 + thuan_score))
    
    else:  # nghich
        # Nghịch: phiên tiếp = đảo chiều xu hướng đang chạy
        # Nếu 2 phiên cuối cùng B → dự đoán A (đảo tiếp)
        if last5[0] == last5[1]:
            # Đang streak ngắn → chuẩn bị đảo
            pred = _opp(last5[0])
        else:
            # Đang zigzag → về xu hướng trước
            pred = _opp(last)
        weight = int(10 * WEIGHT_NGHICH * (1 + nghich_score))
        reason = f"NGHỊCH (reverse pattern)"
    
    # ---------- CONFIDENCE ----------
    conf = 55 + int(mode_score * 35)  # 55-90
    
    return {
        'mode': mode,
        'pred': pred,
        'weight': max(8, min(28, weight)),
        'confidence': min(92, conf),
        'mode_score': round(mode_score, 3),
        'reason': reason,
        'detail': {
            'last5': last5,
            'alternations': alternations,
            'streak': streak,
            'top_count': top_cnt,
            'scores': {k: round(v, 2) for k, v in scores.items()},
        }
    }


# =====================================================================
# 🎲 MONTE CARLO RANDOM ANALYSIS (CHỐNG OVERFIT)
# =====================================================================

def monte_carlo_random_analysis(results, iterations=RANDOM_ITERATIONS):
    """
    Mô phỏng Monte Carlo: tạo nhiều chuỗi random từ phân phối lịch sử,
    kiểm tra pattern 5 phiên nào dẫn đến kết quả gì.
    Mục đích: đánh giá xác suất thực của phiên tiếp theo dựa trên
    pattern 5 phiên hiện tại.
    """
    if len(results) < 20:
        return None
    
    last5 = tuple(results[:5])
    bits = _to_bits(results)
    
    # Đếm tần suất thực từ lịch sử
    tai_after = xiu_after = 0
    pattern_matches = 0
    for i in range(1, len(bits) - 5):
        if bits[i:i + 5] == bits[:5]:
            pattern_matches += 1
            if bits[i - 1] == "1":
                tai_after += 1
            else:
                xiu_after += 1
    
    if pattern_matches < 3:
        return None
    
    # Monte Carlo: sample ngẫu nhiên từ pattern matches
    true_p_tai = tai_after / pattern_matches
    
    random.seed(RANDOM_SEED_BASE + hash(last5) % 100000)
    sampled_tai = 0
    for _ in range(iterations):
        # Bootstrap sampling: chọn ngẫu nhiên 1 phiên trong pattern matches
        if random.random() < true_p_tai:
            sampled_tai += 1
    
    mc_p_tai = sampled_tai / iterations
    
    # Kết hợp cả hai
    p_tai = (true_p_tai * 0.6 + mc_p_tai * 0.4)
    ratio = max(p_tai, 1 - p_tai)
    
    if ratio < 0.62:
        return None
    
    pred = "Tài" if p_tai >= 0.5 else "Xỉu"
    
    # Confidence từ Monte Carlo stability
    stability = 1 - abs(true_p_tai - mc_p_tai)  # càng gần càng ổn định
    weight = 9 + int((ratio - 0.5) * 40) + int(stability * 5)
    weight = max(9, min(26, weight))
    
    return {
        'pred': pred,
        'weight': weight,
        'confidence': 55 + int(ratio * 30),
        'detail': {
            'type': 'monte_carlo',
            'matches': pattern_matches,
            'p_tai_hist': round(true_p_tai, 3),
            'p_tai_mc': round(mc_p_tai, 3),
            'stability': round(stability, 3),
            'iterations': iterations,
        }
    }


# =====================================================================
# 🧬 RANDOM PATTERN SEQUENCE ANALYSIS
# =====================================================================

def random_sequence_analysis(results):
    """
    Phân tích 5 phiên dưới góc nhìn "random sequence":
    - Tìm sequence tương tự trong lịch sử
    - Xem kết quả sau sequence đó phân bố thế nào
    - Áp dụng entropy + z-score để chống noise
    """
    if len(results) < 30:
        return None
    
    bits = _to_bits(results)
    seq5 = bits[:5]
    
    # Tìm các sequence giống (không cần MD5, matching trực tiếp)
    matches = []
    for i in range(1, len(bits) - 5):
        if bits[i:i + 5] == seq5:
            matches.append(i - 1)  # index của phiên kế tiếp
    
    if len(matches) < 4:
        return None
    
    # Đếm kết quả
    tai_count = sum(1 for idx in matches if bits[idx] == "1")
    xiu_count = len(matches) - tai_count
    total = len(matches)
    
    # Laplace smoothing
    p_tai = (tai_count + BAYES_PRIOR_ALPHA) / (total + 2 * BAYES_PRIOR_ALPHA)
    ratio = max(p_tai, 1 - p_tai)
    
    # Z-score: kiểm tra pattern này có ý nghĩa thống kê không
    expected = 0.5
    std_dev = math.sqrt(expected * (1 - expected) / total) if total > 0 else 1
    z_score = abs(p_tai - expected) / std_dev if std_dev > 0 else 0
    
    if z_score < 1.0 and ratio < 0.68:
        return None                # Không đủ ý nghĩa
    
    pred = "Tài" if p_tai >= 0.5 else "Xỉu"
    
    # Weight tăng theo z-score
    weight = 10 + int(min(10, z_score * 3)) + int((ratio - 0.5) * 20)
    weight = max(10, min(26, weight))
    
    return {
        'pred': pred,
        'weight': weight,
        'confidence': 55 + int(ratio * 30),
        'detail': {
            'type': 'random_sequence',
            'seq': seq5,
            'matches': total,
            'tai': tai_count,
            'xiu': xiu_count,
            'ratio': round(ratio, 3),
            'z_score': round(z_score, 2),
        }
    }


# =====================================================================
# 🔮 RANDOM WALK PREDICTION (BƯỚC NGẪU NHIÊN CÓ TRỌNG SỐ)
# =====================================================================

def random_walk_prediction(results):
    """
    Random walk: giả sử kết quả tiếp theo phụ thuộc vào "bước đi" ngẫu nhiên
    có bias. Tính bias từ phân phối 5 phiên gần + lịch sử xa.
    """
    if len(results) < 20:
        return None
    
    # Trọng số recency cho 5 phiên gần
    weights_5 = [1.0, 0.85, 0.7, 0.55, 0.4]
    weighted_5_tai = 0.0
    for i, r in enumerate(results[:5]):
        if r == "Tài":
            weighted_5_tai += weights_5[i]
    
    total_w_5 = sum(weights_5)
    p_5 = weighted_5_tai / total_w_5
    
    # Trọng số 20 phiên
    weights_20 = [math.exp(-i / 10.0) for i in range(min(20, len(results)))]
    weighted_20_tai = 0.0
    for i, r in enumerate(results[:len(weights_20)]):
        if r == "Tài":
            weighted_20_tai += weights_20[i]
    total_w_20 = sum(weights_20)
    p_20 = weighted_20_tai / total_w_20
    
    # Kết hợp: 60% ngắn hạn + 40% trung hạn
    p_tai = p_5 * 0.6 + p_20 * 0.4
    ratio = max(p_tai, 1 - p_tai)
    
    if ratio < 0.6:
        return None
    
    pred = "Tài" if p_tai >= 0.5 else "Xỉu"
    
    # Drift detection: nếu p_5 và p_20 lệch nhau nhiều → tín hiệu thay đổi
    drift = abs(p_5 - p_20)
    if drift > 0.3:
        # Có drift → có thể đang chuyển pha, giảm weight
        weight = 8 + int(ratio * 10)
    else:
        weight = 10 + int((ratio - 0.5) * 35)
    
    weight = max(8, min(24, weight))
    
    return {
        'pred': pred,
        'weight': weight,
        'confidence': 55 + int(ratio * 25),
        'detail': {
            'type': 'random_walk',
            'p_5': round(p_5, 3),
            'p_20': round(p_20, 3),
            'drift': round(drift, 3),
        }
    }


# =====================================================================
# 🎯 5-SESSION DEEP PATTERN MATCHER
# =====================================================================

def five_session_deep_pattern(results):
    """
    Phân tích 5 phiên với ma trận chuyển đổi đặc biệt:
    Xét 5 phiên như vector đặc trưng, tìm các vector tương tự trong lịch sử.
    """
    if len(results) < 40:
        return None
    
    last5 = results[:5]
    bits = _to_bits(results)
    
    # Tìm mẫu gần giống: ít nhất 4/5 phiên khớp
    partial_matches = []
    for i in range(1, len(bits) - 5):
        window = bits[i:i + 5]
        match_count = sum(1 for j in range(5) if window[j] == bits[j])
        if match_count >= 4:
            partial_matches.append((match_count, i - 1))
    
    if len(partial_matches) < 5:
        return None
    
    # Weighted voting by match count
    tai_w = xiu_w = 0.0
    for match_count, idx in partial_matches:
        w = match_count ** 2  # 4→16, 5→25
        if bits[idx] == "1":
            tai_w += w
        else:
            xiu_w += w
    
    total = tai_w + xiu_w
    if total == 0:
        return None
    
    p_tai = tai_w / total
    ratio = max(p_tai, 1 - p_tai)
    
    if ratio < 0.62:
        return None
    
    pred = "Tài" if p_tai >= 0.5 else "Xỉu"
    weight = 11 + int((ratio - 0.5) * 35) + min(6, len(partial_matches) // 2)
    weight = max(11, min(27, weight))
    
    return {
        'pred': pred,
        'weight': weight,
        'confidence': 55 + int(ratio * 30),
        'detail': {
            'type': 'five_session_deep',
            'partial_matches': len(partial_matches),
            'p_tai': round(p_tai, 3),
            'ratio': round(ratio, 3),
        }
    }


# =====================================================================
# 🧠 TỔNG HỢP LC79 RANDOM-5 PRO
# =====================================================================

def lc79_random5_predict(results, dice_sums=None):
    """
    Tổng hợp tất cả phân tích, đặc biệt ưu tiên 5 phiên gần nhất.
    """
    n = len(results)
    if n == 0:
        return "Tài", 50, {"mode": "empty"}
    
    # Warmup
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
    if n == 4:
        # Phân tích đơn giản 4 phiên
        alt = sum(1 for i in range(3) if results[i] != results[i + 1])
        if alt == 3:
            return _opp(results[0]), 62, {"mode": "warmup_4_alt"}
        streak = 1
        for r in results[1:]:
            if r == results[0]:
                streak += 1
            else:
                break
        if streak >= 3:
            return results[0], 60, {"mode": "warmup_4_streak"}
        return _opp(results[0]), 55, {"mode": "warmup_4_alt"}
    
    # =====================================================
    # TỪ 5 PHIÊN TRỞ LÊN → PHÂN TÍCH ĐẦY ĐỦ
    # =====================================================
    layers = []
    layer_names = []
    
    # 🎯 TẦNG 1 (ƯU TIÊN CAO): 5-phiên Đảo/Thuận/Nghịch
    r = analyze_5session_mode(results)
    if r:
        layers.append((r['pred'], r['weight'], r['detail']))
        layer_names.append(f"5S_{r['mode'].upper()}")
    
    # 🎯 TẦNG 2: Monte Carlo Random
    r = monte_carlo_random_analysis(results)
    if r:
        layers.append((r['pred'], r['weight'], r['detail']))
        layer_names.append("MonteCarlo")
    
    # 🎯 TẦNG 3: Random Sequence
    r = random_sequence_analysis(results)
    if r:
        layers.append((r['pred'], r['weight'], r['detail']))
        layer_names.append("RandomSeq")
    
    # 🎯 TẦNG 4: Random Walk
    r = random_walk_prediction(results)
    if r:
        layers.append((r['pred'], r['weight'], r['detail']))
        layer_names.append("RandomWalk")
    
    # 🎯 TẦNG 5: 5-Session Deep Pattern
    r = five_session_deep_pattern(results)
    if r:
        layers.append((r['pred'], r['weight'], r['detail']))
        layer_names.append("DeepPattern")
    
    # 🎯 TẦNG 6: MD5 Fingerprint (backup)
    r = lc79_fingerprint(results)
    if r:
        layers.append((r[0], r[1], r[2]))
        layer_names.append("FingerprintMD5")
    
    # 🎯 TẦNG 7: Momentum + Entropy
    r = lc79_momentum_entropy(results)
    if r:
        layers.append((r[0], r[1], r[2]))
        layer_names.append("Momentum")
    
    # 🎯 TẦNG 8: Dice analysis
    if dice_sums:
        r = lc79_dice_analysis(dice_sums)
        if r:
            layers.append((r[0], r[1], r[2]))
            layer_names.append("DiceBias")
    
    if not layers:
        return _opp(results[0]), 54, {"mode": "fallback_alt"}
    
    # =====================================================
    # WEIGHTED VOTING VỚI BOOST 5-PHIÊN LAYERS
    # =====================================================
    st = sx = 0.0
    for i, (pred, w, _) in enumerate(layers):
        # Boost các layer 5-phiên (index 0-4)
        boost = FIVE_SESSION_WEIGHT if i < 5 else 1.0
        effective_w = w * boost
        if pred == "Tài":
            st += effective_w
        else:
            sx += effective_w
    
    total_w = st + sx
    if total_w == 0:
        return _opp(results[0]), 54, {"mode": "no_signal"}
    
    if st > sx:
        pred = "Tài"
        ratio = st / total_w
    elif sx > st:
        pred = "Xỉu"
        ratio = sx / total_w
    else:
        return _opp(results[0]), 55, {"mode": "tie"}
    
    # =====================================================
    # CONFIDENCE CALIBRATION
    # =====================================================
    agree_layers = sum(1 for p, _, _ in layers if p == pred)
    n_layers = len(layers)
    
    base_conf = 52
    base_conf += int((ratio - 0.5) * 56)
    
    if n_layers >= 2:
        agreement_pct = agree_layers / n_layers
        base_conf += int(agreement_pct * 14)
        if agree_layers == n_layers and n_layers >= 4:
            base_conf += 5
    base_conf += min(4, max(0, n_layers - 3))
    
    if n_layers == 1:
        base_conf -= 8
    
    # Boost cho 5-session mode rõ ràng
    if layers and layer_names[0].startswith("5S_"):
        first_detail = layers[0][2]
        if isinstance(first_detail, dict):
            mode_score = first_detail.get('mode_score', 0)
            if isinstance(mode_score, (int, float)) and mode_score >= 0.8:
                base_conf += 4
    
    conf = max(CONFIDENCE_FLOOR, min(CONFIDENCE_CEIL, base_conf))
    
    meta = {
        "mode": "lc79_random5_pro",
        "n_layers": n_layers,
        "agree": agree_layers,
        "ratio": round(ratio, 3),
        "five_session_boost": FIVE_SESSION_WEIGHT,
        "layers": [
            {"name": name, "pred": p, "w": w, "info": info}
            for name, (p, w, info) in zip(layer_names, layers)
        ],
    }
    return pred, conf, meta


# =====================================================================
# CÁC TẦNG CŨ (GIỮ LÀM BACKUP)
# =====================================================================

def lc79_fingerprint(results):
    if len(results) < 10:
        return None
    bits = _to_bits(results)
    best = None
    for ws in (6, 5, 4, 3):
        if len(bits) < ws + 5:
            continue
        cur_fp = bits[:ws]
        tai_next = xiu_next = 0
        for i in range(1, len(bits) - ws):
            if bits[i:i + ws] == cur_fp:
                if bits[i - 1] == "1":
                    tai_next += 1
                else:
                    xiu_next += 1
        total = tai_next + xiu_next
        if total < FINGERPRINT_MIN_MATCH:
            continue
        p_tai = (tai_next + BAYES_PRIOR_ALPHA) / (total + 2 * BAYES_PRIOR_ALPHA)
        ratio = max(p_tai, 1 - p_tai)
        if ratio < 0.62:
            continue
        score = ratio * math.log2(total + 1)
        if best is None or score > best['score']:
            pred = "Tài" if p_tai >= 0.5 else "Xỉu"
            best = {'pred': pred, 'ratio': ratio, 'samples': total,
                    'window': ws, 'score': score}
    if not best:
        return None
    sample_bonus = min(7, int(math.log2(best['samples'] + 1) * 2.5))
    weight = 11 + sample_bonus + int((best['ratio'] - 0.5) * 30)
    weight = max(11, min(24, weight))
    return (best['pred'], weight, {
        "fp": _md5(bits[:best['window']])[:8],
        "samples": best['samples'],
        "ratio": round(best['ratio'], 3),
        "window": best['window'],
    })


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
    
    # Alt pattern
    if len(results) >= 6:
        alt = all(results[i] != results[i + 1] for i in range(6))
        if alt:
            return (_opp(results[0]), 13, {"type": "alt_1_1", "entropy": round(e, 3)})
    
    if MOMENTUM_MIN_STREAK <= streak <= 7:
        ratio = results[:10].count(results[0]) / 10
        if ratio >= 0.65:
            w = 13 if e < 0.85 else 10
            return (results[0], w, {"type": "momentum_hold", "streak": streak})
        if ratio <= 0.35:
            w = 13 if e < 0.85 else 10
            return (_opp(results[0]), w, {"type": "momentum_break", "streak": streak})
    
    if e >= ENTROPY_HIGH:
        return (_opp(results[0]), 9, {"type": "entropy_reverse"})
    
    if e <= ENTROPY_LOW and len(results) >= 20:
        w = results[:20]
        tc = w.count("Tài")
        if tc >= 15:
            return ("Xỉu", 12, {"type": "pressure_tai", "tai_count": tc})
        if tc <= 5:
            return ("Tài", 12, {"type": "pressure_xiu", "tai_count": tc})
    return None


def _parse_dice_history(valid_rows):
    sums = []
    for row in valid_rows:
        try:
            dice = row.get('dice', '')
            if '+' in dice:
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
    })


# =====================================================================
# ADAPTIVE RECOVERY
# =====================================================================
def apply_recovery(base_pred, conf, loss_streak, results=None):
    if loss_streak < 2:
        return base_pred, conf, False, None
    if results and len(results) >= 15:
        e = _entropy(results[:20] if len(results) >= 20 else results)
        if e >= 0.95 and loss_streak < 4:
            return base_pred, max(50, conf - 6), False, "high_entropy_hold"
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
            rows.append({'phien': int(phien), 'result': res,
                         'dice': dice_str, 'total': total})
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
    pred, conf, _ = lc79_random5_predict(hist)
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
# HTML TEMPLATE
# =====================================================================
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>LEMINH · LC79 RANDOM-5</title>
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
        --dao-color: #a78bfa; --dao-bg: rgba(167, 139, 250, 0.15);
        --thuan-color: #22d3ee; --thuan-bg: rgba(34, 211, 238, 0.15);
        --nghich-color: #f59e0b; --nghich-bg: rgba(245, 158, 11, 0.15);
    }
    * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Plus Jakarta Sans', sans-serif; }
    body { background: linear-gradient(135deg, #0f172a 0%, #1e1b4b 100%); color: var(--text-main); min-height: 100vh; padding-bottom: 40px; }
    .header { background: rgba(30, 41, 59, 0.85); backdrop-filter: blur(12px); border-bottom: 1px solid var(--border-color); padding: 16px 24px; display: flex; justify-content: space-between; align-items: center; position: sticky; top: 0; z-index: 100; }
    .header-title { font-size: 22px; font-weight: 800; color: #38bdf8; display: flex; align-items: center; gap: 10px; text-shadow: 0 0 20px rgba(56, 189, 248, 0.4); }
    .header-title small { font-size: 10px; color: var(--text-muted); font-weight: 600; letter-spacing: 1px; }
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
    .mode-card { max-width: 800px; margin: 0 auto 16px; padding: 0 16px; }
    .mode-inner { background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 14px; padding: 16px 20px; box-shadow: 0 4px 20px rgba(0,0,0,0.2); }
    .mode-title { font-size: 11px; color: var(--text-muted); text-transform: uppercase; font-weight: 700; margin-bottom: 12px; letter-spacing: 0.5px; }
    .mode-row { display: flex; gap: 10px; flex-wrap: wrap; }
    .mode-chip { flex: 1; min-width: 120px; padding: 12px; border-radius: 10px; text-align: center; border: 1px solid var(--border-color); transition: 0.3s; }
    .mode-chip .mode-name { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 4px; }
    .mode-chip .mode-score { font-size: 20px; font-weight: 800; }
    .mode-chip.dao { background: var(--dao-bg); border-color: rgba(167,139,250,0.3); }
    .mode-chip.dao .mode-name { color: var(--dao-color); }
    .mode-chip.thuan { background: var(--thuan-bg); border-color: rgba(34,211,238,0.3); }
    .mode-chip.thuan .mode-name { color: var(--thuan-color); }
    .mode-chip.nghich { background: var(--nghich-bg); border-color: rgba(245,158,11,0.3); }
    .mode-chip.nghich .mode-name { color: var(--nghich-color); }
    .mode-chip.active { transform: scale(1.05); box-shadow: 0 0 20px rgba(255,255,255,0.15); }
    .table-wrapper { max-width: 800px; margin: 0 auto; padding: 0 16px; }
    .table-container { background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 16px; box-shadow: 0 10px 30px rgba(0,0,0,0.3); max-height: 65vh; overflow-y: auto; }
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
    .conf-badge.ultra { color: #fbbf24; background: rgba(251, 191, 36, 0.15); box-shadow: 0 0 10px rgba(251, 191, 36, 0.3); }
    .loading { text-align: center; padding: 40px; color: var(--tai-color); font-weight: 600; }
    .layers-info { max-width: 800px; margin: 16px auto; padding: 0 16px; }
    .layers-card { background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 14px; padding: 14px 18px; }
    .layers-title { font-size: 11px; color: var(--text-muted); text-transform: uppercase; font-weight: 700; margin-bottom: 10px; letter-spacing: 0.5px; }
    .layer-chips { display: flex; flex-wrap: wrap; gap: 8px; }
    .layer-chip { font-size: 11px; font-weight: 700; padding: 4px 10px; border-radius: 8px; background: rgba(255,255,255,0.05); color: var(--text-muted); border: 1px solid var(--border-color); }
    .layer-chip.tai { background: var(--tai-bg); color: var(--tai-color); border-color: rgba(56,189,248,0.3); }
    .layer-chip.xiu { background: var(--xiu-bg); color: var(--xiu-color); border-color: rgba(244,114,182,0.3); }
    @media (max-width: 500px) {
        .data-table th, .data-table td { padding: 10px 6px; font-size: 12px; }
        .badge { padding: 4px 8px; font-size: 11px; min-width: 50px; }
        .header-title { font-size: 18px; }
        .time-update { display: none; }
        .stats-bar { grid-template-columns: repeat(2, 1fr); }
        .stat-value { font-size: 18px; }
        .mode-chip { min-width: 90px; padding: 10px 8px; }
        .mode-chip .mode-score { font-size: 16px; }
    }
</style>
</head>
<body>

<div class="header">
    <div class="header-title">LEMINH <small>LC79 RANDOM-5</small></div>
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

<div class="mode-card" id="mode-card" style="display:none;">
    <div class="mode-inner">
        <div class="mode-title"> Phân tích LC79</div>
        <div class="mode-row" id="mode-row"></div>
    </div>
</div>

<div class="layers-info" id="layers-info" style="display:none;">
    <div class="layers-card">
        <div class="layers-title">XU HUONG API</div>
        <div class="layer-chips" id="layer-chips"></div>
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
        renderLayers(data.meta || {});
        renderMode(data.meta || {});
    } catch (e) { console.error("Lỗi tải dữ liệu:", e); }
}

function renderMode(meta) {
    const card = document.getElementById('mode-card');
    const row = document.getElementById('mode-row');
    if (!meta || !meta.layers) { card.style.display = 'none'; return; }
    
    // Tìm layer 5S_
    const fiveS = meta.layers.find(l => l.name && l.name.startsWith('5S_'));
    if (!fiveS || !fiveS.info || !fiveS.info.scores) { card.style.display = 'none'; return; }
    
    card.style.display = 'block';
    const scores = fiveS.info.scores;
    const active = fiveS.info.mode || '';
    
    const names = { dao: 'ĐẢO', thuan: 'THUẬN', nghich: 'NGHỊCH' };
    const icons = { dao: '⇄', thuan: '→', nghich: '⟲' };
    
    let html = '';
    ['dao', 'thuan', 'nghich'].forEach(k => {
        const val = scores[k] || 0;
        const pct = Math.round(val * 100);
        const cls = active === k ? 'active' : '';
        html += `<div class="mode-chip ${k} ${cls}">
            <div class="mode-name">${icons[k]} ${names[k]}</div>
            <div class="mode-score">${pct}%</div>
        </div>`;
    });
    row.innerHTML = html;
}

function renderLayers(meta) {
    const info = document.getElementById('layers-info');
    const chips = document.getElementById('layer-chips');
    if (!meta || !meta.layers || !meta.layers.length) {
        info.style.display = 'none';
        return;
    }
    info.style.display = 'block';
    let html = '';
    meta.layers.forEach(l => {
        const cls = l.pred === 'Tài' ? 'tai' : 'xiu';
        const shortName = l.name.replace(/_/g, ' ').toUpperCase();
        html += `<span class="layer-chip ${cls}">${shortName} → ${l.pred} (w${l.w})</span>`;
    });
    chips.innerHTML = html;
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
            recBadge = `<span class="recovery-badge" title="Auto-flip">⚡</span>`;
        }
        let confHtml = "";
        if (row.conf && row.prediction !== "-") {
            let cls = 'conf-badge';
            if (row.conf >= 80) cls = 'conf-badge ultra';
            else if (row.conf >= 70) cls = 'conf-badge high';
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
        title: 'Xác nhận Reset?', text: "Toàn bộ lịch sử hiển thị sẽ bị xoá!",
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
                    Swal.fire({ title: 'Thành công!', text: 'Đã reset.', icon: 'success', background: '#1e293b', color: '#f8fafc', confirmButtonColor: '#0ea5e9' });
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
        dice_sums_next = _parse_dice_history(valid[::-1])

        base_pred, base_conf, meta = lc79_random5_predict(history_for_next, dice_sums_next)
        next_pred, next_conf, flipped, reason = apply_recovery(
            base_pred, base_conf, loss_streak, history_for_next
        )

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
            'algorithm': 'LC79_RANDOM5',
            'meta': meta,
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
        dice_sums = _parse_dice_history(valid[::-1])

        loss_streak = compute_loss_streak(valid)
        base_pred, base_conf, meta = lc79_random5_predict(results, dice_sums)
        pred, conf, flipped, reason = apply_recovery(
            base_pred, base_conf, loss_streak, results
        )

        # Lấy phân tích mode 5 phiên
        five_analysis = analyze_5session_mode(results)

        return jsonify({
            'algorithm': 'LC79_RANDOM5',
            'last_phien': valid[-1]['phien'],
            'next_phien': valid[-1]['phien'] + 1,
            'last_result': valid[-1]['result'],
            'last_dice': valid[-1]['dice'],
            'last_5': results[:5],
            'prediction': pred,
            'confidence': conf,
            'base_prediction': base_pred,
            'base_confidence': base_conf,
            'five_session_mode': five_analysis,
            'recovery_flipped': flipped,
            'recovery_reason': reason,
            'loss_streak': loss_streak,
            'total_sessions': len(valid),
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
        base_pred, base_conf, meta = lc79_random5_predict(results, dice_sums)
        pred, conf, flipped, reason = apply_recovery(
            base_pred, base_conf, loss_streak, results
        )

        conf_str = f"{conf:.1f}".replace(".", ",") + "%"

        last = valid[-1]
        dice_parts = last['dice'].split(" + ")
        dice_short = "-".join(dice_parts) if len(dice_parts) == 3 else last['dice']
        ket_qua_str = f"{last['result'].lower()} {dice_short}"

        groups = get_groups(results)
        lich_su = "-" + "-".join(str(g) for g in groups[:4])

        next_phien = last['phien'] + 1
        five_analysis = analyze_5session_mode(results)

        mode_str = "?"
        if five_analysis:
            mode_str = five_analysis.get('mode', '?').upper()

        return jsonify({
            'id': '@cskhleminh',
            'algorithm': 'LC79_RANDOM5',
            'phien': str(next_phien),
            'ket_qua': ket_qua_str,
            'du_doan': pred,
            'do_tin_cay': conf_str,
            'lich_su': lich_su,
            'mode_5phien': mode_str,
            'last_5': results[:5],
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
                    'algorithm': 'LC79_RANDOM5',
                    'message': 'Đã reset'
                })
        return jsonify({'status': 'error', 'message': 'Lỗi mạng'}), 400
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
