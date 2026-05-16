// ==========================================
// Anti-Gravity Dashboard — script.js
// ==========================================

const AI_TECH_STOCKS = [
    "NVDA", "AMD", "TSM", "AVGO", "MU", "QCOM", "ARM", "MRVL", "AMAT", "LRCX", "KLAC", "TXN", "INTC", "MPWR",
    "MSFT", "GOOGL", "AMZN", "META", "AAPL", "IBM", "PLTR", "CRM", "ORCL", "NOW", "SNOW", "DDOG", "MDB", "ADBE", "INTU", "PATH", "APP",
    "NET", "CRWD", "PANW", "FTNT", "ZS", "OKTA",
    "SMCI", "DELL", "HPE", "ANET", "PSTG", "NTAP",
    "VRT", "ETN", "PWR", "CEG", "NEE", "GE", "DUK",
    "TSLA", "UBER", "SYM"
];

let selectedStock = "NVDA";
let currentTimeframe = "3m";
let currentBtSignal = "td9_sell";
let globalState = { td9: {}, ma: {}, mtf: {}, alerts: [], engineOnline: false, lastUpdated: null, qqqStatus: null };
let backtestData = {};
let intradayRS = []; // sorted array of {ticker, ret, label}

// ==========================================
// 1. Navigation
// ==========================================
const viewTitles = {
    'dashboard': '市場監控中心',
    'heatmap': 'TD9 陣列熱力圖',
    'alerts-history': '系統警報紀錄',
    'settings': '掃描器參數設定',
};

document.querySelectorAll('.nav-link').forEach(link => {
    link.addEventListener('click', (e) => {
        e.preventDefault();
        const target = link.getAttribute('data-view');

        // Update nav
        document.querySelectorAll('.nav-link').forEach(l => l.classList.remove('active'));
        link.classList.add('active');

        // Update view
        document.querySelectorAll('.view-panel').forEach(v => v.classList.remove('active'));
        const panel = document.getElementById(`view-${target}`);
        if (panel) panel.classList.add('active');

        // Update title
        document.getElementById('view-title').textContent = viewTitles[target] || '';

        // Render view-specific content
        if (target === 'heatmap') renderHeatmap();
        if (target === 'alerts-history') renderAlertsHistory();
    });
});

// ==========================================
// 2. Chart Init
// ==========================================
const chartDom = document.getElementById('tvchart');
const qqqChartDom = document.getElementById('qqqchart');
let chart, candleSeries, volumeSeries;
let qqqChart, qqqSeries, qqqVolumeSeries;
let qqqMaLines = { ma5: null, ma10: null, ma20: null };

try {
    const utc8DateFormatter = (timestamp) => {
        const d = new Date((timestamp + 8 * 3600) * 1000);
        return `${d.getUTCFullYear()}-${String(d.getUTCMonth()+1).padStart(2,'0')}-${String(d.getUTCDate()).padStart(2,'0')} ${String(d.getUTCHours()).padStart(2, '0')}:${String(d.getUTCMinutes()).padStart(2, '0')}`;
    };

    const utc8TickFormatter = (time, tickMarkType, locale) => {
        const d = new Date((time + 8 * 3600) * 1000);
        return `${String(d.getUTCMonth()+1).padStart(2,'0')}/${String(d.getUTCDate()).padStart(2,'0')} ${String(d.getUTCHours()).padStart(2, '0')}:${String(d.getUTCMinutes()).padStart(2, '0')}`;
    };

    chart = LightweightCharts.createChart(chartDom, {
        layout: { background: { type: 'solid', color: 'transparent' }, textColor: '#94a3b8' },
        grid: { vertLines: { color: 'rgba(255,255,255,0.03)' }, horzLines: { color: 'rgba(255,255,255,0.03)' } },
        crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
        rightPriceScale: { borderColor: 'rgba(255,255,255,0.08)' },
        timeScale: { borderColor: 'rgba(255,255,255,0.08)', timeVisible: true, secondsVisible: false, tickMarkFormatter: utc8TickFormatter },
        localization: { timeFormatter: utc8DateFormatter }
    });
    candleSeries = chart.addCandlestickSeries({
        upColor: '#ef4444', downColor: '#10b981',
        borderDownColor: '#10b981', borderUpColor: '#ef4444',
        wickDownColor: '#10b981', wickUpColor: '#ef4444',
    });
    candleSeries.priceScale().applyOptions({
        scaleMargins: { top: 0.02, bottom: 0.25 }
    });
    volumeSeries = chart.addHistogramSeries({
        color: '#26a69a', priceFormat: { type: 'volume' }, priceScaleId: '',
    });
    volumeSeries.priceScale().applyOptions({ scaleMargins: { top: 0.78, bottom: 0 } });

    qqqChart = LightweightCharts.createChart(qqqChartDom, {
        layout: { background: { type: 'solid', color: 'transparent' }, textColor: '#94a3b8' },
        grid: { vertLines: { color: 'rgba(255,255,255,0.03)' }, horzLines: { color: 'rgba(255,255,255,0.03)' } },
        crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
        rightPriceScale: { borderColor: 'rgba(255,255,255,0.08)' },
        timeScale: { borderColor: 'rgba(255,255,255,0.08)', timeVisible: true, secondsVisible: false, tickMarkFormatter: utc8TickFormatter },
        localization: { timeFormatter: utc8DateFormatter }
    });
    qqqSeries = qqqChart.addCandlestickSeries({
        upColor: '#ef4444', downColor: '#10b981',
        borderDownColor: '#10b981', borderUpColor: '#ef4444',
        wickDownColor: '#10b981', wickUpColor: '#ef4444',
    });
    qqqSeries.priceScale().applyOptions({
        scaleMargins: { top: 0.02, bottom: 0.25 }
    });
    qqqVolumeSeries = qqqChart.addHistogramSeries({
        color: '#26a69a', priceFormat: { type: 'volume' }, priceScaleId: '',
    });
    qqqVolumeSeries.priceScale().applyOptions({ scaleMargins: { top: 0.78, bottom: 0 } });

    // Sync time scales
    chart.timeScale().subscribeVisibleLogicalRangeChange(range => {
        if (range) qqqChart.timeScale().setVisibleLogicalRange(range);
    });
    qqqChart.timeScale().subscribeVisibleLogicalRangeChange(range => {
        if (range) chart.timeScale().setVisibleLogicalRange(range);
    });

    new ResizeObserver(entries => {
        if (!entries.length) return;
        const r = entries[0].contentRect;
        chart.applyOptions({ height: r.height, width: r.width });
    }).observe(chartDom);
    new ResizeObserver(entries => {
        if (!entries.length) return;
        const r = entries[0].contentRect;
        qqqChart.applyOptions({ height: r.height, width: r.width });
    }).observe(qqqChartDom);
} catch (e) { console.warn('Chart init failed:', e); }

// ==========================================
// 3. Chart Data Loading (Real from JSON)
// ==========================================
async function loadChartData(ticker, tf) {
    const suffix = tf === '1d' ? 'daily' : '3m';
    let dataLoaded = false;
    let stockData = null;
    let qqqData = null;
    
    // Preserve zoom: save current visible range before loading new data
    let savedRange = null;
    try { savedRange = chart.timeScale().getVisibleLogicalRange(); } catch(e) {}

    try {
        const res = await fetch(`charts/${ticker}_${suffix}.json?t=${Date.now()}`);
        if (res.ok) {
            const data = await res.json();
            if (data && data.length > 0) {
                stockData = data;
                candleSeries.setData(data);
                const volumeData = data.map(d => ({
                    time: d.time,
                    value: d.volume || 0,
                    color: d.close >= d.open ? '#ef4444' : '#10b981'
                }));
                volumeSeries.setData(volumeData);
                dataLoaded = true;
            }
        }
    } catch (e) { console.warn(`Chart fetch failed for ${ticker}:`, e); }

    if (!dataLoaded) {
        stockData = generateDummyData();
        candleSeries.setData(stockData);
    }

    try {
        const resQqq = await fetch(`charts/QQQ_${suffix}.json?t=${Date.now()}`);
        if (resQqq.ok) {
            const dataQqq = await resQqq.json();
            if (dataQqq && dataQqq.length > 0) {
                qqqData = dataQqq;
                qqqSeries.setData(dataQqq);
                const qqqVolumeData = dataQqq.map(d => ({
                    time: d.time,
                    value: d.volume || 0,
                    color: d.close >= d.open ? '#ef4444' : '#10b981'
                }));
                qqqVolumeSeries.setData(qqqVolumeData);
            }
        }
    } catch (e) { console.warn('QQQ chart fetch failed:', e); }

    // Add Markers for 3m timeframe
    if (tf === '3m' && stockData && qqqData) {
        const { stockMarkers, qqqMarkers } = calculateMarkers(stockData, qqqData);
        candleSeries.setMarkers(stockMarkers);
        qqqSeries.setMarkers(qqqMarkers);
        calculateVolumeProfile(stockData);
    } else {
        candleSeries.setMarkers([]);
        qqqSeries.setMarkers([]);
        clearVPLines();
    }

    // Restore zoom: if we had a saved range (user was zoomed in), restore it
    if (savedRange && savedRange.from !== undefined) {
        try {
            chart.timeScale().setVisibleLogicalRange(savedRange);
        } catch(e) {
            chart.timeScale().fitContent();
        }
    } else {
        chart.timeScale().fitContent();
    }
}

let vpLines = [];

function clearVPLines() {
    vpLines.forEach(line => {
        try { candleSeries.removePriceLine(line); } catch(e) {}
    });
    vpLines = [];
}

function calculateVolumeProfile(data) {
    clearVPLines();
    if (!data || data.length === 0) return;
    
    // Split data into days based on > 12 hours gap
    let days = [];
    let currentDay = [];
    let lastTime = 0;
    
    for (let i = 0; i < data.length; i++) {
        if (data[i].time - lastTime > 43200 && lastTime !== 0) {
            if (currentDay.length > 0) days.push(currentDay);
            currentDay = [];
        }
        currentDay.push(data[i]);
        lastTime = data[i].time;
    }
    if (currentDay.length > 0) days.push(currentDay);
    
    function calcProfileForDay(dayData, isToday) {
        if (!dayData || dayData.length === 0) return;
        let high = -Infinity, low = Infinity, totalVol = 0;
        
        dayData.forEach(d => {
            if (d.high > high) high = d.high;
            if (d.low < low) low = d.low;
            totalVol += (d.volume || 0);
        });
        
        if (totalVol === 0 || high === low) return;
        
        const BINS = 100;
        const binSize = (high - low) / BINS;
        let profile = new Array(BINS).fill(0);
        
        dayData.forEach(d => {
            let topBin = Math.floor((d.high - low) / binSize);
            let botBin = Math.floor((d.low - low) / binSize);
            if (topBin >= BINS) topBin = BINS - 1;
            if (botBin < 0) botBin = 0;
            
            let binsCovered = topBin - botBin + 1;
            let volPerBin = (d.volume || 0) / binsCovered;
            
            for (let b = botBin; b <= topBin; b++) {
                profile[b] += volPerBin;
            }
        });
        
        let maxVol = -1, pocIdx = 0;
        for (let i = 0; i < BINS; i++) {
            if (profile[i] > maxVol) {
                maxVol = profile[i];
                pocIdx = i;
            }
        }
        
        let pocPrice = low + (pocIdx + 0.5) * binSize;
        
        if (isToday) {
            vpLines.push(candleSeries.createPriceLine({ price: pocPrice, color: '#f97316', lineWidth: 2, lineStyle: 0, axisLabelVisible: true, title: 'POC (最大量)' }));
        } else {
            // Yesterday's lines (thinner, more transparent)
            vpLines.push(candleSeries.createPriceLine({ price: pocPrice, color: 'rgba(249, 115, 22, 0.4)', lineWidth: 1, lineStyle: 0, axisLabelVisible: true, title: 'Y-POC' }));
        }
    }

    if (days.length >= 2) {
        calcProfileForDay(days[days.length - 2], false); // Yesterday
    }
    if (days.length >= 1) {
        calcProfileForDay(days[days.length - 1], true);  // Today
    }
}

function calculateMarkers(stockData, qqqData) {
    let qqqMarkers = [];
    let stockMarkers = [];
    
    let qIndex = 0;
    let sHod = -Infinity, sLod = Infinity;
    let qHod = -Infinity, qLod = Infinity;
    let lastTime = 0;
    
    for (let i = 0; i < stockData.length; i++) {
        let sCandle = stockData[i];
        
        while (qIndex < qqqData.length && qqqData[qIndex].time < sCandle.time) qIndex++;
        if (qIndex >= qqqData.length || qqqData[qIndex].time !== sCandle.time) continue;
        
        let qCandle = qqqData[qIndex];
        
        // Reset daily on gaps > 12 hours (43200 seconds)
        if (sCandle.time - lastTime > 43200) {
            qHod = qCandle.high;
            qLod = qCandle.low;
            sHod = sCandle.high;
            sLod = sCandle.low;
            lastTime = sCandle.time;
            continue; 
        }
        lastTime = sCandle.time;
        
        let isQNewHigh = qCandle.high > qHod;
        let isQNewLow = qCandle.low < qLod;
        
        if (isQNewHigh) {
            qHod = qCandle.high;
            qqqMarkers.push({ time: qCandle.time, position: 'aboveBar', color: '#ef4444', shape: 'arrowDown', text: 'HOD' });
            
            let isSNewHigh = sCandle.high > sHod;
            if (isSNewHigh) {
                stockMarkers.push({ time: sCandle.time, position: 'aboveBar', color: '#ef4444', shape: 'arrowDown', text: '強:過高' });
            } else {
                stockMarkers.push({ time: sCandle.time, position: 'aboveBar', color: '#94a3b8', shape: 'arrowDown', text: '弱:未過高' });
            }
        }
        
        if (isQNewLow) {
            qLod = qCandle.low;
            qqqMarkers.push({ time: qCandle.time, position: 'belowBar', color: '#10b981', shape: 'arrowUp', text: 'LOD' });
            
            let isSNewLow = sCandle.low < sLod;
            if (isSNewLow) {
                stockMarkers.push({ time: sCandle.time, position: 'belowBar', color: '#10b981', shape: 'arrowUp', text: '弱:破底' });
            } else {
                stockMarkers.push({ time: sCandle.time, position: 'belowBar', color: '#f59e0b', shape: 'arrowUp', text: '強:沒破底' });
            }
        }
        
        sHod = Math.max(sHod, sCandle.high);
        sLod = Math.min(sLod, sCandle.low);
    }
    
    return { stockMarkers, qqqMarkers };
}

function generateDummyData() {
    const data = [];
    let t = new Date('2026-05-02T09:30:00Z').getTime() / 1000;
    let p = 130;
    for (let i = 0; i < 80; i++) {
        const o = p + (Math.random() - 0.48) * 2;
        const c = o + (Math.random() - 0.48) * 3;
        data.push({ time: t, open: o, high: Math.max(o, c) + Math.random() * 1.5, low: Math.min(o, c) - Math.random() * 1.5, close: c });
        t += 180; p = c;
    }
    return data;
}

// Stock selector
const stockSelector = document.getElementById('stock-selector');
AI_TECH_STOCKS.forEach(t => {
    const opt = document.createElement('option');
    opt.value = t; opt.textContent = t;
    if (t === selectedStock) opt.selected = true;
    stockSelector.appendChild(opt);
});
stockSelector.addEventListener('change', e => {
    selectedStock = e.target.value;
    updateChartTitle();
    highlightSelectedNode();
    loadChartData(selectedStock, currentTimeframe);
    renderBacktest();
    renderFundamentals();
    renderInstitutional();
});

// Timeframe buttons
document.querySelectorAll('.btn-tf').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('.btn-tf').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        currentTimeframe = btn.getAttribute('data-tf');
        loadChartData(selectedStock, currentTimeframe);
    });
});

function updateChartTitle() {
    let mtfTrend15 = globalState.mtf[selectedStock] ? globalState.mtf[selectedStock]['15m'] : '震盪';
    let mtfTrend60 = globalState.mtf[selectedStock] ? globalState.mtf[selectedStock]['60m'] : '震盪';
    let getDot = (trend) => trend === '多頭' ? '🔴' : trend === '空頭' ? '🟢' : '⚪';
    let dotsHtml = `<span style="font-size:12px; margin-left: 10px; opacity: 0.8;" title="15分K與60分K大環境趨勢">[15m:${getDot(mtfTrend15)} 60m:${getDot(mtfTrend60)}]</span>`;
    
    document.getElementById('chart-title').innerHTML =
        `個股分析圖表 — <span class="highlight">${selectedStock}</span>${dotsHtml}`;
}

function changeChartStock(ticker) {
    selectedStock = ticker;
    stockSelector.value = ticker;
    updateChartTitle();
    highlightSelectedNode();
    loadChartData(selectedStock, currentTimeframe);
    renderBacktest();
    renderFundamentals();
    renderInstitutional();
    renderStrengthRanking();
}

// ==========================================
// Quick Search — 即時搜尋任意股票
// ==========================================
async function quickSearch(ticker) {
    ticker = ticker.toUpperCase().trim();
    if (!ticker) return;

    var searchBtn = document.getElementById('btn-quick-search');
    var searchInput = document.getElementById('quick-search');
    searchBtn.disabled = true;
    searchBtn.textContent = '⏳';
    searchInput.style.borderColor = 'rgba(59,130,246,0.6)';

    try {
        var res = await fetch('/api/quick_chart', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ticker: ticker })
        });
        var data = await res.json();

        if (data.status === 'ok' && (data.has_3m || data.has_daily)) {
            // Add to dropdown if not already there
            var exists = false;
            for (var i = 0; i < stockSelector.options.length; i++) {
                if (stockSelector.options[i].value === ticker) { exists = true; break; }
            }
            if (!exists) {
                var opt = document.createElement('option');
                opt.value = ticker;
                opt.textContent = ticker + ' ★';
                stockSelector.appendChild(opt);
            }
            // Switch to it
            changeChartStock(ticker);
            searchInput.style.borderColor = 'rgba(16,185,129,0.6)';
            setTimeout(function() { searchInput.style.borderColor = ''; }, 2000);
        } else {
            searchInput.style.borderColor = 'rgba(239,68,68,0.6)';
            searchInput.value = '找不到 ' + ticker;
            setTimeout(function() { searchInput.value = ''; searchInput.style.borderColor = ''; }, 2000);
        }
    } catch(e) {
        searchInput.style.borderColor = 'rgba(239,68,68,0.6)';
        setTimeout(function() { searchInput.style.borderColor = ''; }, 2000);
    }

    searchBtn.disabled = false;
    searchBtn.textContent = '🔍';
}

document.getElementById('btn-quick-search').addEventListener('click', function() {
    quickSearch(document.getElementById('quick-search').value);
});
document.getElementById('quick-search').addEventListener('keydown', function(e) {
    if (e.key === 'Enter') quickSearch(this.value);
});

// ==========================================
// Watchlist Management — 追蹤清單管理
// ==========================================
document.getElementById('btn-add-watchlist').addEventListener('click', async function() {
    var ticker = selectedStock || document.getElementById('quick-search').value.toUpperCase().trim();
    if (!ticker) return;

    try {
        var res = await fetch('/api/watchlist', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'add', ticker: ticker })
        });
        var data = await res.json();
        if (data.status === 'ok') {
            this.textContent = '✓';
            setTimeout(() => { this.textContent = '+'; }, 1500);
            // Refresh stock list
            refreshStockList(data.stocks);
        }
    } catch(e) {}
});

document.getElementById('btn-remove-watchlist').addEventListener('click', async function() {
    var ticker = selectedStock;
    if (!ticker) return;

    try {
        var res = await fetch('/api/watchlist', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'remove', ticker: ticker })
        });
        var data = await res.json();
        if (data.status === 'ok') {
            this.textContent = '✓';
            setTimeout(() => { this.textContent = '−'; }, 1500);
            refreshStockList(data.stocks);
        }
    } catch(e) {}
});

function refreshStockList(stocks) {
    // Update the global list
    AI_TECH_STOCKS.length = 0;
    stocks.forEach(function(s) { AI_TECH_STOCKS.push(s); });

    // Rebuild dropdown
    stockSelector.innerHTML = '';
    AI_TECH_STOCKS.forEach(function(t) {
        var opt = document.createElement('option');
        opt.value = t; opt.textContent = t;
        if (t === selectedStock) opt.selected = true;
        stockSelector.appendChild(opt);
    });

    // Rebuild stock grid
    renderStockGrid();
    highlightSelectedNode();
}

// ==========================================
// 4. Data Polling
// ==========================================
async function fetchJSON(file) {
    try {
        const res = await fetch(`${file}?t=${Date.now()}`);
        if (!res.ok) return null;
        return await res.json();
    } catch (e) { return null; }
}

async function pollData() {
    var fetches = [fetchJSON('td9_data.json'), fetchJSON('ma_data.json'), fetchJSON('mtf_trend.json')];
    // Only poll live scanner files if they've been found before
    if (!pollData._liveUnavailable) {
        fetches.push(fetchJSON('hod_data.json'), fetchJSON('orb_data.json'), fetchJSON('live_data.json'));
    } else {
        fetches.push(null, null, null);
    }
    const [td9, ma, mtf, hod, orb, live] = await Promise.all(fetches);

    // If none of the live files exist, stop polling them
    if (!hod && !orb && !live && !pollData._liveUnavailable) {
        pollData._liveCheckCount = (pollData._liveCheckCount || 0) + 1;
        if (pollData._liveCheckCount > 1) pollData._liveUnavailable = true;
    }
    if (hod || orb || live) { pollData._liveUnavailable = false; pollData._liveCheckCount = 0; }

    let dataFound = false;
    if (td9) { globalState.td9 = td9.results || {}; dataFound = true; }
    if (ma) { globalState.ma = ma.results || {}; dataFound = true; }
    if (mtf) { globalState.mtf = mtf || {}; dataFound = true; }

    let newAlerts = [];
    if (hod && hod.alerts) { newAlerts = newAlerts.concat(hod.alerts.map(a => ({ ...a, source: 'HOD' }))); dataFound = true; }
    if (orb && orb.alerts) { newAlerts = newAlerts.concat(orb.alerts.map(a => ({ ...a, source: 'ORB' }))); dataFound = true; }
    newAlerts.sort((a, b) => (b.time || '').localeCompare(a.time || ''));
    if (newAlerts.length > 0) globalState.alerts = newAlerts;

    // TD9/MA alerts for history view
    if (td9) {
        Object.entries(globalState.td9).forEach(([ticker, val]) => {
            if (val >= 8 || val <= -8) {
                const exists = globalState.alerts.find(a => a.symbol === ticker && a.source === 'TD9');
                if (!exists) {
                    const type = val > 0 ? 'down' : 'up';  // positive TD = rising exhaustion (sell signal)
                    const label = val > 0 ? `上漲竭盡 TD${val}` : `下跌竭盡 TD${Math.abs(val)}`;
                    globalState.alerts.push({ symbol: ticker, type, title: label, desc: '日線級別', time: '09:30', source: 'TD9' });
                }
            }
        });
        Object.entries(globalState.ma).forEach(([ticker, maVal]) => {
            const exists = globalState.alerts.find(a => a.symbol === ticker && a.source === 'MA');
            if (!exists) {
                globalState.alerts.push({ symbol: ticker, type: 'up', title: `回測 ${maVal}`, desc: '200MA之上', time: '09:30', source: 'MA' });
            }
        });
    }

    if (dataFound) {
        globalState.engineOnline = true;
        globalState.lastUpdated = td9?.last_updated || ma?.last_updated || hod?.last_updated || '';
    }

    if (live && live.qqq_status) {
        globalState.qqqStatus = live.qqq_status;
    }

    // Fallback: load qqq_ma.json (generated by generate_chart_data.py)
    if (!globalState.qqqStatus || !globalState.qqqStatus.ma5) {
        const qqqMa = await fetchJSON('qqq_ma.json');
        if (qqqMa) {
            if (!globalState.qqqStatus) globalState.qqqStatus = {};
            globalState.qqqStatus.ma5 = qqqMa.ma5;
            globalState.qqqStatus.ma10 = qqqMa.ma10;
            globalState.qqqStatus.ma20 = qqqMa.ma20;
        }
    }

    renderDashboard();
}

// ==========================================
// 5. Render: Dashboard View
// ==========================================
function renderDashboard() {
    renderEngineStatus();
    renderScannerCounts();
    renderStockGrid();
    renderAlerts();
    highlightSelectedNode();
    renderStrengthRanking();
    
    if (globalState.qqqStatus) {
        updateQQQSupportLines(globalState.qqqStatus);
    }
}

function updateQQQSupportLines(status) {
    if (!status) return;
    
    if (status.ma5) document.getElementById('ma5-val').textContent = status.ma5;
    if (status.ma10) document.getElementById('ma10-val').textContent = status.ma10;
    if (status.ma20) document.getElementById('ma20-val').textContent = status.ma20;
    
    if (status.ma5 && !qqqMaLines.ma5) {
        qqqMaLines.ma5 = qqqSeries.createPriceLine({ price: status.ma5, color: '#f59e0b', lineWidth: 1, lineStyle: 2, title: '5MA', axisLabelVisible: true });
    } else if (status.ma5 && qqqMaLines.ma5) {
        qqqMaLines.ma5.applyOptions({ price: status.ma5 });
    }
    
    if (status.ma10 && !qqqMaLines.ma10) {
        qqqMaLines.ma10 = qqqSeries.createPriceLine({ price: status.ma10, color: '#3b82f6', lineWidth: 1, lineStyle: 2, title: '10MA', axisLabelVisible: true });
    } else if (status.ma10 && qqqMaLines.ma10) {
        qqqMaLines.ma10.applyOptions({ price: status.ma10 });
    }
    
    if (status.ma20 && !qqqMaLines.ma20) {
        qqqMaLines.ma20 = qqqSeries.createPriceLine({ price: status.ma20, color: '#8b5cf6', lineWidth: 1, lineStyle: 2, title: '20MA', axisLabelVisible: true });
    } else if (status.ma20 && qqqMaLines.ma20) {
        qqqMaLines.ma20.applyOptions({ price: status.ma20 });
    }
}

function renderEngineStatus() {
    const el = document.getElementById('engine-status');
    const upEl = document.getElementById('last-update');
    const dot = el.querySelector('.status-dot');
    if (globalState.engineOnline) {
        dot.className = 'status-dot online';
        el.querySelector('span').textContent = 'Python 引擎已連線';
        upEl.textContent = `最後同步: ${globalState.lastUpdated || 'N/A'}`;
    } else {
        dot.className = 'status-dot offline';
        el.querySelector('span').textContent = '等待 Python 引擎...';
        upEl.textContent = '尚未同步';
    }
}

function renderScannerCounts() {
    let buy = 0, sell = 0;
    Object.values(globalState.td9).forEach(v => { if (v >= 8) buy++; else if (v <= -8) sell++; });
    document.querySelector('#count-buy span').textContent = buy;
    document.querySelector('#count-sell span').textContent = sell;
    document.querySelector('#count-ma span').textContent = Object.keys(globalState.ma).length;
    document.querySelector('#count-alerts span').textContent = globalState.alerts.filter(a => a.source === 'HOD' || a.source === 'ORB').length;
}

function renderStockGrid() {
    const grid = document.getElementById('stock-grid');
    grid.innerHTML = '';
    AI_TECH_STOCKS.forEach(ticker => {
        const tdVal = globalState.td9[ticker] || 0;
        const maVal = globalState.ma[ticker];
        const mtfTrend = globalState.mtf[ticker] ? globalState.mtf[ticker]['60m'] : null;
        let cls = 'stock-node', tdText = `TD: ${tdVal}`;

        if (tdVal >= 8) { cls += ' td9-buy'; tdText = `TD: <span class='green'>${tdVal}</span>`; }
        else if (tdVal <= -8) { cls += ' td9-sell'; tdText = `TD: <span class='red'>${Math.abs(tdVal)}</span>`; }
        else if (maVal) { cls += ' ma-touch'; tdText = `回測 <span class='blue'>${maVal}</span>`; }

        let trendDot = '';
        if (mtfTrend === '多頭') trendDot = '<span style="color:#ef4444; font-size:8px;">🔴</span>';
        else if (mtfTrend === '空頭') trendDot = '<span style="color:#10b981; font-size:8px;">🟢</span>';
        else if (mtfTrend === '震盪') trendDot = '<span style="color:#94a3b8; font-size:8px;">⚪</span>';

        const div = document.createElement('div');
        div.className = cls;
        div.setAttribute('data-ticker', ticker);
        div.onclick = () => changeChartStock(ticker);
        div.innerHTML = `<div class="node-symbol">${ticker} ${trendDot}</div><div class="node-td">${tdText}</div>`;
        grid.appendChild(div);
    });
}

function highlightSelectedNode() {
    document.querySelectorAll('.stock-node').forEach(el => {
        el.classList.toggle('selected', el.getAttribute('data-ticker') === selectedStock);
    });
}

function renderAlerts() {
    const feed = document.getElementById('alerts-feed');
    const badge = document.getElementById('alert-total-badge');
    feed.innerHTML = '';

    const realAlerts = globalState.alerts.filter(a => a.source === 'HOD' || a.source === 'ORB');

    if (realAlerts.length === 0) {
        feed.innerHTML = `<div class="empty-state"><span class="empty-icon">📡</span><p>等待掃描器偵測訊號...</p><small>HOD / ORB 策略觸發時自動顯示</small></div>`;
        badge.textContent = '0 筆';
        return;
    }
    badge.textContent = `${realAlerts.length} 筆`;
    realAlerts.slice(0, 15).forEach(a => {
        let icon = a.type === 'up' ? '🛡️' : a.type === 'down' ? '🔴' : '🔥';
        let cardClass = `alert-card ${a.type || 'surge'}`;
        
        let title = a.title || '';
        let mtfTrend = globalState.mtf[a.symbol] ? globalState.mtf[a.symbol]['60m'] : '震盪';
        
        // Counter-trend logic
        let isBuySignal = title.includes('突破') || title.includes('動能') || title.includes('買');
        let isSellSignal = title.includes('破底') || title.includes('賣');
        
        if (isBuySignal && mtfTrend !== '多頭') {
            title = '⚠️[逆勢] ' + title;
            cardClass += ' counter-trend';
        } else if (isSellSignal && mtfTrend !== '空頭') {
            title = '⚠️[逆勢] ' + title;
            cardClass += ' counter-trend';
        }

        const card = document.createElement('div');
        card.className = cardClass;
        card.onclick = () => changeChartStock(a.symbol);
        card.innerHTML = `<div class="alert-icon">${icon}</div><div class="alert-info"><h4>${a.symbol} <span style="font-size:10px;color:#94a3b8">[60m:${mtfTrend}]</span></h4><p>${title} ${a.desc || ''}</p></div><span class="alert-time">${a.time || '--'}</span>`;
        feed.appendChild(card);
    });
}

// ==========================================
// 6. Render: TD9 Heatmap View
// ==========================================
function renderHeatmap() {
    const grid = document.getElementById('heatmap-grid');
    grid.innerHTML = '';

    AI_TECH_STOCKS.forEach(ticker => {
        const tdVal = globalState.td9[ticker] || 0;
        const cell = document.createElement('div');
        cell.className = 'heatmap-cell';
        cell.onclick = () => {
            // Switch to dashboard and focus on this stock
            document.querySelector('.nav-link[data-view="dashboard"]').click();
            setTimeout(() => changeChartStock(ticker), 100);
        };

        // Color based on TD value
        const intensity = Math.min(Math.abs(tdVal) / 9, 1);
        let bg, borderColor;
        if (tdVal >= 8) {
            bg = `rgba(239, 68, 68, ${0.15 + intensity * 0.4})`;
            borderColor = `rgba(239, 68, 68, ${0.3 + intensity * 0.5})`;
        } else if (tdVal <= -8) {
            bg = `rgba(16, 185, 129, ${0.15 + intensity * 0.4})`;
            borderColor = `rgba(16, 185, 129, ${0.3 + intensity * 0.5})`;
        } else if (tdVal > 0) {
            bg = `rgba(245, 158, 11, ${0.05 + intensity * 0.2})`;
            borderColor = `rgba(245, 158, 11, ${0.1 + intensity * 0.3})`;
        } else if (tdVal < 0) {
            bg = `rgba(6, 182, 212, ${0.05 + intensity * 0.2})`;
            borderColor = `rgba(6, 182, 212, ${0.1 + intensity * 0.3})`;
        } else {
            bg = 'var(--bg-card)';
            borderColor = 'var(--border-glow)';
        }

        cell.style.background = bg;
        cell.style.borderColor = borderColor;

        let label = '';
        if (tdVal >= 9) label = '⚠️ 賣出竭盡';
        else if (tdVal >= 8) label = '注意竭盡';
        else if (tdVal <= -9) label = '🟢 買進竭盡';
        else if (tdVal <= -8) label = '注意竭盡';
        else if (tdVal > 0) label = '連漲中';
        else if (tdVal < 0) label = '連跌中';
        else label = '中性';

        cell.innerHTML = `
            <div class="hm-ticker">${ticker}</div>
            <div class="hm-td" style="color: ${tdVal > 0 ? (tdVal >= 8 ? '#ef4444' : '#f59e0b') : (tdVal <= -8 ? '#10b981' : tdVal < 0 ? '#06b6d4' : '#64748b')}">${tdVal > 0 ? '+' : ''}${tdVal}</div>
            <div class="hm-label">${label}</div>
        `;
        grid.appendChild(cell);
    });
}

// ==========================================
// 7. Render: Alerts History View
// ==========================================
function renderAlertsHistory(filter = 'all') {
    const list = document.getElementById('alerts-history-list');
    list.innerHTML = '';

    let filtered = globalState.alerts;
    if (filter === 'td9') filtered = filtered.filter(a => a.source === 'TD9');
    else if (filter === 'ma') filtered = filtered.filter(a => a.source === 'MA');
    else if (filter === 'hod') filtered = filtered.filter(a => a.source === 'HOD');
    else if (filter === 'orb') filtered = filtered.filter(a => a.source === 'ORB');

    if (filtered.length === 0) {
        list.innerHTML = `<div class="empty-state"><span class="empty-icon">📋</span><p>目前沒有符合條件的警報紀錄</p></div>`;
        return;
    }

    filtered.forEach(a => {
        let icon = '📊';
        if (a.source === 'TD9') icon = a.type === 'down' ? '🔴' : '🟢';
        else if (a.source === 'MA') icon = '🔵';
        else if (a.source === 'HOD') icon = '🔥';
        else if (a.source === 'ORB') icon = '💥';

        const card = document.createElement('div');
        card.className = 'history-card';
        card.innerHTML = `
            <div class="h-icon">${icon}</div>
            <div class="h-body">
                <h4>${a.symbol}</h4>
                <p>${a.title || ''} ${a.desc || ''}</p>
            </div>
            <div class="h-time">
                <span>${a.source}</span>
                <small>${a.time || '--'}</small>
            </div>
        `;
        list.appendChild(card);
    });
}

// Filter buttons
document.querySelectorAll('.filter-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        renderAlertsHistory(btn.getAttribute('data-filter'));
    });
});

// ==========================================
// 8. Market Status
// ==========================================
function updateMarketStatus() {
    const badge = document.getElementById('market-badge');
    const now = new Date();
    const utcH = now.getUTCHours();
    const etH = (utcH - 4 + 24) % 24;
    const day = now.getUTCDay();
    if (day >= 1 && day <= 5 && etH >= 9 && etH < 16) {
        badge.textContent = '美股盤中'; badge.className = 'market-badge open';
    } else if (day >= 1 && day <= 5 && etH >= 4 && etH < 9) {
        badge.textContent = '盤前交易'; badge.className = 'market-badge';
    } else {
        badge.textContent = '休市中'; badge.className = 'market-badge';
    }
}

// ==========================================
// 9. Backtest Panel
// ==========================================

const SIGNAL_LABELS = {
    'td9_sell': 'TD9 賣出竭盡', 'td9_buy': 'TD9 買進竭盡',
    'td8_sell': 'TD8 賣出竭盡', 'td8_buy': 'TD8 買進竭盡',
    'ma_pullback': '均線回測',
};

async function loadBacktestData() {
    const data = await fetchJSON('backtest_data.json');
    if (data && data.stocks) {
        backtestData = data.stocks;
        renderBacktest();
    }
}

function renderBacktest() {
    const cardsEl = document.getElementById('bt-cards');
    const tbodyEl = document.getElementById('bt-tbody');
    document.getElementById('bt-ticker').textContent = selectedStock;

    const stockBt = backtestData[selectedStock];
    if (!stockBt) {
        cardsEl.innerHTML = '<div class="bt-loading">此檔暫無歷史回測資料</div>';
        tbodyEl.innerHTML = '';
        return;
    }

    // Find best available signal for stat cards
    const sigData = stockBt[currentBtSignal];

    // Render stat cards for current signal
    if (!sigData || sigData.total_signals === 0) {
        cardsEl.innerHTML = `<div class="bt-loading">「${SIGNAL_LABELS[currentBtSignal]}」在 ${selectedStock} 過去 1 年未觸發過</div>`;
    } else {
        const wr5 = sigData.win_rate_5d;
        const avg5 = sigData.avg_5d;
        const wrColor = wr5 >= 60 ? 'positive' : wr5 <= 40 ? 'negative' : 'neutral';
        const avgColor = avg5 > 0 ? 'positive' : avg5 < 0 ? 'negative' : 'neutral';

        const wrBarColor = wr5 >= 60 ? '#10b981' : wr5 >= 45 ? '#f59e0b' : '#ef4444';

        cardsEl.innerHTML = `
            <div class="bt-stat-card">
                <div class="bt-card-label">觸發次數</div>
                <div class="bt-card-value neutral">${sigData.total_signals}</div>
                <div class="bt-card-sub">過去 1 年</div>
            </div>
            <div class="bt-stat-card">
                <div class="bt-card-label">5 日勝率</div>
                <div class="bt-card-value ${wrColor}">${wr5 !== null ? wr5 + '%' : 'N/A'}</div>
                <div class="bt-wr-bar"><div class="bt-wr-bar-fill" style="width:${wr5 || 0}%; background:${wrBarColor}"></div></div>
            </div>
            <div class="bt-stat-card">
                <div class="bt-card-label">5 日平均報酬</div>
                <div class="bt-card-value ${avgColor}">${avg5 !== null ? (avg5 > 0 ? '+' : '') + avg5 + '%' : 'N/A'}</div>
                <div class="bt-card-sub">${currentBtSignal.includes('sell') ? '做空視角' : '做多視角'}</div>
            </div>
            <div class="bt-stat-card">
                <div class="bt-card-label">1D / 3D / 10D 勝率</div>
                <div class="bt-card-value neutral" style="font-size:18px">
                    ${fmtWR(sigData.win_rate_1d)} / ${fmtWR(sigData.win_rate_3d)} / ${fmtWR(sigData.win_rate_10d)}
                </div>
                <div class="bt-card-sub">Avg: ${fmtRet(sigData.avg_1d)} / ${fmtRet(sigData.avg_3d)} / ${fmtRet(sigData.avg_10d)}</div>
            </div>
        `;
    }

    // Update tab active state
    document.querySelectorAll('.bt-tab').forEach(t => t.classList.toggle('active', t.dataset.sig === currentBtSignal));
    document.getElementById('bt-signal-label').textContent = SIGNAL_LABELS[currentBtSignal] || '';

    // Render history table
    renderBtTable(stockBt, currentBtSignal);
}

function fmtWR(v) { return v !== null && v !== undefined ? v + '%' : '--'; }
function fmtRet(v) {
    if (v === null || v === undefined) return '--';
    return (v > 0 ? '+' : '') + v + '%';
}

function renderBtTable(stockBt, signal) {
    const tbody = document.getElementById('bt-tbody');
    const sigData = stockBt[signal];
    if (!sigData || !sigData.history || sigData.history.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" class="bt-no-data">無歷史觸發紀錄</td></tr>';
        return;
    }

    const isShort = signal.includes('sell');
    tbody.innerHTML = sigData.history
        .sort((a, b) => b.date.localeCompare(a.date))
        .map(e => {
            return `<tr>
                <td>${e.date}</td>
                <td>$${e.price}</td>
                <td class="${retClass(e.ret_1d, isShort)}">${retFmt(e.ret_1d)}</td>
                <td class="${retClass(e.ret_3d, isShort)}">${retFmt(e.ret_3d)}</td>
                <td class="${retClass(e.ret_5d, isShort)}">${retFmt(e.ret_5d)}</td>
                <td class="${retClass(e.ret_10d, isShort)}">${retFmt(e.ret_10d)}</td>
            </tr>`;
        }).join('');
}

function retFmt(v) {
    if (v === null || v === undefined) return '--';
    return (v > 0 ? '+' : '') + v + '%';
}
function retClass(v, isShort) {
    if (v === null || v === undefined) return 'ret-na';
    if (isShort) return v < 0 ? 'ret-up' : v > 0 ? 'ret-down' : 'ret-na';
    return v > 0 ? 'ret-up' : v < 0 ? 'ret-down' : 'ret-na';
}

// Tab switching
document.querySelectorAll('.bt-tab').forEach(tab => {
    tab.addEventListener('click', () => {
        currentBtSignal = tab.dataset.sig;
        renderBacktest();
    });
});

// ==========================================
// 10. Bootstrap
// ==========================================
renderStockGrid();
highlightSelectedNode();
updateMarketStatus();
setInterval(updateMarketStatus, 60000);

// Load real chart data for initial stock
loadChartData(selectedStock, currentTimeframe);

// Load backtest data
loadBacktestData();

// Load fundamentals data
loadFundamentalsData();

// Load institutional data
loadInstitutionalData();

// Load intraday strength ranking
loadIntradayRS();

// Poll scanner data
pollData();
setInterval(pollData, 5000);

// ==========================================
// Chart Auto-Refresh System
// ==========================================
// Show last chart update time
async function updateChartRefreshTime() {
    try {
        var meta = await fetchJSON('charts/_meta.json');
        if (meta && meta.last_updated) {
            var el = document.getElementById('chart-refresh-time');
            if (el) el.textContent = '更新: ' + meta.last_updated.split(' ')[1];
        }
    } catch(e) {}
}
updateChartRefreshTime();

// Manual refresh button
document.getElementById('btn-refresh-charts').addEventListener('click', async function() {
    var btn = this;
    btn.disabled = true;
    btn.textContent = '⏳ 刷新中...';
    try {
        var res = await fetch('/api/refresh_charts', { method: 'POST' });
        if (res.ok) {
            // Wait a moment for the backend to finish generating
            await new Promise(r => setTimeout(r, 8000));
            // Reload current chart and strength ranking
            loadChartData(selectedStock, currentTimeframe);
            cachedQQQ3m = null; // force re-fetch QQQ
            loadIntradayRS();
            updateChartRefreshTime();
        }
    } catch(e) { console.warn('Refresh failed:', e); }
    btn.disabled = false;
    btn.textContent = '🔄 刷新';
});

// Auto-reload chart data every 3 minutes (frontend side)
setInterval(function() {
    loadChartData(selectedStock, currentTimeframe);
    cachedQQQ3m = null;
    loadIntradayRS();
    updateChartRefreshTime();
}, 180000); // 3 minutes

// ==========================================
// 11. Send LINE Action
// ==========================================
document.getElementById('btn-send-line')?.addEventListener('click', async () => {
    if (globalState.alerts.length === 0) {
        alert('目前沒有警報可以發送');
        return;
    }
    const realAlerts = globalState.alerts.filter(a => a.source === 'HOD' || a.source === 'ORB');
    if (realAlerts.length === 0) {
        alert('目前沒有即時警報可以發送');
        return;
    }
    
    const btn = document.getElementById('btn-send-line');
    const originalText = btn.textContent;
    btn.textContent = '發送中...';
    btn.disabled = true;

    const now = new Date();
    let msg = `【即時戰情】 ${now.getHours().toString().padStart(2, '0')}:${now.getMinutes().toString().padStart(2, '0')}\n`;
    realAlerts.slice(0, 5).forEach(a => {
        let icon = a.type === 'surge' ? '🔥' : (a.type === 'up' ? '🛡️' : '🚀');
        msg += `${icon} ${a.symbol} ${a.title} | ${a.desc} | 量: ${a.vol_ratio || '--'}x\n`;
    });
    if (realAlerts.length > 5) msg += `...及其他 ${realAlerts.length - 5} 筆\n`;
    
    try {
        const res = await fetch('/api/send_line', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: msg })
        });
        if (res.ok) {
            alert('✅ LINE 推播發送成功！');
        } else {
            alert('⚠️ 發送失敗');
        }
    } catch (e) {
        alert('⚠️ 無法連線至伺服器發送推播');
    } finally {
        btn.textContent = originalText;
        btn.disabled = false;
    }
});

// ==========================================
// 12. Fundamentals Panel
// ==========================================
let fundamentalsData = {};

// ==========================================
// On-demand Data Download (Fundamentals & Institutional)
// ==========================================
async function downloadFundInstData() {
    var btnF = document.getElementById('btn-download-fund');
    var btnI = document.getElementById('btn-download-inst');
    if(btnF) { btnF.disabled=true; btnF.textContent='⏳ 下載中...'; }
    if(btnI) { btnI.disabled=true; btnI.textContent='⏳ 下載中...'; }

    try {
        var res = await fetch('/api/fund_inst', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ticker: selectedStock })
        });
        if (res.ok) {
            await loadFundamentalsData();
            await loadInstitutionalData();
        }
    } catch(e) {}

    if(btnF) { btnF.disabled=false; btnF.textContent='⬇️ 下載數據'; }
    if(btnI) { btnI.disabled=false; btnI.textContent='⬇️ 下載數據'; }
}

document.getElementById('btn-download-fund')?.addEventListener('click', downloadFundInstData);
document.getElementById('btn-download-inst')?.addEventListener('click', downloadFundInstData);

async function loadFundamentalsData() {
    const data = await fetchJSON('fundamentals_data.json');
    if (data && data.stocks) {
        fundamentalsData = data.stocks;
        const el = document.getElementById('fund-update');
        if (el && data.last_updated) el.textContent = '更新: ' + data.last_updated;
        renderFundamentals();
    }
}

function renderFundamentals() {
    const d = fundamentalsData[selectedStock];
    const tickerEl = document.getElementById('fund-ticker');
    if (tickerEl) tickerEl.textContent = selectedStock;

    if (!d) {
        const n = document.getElementById('fund-name');
        if (n) n.textContent = '';
        const s = document.getElementById('fund-sector');
        if (s) s.textContent = '--';
        ['fund-valuation-grid','fund-profit-grid','fund-growth-grid','fund-market-grid'].forEach(function(id) {
            const e = document.getElementById(id);
            if (e) e.innerHTML = '<div class="fund-metric-card" style="grid-column:1/-1;text-align:center;padding:20px;border:1px dashed rgba(255,255,255,0.1);"><div class="fund-metric-label">尚未下載 (請按上方 ⬇️下載數據 按鈕)</div></div>';
        });
        return;
    }

    const n2 = document.getElementById('fund-name');
    if (n2) n2.textContent = d.name || '';
    const s2 = document.getElementById('fund-sector');
    if (s2) s2.textContent = (d.sector || '--') + ' · ' + (d.industry || '');

    renderMetricGrid('fund-valuation-grid', [
        mkM('P/E (TTM)', d.pe_ttm, 'x', {evalPE:true}),
        mkM('P/E (FWD)', d.pe_fwd, 'x', {evalPE:true, sub: d.pe_ttm&&d.pe_fwd?(d.pe_fwd<d.pe_ttm?'↓ 低於TTM':'↑ 高於TTM'):null, subDir:d.pe_fwd<d.pe_ttm?'up':'down'}),
        mkM('P/S', d.ps, 'x'), mkM('P/B', d.pb, 'x'),
        mkM('PEG', d.peg, ''), mkM('EV/EBITDA', d.ev_ebitda, 'x'),
    ]);

    renderMetricGrid('fund-profit-grid', [
        mkM('EPS (TTM)', d.eps_ttm, '$', {color:d.eps_ttm>0?'up':'down'}),
        mkM('EPS (FWD)', d.eps_fwd, '$', {sub:d.eps_ttm&&d.eps_fwd?'TTM→FWD '+(d.eps_fwd>d.eps_ttm?'↑':'↓'):null, subDir:d.eps_fwd>d.eps_ttm?'up':'down'}),
        mkM('營收 (TTM)', d.rev_fmt, '', {raw:true}),
        mkM('毛利率', d.gross_margin, '%', {evalMargin:true}),
        mkM('營業利潤率', d.op_margin, '%', {color:d.op_margin>0?'':'down'}),
        mkM('淨利率', d.net_margin, '%', {color:d.net_margin>0?'':'down'}),
        mkM('ROE', d.roe, '%', {evalROE:true}),
        mkM('ROA', d.roa, '%'),
    ]);

    renderMetricGrid('fund-growth-grid', [
        mkM('營收成長 YoY', d.rev_growth, '%', {sign:true, color:d.rev_growth>0?'up':'down'}),
        mkM('盈餘成長 YoY', d.earn_growth, '%', {sign:true, color:d.earn_growth>0?'up':'down'}),
        mkM('負債/權益比', d.de_ratio, '', {evalDE:true}),
        mkM('流動比率', d.current_ratio, 'x', {evalCR:true}),
        mkM('自由現金流', d.fcf_fmt, '', {raw:true, color:d.fcf>0?'up':'down'}),
        mkM('FCF Yield', d.fcf_yield, '%', {color:d.fcf_yield>0?'up':'down'}),
    ]);

    var mkt = [
        mkM('市值', d.mcap_fmt, '', {raw:true}),
        mkM('Beta', d.beta, ''),
        mkM('距52W高', d.pct_h52, '%', {sign:true, color:'down'}),
        mkM('距52W低', d.pct_l52, '%', {sign:true, color:'up'}),
        mkM('股息率', d.div_yield, '%'),
        mkM('分析師目標', d.target, '$', {sub:d.upside!=null?'潛在 '+(d.upside>0?'+':'')+d.upside+'%':null, subDir:d.upside>0?'up':'down'}),
        mkM('分析師人數', d.analysts, '人'),
        {label:'建議', value:d.rec, isBadge:true}
    ];
    renderMetricGrid('fund-market-grid', mkt);

    renderFundBarChart('fund-rev-chart', d.quarters, 'revenue', 'revenue_fmt', 'rev_yoy');
    renderFundBarChart('fund-eps-chart', d.quarters, 'eps', null, 'eps_yoy');
    renderFundBarChart('fund-gm-chart', d.quarters, 'gross_margin', null, null, '%');

    renderTargetBar('fund-target-bar', d.target_lo, d.target_hi, d.price, d.target, d.rec, d.analysts);
    render52WBar('fund-52w-bar', d.l52, d.h52, d.price, d.pct_h52);
}

function mkM(label, value, fmt, opts) {
    var m = {label:label, value:value, fmt:fmt||''};
    if (opts) Object.assign(m, opts);
    return m;
}

function renderMetricGrid(cid, metrics) {
    var el = document.getElementById(cid);
    if (!el) return;
    el.innerHTML = '';
    var recMap = {buy:'Buy',strong_buy:'Strong Buy',hold:'Hold',sell:'Sell',strong_sell:'Strong Sell',underperform:'Sell',outperform:'Buy'};

    metrics.forEach(function(m) {
        var card = document.createElement('div');
        card.className = 'fund-metric-card';

        if (m.evalPE && m.value) card.className += m.value<20?' val-good':m.value>50?' val-bad':' val-warn';
        if (m.evalMargin && m.value) card.className += m.value>=40?' val-good':m.value>=20?' val-warn':' val-bad';
        if (m.evalROE && m.value) card.className += m.value>=20?' val-good':m.value>=10?' val-warn':' val-bad';
        if (m.evalDE && m.value!=null) card.className += m.value<50?' val-good':m.value>150?' val-bad':' val-warn';
        if (m.evalCR && m.value) card.className += m.value>=1.5?' val-good':m.value>=1?' val-warn':' val-bad';

        if (m.isBadge) {
            var rc = (m.value&&(m.value.includes('buy')||m.value==='outperform'))?'buy':(m.value&&(m.value.includes('sell')||m.value==='underperform'))?'sell':'hold';
            card.innerHTML = '<div class="fund-metric-label">'+m.label+'</div><div style="margin-top:4px"><span class="fund-rec-badge '+rc+'">'+(recMap[m.value]||m.value||'--')+'</span></div>';
            el.appendChild(card);
            return;
        }

        var dv;
        if (m.value==null) { dv='--'; }
        else if (m.raw) { dv=m.value; }
        else {
            var pre = m.fmt==='$'?'$':'';
            var suf = m.fmt==='%'?'%':m.fmt==='x'?'x':m.fmt==='人'?'人':'';
            var sg = m.sign&&m.value>0?'+':'';
            if(m.fmt==='$') suf='';
            dv = pre+sg+m.value+suf;
        }

        var vc = m.value==null?'muted':(m.color||'');
        var sub = m.sub ? '<div class="fund-metric-sub '+(m.subDir||'')+'">' + m.sub + '</div>' : '';
        card.innerHTML = '<div class="fund-metric-label">'+m.label+'</div><div class="fund-metric-value '+vc+'">'+dv+'</div>'+sub;
        el.appendChild(card);
    });
}

function renderFundBarChart(cid, quarters, field, fmtField, yoyField, suffix) {
    var el = document.getElementById(cid);
    if (!el) return;
    el.innerHTML = '';
    if (!quarters || quarters.length===0) {
        el.innerHTML = '<div style="color:var(--text-muted);font-size:12px;text-align:center;padding:40px 0">暫無季度數據</div>';
        return;
    }
    var qs = quarters.slice(0,8).reverse();
    var values = qs.map(function(q){return q[field]}).filter(function(v){return v!=null});
    if (values.length===0) { el.innerHTML='<div style="color:var(--text-muted);font-size:12px;text-align:center;padding:40px 0">暫無數據</div>'; return; }
    var maxVal = Math.max.apply(null, values.map(function(v){return Math.abs(v)}));

    qs.forEach(function(q) {
        var val = q[field];
        if (val==null) return;
        var col = document.createElement('div');
        col.className = 'fund-bar-col';
        var barH = maxVal>0 ? Math.max(3,(Math.abs(val)/maxVal)*100) : 3;
        var bar = document.createElement('div');
        bar.className = 'fund-bar '+(val>=0?'positive':'negative');
        bar.style.height = barH+'%';
        if (yoyField && q[yoyField]!=null) {
            var yoy = document.createElement('span');
            yoy.className = 'fund-bar-yoy '+(q[yoyField]>=0?'up':'down');
            yoy.textContent = (q[yoyField]>0?'+':'')+q[yoyField]+'%';
            bar.appendChild(yoy);
        }
        var vl = document.createElement('div');
        vl.className = 'fund-bar-value';
        if (fmtField && q[fmtField]) vl.textContent=q[fmtField];
        else if (suffix) vl.textContent=val+suffix;
        else vl.textContent=typeof val==='number'?(Math.abs(val)>=1e9?(val/1e9).toFixed(1)+'B':val.toFixed(2)):val;
        var ql = document.createElement('div');
        ql.className = 'fund-bar-label';
        ql.textContent = q.label||'';
        col.appendChild(vl); col.appendChild(bar); col.appendChild(ql);
        el.appendChild(col);
    });
}

function renderTargetBar(cid, lo, hi, cur, target, rec, analysts) {
    var el = document.getElementById(cid);
    if (!el) return;
    if (!lo||!hi||!cur) { el.innerHTML='<div style="color:var(--text-muted);font-size:12px">暫無分析師數據</div>'; return; }
    var range=hi-lo, curPct=range>0?Math.min(100,Math.max(0,((cur-lo)/range)*100)):50;
    var tPct=target&&range>0?Math.min(100,Math.max(0,((target-lo)/range)*100)):null;
    var recMap={buy:'Buy',strong_buy:'Strong Buy',hold:'Hold',sell:'Sell',strong_sell:'Strong Sell',underperform:'Sell',outperform:'Buy'};
    var rc=(rec&&(rec.includes('buy')||rec==='outperform'))?'buy':(rec&&(rec.includes('sell')||rec==='underperform'))?'sell':'hold';
    var tDot=tPct!=null?'<div style="position:absolute;top:50%;left:'+tPct+'%;transform:translate(-50%,-50%);width:10px;height:10px;border-radius:50%;background:var(--color-surge);border:2px solid #fff;z-index:1" title="目標價 $'+target+'"></div>':'';
    el.innerHTML='<div class="fund-range-bar-wrap"><div class="fund-range-fill" style="left:0;width:100%"></div><div class="fund-range-dot" style="left:'+curPct+'%"><div class="fund-range-current">$'+cur+'</div></div>'+tDot+'</div><div class="fund-range-labels"><span>最低 $'+lo+'</span>'+(target?'<span style="color:var(--color-surge)">目標 $'+target+'</span>':'')+'<span>最高 $'+hi+'</span></div><div class="fund-range-info"><span class="fund-rec-badge '+rc+'">'+(recMap[rec]||rec||'--')+'</span><span>'+(analysts||0)+' 位分析師</span></div>';
}

function render52WBar(cid, lo, hi, cur, pctH) {
    var el = document.getElementById(cid);
    if (!el) return;
    if (!lo||!hi||!cur) { el.innerHTML='<div style="color:var(--text-muted);font-size:12px">暫無數據</div>'; return; }
    var range=hi-lo, curPct=range>0?Math.min(100,Math.max(0,((cur-lo)/range)*100)):50;
    el.innerHTML='<div class="fund-range-bar-wrap"><div class="fund-range-fill" style="left:0;width:'+curPct+'%"></div><div class="fund-range-dot" style="left:'+curPct+'%"><div class="fund-range-current">$'+cur+'</div></div></div><div class="fund-range-labels"><span>52W低 $'+lo+'</span><span>52W高 $'+hi+'</span></div><div class="fund-range-info"><span>距高點: '+(pctH!=null?pctH+'%':'--')+'</span><span>區間位置: '+curPct.toFixed(0)+'%</span></div>';
}

// ==========================================
// 13. Institutional / Smart Money Panel
// ==========================================
let institutionalData = {};

async function loadInstitutionalData() {
    var data = await fetchJSON('institutional_data.json');
    if (data && data.stocks) {
        institutionalData = data.stocks;
        var el = document.getElementById('inst-update');
        if (el && data.last_updated) el.textContent = '更新: ' + data.last_updated;
        renderInstitutional();
    }
}

function renderInstitutional() {
    var d = institutionalData[selectedStock];
    var tEl = document.getElementById('inst-ticker');
    if (tEl) tEl.textContent = selectedStock;
    var nEl = document.getElementById('inst-name');
    if (nEl) nEl.textContent = d ? (d.name || '') : '';

    if (!d) {
        ['inst-ownership','inst-short','inst-insider-summary','inst-holders-table','inst-insider-table'].forEach(function(id){
            var e = document.getElementById(id);
            if (e) e.innerHTML = '<div style="color:var(--text-muted);font-size:12px;padding:30px;text-align:center;border:1px dashed rgba(255,255,255,0.1);border-radius:10px;">尚未下載 (請按上方 ⬇️下載數據 按鈕)</div>';
        });
        return;
    }

    renderOwnership(d.ownership);
    renderShortInterest(d.short);
    renderInsiderSummary(d.insider);
    renderTopHolders(d.top_institutions);
    renderInsiderTable(d.insider);
}

function renderOwnership(own) {
    var el = document.getElementById('inst-ownership');
    if (!el || !own) return;
    var inst = own.institutional_pct || 0;
    var ins = own.insider_pct || 0;
    var ret = own.retail_pct || 0;
    var total = inst + ins + ret;
    if (total === 0) { el.innerHTML = '<div style="color:var(--text-muted);font-size:12px">暫無數據</div>'; return; }
    var a1 = (inst/total)*360;
    var a2 = (ins/total)*360;
    var a3 = (ret/total)*360;
    var g = 'conic-gradient(#3b82f6 0deg '+a1+'deg, #f59e0b '+a1+'deg '+(a1+a2)+'deg, #64748b '+(a1+a2)+'deg 360deg)';
    el.innerHTML = '<div class="inst-donut-row"><div class="inst-donut" style="background:'+g+'"><div class="inst-donut-center"><span>持股</span><strong>'+total.toFixed(0)+'%</strong></div></div><div class="inst-legend"><div class="inst-legend-item"><div class="inst-legend-dot" style="background:#3b82f6"></div>機構 Institutional<span class="inst-legend-pct">'+inst+'%</span></div><div class="inst-legend-item"><div class="inst-legend-dot" style="background:#f59e0b"></div>內部人 Insider<span class="inst-legend-pct">'+ins+'%</span></div><div class="inst-legend-item"><div class="inst-legend-dot" style="background:#64748b"></div>散戶 Retail<span class="inst-legend-pct">'+ret+'%</span></div>'+(own.institutional_count?'<div style="font-size:11px;color:var(--text-muted);margin-top:4px">機構數: '+own.institutional_count+' 家</div>':'')+'</div></div>';
}

function renderShortInterest(s) {
    var el = document.getElementById('inst-short');
    if (!el || !s) return;
    var sqMap = {high:'🔴 高風險 — 軋空可能',medium:'🟡 中等 — 持續觀察',low:'🟢 低風險'};
    el.innerHTML = '<div class="inst-short-grid"><div class="inst-short-row"><span class="inst-short-label">放空股數</span><span class="inst-short-val">'+(s.shares_short_fmt||'--')+'</span></div><div class="inst-short-row"><span class="inst-short-label">放空佔流通股 %</span><span class="inst-short-val">'+(s.short_pct!=null?s.short_pct+'%':'--')+'</span></div><div class="inst-short-row"><span class="inst-short-label">空頭回補天數</span><span class="inst-short-val">'+(s.short_ratio!=null?s.short_ratio+'天':'--')+'</span></div><div class="inst-short-row"><span class="inst-short-label">流通股數</span><span class="inst-short-val" style="font-size:14px">'+(s.float_shares_fmt||'--')+'</span></div><div class="inst-squeeze-badge '+s.squeeze_risk+'">'+(sqMap[s.squeeze_risk]||'--')+'</div></div>';
}

function renderInsiderSummary(ins) {
    var el = document.getElementById('inst-insider-summary');
    if (!el || !ins) return;
    var sentMap = {bullish:'🟢 偏多 — 內部人淨買入',bearish:'🔴 偏空 — 內部人淨賣出',neutral:'⚪ 中性'};
    el.innerHTML = '<div class="inst-insider-stat"><div class="inst-stat-box"><div class="stat-label">買入</div><div class="stat-val up">'+ins.buy_count+'</div><div class="stat-sub">'+(ins.buy_total_fmt||'$0')+'</div></div><div class="inst-stat-box"><div class="stat-label">賣出</div><div class="stat-val down">'+ins.sell_count+'</div><div class="stat-sub">'+(ins.sell_total_fmt||'$0')+'</div></div></div><div class="inst-sentiment-badge '+ins.net_sentiment+'">'+(sentMap[ins.net_sentiment]||'--')+'</div>';
}

function renderTopHolders(holders) {
    var el = document.getElementById('inst-holders-table');
    if (!el) return;
    if (!holders || holders.length===0) { el.innerHTML='<div style="color:var(--text-muted);font-size:12px;padding:16px">暫無機構持股數據</div>'; return; }
    var html = '<table class="inst-table"><thead><tr><th>#</th><th>機構名稱</th><th>持股數</th><th>持股金額</th><th>佔比</th><th>申報日</th></tr></thead><tbody>';
    holders.forEach(function(h,i) {
        html += '<tr><td>'+(i+1)+'</td><td>'+h.name+'</td><td>'+(h.shares_fmt||'--')+'</td><td>'+(h.value_fmt||'--')+'</td><td>'+(h.pct!=null?h.pct+'%':'--')+'</td><td>'+(h.date||'--')+'</td></tr>';
    });
    html += '</tbody></table>';
    el.innerHTML = html;
}

function renderInsiderTable(ins) {
    var el = document.getElementById('inst-insider-table');
    if (!el) return;
    if (!ins || !ins.trades || ins.trades.length===0) { el.innerHTML='<div style="color:var(--text-muted);font-size:12px;padding:16px">暫無內部人交易紀錄</div>'; return; }
    var html = '<table class="inst-table"><thead><tr><th>日期</th><th>內部人</th><th>買/賣</th><th>股數</th><th>金額</th><th>說明</th></tr></thead><tbody>';
    ins.trades.forEach(function(t) {
        var ac = t.action==='buy'?'inst-trade-buy':t.action==='sell'?'inst-trade-sell':'inst-trade-other';
        var labelMap = {buy:'買入',sell:'賣出',exercise:'行權',grant:'授予',gift:'贈予',other:'其他'};
        html += '<tr><td>'+t.date+'</td><td style="max-width:150px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="'+t.insider+'">'+t.insider+'</td><td class="'+ac+'">'+(labelMap[t.action]||'其他')+'</td><td>'+(t.shares_fmt||'--')+'</td><td>'+(t.value_fmt||'--')+'</td><td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="'+t.desc+'">'+t.desc+'</td></tr>';
    });
    html += '</tbody></table>';
    el.innerHTML = html;
}

// ==========================================
// 14. Intraday Strength Ranking (HOD/LOD based)
// ==========================================
var cachedQQQ3m = null;

async function loadIntradayRS() {
    if (!cachedQQQ3m) {
        try {
            var qRes = await fetch('charts/QQQ_3m.json?t=' + Date.now());
            if (qRes.ok) cachedQQQ3m = await qRes.json();
        } catch(e) {}
    }
    if (!cachedQQQ3m || cachedQQQ3m.length < 2) return;
    var qqqData = cachedQQQ3m;
    var todayQQQ = qqqData.slice(findTodayStart(qqqData));
    var promises = AI_TECH_STOCKS.map(function(ticker) {
        return fetch('charts/' + ticker + '_3m.json?t=' + Date.now())
            .then(function(r) { return r.ok ? r.json() : null; })
            .then(function(data) {
                if (!data || data.length < 2) return null;
                var ts = findTodayStart(data);
                var todayData = data.slice(ts);
                var a = analyzeStrength(todayData, todayQQQ);
                var ret = todayData.length > 0 ? ((todayData[todayData.length-1].close - todayData[0].open) / todayData[0].open * 100) : 0;
                return { ticker:ticker, ret:ret, strong:a.strong, weak:a.weak, net:a.strong-a.weak,
                    label: a.strong>a.weak?'強':a.weak>a.strong?'弱':'中' };
            }).catch(function() { return null; });
    });
    var all = await Promise.all(promises);
    intradayRS = all.filter(function(x) { return x !== null; });
    intradayRS.sort(function(a, b) { return b.net !== a.net ? b.net - a.net : b.ret - a.ret; });
    renderStrengthRanking();
}

function analyzeStrength(sData, qData) {
    var strong=0, weak=0, qi=0, sH=-Infinity, sL=Infinity, qH=-Infinity, qL=Infinity, lt=0;
    for (var i=0; i<sData.length; i++) {
        var s=sData[i];
        while (qi<qData.length && qData[qi].time<s.time) qi++;
        if (qi>=qData.length || qData[qi].time!==s.time) continue;
        var q=qData[qi];
        if (s.time-lt>43200) { qH=q.high; qL=q.low; sH=s.high; sL=s.low; lt=s.time; continue; }
        lt=s.time;
        if (q.high>qH) { qH=q.high; if(s.high>sH) strong++; else weak++; }
        if (q.low<qL) { qL=q.low; if(s.low<sL) weak++; else strong++; }
        sH=Math.max(sH,s.high); sL=Math.min(sL,s.low);
    }
    return {strong:strong, weak:weak};
}

function findTodayStart(data) {
    for (var i=data.length-1; i>0; i--) { if(data[i].time-data[i-1].time>43200) return i; }
    return 0;
}

function renderStrengthRanking() {
    var el = document.getElementById('rs-strength-list');
    if (!el) return;
    if (intradayRS.length===0) { el.innerHTML='<div style="color:var(--text-muted);font-size:12px;padding:12px;text-align:center">載入中...</div>'; return; }
    el.innerHTML = '';
    intradayRS.forEach(function(item, i) {
        var div = document.createElement('div');
        div.className = 'rs-item';
        div.onclick = function() { changeChartStock(item.ticker); };
        var retClass = item.ret>=0?'up':'down';
        var badge = '';
        if (item.label==='強') badge='<span class="rs-hod-badge" style="background:rgba(239,68,68,0.15);color:#ef4444">強'+item.strong+'</span>';
        else if (item.label==='弱') badge='<span class="rs-hod-badge" style="background:rgba(16,185,129,0.15);color:#10b981">弱'+item.weak+'</span>';
        else badge='<span class="rs-hod-badge">中</span>';
        var netClass = item.net>0?'up':item.net<0?'down':'';
        var netText = item.net>0?'+'+item.net:item.net===0?'0':''+item.net;
        
        var mtfTrend15 = globalState.mtf[item.ticker] ? globalState.mtf[item.ticker]['15m'] : '震盪';
        var mtfTrend60 = globalState.mtf[item.ticker] ? globalState.mtf[item.ticker]['60m'] : '震盪';
        var getDot = function(t) { return t === '多頭' ? '🔴' : t === '空頭' ? '🟢' : '⚪'; };
        var trendDots = '<span style="font-size:8px; margin-left:4px;" title="15m/60m">'+getDot(mtfTrend15)+getDot(mtfTrend60)+'</span>';

        div.innerHTML = '<span class="rs-rank">'+(i+1)+'</span><span class="rs-ticker">'+item.ticker+trendDots+'</span>'+badge+
            '<span class="rs-ret '+retClass+'">'+(item.ret>=0?'+':'')+item.ret.toFixed(1)+'%</span>'+
            '<span class="rs-rs '+netClass+'">'+netText+'</span>';
        if (item.ticker===selectedStock) { div.style.background='rgba(139,92,246,0.1)'; div.style.border='1px solid rgba(139,92,246,0.3)'; }
        el.appendChild(div);
    });
}
