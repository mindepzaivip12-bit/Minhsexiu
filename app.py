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

# ✅ API MỚI - đã thay thế API cũ
API_URL_MD5 = "https://wtxmd52.macminim6.online/v1/txmd5/lite-sessions?cp=R&cl=R&pf=web&at=bd651d261174ed4a619dfde6c5432fdd"

HISTORY_CACHE = {}
PREDICTION_CACHE = {}
MAX_PREDICTION_CACHE = 2000
RESET_ID = 0
BOOTSTRAP_SESSIONS = []  # Lưu session mồi khi reset để dự đoán ngay không đợi 3 phiên

# =====================================================================
# PATTERN DATABASE (chuỗi 13 phiên T/X -> kết quả phiên kế tiếp)
# =====================================================================
PATTERN_DB = {
    "TTTTTTTTTTTTT": "Tài",
    "TTTTTTTTTTTTX": "Tài",
    "TTTTTTTTTTTXT": "Xỉu",
    "TTTTTTTTTTTXX": "Xỉu",
    "TTTTTTTTTTXTT": "Tài",
    "TTTTTTTTTTXTX": "Tài",
    "TTTTTTTTTTXXT": "Xỉu",
    "TTTTTTTTTTXXX": "Tài",
    "TTTTTTTTTXTTT": "Xỉu",
    "TTTTTTTTTXTTX": "Xỉu",
    "TTTTTTTTTXTXT": "Xỉu",
    "TTTTTTTTTXTXX": "Xỉu",
    "TTTTTTTTTXXTT": "Tài",
    "TTTTTTTTTXXTX": "Tài",
    "TTTTTTTTTXXXT": "Xỉu",
    "TTTTTTTTTXXXX": "Tài",
    "TTTTTTTTXTTTT": "Tài",
    "TTTTTTTTXTTTX": "Tài",
    "TTTTTTTTXTTXT": "Xỉu",
    "TTTTTTTTXTTXX": "Xỉu",
    "TTTTTTTTXTXTT": "Tài",
    "TTTTTTTTXTXTX": "Tài",
    "TTTTTTTTXTXXT": "Tài",
    "TTTTTTTTXTXXX": "Tài",
    "TTTTTTTTXXTTT": "Tài",
    "TTTTTTTTXXTTX": "Tài",
    "TTTTTTTTXXTXT": "Xỉu",
    "TTTTTTTTXXTXX": "Xỉu",
    "TTTTTTTTXXXTT": "Tài",
    "TTTTTTTTXXXTX": "Tài",
    "TTTTTTTTXXXXT": "Xỉu",
    "TTTTTTTTXXXXX": "Xỉu",
    "TTTTTTTXTTTTT": "Tài",
    "TTTTTTTXTTTTX": "Tài",
    "TTTTTTTXTTTXT": "Xỉu",
    "TTTTTTTXTTTXX": "Xỉu",
    "TTTTTTTXTTXTT": "Tài",
    "TTTTTTTXTTXTX": "Tài",
    "TTTTTTTXTTXXT": "Tài",
    "TTTTTTTXTTXXX": "Xỉu",
    "TTTTTTTXTXTTT": "Xỉu",
    "TTTTTTTXTXTTX": "Xỉu",
    "TTTTTTTXTXTXT": "Xỉu",
    "TTTTTTTXTXTXX": "Xỉu",
    "TTTTTTTXTXXTT": "Tài",
    "TTTTTTTXTXXTX": "Tài",
    "TTTTTTTXTXXXT": "Xỉu",
    "TTTTTTTXTXXXX": "Tài",
    "TTTTTTTXXTTTT": "Tài",
    "TTTTTTTXXTTTX": "Tài",
    "TTTTTTTXXTTXT": "Xỉu",
    "TTTTTTTXXTTXX": "Xỉu",
    "TTTTTTTXXTXTT": "Tài",
    "TTTTTTTXXTXTX": "Tài",
    "TTTTTTTXXTXXT": "Tài",
    "TTTTTTTXXTXXX": "Tài",
    "TTTTTTTXXXTTT": "Tài",
    "TTTTTTTXXXTTX": "Tài",
    "TTTTTTTXXXTXT": "Xỉu",
    "TTTTTTTXXXTXX": "Xỉu",
    "TTTTTTTXXXXTT": "Tài",
    "TTTTTTTXXXXTX": "Tài",
    "TTTTTTTXXXXXT": "Xỉu",
    "TTTTTTTXXXXXX": "Xỉu",
    "TTTTTTXTTTTTT": "Tài",
    "TTTTTTXTTTTTX": "Tài",
    "TTTTTTXTTTTXT": "Xỉu",
    "TTTTTTXTTTTXX": "Xỉu",
    "TTTTTTXTTTXTT": "Tài",
    "TTTTTTXTTTXTX": "Tài",
    "TTTTTTXTTTXXT": "Tài",
    "TTTTTTXTTTXXX": "Xỉu",
    "TTTTTTXTTXTTT": "Xỉu",
    "TTTTTTXTTXTTX": "Xỉu",
    "TTTTTTXTTXTXT": "Xỉu",
    "TTTTTTXTTXTXX": "Xỉu",
    "TTTTTTXTTXXTT": "Tài",
    "TTTTTTXTTXXTX": "Tài",
    "TTTTTTXTTXXXT": "Xỉu",
    "TTTTTTXTTXXXX": "Tài",
    "TTTTTTXTXTTTT": "Tài",
    "TTTTTTXTXTTTX": "Tài",
    "TTTTTTXTXTTXT": "Xỉu",
    "TTTTTTXTXTTXX": "Xỉu",
    "TTTTTTXTXTXTT": "Tài",
    "TTTTTTXTXTXTX": "Tài",
    "TTTTTTXTXTXXT": "Tài",
    "TTTTTTXTXTXXX": "Tài",
    "TTTTTTXTXXTTT": "Tài",
    "TTTTTTXTXXTTX": "Tài",
    "TTTTTTXTXXTXT": "Xỉu",
    "TTTTTTXTXXTXX": "Xỉu",
    "TTTTTTXTXXXTT": "Tài",
    "TTTTTTXTXXXTX": "Tài",
    "TTTTTTXTXXXXT": "Xỉu",
    "TTTTTTXTXXXXX": "Xỉu",
    "TTTTTTXXTTTTT": "Tài",
    "TTTTTTXXTTTTX": "Tài",
    "TTTTTTXXTTTXT": "Xỉu",
    "TTTTTTXXTTTXX": "Xỉu",
    "TTTTTTXXTTXTT": "Tài",
    "TTTTTTXXTTXTX": "Tài",
    "TTTTTTXXTTXXT": "Tài",
    "TTTTTTXXTTXXX": "Xỉu",
    "TTTTTTXXTXTTT": "Xỉu",
    "TTTTTTXXTXTTX": "Xỉu",
    "TTTTTTXXTXTXT": "Xỉu",
    "TTTTTTXXTXTXX": "Xỉu",
    "TTTTTTXXTXXTT": "Tài",
    "TTTTTTXXTXXTX": "Tài",
    "TTTTTTXXTXXXT": "Xỉu",
    "TTTTTTXXTXXXX": "Tài",
    "TTTTTTXXXTTTT": "Tài",
    "TTTTTTXXXTTTX": "Tài",
    "TTTTTTXXXTTXT": "Xỉu",
    "TTTTTTXXXTTXX": "Xỉu",
    "TTTTTTXXXTXTT": "Tài",
    "TTTTTTXXXTXTX": "Tài",
    "TTTTTTXXXTXXT": "Tài",
    "TTTTTTXXXTXXX": "Tài",
    "TTTTTTXXXXTTT": "Tài",
    "TTTTTTXXXXTTX": "Tài",
    "TTTTTTXXXXTXT": "Xỉu",
    "TTTTTTXXXXTXX": "Xỉu",
    "TTTTTTXXXXXTT": "Tài",
    "TTTTTTXXXXXTX": "Tài",
    "TTTTTTXXXXXXT": "Xỉu",
    "TTTTTTXXXXXXX": "Xỉu",
}

def tx_encode(results, length):
    if len(results) < length: return None
    return "".join("T" if r == "Tài" else "X" for r in results[:length])


# =====================================================================
# HTML TEMPLATE (giữ nguyên 100%)
# =====================================================================
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
    <div class="header-title">LE MINH <span>MD5 </span></div>
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
# DATA FETCHER
# =====================================================================
def fetch_sessions():
    try:
        headers = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'}
        r = requests.get(API_URL_MD5, timeout=5, headers=headers)
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


# =====================================================================
# UTILS
# =====================================================================
def get_groups(results):
    if not results: return []
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


def _opp(v):
    return "Xỉu" if v == "Tài" else "Tài"


def _count_alternations(results, max_len=15):
    count = 0
    for i in range(min(len(results) - 1, max_len)):
        if results[i] != results[i+1]:
            count += 1
        else:
            break
    return count


def _count_streak(results):
    if not results: return 0
    r0 = results[0]; c = 1
    for r in results[1:]:
        if r == r0: c += 1
        else: break
    return c


def _regime(results):
    if len(results) < 5: return "balanced"
    alt = _count_alternations(results, 12)
    streak = _count_streak(results)
    if alt >= 4 and alt > streak: return "alternation"
    if streak >= 3: return "streak"
    return "balanced"


def _entropy_bits(arr):
    if not arr: return 0.0
    p = arr.count("Tài") / len(arr)
    if p in (0, 1): return 0.0
    return -(p * math.log2(p) + (1-p) * math.log2(1-p))


# =====================================================================
# PATTERN LOOKUP
# =====================================================================
def algo_pattern_lookup(results):
    key = tx_encode(results, 13)
    if key and key in PATTERN_DB:
        return (PATTERN_DB[key], 30, "pattern_db")
    return None


# =====================================================================
# CORE STATISTICAL ALGORITHMS
# =====================================================================
def algo_bet_break(results, groups):
    if not groups or len(results) < 3: return []
    sigs = []
    r0 = results[0]; g0 = groups[0]
    if   g0 >= 12: sigs.append((_opp(r0), 44, "bet_12plus"))
    elif g0 >= 10: sigs.append((_opp(r0), 40, "bet_10plus"))
    elif g0 >= 8:  sigs.append((_opp(r0), 34, "bet_8_9"))
    elif g0 >= 6:  sigs.append((_opp(r0), 26, "bet_6_7"))
    elif g0 == 5:  sigs.append((_opp(r0), 19, "bet_5"))
    elif g0 == 4:  sigs.append((_opp(r0), 13, "bet_4"))
    elif g0 == 3:
        if len(groups) >= 5:
            past = groups[1:min(12, len(groups))]
            avg_g = sum(past) / len(past)
            if   avg_g < 2.3: sigs.append((_opp(r0), 14, "bet_3_break"))
            elif avg_g >= 3.5: sigs.append((r0, 12, "bet_3_cont"))
            else:              sigs.append((_opp(r0), 10, "bet_3_def"))
        else: sigs.append((_opp(r0), 10, "bet_3_def"))
    return sigs


def algo_alternation(results):
    if len(results) < 4: return None
    alt = _count_alternations(results)
    if   alt >= 8: return (_opp(results[0]), 28, "alt_8plus")
    elif alt >= 6: return (_opp(results[0]), 24, "alt_6_7")
    elif alt >= 4: return (_opp(results[0]), 17, "alt_4_5")
    elif alt >= 3: return (_opp(results[0]), 11, "alt_3")
    return None


def algo_recency_momentum(results):
    n = len(results)
    if n < 8: return None
    st = sx = 0
    for i in range(min(n, 20)):
        w = 20 - i
        if results[i] == "Tài": st += w
        else: sx += w
    total = st + sx
    if total == 0: return None
    if max(st, sx) / total < 0.68: return None
    return ("Tài", 10, "recency_tai") if st > sx else ("Xỉu", 10, "recency_xiu")


def algo_weighted_trend(results):
    n = len(results)
    if n < 10: return None
    weights = [10.0, 8.0, 6.0, 4.0, 3.0, 2.0, 1.5, 1.0, 0.8, 0.5]
    tw = sw = 0.0
    for i, r in enumerate(results[:10]):
        w = weights[i]
        tw += w
        if r == "Tài": sw += w
    if tw == 0: return None
    ratio = sw / tw
    if ratio >= 0.72: return ("Tài", 11, "wt_trend_tai")
    if ratio <= 0.28: return ("Xỉu", 11, "wt_trend_xiu")
    return None


def algo_markov1(results):
    if len(results) < 15: return None
    tr = {"Tài": {"Tài": 0, "Xỉu": 0}, "Xỉu": {"Tài": 0, "Xỉu": 0}}
    for i in range(len(results[:45]) - 1):
        tr[results[i+1]][results[i]] += 1
    last = results[0]; tot = sum(tr[last].values())
    if tot < 5: return None
    p = tr[last]["Tài"] / tot
    if   p >= 0.65: return ("Tài", 11, "markov1")
    elif p <= 0.35: return ("Xỉu", 11, "markov1")
    return None


def algo_markov2(results):
    if len(results) < 20: return None
    key = (results[1], results[0])
    tr = defaultdict(lambda: {"Tài": 0, "Xỉu": 0})
    for i in range(len(results[:60]) - 2):
        k = (results[i+2], results[i+1])
        tr[k][results[i]] += 1
    d = tr.get(key)
    if not d: return None
    tot = d["Tài"] + d["Xỉu"]
    if tot < 4: return None
    p = d["Tài"] / tot
    if   p >= 0.70: return ("Tài", 13, "markov2")
    elif p <= 0.30: return ("Xỉu", 13, "markov2")
    return None


def algo_multi_window(results):
    n = len(results)
    if n < 15: return None
    w5 = results[:5].count("Tài"); w10 = results[:10].count("Tài"); w15 = results[:15].count("Tài")
    vt = sum([w5 >= 4, w10 >= 7, w15 >= 10]); vx = sum([w5 <= 1, w10 <= 3, w15 <= 5])
    if   vt >= 2: return ("Tài", 12, "multi_window")
    elif vx >= 2: return ("Xỉu", 12, "multi_window")
    return None


def algo_hot_tilt(results):
    n = len(results)
    if n < 12: return None
    for w, hi, lo in ((8, 7, 1), (12, 10, 2)):
        if n < w: continue
        tc = results[:w].count("Tài")
        if tc >= hi: return ("Xỉu", 15 if w == 8 else 13, f"hot_tai_{w}")
        elif tc <= lo: return ("Tài", 15 if w == 8 else 13, f"hot_xiu_{w}")
    return None


def algo_tilt_ratio(results):
    n = len(results)
    if n < 15: return None
    for window in (15, 20, 30):
        if n < window: continue
        tr = results[:window].count("Tài") / window
        if   tr >= 0.73: return ("Xỉu", 11, f"tilt_tai_{window}")
        elif tr <= 0.27: return ("Tài", 11, f"tilt_xiu_{window}")
    return None


def algo_entropy(results):
    if len(results) < 20: return None
    window = results[:20]
    p_tai = window.count("Tài") / 20
    if p_tai in (0, 1): return ("Tài" if p_tai > 0.5 else "Xỉu", 9, "entropy_zero")
    entropy = _entropy_bits(window)
    if entropy < 0.6: return ("Tài" if p_tai > 0.5 else "Xỉu", 10, "entropy_low")
    return None


def algo_simple_enhanced(results):
    if len(results) < 3: return None
    r0 = results[0]; streak = 1
    for r in results[1:]:
        if r == r0: streak += 1
        else: break
    if streak == 1:
        if len(results) >= 3 and results[1] == results[2]:
            return (_opp(r0), 8, "simple_break_pair")
        return (r0, 8, "simple_continue")
    if streak == 2: return (r0, 9, "simple_momentum")
    if streak == 3: return (_opp(r0), 10, "simple_break_3")
    if streak >= 4: return (_opp(r0), 12, "simple_break_4")
    return None


def algo_runlength_compare(results):
    n = len(results)
    if n < 20: return None
    groups = get_groups(results)
    if len(groups) < 4: return None
    cur = groups[0]; past = groups[1:min(20, len(groups))]
    if not past: return None
    avg = sum(past) / len(past)
    if cur > avg * 1.6 and cur >= 3: return (_opp(results[0]), 12, "rl_break")
    if cur < 1.5 and avg >= 2.5: return (results[0], 8, "rl_continue")
    return None


def algo_pressure_pullback(results):
    n = len(results)
    if n < 18: return None
    w = results[:18]
    tc = w.count("Tài")
    if tc >= 15: return ("Xỉu", 13, "pressure_tai")
    if tc <= 3:  return ("Tài", 13, "pressure_xiu")
    return None


def algo_consecutive_pattern(results):
    if len(results) < 25: return None
    key = tuple(results[:3])
    tai_next = xiu_next = 0
    for i in range(len(results[:60]) - 3):
        if tuple(results[i:i+3]) == key:
            nxt = results[i+3]
            if nxt == "Tài": tai_next += 1
            else: xiu_next += 1
    tot = tai_next + xiu_next
    if tot < 3: return None
    p = tai_next / tot
    if   p >= 0.68: return ("Tài", 12, "pattern3")
    elif p <= 0.32: return ("Xỉu", 12, "pattern3")
    return None


def algo_mean_reversion(results):
    n = len(results)
    if n < 30: return None
    w = results[:30]
    tc = w.count("Tài")
    if tc >= 22: return ("Xỉu", 12, "reversion_tai")
    if tc <= 8:  return ("Tài", 12, "reversion_xiu")
    return None


def algo_volatility_break(results):
    n = len(results)
    if n < 12: return None
    window = results[:12]
    changes = sum(1 for i in range(len(window)-1) if window[i] != window[i+1])
    rate = changes / (len(window) - 1)
    if rate >= 0.85:
        return (results[1], 10, "vol_high_break")
    if rate <= 0.15:
        return (results[0], 11, "vol_low_continue")
    return None


def algo_streak_probability(results):
    n = len(results)
    if n < 25: return None
    streak = _count_streak(results)
    if streak < 3: return None
    current = results[0]
    breaks = totals = 0
    i = 0
    while i < len(results) - streak - 1:
        j = i
        cnt = 1
        while j + 1 < len(results) and results[j] == results[j+1]:
            cnt += 1; j += 1
        if cnt >= streak and results[i] == current:
            totals += 1
            if j + 1 < len(results) and results[j+1] != current:
                breaks += 1
        i = j + 1
    if totals < 3: return None
    p_break = breaks / totals
    if p_break >= 0.6: return (_opp(current), 13, "streak_break_prob")
    if p_break <= 0.4: return (current, 10, "streak_cont_prob")
    return None


# =====================================================================
# MASTER ANALYZER
# =====================================================================
def analyze_full_bridge(results):
    if len(results) < 3: return []
    signals = []
    groups = get_groups(results)

    signals.extend(algo_bet_break(results, groups))

    pdb = algo_pattern_lookup(results)
    if pdb: signals.append(pdb)

    alt = algo_alternation(results)
    if alt: signals.append(alt)

    for fn in (algo_recency_momentum, algo_weighted_trend, algo_markov1, algo_markov2,
               algo_multi_window, algo_hot_tilt, algo_tilt_ratio,
               algo_entropy, algo_simple_enhanced, algo_runlength_compare,
               algo_pressure_pullback, algo_consecutive_pattern,
               algo_mean_reversion, algo_volatility_break, algo_streak_probability):
        s = fn(results)
        if s: signals.append(s)

    return signals


# =====================================================================
# BASE PREDICTOR
# =====================================================================
def full_bridge_predict(results):
    if not results: return "Tài", 50
    if len(results) == 1: return _opp(results[0]), 52
    if len(results) == 2:
        if results[0] == results[1]: return _opp(results[0]), 55
        return results[1], 52
    signals = analyze_full_bridge(results)
    if not signals: return results[0], 52
    st = sx = 0.0
    for p, w, name in signals:
        if p == "Tài": st += w
        else: sx += w
    total = st + sx
    if total == 0: return results[0], 50
    ratio = max(st, sx) / total
    evidence = min(1.0, total / 40.0)
    conf = int(max(50, min(92, 50 + ((ratio - 0.5) * 100) * evidence)))
    if st > sx: return "Tài", conf
    if sx > st: return "Xỉu", conf
    return _opp(results[0]), 50


# =====================================================================
# ✅ BOOTSTRAP PREDICTOR - Dự đoán ngay sau reset (không cần 3 phiên)
# =====================================================================
def bootstrap_predict(results):
    """
    Khi ít dữ liệu (<3 phiên), dùng logic đặc biệt để vẫn đưa ra dự đoán
    thay vì trả về "-".
    """
    n = len(results)
    if n == 0:
        # Không có phiên nào → dự đoán mặc định 50/50, ưu tiên Tài
        return "Tài", 50, "bootstrap_empty"
    if n == 1:
        # 1 phiên → đảo chiều (xác suất break cao)
        return _opp(results[0]), 55, "bootstrap_1_opp"
    if n == 2:
        if results[0] == results[1]:
            # 2 phiên giống nhau → đảo chiều
            return _opp(results[0]), 58, "bootstrap_2_opp"
        else:
            # 2 phiên khác nhau → theo phiên gần nhất (momentum)
            return results[0], 54, "bootstrap_2_momentum"
    # ≥3 phiên → chạy thuật toán đầy đủ
    pred, conf, detail = full_bridge_predict_adaptive(results)
    return pred, conf, detail


# =====================================================================
# ADAPTIVE PREDICTOR
# =====================================================================
def full_bridge_predict_adaptive(results, replay_n=100):
    n = len(results)
    if n == 0: return "Tài", 50, []
    if n == 1: return _opp(results[0]), 55, []
    if n == 2:
        if results[0] == results[1]: return _opp(results[0]), 58, []
        return results[0], 54, []

    signal_stats = defaultdict(lambda: {'wins': 0.0, 'total': 0.0})
    replay_count = min(replay_n, n - 3)
    for j in range(replay_count):
        history = results[j + 1:]; actual = results[j]
        decay = math.exp(-j / max(replay_count / 2.0, 1.0))
        for pred_sig, weight, name in analyze_full_bridge(history):
            signal_stats[name]['total'] += decay
            if pred_sig == actual:
                signal_stats[name]['wins'] += decay

    signals = analyze_full_bridge(results)
    if not signals:
        return results[0], 52, []

    regime = _regime(results)

    st = sx = 0.0; detail = []
    has_pattern_hit = False
    for pred_sig, weight, name in signals:
        if name == "pattern_db": has_pattern_hit = True
        s = signal_stats[name]
        if s['total'] >= 3:
            alpha = 2.0
            acc = (s['wins'] + alpha * 0.5) / (s['total'] + alpha)
            samples = int(s['total'])
        else:
            acc, samples = 0.5, int(s['total'])

        accuracy_mult = 0.4 + max(0.0, min(1.9, (acc - 0.3) * 3.0))

        if regime == "streak":
            if "break" in name or "bet_" in name:
                accuracy_mult *= 1.10
        elif regime == "alternation":
            if "alt_" in name or "pattern" in name or "vol_" in name:
                accuracy_mult *= 1.10

        adjusted = weight * accuracy_mult
        if pred_sig == "Tài": st += adjusted
        else: sx += adjusted
        detail.append({'name': name, 'pred': pred_sig, 'base_weight': weight,
                       'accuracy': round(acc, 3), 'samples': samples,
                       'adjusted_weight': round(adjusted, 2)})

    total = st + sx
    if total == 0:
        return results[0], 50, detail
    if st > sx: pred, ratio = "Tài", st / total
    elif sx > st: pred, ratio = "Xỉu", sx / total
    else: return _opp(results[0]), 50, detail

    base_conf = 50 + (ratio - 0.5) * 100
    evidence = min(1.0, total / 50.0)
    conf = 50 + (base_conf - 50) * evidence

    if ratio >= 0.72: conf += 6
    if ratio >= 0.80 and len(signals) >= 5: conf += 6
    if len(signals) >= 8 and ratio >= 0.65: conf += 3
    if len(signals) >= 10 and ratio >= 0.60: conf += 2
    if len(signals) < 4: conf -= 5

    if has_pattern_hit:
        pdb_pred = algo_pattern_lookup(results)[0]
        if pdb_pred == pred:
            conf += 8
        else:
            conf -= 5

    return pred, int(max(50, min(95, conf))), detail


# =====================================================================
# PREDICTION CACHE
# =====================================================================
def get_prediction_for(valid, i):
    key = valid[i]['phien']
    if key in PREDICTION_CACHE:
        return PREDICTION_CACHE[key]
    hist = [v['result'] for v in valid[:i]][::-1]
    # ✅ Dùng bootstrap để luôn có dự đoán, kể cả khi < 3 phiên
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
def compute_loss_streak(valid, max_scan=20):
    if len(valid) < 3: return 0
    streak = 0
    end = len(valid) - 1
    start = max(0, end - max_scan)
    for i in range(end, start - 1, -1):
        pred, conf, is_correct = get_prediction_for(valid, i)
        if is_correct is None: break
        if is_correct: break
        streak += 1
    return streak


# =====================================================================
# AUTO-RECOVERY
# =====================================================================
def apply_recovery(base_pred, conf, loss_streak):
    if loss_streak < 2:
        return base_pred, conf, False, None
    if loss_streak >= 5:
        return _opp(base_pred), min(96, conf + 15), True, "flip_5plus"
    if loss_streak == 4:
        return _opp(base_pred), min(95, conf + 12), True, "flip_4"
    if loss_streak == 3:
        return _opp(base_pred), min(94, conf + 10), True, "flip_3"
    if conf < 75:
        return _opp(base_pred), min(93, conf + 6), True, "flip_2"
    return base_pred, max(50, conf - 12), False, "penalty_2"


# =====================================================================
# ROUTES
# =====================================================================
@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)


@app.route('/api/history')
def history():
    global RESET_ID
    try:
        raw_rows, success = fetch_sessions()
        if not success and not HISTORY_CACHE:
            return jsonify({'error': 'Không thể kết nối API'}), 500
        for row in raw_rows: HISTORY_CACHE[row['phien']] = row
        for k in [k for k in HISTORY_CACHE if k < RESET_ID]: del HISTORY_CACHE[k]
        valid = sorted(HISTORY_CACHE.values(), key=lambda x: x['phien'])
        if not valid:
            return jsonify({'rows': [], 'stats': {'total': 0, 'correct': 0}})

        loss_streak = compute_loss_streak(valid)

        out = []; correct_count = 0; total_past = 0
        latest_session = valid[-1]
        next_phien = latest_session['phien'] + 1
        history_for_next = [v['result'] for v in valid][::-1]

        # ✅ Dùng bootstrap - luôn có dự đoán ngay, không đợi 3 phiên
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
            if i < 0: break
            current_session = valid[i]
            pred, conf, is_correct = get_prediction_for(valid, i)
            if is_correct is not None: total_past += 1
            if is_correct: correct_count += 1
            out.append({'phien': current_session['phien'], 'is_next': False,
                        'prediction': pred, 'result': current_session['result'],
                        'dice': current_session['dice'], 'is_correct': is_correct,
                        'conf': conf, 'recovery_flipped': False})

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
        for row in raw_rows: HISTORY_CACHE[row['phien']] = row
        for k in [k for k in HISTORY_CACHE if k < RESET_ID]: del HISTORY_CACHE[k]
        valid = sorted(HISTORY_CACHE.values(), key=lambda x: x['phien'])
        if not valid: return jsonify({'error': 'Chưa có dữ liệu'}), 400
        results = [v['result'] for v in valid][::-1]

        loss_streak = compute_loss_streak(valid)
        # ✅ Bootstrap predictor
        base_pred, base_conf, detail = bootstrap_predict(results)
        if not isinstance(detail, list): detail = []
        pred, conf, flipped, reason = apply_recovery(base_pred, base_conf, loss_streak)

        pattern_hit = algo_pattern_lookup(results)
        pattern_key = tx_encode(results, 13)

        return jsonify({
            'last_phien': valid[-1]['phien'], 'next_phien': valid[-1]['phien'] + 1,
            'last_result': valid[-1]['result'], 'last_dice': valid[-1]['dice'],
            'prediction': pred, 'confidence': conf,
            'base_prediction': base_pred, 'base_confidence': base_conf,
            'recovery_flipped': flipped, 'recovery_reason': reason,
            'loss_streak': loss_streak,
            'total_sessions': len(valid),
            'pattern_key': pattern_key,
            'pattern_hit': pattern_hit[0] if pattern_hit else None,
            'signals': detail,
            'tai_score': round(sum(s['adjusted_weight'] for s in detail if s['pred'] == 'Tài'), 2),
            'xiu_score': round(sum(s['adjusted_weight'] for s in detail if s['pred'] == 'Xỉu'), 2),
            'signal_count': len(detail),
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/lc79')
def lc79():
    global RESET_ID
    try:
        raw_rows, success = fetch_sessions()
        if not success and not HISTORY_CACHE:
            return jsonify({'error': 'Không thể kết nối API'}), 500
        for row in raw_rows: HISTORY_CACHE[row['phien']] = row
        for k in [k for k in HISTORY_CACHE if k < RESET_ID]: del HISTORY_CACHE[k]
        valid = sorted(HISTORY_CACHE.values(), key=lambda x: x['phien'])
        if not valid: return jsonify({'error': 'Chưa có dữ liệu'}), 400
        results = [v['result'] for v in valid][::-1]

        loss_streak = compute_loss_streak(valid)
        base_pred, base_conf, detail = bootstrap_predict(results)
        if not isinstance(detail, list): detail = []
        pred, conf, flipped, reason = apply_recovery(base_pred, base_conf, loss_streak)

        tai_score = sum(s['adjusted_weight'] for s in detail if s['pred'] == 'Tài')
        xiu_score = sum(s['adjusted_weight'] for s in detail if s['pred'] == 'Xỉu')
        total_sig = tai_score + xiu_score
        if total_sig > 0:
            ratio = max(tai_score, xiu_score) / total_sig
            base_conf_f = 50 + (ratio - 0.5) * 100
            evidence = min(1.0, total_sig / 50.0)
            conf_float = max(50.0, min(95.0, 50 + (base_conf_f - 50) * evidence))
        else:
            conf_float = float(base_conf)
        conf_str = f"{conf_float:.1f}".replace(".", ",") + "%"
        groups = get_groups(results)
        lich_su = "-" + "-".join(str(g) for g in groups[:4])
        last = valid[-1]; next_phien = last['phien'] + 1

        return jsonify({
            'Id': 'le minh v28', 'Phien': str(next_phien),
            'Ket_qua': last['result'].lower(), 'Lich_su': lich_su,
            'Du_doan': pred, 'Do_tin_cay': conf_str,
            'Base_du_doan': base_pred if flipped else None,
            'Auto_flip': flipped,
            'Chuoi_thua': loss_streak,
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/betvipmd5')
def lc79md5():
    global RESET_ID
    try:
        raw_rows, success = fetch_sessions()
        if not success and not HISTORY_CACHE:
            return jsonify({'error': 'Không thể kết nối API'}), 500
        for row in raw_rows: HISTORY_CACHE[row['phien']] = row
        for k in [k for k in HISTORY_CACHE if k < RESET_ID]: del HISTORY_CACHE[k]
        valid = sorted(HISTORY_CACHE.values(), key=lambda x: x['phien'])
        if not valid: return jsonify({'error': 'Chưa có dữ liệu'}), 400

        results = [v['result'] for v in valid][::-1]

        loss_streak = compute_loss_streak(valid)
        base_pred, base_conf, detail = bootstrap_predict(results)
        if not isinstance(detail, list): detail = []
        pred, conf, flipped, reason = apply_recovery(base_pred, base_conf, loss_streak)

        tai_score = sum(s['adjusted_weight'] for s in detail if s['pred'] == 'Tài')
        xiu_score = sum(s['adjusted_weight'] for s in detail if s['pred'] == 'Xỉu')
        total_sig = tai_score + xiu_score
        if total_sig > 0:
            ratio = max(tai_score, xiu_score) / total_sig
            base_conf_f = 50 + (ratio - 0.5) * 100
            evidence = min(1.0, total_sig / 50.0)
            conf_float = max(50.0, min(95.0, 50 + (base_conf_f - 50) * evidence))
        else:
            conf_float = float(base_conf)
        conf_str = f"{conf_float:.1f}".replace(".", ",") + "%"

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
    """
    ✅ Reset thông minh: vẫn giữ 1 phiên mồi + bootstrap để dự đoán NGAY
    """
    global RESET_ID, HISTORY_CACHE, PREDICTION_CACHE, BOOTSTRAP_SESSIONS
    try:
        raw_rows, success = fetch_sessions()
        if success and raw_rows:
            valid = sorted(raw_rows, key=lambda x: x['phien'])
            if valid:
                RESET_ID = valid[-1]['phien']
                # ✅ Lưu 1 phiên cuối làm mồi bootstrap
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
