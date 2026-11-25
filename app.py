import os
import math
import hashlib
from datetime import datetime
import requests
from flask import Flask, render_template_string, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# ⭐ API MỚI
API_URL_MD5 = "https://wtx.tele68.com/v1/tx/lite-sessions?cp=R&cl=R&pf=web&at=a91c94eac39ec0288bb7a5196c1ae409"

HISTORY_CACHE = {}
PREDICTION_CACHE = {}
MAX_CONFIDENCE = 90
RESET_ID = 0
WINDOW = 5

# =====================================================================
# HTML TEMPLATE (GIỮ NGUYÊN)
# =====================================================================
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>LE MINH HU </title>
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
    .header-title span { font-size: 11px; background: linear-gradient(135deg, #38bdf8, #ec4899); color: #fff; padding: 4px 12px; border-radius: 20px; font-weight: 800; letter-spacing: 1px; box-shadow: 0 0 15px rgba(236, 72, 153, 0.4); }
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
    <div class="header-title">LE MINH <span>HU</span></div>
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
            recBadge = `<span class="recovery-badge" title="Auto-flip do thua ${row.loss_streak || 0} lần">⚡ </span>`;
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
# CORE UTILS
# =====================================================================
def _opp(v):
    return "Xỉu" if v == "Tài" else "Tài"


def format_tx(results, length):
    if len(results) < length:
        return None
    return "-".join("T" if r == "Tài" else "X" for r in results[:length])


def _tx(seq):
    return "".join("T" if r == "Tài" else "X" for r in seq)


def _stable_jitter(results, span=2):
    """Jitter DETERMINISTIC dựa trên hash pattern — không random."""
    key = _tx(results[:25])
    h = hashlib.md5(key.encode('utf-8')).hexdigest()
    val = int(h[:8], 16)
    return (val % (span * 2 + 1)) - span


# =====================================================================
# 🔄 ALT 1-1 THUẬN NGHỊCH ANALYZER — CHỈ ĐẢO 1-1 THUẦN
# =====================================================================

class Alt11Analyzer:
    """
    PHÂN TÍCH ĐẢO 1-1 THUẦN (THUẬN NGHỊCH) TRÊN 5 PHIÊN:

    Pattern 5 phiên PHẢI khớp CHÍNH XÁC 1 trong 2 mẫu:
        "TXTXT" (T-X-T-X-T)  → phiên kế = X (Xỉu)
        "XTXTX" (X-T-X-T-X)  → phiên kế = T (Tài)

    Điều kiện xác minh:
        - 4/4 flip (không có 2 phiên liên tiếp giống nhau)
        - Bonus nếu mở rộng 6-7 phiên cũng đảo liên tục
    """

    PATTERNS = {
        "TXTXT": ("X", 0.92),   # phiên kế = Xỉu
        "XTXTX": ("T", 0.92),   # phiên kế = Tài
    }

    MIN_CONF = 0.85

    @staticmethod
    def analyze(seq):
        """
        Trả về: (pred_value|None, confidence 0..1, pattern_str)
        """
        if not seq or len(seq) < 5:
            return None, 0.0, "-"

        s = _tx(seq[:5])
        entry = Alt11Analyzer.PATTERNS.get(s)
        if entry is None:
            return None, 0.0, s

        pred_char, base_conf = entry

        # Xác minh 4/4 flip
        flips = sum(1 for i in range(4) if s[i] != s[i + 1])
        if flips != 4:
            return None, 0.0, s

        conf = base_conf

        # Bonus 6 phiên
        if len(seq) >= 6:
            s6 = _tx(seq[:6])
            flips6 = sum(1 for i in range(5) if s6[i] != s6[i + 1])
            if flips6 == 5:
                conf += 0.04
            elif flips6 <= 3:
                conf -= 0.08

        # Bonus 7 phiên
        if len(seq) >= 7:
            s7 = _tx(seq[:7])
            flips7 = sum(1 for i in range(6) if s7[i] != s7[i + 1])
            if flips7 == 6:
                conf += 0.02
            elif flips7 <= 4:
                conf -= 0.04

        conf = max(0.0, min(1.0, conf))
        pred_val = "Tài" if pred_char == "T" else "Xỉu"
        return pred_val, conf, s


# =====================================================================
# 🧠 HOUSE ENGINE v7 — DETERMINISTIC + ALT 1-1 THUẦN
# =====================================================================

class HouseEngine:
    DEFAULT_WEIGHTS = {
        "markov1": 0.08,
        "markov2": 0.10,
        "markov3": 0.05,
        "pattern_memory": 0.08,
        "multi_tf": 0.06,
        "streak_physics": 0.05,
        "bias_detector": 0.03,
        "alt_combo": 0.55,        # ⭐ TRỌNG TÂM: đảo 1-1 thuận nghịch
    }

    ALT_OVERRIDE_MARGIN = 0.04

    def __init__(self):
        self.weights = dict(self.DEFAULT_WEIGHTS)
        self.recent_perf = []
        self.alt_perf = []
        self.last_alt_conf = 0.0
        self.last_alt_pattern = "-"
        self.last_alt_active = False

    @staticmethod
    def _markov_1(seq):
        tt = tx = xt = xx = 0
        for i in range(len(seq) - 1):
            a, b = seq[i], seq[i + 1]
            if a == "Tài" and b == "Tài": tt += 1
            elif a == "Tài" and b == "Xỉu": tx += 1
            elif a == "Xỉu" and b == "Tài": xt += 1
            else: xx += 1
        last = seq[0]
        if last == "Tài":
            denom = tt + tx
            return tt / denom if denom else 0.5
        else:
            denom = xt + xx
            return xt / denom if denom else 0.5

    @staticmethod
    def _markov_n(seq, n):
        if len(seq) < n + 2:
            return None
        ctx = tuple(seq[:n])
        s = _tx(seq)
        pat = _tx(ctx)
        t_after = x_after = 0
        for i in range(len(s) - n):
            if s[i:i + n] == pat:
                if s[i + n] == "T": t_after += 1
                else: x_after += 1
        total = t_after + x_after
        if total == 0:
            return None
        return t_after / total

    @staticmethod
    def _pattern_memory(seq, max_len=4):
        s = _tx(seq)
        results = {}
        for L in range(2, max_len + 1):
            if len(s) < L + 1:
                continue
            tail = s[:L]
            t_after = x_after = 0
            for i in range(0, len(s) - L):
                if s[i:i + L] == tail:
                    if s[i + L] == "T": t_after += 1
                    else: x_after += 1
            total = t_after + x_after
            if total >= 2:
                results[L] = (t_after / total, total)
            else:
                results[L] = (None, total)
        return results

    @staticmethod
    def _multi_tf_bias(seq):
        weights = {3: 0.35, 5: 0.30, 8: 0.18, 12: 0.12, 20: 0.05}
        total_w = 0.0
        bias = 0.0
        for w_len, w in weights.items():
            if len(seq) >= w_len:
                p_tai = sum(1 for r in seq[:w_len] if r == "Tài") / w_len
                bias += (p_tai - 0.5) * w
                total_w += w
        if total_w == 0:
            return 0.0
        return bias / total_w

    @staticmethod
    def _streak_physics(seq):
        if not seq:
            return 0.0, "empty"
        streak_val = seq[0]
        streak_len = 1
        for r in seq[1:]:
            if r == streak_val: streak_len += 1
            else: break
        flips = sum(1 for i in range(min(len(seq) - 1, 5)) if seq[i] != seq[i + 1])

        bias = 0.0
        reason = ""
        if flips >= 4:
            bias = -0.20 if streak_val == "Tài" else 0.20
            reason = f"dao1-1({streak_len})"
        elif streak_len >= 3:
            decay = max(0.05, 0.25 - 0.04 * (streak_len - 3))
            bias = decay if streak_val == "Xỉu" else -decay
            reason = f"bet{streak_len}"
        else:
            reason = "normal"
        return bias, reason

    @staticmethod
    def _bias_detector(seq):
        if len(seq) < 15:
            return 0.0
        recent = seq[:30]
        p_tai = sum(1 for r in recent if r == "Tài") / len(recent)
        dev = p_tai - 0.5
        if abs(dev) < 0.10:
            return 0.0
        return -dev * 0.5

    @staticmethod
    def _volatility(seq):
        if len(seq) < 10:
            return 0.5
        recent = seq[:15]
        p = sum(1 for r in recent if r == "Tài") / len(recent)
        if p in (0.0, 1.0):
            return 0.0
        ent = -(p * math.log2(p) + (1 - p) * math.log2(1 - p))
        return ent / 1.0

    def reset_state(self):
        self.recent_perf = []
        self.alt_perf = []
        self.last_alt_conf = 0.0
        self.last_alt_pattern = "-"
        self.last_alt_active = False

    def update_performance(self, pred, actual):
        if pred in ("Tài", "Xỉu") and actual in ("Tài", "Xỉu"):
            win = 1 if pred == actual else 0
            self.recent_perf.append(win)
            if len(self.recent_perf) > 60:
                self.recent_perf.pop(0)
            if self.last_alt_active:
                self.alt_perf.append(win)
                if len(self.alt_perf) > 30:
                    self.alt_perf.pop(0)

    def _adaptive_weights(self):
        if len(self.recent_perf) < 15:
            return self.weights
        recent = self.recent_perf[-30:]
        acc = sum(recent) / len(recent)
        w = dict(self.weights)
        if acc < 0.45:
            w["bias_detector"] = min(0.20, w["bias_detector"] + 0.05)
            w["markov1"] = max(0.05, w["markov1"] - 0.02)
            w["pattern_memory"] = max(0.05, w["pattern_memory"] - 0.02)
        elif acc > 0.60:
            w["pattern_memory"] = min(0.18, w["pattern_memory"] + 0.03)
            w["markov2"] = min(0.20, w["markov2"] + 0.02)
            w["bias_detector"] = max(0.02, w["bias_detector"] - 0.02)

        if len(self.alt_perf) >= 8:
            alt_acc = sum(self.alt_perf) / len(self.alt_perf)
            if alt_acc < 0.40:
                w["alt_combo"] = max(0.30, w["alt_combo"] - 0.08)
            elif alt_acc > 0.65:
                w["alt_combo"] = min(0.62, w["alt_combo"] + 0.05)

        s = sum(w.values())
        return {k: v / s for k, v in w.items()}

    def predict(self, results):
        if not results:
            return "Tài", 50, "empty"

        if len(results) < 5:
            t = results.count("Tài")
            x = len(results) - t
            if t > x: return "Tài", 55, "short-majority"
            if x > t: return "Xỉu", 55, "short-majority"
            return results[0], 52, "follow-last"

        signals = {}
        supports = {}

        p1 = self._markov_1(results)
        signals["markov1"] = p1 - 0.5
        supports["markov1"] = 0.6

        for n in (2, 3):
            pn = self._markov_n(results, n)
            if pn is not None:
                signals[f"markov{n}"] = pn - 0.5
                supports[f"markov{n}"] = 0.7 if n == 2 else 0.5

        pm = self._pattern_memory(results, max_len=4)
        pm_bias = 0.0
        pm_support = 0.0
        pm_total = 0
        for L, (p, sup) in pm.items():
            if p is not None:
                w = {2: 0.5, 3: 0.3, 4: 0.2}.get(L, 0.1)
                pm_bias += (p - 0.5) * w
                pm_support += w
                pm_total += sup
        if pm_support > 0:
            pm_bias /= pm_support
        signals["pattern_memory"] = pm_bias
        supports["pattern_memory"] = min(1.0, pm_total / 10.0)

        signals["multi_tf"] = self._multi_tf_bias(results)
        supports["multi_tf"] = 0.8

        sp_bias, sp_reason = self._streak_physics(results)
        signals["streak_physics"] = sp_bias
        supports["streak_physics"] = 0.7

        signals["bias_detector"] = self._bias_detector(results)
        supports["bias_detector"] = 0.6

        # ⭐ ALT 1-1 THUẦN
        alt_pred, alt_conf, alt_pattern = Alt11Analyzer.analyze(results)
        self.last_alt_conf = alt_conf
        self.last_alt_pattern = alt_pattern
        self.last_alt_active = alt_pred is not None and alt_conf >= Alt11Analyzer.MIN_CONF

        if self.last_alt_active:
            alt_sign = 1.0 if alt_pred == "Tài" else -1.0
            signals["alt_combo"] = alt_sign * alt_conf
            supports["alt_combo"] = min(1.0, alt_conf + 0.15)
        else:
            signals["alt_combo"] = 0.0
            supports["alt_combo"] = 0.0

        w = self._adaptive_weights()
        total_score = 0.0
        total_weight = 0.0
        for name, bias in signals.items():
            if name in w and supports.get(name, 0) > 0:
                eff_w = w[name] * supports[name]
                total_score += bias * eff_w
                total_weight += eff_w

        if total_weight == 0:
            return results[0], 52, "fallback"

        score = total_score / total_weight

        # ⭐ OVERRIDE khi ALT 1-1 đủ mạnh
        override_active = False
        if self.last_alt_active and alt_pred:
            alt_sign = 1.0 if alt_pred == "Tài" else -1.0
            if alt_conf >= 0.92 and score * alt_sign < self.ALT_OVERRIDE_MARGIN:
                score = alt_sign * max(abs(score), self.ALT_OVERRIDE_MARGIN + 0.02)
                override_active = True

        pred = "Tài" if score >= 0 else "Xỉu"

        if abs(score) < 0.02:
            pred = results[0]
            label_extra = " edge"
        else:
            label_extra = ""

        vol = self._volatility(results)
        conf_raw = 50 + abs(score) * 40
        conf_raw *= (1 - 0.25 * vol)

        if override_active:
            conf_raw += 10
        elif self.last_alt_active and alt_pred:
            if pred == alt_pred:
                conf_raw += 5

        if self.last_alt_active and not override_active and alt_pred:
            if pred != alt_pred:
                conf_raw -= 5

        conf = int(round(conf_raw))
        conf = max(52, min(MAX_CONFIDENCE, conf))
        conf += _stable_jitter(results, span=2)
        conf = max(52, min(MAX_CONFIDENCE, conf))

        # Label hiển thị
        if self.last_alt_active:
            mode = f"Đảo 1-1 THUẦN 5P ({int(alt_conf*100)}%)"
        else:
            seq5 = results[:5]
            flips = sum(1 for i in range(4) if seq5[i] != seq5[i + 1])
            if flips == 4:
                mode = "Đảo 1-1 (4/5)"
            elif flips == 2:
                mode = "Đảo 2-2 (2/4)"
            elif flips <= 1:
                mode = f"Bệt {self._streak_len(results)}P"
            else:
                mode = "Hỗn tạp"

        if override_active:
            mode += " ⚡OVR"

        label = f"{mode} ({alt_pattern}) | {sp_reason}{label_extra}"

        return pred, conf, label

    @staticmethod
    def _streak_len(seq):
        v = seq[0]
        n = 1
        for r in seq[1:]:
            if r == v: n += 1
            else: break
        return n


ENGINE = HouseEngine()


def compute_prediction(results):
    return ENGINE.predict(results)


# =====================================================================
# ⭐ BACKTEST DETERMINISTIC
# =====================================================================

def run_deterministic_backtest(valid, reset_id):
    global PREDICTION_CACHE

    PREDICTION_CACHE = {k: v for k, v in PREDICTION_CACHE.items() if k[0] == reset_id}

    ENGINE.reset_state()

    result_map = {v['phien']: v['result'] for v in valid}
    phien_list = sorted(result_map.keys())

    history_rev = []
    for phien in phien_list:
        cache_key = (reset_id, phien)

        if cache_key in PREDICTION_CACHE:
            pred, conf, label = PREDICTION_CACHE[cache_key]
        else:
            if len(history_rev) >= 5:
                pred, conf, label = ENGINE.predict(history_rev)
            elif len(history_rev) > 0:
                t = history_rev.count("Tài")
                x = len(history_rev) - t
                if t > x: pred, conf = "Tài", 55
                elif x > t: pred, conf = "Xỉu", 55
                else: pred, conf = history_rev[0], 52
                label = "short-history"
            else:
                pred, conf, label = "-", 50, "init"

            PREDICTION_CACHE[cache_key] = (pred, conf, label)

        actual = result_map[phien]
        ENGINE.update_performance(pred, actual)
        history_rev.insert(0, actual)

    return PREDICTION_CACHE


# =====================================================================
# API & ROUTES
# =====================================================================
def fetch_sessions():
    try:
        headers = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'}
        r = requests.get(API_URL_MD5, timeout=8, headers=headers)
        if r.status_code == 200:
            data = r.json()
            if 'list' in data and data['list']:
                rows = []
                for item in data['list']:
                    res = "Tài" if item['resultTruyenThong'] == "TAI" else "Xỉu"
                    dices = item.get('dices', [0, 0, 0])
                    dice_str = f"{dices[0]} + {dices[1]} + {dices[2]}"
                    total = item.get('point', sum(dices))
                    rows.append({
                        'phien': int(item['id']),
                        'result': res,
                        'dice': dice_str,
                        'total': total
                    })
                return rows, True
    except Exception as e:
        print(f"[API ERROR]: {e}")
    return [], False


@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)


@app.route('/api/history')
def history():
    global RESET_ID
    try:
        raw_rows, success = fetch_sessions()
        if raw_rows:
            for row in raw_rows:
                HISTORY_CACHE[row['phien']] = row
            for k in [k for k in HISTORY_CACHE if k < RESET_ID]:
                del HISTORY_CACHE[k]

        valid = sorted(HISTORY_CACHE.values(), key=lambda x: x['phien'])
        if not valid:
            return jsonify({'rows': [], 'stats': {'total': 0, 'correct': 0}})

        cache = run_deterministic_backtest(valid, RESET_ID)

        history_rev = [v['result'] for v in valid][::-1]
        next_pred, next_conf, next_label = ENGINE.predict(history_rev)
        next_phien = valid[-1]['phien'] + 1

        out = [{
            'phien': next_phien,
            'is_next': True,
            'prediction': next_pred,
            'result': 'Chờ...',
            'dice': "? + ? + ?",
            'is_correct': None,
            'conf': next_conf,
            'recovery_flipped': False,
            'loss_streak': 0,
            'label': next_label
        }]

        correct_count = 0
        total_past = 0
        for v in reversed(valid[-150:]):
            phien = v['phien']
            cache_key = (RESET_ID, phien)
            if cache_key not in cache:
                continue
            pred, conf, _ = cache[cache_key]
            if pred not in ("Tài", "Xỉu"):
                continue
            is_correct = (pred == v['result'])
            total_past += 1
            if is_correct:
                correct_count += 1

            out.append({
                'phien': phien,
                'is_next': False,
                'prediction': pred,
                'result': v['result'],
                'dice': v['dice'],
                'is_correct': is_correct,
                'conf': conf,
                'recovery_flipped': False
            })

        return jsonify({'rows': out, 'stats': {'total': total_past, 'correct': correct_count}})
    except Exception as e:
        return jsonify({'error': str(e), 'rows': [], 'stats': {'total': 0, 'correct': 0}})


@app.route('/api/predict')
def predict():
    global RESET_ID
    try:
        raw_rows, _ = fetch_sessions()
        if raw_rows:
            for row in raw_rows:
                HISTORY_CACHE[row['phien']] = row
            for k in [k for k in HISTORY_CACHE if k < RESET_ID]:
                del HISTORY_CACHE[k]

        valid = sorted(HISTORY_CACHE.values(), key=lambda x: x['phien'])
        if not valid:
            return jsonify({'error': 'Chưa có dữ liệu'})

        run_deterministic_backtest(valid, RESET_ID)

        results = [v['result'] for v in valid][::-1]
        pred, conf, pattern_label = ENGINE.predict(results)
        ls_5p = format_tx(results, 5)

        return jsonify({
            'last_phien': valid[-1]['phien'],
            'next_phien': valid[-1]['phien'] + 1,
            'prediction': pred,
            'confidence': conf,
            'recovery_flipped': False,
            'loss_streak': 0,
            'ls_5p': ls_5p,
            'pattern_label': pattern_label,
            'alt_active': ENGINE.last_alt_active,
            'alt_conf': round(ENGINE.last_alt_conf, 3),
            'alt_pattern': ENGINE.last_alt_pattern,
            'deterministic': True,
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({'error': str(e)})


@app.route('/api/alternating')
def alternating_api():
    """API chuyên biệt: PHÂN TÍCH ĐẢO 1-1 THUẦN trên 5 phiên."""
    global RESET_ID
    try:
        raw_rows, _ = fetch_sessions()
        if raw_rows:
            for row in raw_rows:
                HISTORY_CACHE[row['phien']] = row
            for k in [k for k in HISTORY_CACHE if k < RESET_ID]:
                del HISTORY_CACHE[k]

        valid = sorted(HISTORY_CACHE.values(), key=lambda x: x['phien'])
        if not valid:
            return jsonify({'error': 'Chưa có dữ liệu'})

        results = [v['result'] for v in valid][::-1]

        alt_pred, alt_conf, alt_pattern = Alt11Analyzer.analyze(results)
        ls_5p = format_tx(results, 5) or "-"

        is_alternating = alt_pred is not None and alt_conf >= Alt11Analyzer.MIN_CONF

        if is_alternating:
            verdict = f"ĐẢO 1-1 THUẦN HOÀN HẢO ({alt_pattern})"
        else:
            verdict = "Không có cầu đảo 1-1 thuần trên 5 phiên"

        return jsonify({
            'last_phien': valid[-1]['phien'],
            'next_phien': valid[-1]['phien'] + 1,
            'ls_5p': ls_5p,
            'is_alternating': is_alternating,
            'prediction': alt_pred if alt_pred else "-",
            'confidence': round(alt_conf, 3),
            'pattern': alt_pattern,
            'verdict': verdict,
            'rule': "5 phiên khớp TXTXT/XTXTX (flip 4/4)",
            'deterministic': True,
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({'error': str(e)})


@app.route('/lc79')
@app.route('/lc79hu')          # ⭐ ROUTE MỚI
def lc79_api():
    global RESET_ID
    try:
        raw_rows, _ = fetch_sessions()
        if raw_rows:
            for row in raw_rows:
                HISTORY_CACHE[row['phien']] = row
            for k in [k for k in HISTORY_CACHE if k < RESET_ID]:
                del HISTORY_CACHE[k]

        valid = sorted(HISTORY_CACHE.values(), key=lambda x: x['phien'])
        if not valid:
            return jsonify({'id': '@cskhleminh', 'phien': '-', 'du_doan': '-', 'do_tin_cay': '50%'})

        run_deterministic_backtest(valid, RESET_ID)

        results = [v['result'] for v in valid][::-1]
        pred, conf, pattern_label = ENGINE.predict(results)
        ls_5p = format_tx(results, 5)

        return jsonify({
            'id': '@cskhleminh',
            'phien': str(valid[-1]['phien'] + 1),
            'ket_qua': f"{valid[-1]['result'].lower()} {valid[-1]['dice'].replace(' + ', '-')}",
            'du_doan': pred,
            'do_tin_cay': f"{conf}%",
            'ls_5p': ls_5p,
            'cau': pattern_label,
            'alt_active': ENGINE.last_alt_active,
            'alt_conf': round(ENGINE.last_alt_conf, 3),
            'alt_pattern': ENGINE.last_alt_pattern,
            'auto_flip': False,
            'chuoi_thua': 0,
            'deterministic': True
        })
    except Exception as e:
        return jsonify({'id': '@cskhleminh', 'phien': '-', 'du_doan': '-', 'do_tin_cay': '50%', 'error': str(e)})


@app.route('/api/reset', methods=['POST'])
def reset_api():
    global RESET_ID, HISTORY_CACHE, PREDICTION_CACHE
    try:
        raw_rows, _ = fetch_sessions()
        if raw_rows:
            valid = sorted(raw_rows, key=lambda x: x['phien'])
            if valid:
                RESET_ID = valid[-1]['phien']
                HISTORY_CACHE = {valid[-1]['phien']: valid[-1]}
                PREDICTION_CACHE = {}
                ENGINE.reset_state()
                return jsonify({'status': 'success', 'reset_to': RESET_ID})
        return jsonify({'status': 'error', 'message': 'Lỗi mạng'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, threaded=True)
