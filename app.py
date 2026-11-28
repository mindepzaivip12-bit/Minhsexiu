import os
import math
import hashlib
import traceback
from datetime import datetime
from collections import defaultdict, Counter
import requests
from flask import Flask, render_template_string, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

API_URL_MD5 = "https://wtxmd52.tele68.com/v1/txmd5/lite-sessions?cp=R&cl=R&pf=web&at=d35795e07a393f91815436933a4852df"

HISTORY_CACHE = {}
MAX_CONFIDENCE = 93
MIN_CONFIDENCE = 50
RESET_ID = 0

# ═══════════════════════════════════════════════════════════════════
# ⚡ AUTO MODE STATE
# ═══════════════════════════════════════════════════════════════════
AUTO_MODE_STATE = {
    'current': 'BALANCED',
    'previous': 'BALANCED',
    'switch_count': 0
}

MODE_LABELS = {
    'ALT_AGGRESSIVE': '⚡ 5P ALT',
    'ALT_NORMAL':     '🔄 ALT',
    'STREAK_BREAKER': '🔥 BREAK',
    'DEFENSIVE':      '🛡️ SAFE',
    'BALANCED':       '⚖️ BAL'
}

# ═══════════════════════════════════════════════════════════════════
# HTML TEMPLATE
# ═══════════════════════════════════════════════════════════════════
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>LE MINH MD5 </title>
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
    .header-title { font-size: 22px; font-weight: 800; color: #38bdf8; display: flex; align-items: center; gap: 10px; text-shadow: 0 0 20px rgba(56, 189, 248, 0.4); flex-wrap: wrap; }
    .header-title span.badge-md5 { font-size: 11px; background: linear-gradient(135deg, #38bdf8, #ec4899); color: #fff; padding: 4px 12px; border-radius: 20px; font-weight: 800; letter-spacing: 1px; box-shadow: 0 0 15px rgba(236, 72, 153, 0.4); }
    .mode-badge { font-size: 11px; padding: 4px 10px; border-radius: 20px; font-weight: 800; background: rgba(251, 191, 36, 0.15); color: #fbbf24; border: 1px solid #fbbf24; margin-left: 4px; transition: all 0.3s ease; letter-spacing: 0.5px; }
    .mode-badge.alt-aggressive { background: rgba(52, 211, 153, 0.2); color: #34d399; border-color: #34d399; box-shadow: 0 0 12px rgba(52, 211, 153, 0.5); animation: pulse 1.5s infinite; }
    .mode-badge.alt-normal { background: rgba(56, 189, 248, 0.2); color: #38bdf8; border-color: #38bdf8; box-shadow: 0 0 10px rgba(56, 189, 248, 0.4); }
    .mode-badge.streak-breaker { background: rgba(248, 113, 113, 0.2); color: #f87171; border-color: #f87171; }
    .mode-badge.defensive { background: rgba(251, 146, 60, 0.2); color: #fb923c; border-color: #fb923c; }
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
        .header-title { font-size: 16px; }
        .time-update { display: none; }
        .stats-bar { grid-template-columns: repeat(2, 1fr); }
        .stat-value { font-size: 18px; }
        .mode-badge { font-size: 10px; padding: 3px 8px; }
    }
</style>
</head>
<body>

<div class="header">
    <div class="header-title">
        LE MINH <span class="badge-md5">MD5</span>
        <span class="mode-badge" id="mode-badge">⚖️ BAL</span>
    </div>
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
                <tr><th>Phiên</th><th>Dự đoán</th><th>Kết quả</th><th>Trạng thái</th></tr>
            </thead>
            <tbody id="table-body">
                <tr><td colspan="4" class="loading">Đang kết nối Server...</td></tr>
            </tbody>
        </table>
    </div>
</div>

<script>
let isResetting = false;
let lastMode = '';

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

function updateMode(mode, label) {
    const badge = document.getElementById('mode-badge');
    if (!badge) return;
    badge.innerText = label || mode;
    badge.className = 'mode-badge';
    const cls = mode.toLowerCase().replace(/_/g, '-');
    if (cls !== 'balanced') badge.classList.add(cls);
    if (lastMode && lastMode !== mode) {
        // Mode switched
        console.log(`[AUTO] Mode: ${lastMode} → ${mode}`);
    }
    lastMode = mode;
}

async function fetchData() {
    if (isResetting) return;
    try {
        const r = await fetch('/api/history', { cache: 'no-store' });
        const data = await r.json();
        const now = new Date();
        document.getElementById('time-update').innerText = now.toLocaleTimeString('vi-VN');
        updateMode(data.mode || 'BALANCED', data.mode_label);
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
        if (row.recovery_flipped) recBadge = `<span class="recovery-badge" title="Auto-flip do thua ${row.loss_streak || 0} lần">⚡ </span>`;
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
            statusHtml = row.recovery_flipped ? `<span class="status st-recover">⚡ Đảo chiều</span>` : `<span class="status st-wait">Chờ kết quả</span>`;
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

# ═══════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════

def _opp(v): return "Xỉu" if v == "Tài" else "Tài"

def _to_binary(results):
    return [1 if r == "Tài" else 0 for r in results]

def _count_streak(results):
    if not results: return 0
    r0 = results[0]; c = 1
    for r in results[1:]:
        if r == r0: c += 1
        else: break
    return c

def get_groups(results):
    if not results: return []
    groups = []
    current_val = results[0]; count = 1
    for r in results[1:]:
        if r == current_val: count += 1
        else:
            groups.append(count); current_val = r; count = 1
    groups.append(count)
    return groups

def _regime(results):
    if len(results) < 5: return "balanced"
    streak = _count_streak(results)
    alt = sum(1 for i in range(min(5, len(results)-1)) if results[i] != results[i+1])
    if alt >= 4 and alt > streak: return "alternation"
    if streak >= 3: return "streak"
    return "balanced"

def encode_tx(results, length):
    if not results or len(results) < length: return None
    return "".join("T" if r == "Tài" else "X" for r in results[:length])

def learn_md5_db(valid):
    db = defaultdict(lambda: {"Tài": 0, "Xỉu": 0})
    n = len(valid)
    if n < 15: return db
    for i in range(13, n):
        hist = [v['result'] for v in valid[i-13:i]][::-1]
        tx = encode_tx(hist, 13)
        h = hashlib.md5(f"leminh_v2|{tx}".encode()).hexdigest()
        db[h][valid[i]['result']] += 1
    return db

# ═══════════════════════════════════════════════════════════════════
# ⚡ AUTO MODE ENGINE (NEW)
# ═══════════════════════════════════════════════════════════════════

def detect_perfect_alternation_5p(results):
    """
    🎯 Phát hiện 5 phiên gần nhất có đảo 1-1 hoàn hảo (4 transitions)
    Pattern: TXTXT hoặc XTXTX
    """
    if len(results) < 5:
        return None
    last5 = results[:5]
    transitions = sum(1 for i in range(4) if last5[i] != last5[i+1])
    if transitions != 4:
        return None

    # Kiểm tra độ dài đảo mở rộng (để cảnh báo gãy)
    alt_streak = 0
    for i in range(min(len(results) - 1, 15)):
        if results[i] != results[i+1]:
            alt_streak += 1
        else:
            break

    if alt_streak >= 8:
        return {'direction': results[0], 'warning': 'alt_break', 'alt_streak': alt_streak}
    if alt_streak >= 6:
        return {'direction': _opp(last5[0]), 'warning': 'alt_warn', 'alt_streak': alt_streak}
    return {'direction': _opp(last5[0]), 'warning': None, 'alt_streak': alt_streak}

def get_auto_mode(results, loss_streak):
    """
    ⚡ Tự động chọn chế độ thuật toán dựa trên bối cảnh
    """
    if len(results) < 5:
        return 'BALANCED'

    regime = _regime(results)
    perfect_5p = detect_perfect_alternation_5p(results)

    # ⚡ Ưu tiên 1: 5P đảo 1-1 hoàn hảo → aggressive
    if perfect_5p and perfect_5p.get('warning') is None:
        return 'ALT_AGGRESSIVE'
    # 🛡️ Ưu tiên 2: đang thua → phòng thủ
    if loss_streak >= 2:
        return 'DEFENSIVE'
    # 🔥 Ưu tiên 3: thị trường bệt → breaker
    if regime == 'streak':
        return 'STREAK_BREAKER'
    # 🔄 Ưu tiên 4: đảo thông thường
    if regime == 'alternation':
        return 'ALT_NORMAL'
    return 'BALANCED'

def apply_mode_weights(mode):
    """
    Hệ số nhân trọng số cho từng nhóm tín hiệu theo mode
    """
    table = {
        'ALT_AGGRESSIVE': {'5p_alt': 2.2, '5p': 1.6, 'streak': 0.4, 'pattern': 0.7, 'stat': 0.5},
        'ALT_NORMAL':     {'5p_alt': 1.6, '5p': 1.4, 'streak': 0.7, 'pattern': 1.0, 'stat': 0.9},
        'STREAK_BREAKER': {'5p_alt': 0.5, '5p': 0.9, 'streak': 1.9, 'pattern': 1.0, 'stat': 0.9},
        'DEFENSIVE':      {'5p_alt': 0.7, '5p': 0.8, 'streak': 0.8, 'pattern': 1.3, 'stat': 1.4},
        'BALANCED':       {'5p_alt': 1.0, '5p': 1.0, 'streak': 1.0, 'pattern': 1.0, 'stat': 1.0}
    }
    return table.get(mode, table['BALANCED'])

def categorize_signal(tag):
    """Phân loại tín hiệu theo tag"""
    if tag.startswith('5p_v2_alt_') or tag.startswith('alt_streak_') or tag == 'palindrome_alt5':
        return '5p_alt'
    if tag.startswith('5p_') or tag.startswith('palindrome_') or tag.startswith('majority_') or tag.startswith('phase_'):
        return '5p'
    if tag.startswith('streak_') or tag.startswith('anti_streak_') or tag.startswith('gap_'):
        return 'streak'
    if tag.startswith('pattern_') or tag.startswith('zigzag_') or tag.startswith('ngram_') or tag.startswith('trans_'):
        return 'pattern'
    return 'stat'

# ═══════════════════════════════════════════════════════════════════
# 🎯 PRIMARY - 5 PHIÊN ĐẢO THUẬN NGHỊCH 1-1
# ═══════════════════════════════════════════════════════════════════

def algo_5p_pattern_v2(results):
    if len(results) < 5:
        return None
    last5 = results[:5]
    transitions = sum(1 for i in range(4) if last5[i] != last5[i+1])
    alt_streak = 0
    for i in range(min(len(results) - 1, 10)):
        if results[i] != results[i + 1]: alt_streak += 1
        else: break

    if transitions == 4:
        if alt_streak >= 7: return (results[0], 72, "5p_v2_alt_break_7")
        if alt_streak >= 5: return (_opp(last5[0]), 78, "5p_v2_alt_continue_long")
        return (_opp(last5[0]), 90, "5p_v2_alt_perfect")

    if transitions == 3:
        bet_positions = [i for i in range(4) if last5[i] == last5[i + 1]]
        if bet_positions:
            bet_pos = bet_positions[0]
            if bet_pos == 0: return (_opp(last5[0]), 76, "5p_v2_3t_bet_head")
            if bet_pos == 3: return (_opp(last5[0]), 84, "5p_v2_3t_dao_tail")
            return (_opp(last5[0]), 80, "5p_v2_3t_dao_mid")
        return (_opp(last5[0]), 78, "5p_v2_3t_default")

    if transitions == 2:
        if last5[0] != last5[1]:
            if last5[2] == last5[3] == last5[4]:
                return (_opp(last5[0]), 70, "5p_v2_2t_dao_then_bet")
            return (_opp(last5[0]), 68, "5p_v2_2t_dao_head")
        return (_opp(last5[0]), 64, "5p_v2_2t_bet_head")

    if transitions == 1:
        streak = _count_streak(results)
        if streak >= 4: return (_opp(last5[0]), 74, "5p_v2_1t_break4")
        return (_opp(last5[0]), 68, "5p_v2_1t_default")

    if transitions == 0:
        streak = _count_streak(results)
        if streak >= 7: return (_opp(last5[0]), 80, "5p_v2_bet7_break")
        if streak >= 6: return (_opp(last5[0]), 76, "5p_v2_bet6_break")
        return (_opp(last5[0]), 72, "5p_v2_bet5_break")
    return None

def algo_alternation_streak(results):
    if len(results) < 8: return None
    alt_streak = 0
    for i in range(min(len(results) - 1, 15)):
        if results[i] != results[i + 1]: alt_streak += 1
        else: break
    if alt_streak < 2: return None
    if alt_streak >= 8: return (results[0], 84, f"alt_streak_break_{alt_streak}")
    if alt_streak == 7: return (results[0], 76, "alt_streak_break_7")
    if alt_streak == 6: return (_opp(results[0]), 58, "alt_streak_warn_6")
    if alt_streak >= 4: return (_opp(results[0]), 78, "alt_streak_continue_mid")
    return (_opp(results[0]), 82, "alt_streak_continue_short")

def algo_5p_palindrome(results):
    if len(results) < 5: return None
    last5 = results[:5]
    if last5[0] == last5[4] and last5[1] == last5[3]:
        transitions = sum(1 for i in range(4) if last5[i] != last5[i + 1])
        if transitions == 4: return (_opp(last5[0]), 88, "palindrome_alt5")
        if transitions == 2: return (_opp(last5[0]), 74, "palindrome_bet5")
    return None

def algo_5p_majority(results):
    if len(results) < 5: return None
    last5 = results[:5]
    tai_count = sum(1 for r in last5 if r == "Tài")
    if tai_count == 5: return ("Xỉu", 76, "majority_5T")
    if tai_count == 4: return ("Xỉu", 66, "majority_4T")
    if tai_count == 1: return ("Tài", 66, "majority_4X")
    if tai_count == 0: return ("Tài", 76, "majority_5X")
    return None

def algo_5p_with_streak_context(results):
    if len(results) < 8: return None
    last5 = results[:5]
    streak = _count_streak(results)
    trans_5 = sum(1 for i in range(4) if last5[i] != last5[i + 1])
    if streak >= 3 and trans_5 >= 2: return (_opp(results[0]), 74, "5p_streak_break")
    if streak <= 2 and trans_5 == 4: return (_opp(results[0]), 84, "5p_streak_alt")
    return None

def algo_5p_phase_cycle(results):
    if len(results) < 12: return None
    for period in [2, 3, 4]:
        if len(results) < period * 3 + 1: continue
        matches = 0; checks = 0
        for i in range(period, min(len(results), period * 4)):
            checks += 1
            if results[i] == results[i - period]: matches += 1
        if checks >= 6 and matches / checks >= 0.75:
            next_in_cycle = results[period - 1]
            confidence = 68 + int((matches / checks - 0.75) * 60)
            confidence = min(84, confidence)
            return (next_in_cycle, confidence, f"phase_{period}")
    return None

# ═══════════════════════════════════════════════════════════════════
# 🎯 SECONDARY
# ═══════════════════════════════════════════════════════════════════

def algo_streak_analysis(results):
    if len(results) < 6: return None
    streak = _count_streak(results); streak_val = results[0]
    if streak >= 7: return (_opp(streak_val), 82, f"streak_break_{streak}")
    if streak >= 6: return (_opp(streak_val), 78, "streak_break_6")
    if streak == 5: return (_opp(streak_val), 72, "streak_break_5")
    if streak == 4:
        if len(results) >= 10:
            same = sum(1 for r in results[:10] if r == streak_val)
            if same >= 7: return (streak_val, 62, "streak_continue_4")
        return (_opp(streak_val), 68, "streak_break_4")
    if streak == 3:
        if len(results) >= 10:
            same = sum(1 for r in results[:10] if r == streak_val)
            if same >= 7: return (streak_val, 60, "streak_continue_3")
        return (_opp(streak_val), 58, "streak_break_3")
    return None

def algo_pattern_match(results):
    if len(results) < 20: return None
    best = None
    for plen in [2, 3, 4, 5, 6]:
        if len(results) < plen + 10: continue
        pattern = results[:plen]
        matches_tai = 0; matches_xiu = 0
        for i in range(plen, len(results) - plen + 1):
            if results[i:i + plen] == pattern:
                if results[i - 1] == "Tài": matches_tai += 1
                else: matches_xiu += 1
        total = matches_tai + matches_xiu
        if total >= 3:
            if matches_tai / total >= 0.7:
                w = 15 + plen * 3
                if best is None or w > best[1]: best = ("Tài", w, f"pattern_{plen}T")
            elif matches_xiu / total >= 0.7:
                w = 15 + plen * 3
                if best is None or w > best[1]: best = ("Xỉu", w, f"pattern_{plen}X")
    return best

def algo_zigzag_detection(results):
    if len(results) < 8: return None
    groups = get_groups(results)
    if len(groups) < 3: return None
    if groups[0] == 2 and groups[1] == 2 and groups[2] == 2: return (_opp(results[0]), 76, "zigzag_222")
    if groups[0] == 3 and groups[1] == 3 and groups[2] == 3: return (_opp(results[0]), 72, "zigzag_333")
    if len(groups) >= 4:
        if groups[0] == 2 and groups[1] == 1 and groups[2] == 1: return (results[0], 62, "zigzag_transition")
    return None

def algo_n_gram_backoff(results):
    if len(results) < 15: return None
    for n in [6, 5, 4, 3]:
        if len(results) < n + 8: continue
        pattern = tuple(results[:n])
        counts = Counter()
        for i in range(n, len(results)):
            if tuple(results[i:i + n]) == pattern:
                if i > 0: counts[results[i - 1]] += 1
        total = counts["Tài"] + counts["Xỉu"]
        if total >= 3:
            p_tai = counts["Tài"] / total
            w = 12 + n * 4
            if p_tai >= 0.70: return ("Tài", w, f"ngram_{n}T")
            if p_tai <= 0.30: return ("Xỉu", w, f"ngram_{n}X")
    return None

# ═══════════════════════════════════════════════════════════════════
# 🎯 AUXILIARY
# ═══════════════════════════════════════════════════════════════════

def algo_momentum(results):
    if len(results) < 10: return None
    last3 = results[:3]; last10 = results[:10]
    tai_3 = sum(1 for r in last3 if r == "Tài")
    tai_10 = sum(1 for r in last10 if r == "Tài")
    if tai_3 == 3 and 3 <= tai_10 <= 6: return ("Tài", 62, "momentum_T")
    if tai_3 == 0 and 4 <= tai_10 <= 7: return ("Xỉu", 62, "momentum_X")
    return None

def algo_frequency_bias(results, window=20):
    if len(results) < window: return None
    recent = results[:window]
    tai = sum(1 for r in recent if r == "Tài")
    ratio = tai / window
    if ratio >= 0.72: return ("Xỉu", int(55 + (ratio - 0.72) * 100), "freq_bias_X")
    if ratio <= 0.28: return ("Tài", int(55 + (0.28 - ratio) * 100), "freq_bias_T")
    return None

def algo_markov(results):
    if len(results) < 20: return None
    key = (results[1], results[0])
    counts = {"Tài": 1, "Xỉu": 1}
    for i in range(min(len(results) - 2, 60)):
        if (results[i + 2], results[i + 1]) == key: counts[results[i]] += 1
    total = counts["Tài"] + counts["Xỉu"]
    if total < 6: return None
    p_tai = counts["Tài"] / total
    if p_tai >= 0.68: return ("Tài", 18, "markov2")
    if p_tai <= 0.32: return ("Xỉu", 18, "markov2")
    return None

def algo_markov_order3(results):
    if len(results) < 30: return None
    key = (results[2], results[1], results[0])
    counts = {"Tài": 1, "Xỉu": 1}
    for i in range(1, min(len(results) - 2, 81)):
        if (results[i + 2], results[i + 1], results[i]) == key: counts[results[i - 1]] += 1
    total = counts["Tài"] + counts["Xỉu"]
    if total < 5: return None
    p_tai = counts["Tài"] / total
    if p_tai >= 0.70: return ("Tài", 20, "markov3")
    if p_tai <= 0.30: return ("Xỉu", 20, "markov3")
    return None

def algo_trend_break(results):
    if len(results) < 20: return None
    window = list(reversed(results[:20])); b = _to_binary(window)
    x_mean = 9.5; y_mean = sum(b) / 20
    num = sum((i - x_mean) * (b[i] - y_mean) for i in range(20))
    den = sum((i - x_mean) ** 2 for i in range(20))
    slope = num / den if den else 0
    last3 = b[-3:]
    if slope > 0.05 and sum(last3) <= 1: return ("Tài", 15, "trend_T")
    if slope < -0.05 and sum(last3) >= 2: return ("Xỉu", 15, "trend_X")
    return None

def algo_gap_analysis(results):
    if len(results) < 25: return None
    last_tai = -1; last_xiu = -1
    gaps_tai = []; gaps_xiu = []
    for i, r in enumerate(results):
        if r == "Tài":
            if last_tai >= 0: gaps_tai.append(i - last_tai)
            last_tai = i
        else:
            if last_xiu >= 0: gaps_xiu.append(i - last_xiu)
            last_xiu = i
    cur_streak = _count_streak(results); cur_val = results[0]
    if cur_val == "Tài" and gaps_tai:
        avg = sum(gaps_tai) / len(gaps_tai)
        if cur_streak >= avg * 1.5 and cur_streak >= 3: return (_opp(cur_val), 66, "gap_overdue_T")
    if cur_val == "Xỉu" and gaps_xiu:
        avg = sum(gaps_xiu) / len(gaps_xiu)
        if cur_streak >= avg * 1.5 and cur_streak >= 3: return (_opp(cur_val), 66, "gap_overdue_X")
    return None

def algo_entropy_analysis(results, window=15):
    if len(results) < window: return None
    recent = results[:window]
    p_tai = sum(1 for r in recent if r == "Tài") / window
    p_xiu = 1 - p_tai
    entropy = 0
    if p_tai > 0: entropy -= p_tai * math.log2(p_tai)
    if p_xiu > 0: entropy -= p_xiu * math.log2(p_xiu)
    if entropy >= 0.95:
        alt = sum(1 for i in range(4) if results[i] != results[i + 1])
        if alt >= 3: return (_opp(results[0]), 70, "entropy_alt")
    if entropy <= 0.5:
        if _count_streak(results) >= 3: return (_opp(results[0]), 68, "entropy_low_break")
    return None

def algo_multi_window_consensus(results):
    if len(results) < 15: return None
    votes = []
    for w in [5, 7, 10, 15]:
        if len(results) < w: continue
        seg = results[:w]
        tai = sum(1 for r in seg if r == "Tài")
        ratio = tai / w
        if ratio >= 0.70: votes.append("Tài")
        elif ratio <= 0.30: votes.append("Xỉu")
        else: votes.append(None)
    valid = [v for v in votes if v is not None]
    if len(valid) >= 3:
        if all(v == "Tài" for v in valid): return ("Tài", 65, "mwc_T")
        if all(v == "Xỉu" for v in valid): return ("Xỉu", 65, "mwc_X")
    if len(votes) >= 2:
        if votes[0] == "Tài" and votes[-1] is None: return ("Xỉu", 58, "mwc_rev_X")
        if votes[0] == "Xỉu" and votes[-1] is None: return ("Tài", 58, "mwc_rev_T")
    return None

def algo_anti_streak(results):
    if len(results) < 30: return None
    cur_streak = _count_streak(results); cur_val = results[0]
    if cur_streak < 3: return None
    max_streak = 0; current = 1
    for i in range(1, min(len(results), 30)):
        if results[i] == results[i - 1]:
            current += 1; max_streak = max(max_streak, current)
        else: current = 1
    if cur_streak >= max_streak and cur_streak >= 4: return (_opp(cur_val), 72, "anti_streak_max")
    return None

def algo_transition_matrix(results):
    if len(results) < 25: return None
    key = tuple(results[:3]); trans = Counter()
    for i in range(len(results) - 3 - 1):
        if tuple(results[i:i + 3]) == key: trans[results[i + 3]] += 1
    total = trans["Tài"] + trans["Xỉu"]
    if total >= 4:
        p_tai = trans["Tài"] / total
        if p_tai >= 0.70: return ("Tài", 55, "trans_matrix_T")
        if p_tai <= 0.30: return ("Xỉu", 55, "trans_matrix_X")
    return None

def algo_streak_length_distribution(results):
    if len(results) < 40: return None
    cur_streak = _count_streak(results); cur_val = results[0]
    streak_lengths = []; i = 0
    while i < min(len(results), 40):
        j = i
        while j < len(results) and results[j] == results[i]: j += 1
        if results[i] == cur_val: streak_lengths.append(j - i)
        i = j
    if len(streak_lengths) >= 3:
        if cur_streak > max(streak_lengths[:-1] if len(streak_lengths) > 1 else streak_lengths):
            return (_opp(cur_val), 70, "streak_dist_break")
    return None

def algo_md5_deep(results, md5_db):
    if len(results) < 13: return None
    tx = encode_tx(results, 13)
    h = hashlib.md5(f"leminh_v2|{tx}".encode()).hexdigest()
    if h in md5_db:
        counts = md5_db[h]
        total = counts["Tài"] + counts["Xỉu"]
        if total >= 2:
            p_tai = (counts["Tài"] + 1) / (total + 2)
            if p_tai >= 0.65: return ("Tài", 22, "md5_deep")
            if p_tai <= 0.35: return ("Xỉu", 22, "md5_deep")
    return None

# ═══════════════════════════════════════════════════════════════════
# 🎯 SIGNAL AGGREGATION với AUTO MODE
# ═══════════════════════════════════════════════════════════════════

def compute_prediction(results, md5_db, mode='BALANCED'):
    if not results:
        return "Tài", 50
    if len(results) == 1:
        return _opp(results[0]), 52

    signals = []

    for fn in [algo_5p_pattern_v2, algo_alternation_streak, algo_5p_palindrome,
               algo_5p_majority, algo_5p_with_streak_context]:
        s = fn(results)
        if s: signals.append(s)

    for fn in [algo_streak_analysis, algo_pattern_match, algo_zigzag_detection,
               algo_n_gram_backoff, algo_5p_phase_cycle]:
        s = fn(results)
        if s: signals.append(s)

    for fn in [algo_momentum, algo_frequency_bias, algo_trend_break, algo_markov,
               algo_markov_order3, algo_gap_analysis, algo_entropy_analysis,
               algo_multi_window_consensus, algo_anti_streak, algo_transition_matrix,
               algo_streak_length_distribution]:
        s = fn(results)
        if s: signals.append(s)

    s = algo_md5_deep(results, md5_db)
    if s: signals.append(s)

    if not signals:
        return results[0], 52

    # ⚡ Áp dụng trọng số auto mode
    weights = apply_mode_weights(mode)
    st = 0.0; sx = 0.0
    for p, w, tag in signals:
        cat = categorize_signal(tag)
        mult = weights.get(cat, 1.0)
        if p == "Tài": st += w * mult
        else: sx += w * mult

    total = st + sx
    if total == 0:
        return results[0], 50

    if st > sx: pred = "Tài"
    elif sx > st: pred = "Xỉu"
    else: return _opp(results[0]), 50

    ratio = max(st, sx) / total
    conf = 50 + (ratio - 0.5) * 100

    n = len(signals)
    if n >= 10: conf += 11
    elif n >= 8: conf += 9
    elif n >= 6: conf += 6
    elif n >= 4: conf += 4

    agree = sum(w for p, w, _ in signals if p == pred)
    agree_ratio = agree / total
    if agree_ratio >= 0.90: conf += 6
    elif agree_ratio >= 0.80: conf += 4
    elif agree_ratio >= 0.65: conf += 2

    # Bonus cho 5P dominance
    primary_votes = [s for s in signals if s[2].startswith("5p_") or s[2].startswith("alt_") or s[2].startswith("palindrome") or s[2].startswith("majority")]
    if len(primary_votes) >= 3:
        primary_preds = {s[0] for s in primary_votes}
        if primary_preds == {pred}:
            conf += 5

    # ⚡ Bonus thêm cho ALT_AGGRESSIVE
    if mode == 'ALT_AGGRESSIVE' and categorize_signal(signals[0][2]) == '5p_alt':
        conf += 3

    conf = int(max(MIN_CONFIDENCE, min(MAX_CONFIDENCE, conf)))
    return pred, conf

def apply_recovery(base_pred, conf, loss_streak, results, mode='BALANCED'):
    if loss_streak < 2:
        return base_pred, conf, False
    # DEFENSIVE đã tự cân bằng → chỉ flip khi rất xấu
    if mode == 'DEFENSIVE' and loss_streak < 3:
        return base_pred, conf, False
    regime = _regime(results) if results else "balanced"
    if loss_streak == 2:
        if regime == "alternation": return base_pred, max(55, conf - 5), False
        if conf >= 86: return base_pred, conf, False
        return _opp(base_pred), min(MAX_CONFIDENCE, max(conf, 75)), True
    if loss_streak >= 3:
        return _opp(base_pred), min(MAX_CONFIDENCE, conf + 12), True
    return base_pred, conf, False

def compute_loss_streak(valid, md5_db):
    if len(valid) < 5: return 0
    streak = 0
    for i in range(len(valid) - 1, max(2, len(valid) - 8), -1):
        if i < 3: break
        hist = [v['result'] for v in valid[:i]][::-1]
        pred, _ = compute_prediction(hist, md5_db, 'BALANCED')
        if pred == valid[i]['result']: break
        streak += 1
    return streak

# ═══════════════════════════════════════════════════════════════════
# API & ROUTES
# ═══════════════════════════════════════════════════════════════════

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
                    rows.append({'phien': int(item['id']), 'result': res, 'dice': dice_str, 'total': total})
                return rows, True
    except Exception as e:
        print(f"[API ERROR]: {e}")
    return [], False

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/api/history')
def history():
    global RESET_ID, AUTO_MODE_STATE
    try:
        raw_rows, _ = fetch_sessions()
        if raw_rows:
            for row in raw_rows: HISTORY_CACHE[row['phien']] = row
            for k in [k for k in HISTORY_CACHE if k < RESET_ID]: del HISTORY_CACHE[k]
        valid = sorted(HISTORY_CACHE.values(), key=lambda x: x['phien'])
        if not valid:
            return jsonify({'rows': [], 'stats': {'total': 0, 'correct': 0},
                           'mode': 'BALANCED', 'mode_label': MODE_LABELS['BALANCED']})

        md5_db = learn_md5_db(valid)
        loss_streak = compute_loss_streak(valid, md5_db)
        history_for_next = [v['result'] for v in valid][::-1]

        # ⚡ AUTO MODE selection
        mode = get_auto_mode(history_for_next, loss_streak)
        AUTO_MODE_STATE['previous'] = AUTO_MODE_STATE['current']
        if AUTO_MODE_STATE['current'] != mode:
            AUTO_MODE_STATE['switch_count'] += 1
        AUTO_MODE_STATE['current'] = mode

        out = []; correct_count = 0; total_past = 0

        base_pred, base_conf = compute_prediction(history_for_next, md5_db, mode)
        next_pred, next_conf, flipped = apply_recovery(base_pred, base_conf, loss_streak, history_for_next, mode)
        out.append({
            'phien': valid[-1]['phien'] + 1, 'is_next': True,
            'prediction': next_pred, 'result': 'Chờ...', 'dice': "? + ? + ?",
            'is_correct': None, 'conf': next_conf,
            'recovery_flipped': flipped, 'loss_streak': loss_streak
        })

        for i in range(len(valid) - 1, max(-1, len(valid) - 150), -1):
            hist = [v['result'] for v in valid[:i]][::-1]
            if len(hist) >= 5:
                mode_i = get_auto_mode(hist, 0)
            else:
                mode_i = 'BALANCED'
            pred, conf = compute_prediction(hist, md5_db, mode_i) if len(hist) >= 3 else ("-", 50)
            is_correct = (pred == valid[i]['result']) if pred != "-" else None
            if is_correct is not None: total_past += 1
            if is_correct: correct_count += 1
            out.append({
                'phien': valid[i]['phien'], 'is_next': False, 'prediction': pred,
                'result': valid[i]['result'], 'dice': valid[i]['dice'],
                'is_correct': is_correct, 'conf': conf, 'recovery_flipped': False
            })

        return jsonify({
            'rows': out,
            'stats': {'total': total_past, 'correct': correct_count},
            'mode': mode,
            'mode_label': MODE_LABELS.get(mode, mode),
            'loss_streak': loss_streak,
            'switch_count': AUTO_MODE_STATE['switch_count']
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e), 'rows': [], 'stats': {'total': 0, 'correct': 0},
                       'mode': 'BALANCED', 'mode_label': MODE_LABELS['BALANCED']})

@app.route('/api/predict')
def predict():
    global RESET_ID
    try:
        raw_rows, _ = fetch_sessions()
        if raw_rows:
            for row in raw_rows: HISTORY_CACHE[row['phien']] = row
            for k in [k for k in HISTORY_CACHE if k < RESET_ID]: del HISTORY_CACHE[k]
        valid = sorted(HISTORY_CACHE.values(), key=lambda x: x['phien'])
        if not valid: return jsonify({'error': 'Chưa có dữ liệu'})
        results = [v['result'] for v in valid][::-1]
        md5_db = learn_md5_db(valid)
        loss_streak = compute_loss_streak(valid, md5_db)
        mode = get_auto_mode(results, loss_streak)
        base_pred, base_conf = compute_prediction(results, md5_db, mode)
        pred, conf, flipped = apply_recovery(base_pred, base_conf, loss_streak, results, mode)
        return jsonify({
            'last_phien': valid[-1]['phien'], 'next_phien': valid[-1]['phien'] + 1,
            'prediction': pred, 'confidence': conf, 'recovery_flipped': flipped,
            'loss_streak': loss_streak, 'mode': mode,
            'mode_label': MODE_LABELS.get(mode, mode),
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/lc79')
@app.route('/lc79md5')
def lc79_api():
    global RESET_ID
    try:
        raw_rows, _ = fetch_sessions()
        if raw_rows:
            for row in raw_rows: HISTORY_CACHE[row['phien']] = row
            for k in [k for k in HISTORY_CACHE if k < RESET_ID]: del HISTORY_CACHE[k]
        valid = sorted(HISTORY_CACHE.values(), key=lambda x: x['phien'])
        if not valid:
            return jsonify({'id': '@cskhleminh', 'phien': '-', 'du_doan': '-', 'do_tin_cay': '50%'})
        results = [v['result'] for v in valid][::-1]
        md5_db = learn_md5_db(valid)
        loss_streak = compute_loss_streak(valid, md5_db)
        mode = get_auto_mode(results, loss_streak)
        base_pred, base_conf = compute_prediction(results, md5_db, mode)
        pred, conf, flipped = apply_recovery(base_pred, base_conf, loss_streak, results, mode)
        groups = get_groups(results)
        lich_su = "-" + "-".join(str(g) for g in groups[:4])
        return jsonify({
            'id': '@cskhleminh',
            'phien': str(valid[-1]['phien'] + 1),
            'ket_qua': f"{valid[-1]['result'].lower()} {valid[-1]['dice'].replace(' + ', '-')}",
            'du_doan': pred,
            'do_tin_cay': f"{conf}%",
            'lich_su': lich_su,
            'auto_flip': flipped,
            'chuoi_thua': loss_streak,
            'auto_mode': mode,
            'auto_mode_label': MODE_LABELS.get(mode, mode)
        })
    except Exception as e:
        return jsonify({'id': '@cskhleminh', 'phien': '-', 'du_doan': '-', 'do_tin_cay': '50%', 'error': str(e)})

@app.route('/api/reset', methods=['POST'])
def reset_api():
    global RESET_ID, HISTORY_CACHE, AUTO_MODE_STATE
    try:
        raw_rows, _ = fetch_sessions()
        if raw_rows:
            valid = sorted(raw_rows, key=lambda x: x['phien'])
            if valid:
                RESET_ID = valid[-1]['phien']
                HISTORY_CACHE = {valid[-1]['phien']: valid[-1]}
                AUTO_MODE_STATE['switch_count'] = 0
                return jsonify({'status': 'success', 'reset_to': RESET_ID})
        return jsonify({'status': 'error', 'message': 'Lỗi mạng'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, threaded=True)
