/**
 * Bybit Spot AI Screener Pro - Frontend Client Logic
 */

const state = {
    setups: [],
    activeFilter: 'all',
    searchQuery: '',
    timeframe: '240',
    soundEnabled: true,
    isScanning: false,
    activeChartWidget: null,
    account: {
        connected: false,
        totalEquity: 0,
        totalWallet: 0,
        usdtAvailable: 0,
        holdings: [],
        maskedKey: ''
    },
    activeTradeSetup: null,
    orderType: 'Market',
    tpStrategy: 'TP1_TP2',
    openOrders: [],
    tradingMode: localStorage.getItem('bybit_spot_mode') || 'demo',
    demo: {
        balanceUsdt: 10000.0,
        initialBalance: 10000.0,
        totalEquity: 10000.0,
        openPositions: [],
        history: [],
        stats: {
            totalTrades: 0,
            winningTrades: 0,
            losingTrades: 0,
            winRate: 0.0,
            totalRealizedPnL: 0.0
        }
    }
};

// Web Audio API for Golden Setup Alerts
function playAlertBeep() {
    if (!state.soundEnabled) return;
    try {
        const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        const osc = audioCtx.createOscillator();
        const gain = audioCtx.createGain();

        osc.type = 'sine';
        osc.frequency.setValueAtTime(587.33, audioCtx.currentTime); // D5
        osc.frequency.setValueAtTime(880.00, audioCtx.currentTime + 0.1); // A5

        gain.gain.setValueAtTime(0.2, audioCtx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.01, audioCtx.currentTime + 0.4);

        osc.connect(gain);
        gain.connect(audioCtx.destination);

        osc.start();
        osc.stop(audioCtx.currentTime + 0.45);
    } catch (e) {
        console.warn('Audio play error:', e);
    }
}

// Start on DOM Loaded
document.addEventListener('DOMContentLoaded', () => {
    initTradingModeUI();
    fetchDemoAccountState();
    // Live update open demo positions every 2 seconds from Bybit Spot ticker
    setInterval(fetchDemoAccountState, 2000);

    fetchRealPositions();
    // Live update open real positions every 2 seconds from Bybit Spot ticker
    setInterval(fetchRealPositions, 2000);

    fetchMarketPulse();
    setInterval(fetchMarketPulse, 10000);

    fetchAccountStatus();
    fetchAccountBalance();
    setInterval(fetchAccountBalance, 12000);

    fetchLatestScreener();

    // Auto scan every 5 minutes
    setInterval(() => {
        if (!state.isScanning) {
            triggerScan(true);
        }
    }, 5 * 60 * 1000);
});

// Market Pulse
async function fetchMarketPulse() {
    try {
        const res = await fetch('/api/pulse');
        const data = await res.json();
        
        if (data.BTCUSDT) {
            document.getElementById('btcPrice').innerText = `$${formatPrice(data.BTCUSDT.price)}`;
            const chgEl = document.getElementById('btcChg');
            const chg = data.BTCUSDT.chg;
            chgEl.innerText = `${chg >= 0 ? '+' : ''}${chg.toFixed(2)}%`;
            chgEl.className = `pulse-chg ${chg >= 0 ? 'up' : 'down'}`;
        }
        if (data.ETHUSDT) {
            document.getElementById('ethPrice').innerText = `$${formatPrice(data.ETHUSDT.price)}`;
            const chgEl = document.getElementById('ethChg');
            const chg = data.ETHUSDT.chg;
            chgEl.innerText = `${chg >= 0 ? '+' : ''}${chg.toFixed(2)}%`;
            chgEl.className = `pulse-chg ${chg >= 0 ? 'up' : 'down'}`;
        }
        if (data.SOLUSDT) {
            document.getElementById('solPrice').innerText = `$${formatPrice(data.SOLUSDT.price)}`;
            const chgEl = document.getElementById('solChg');
            const chg = data.SOLUSDT.chg;
            chgEl.innerText = `${chg >= 0 ? '+' : ''}${chg.toFixed(2)}%`;
            chgEl.className = `pulse-chg ${chg >= 0 ? 'up' : 'down'}`;
        }
    } catch (e) {}
}

// Fetch Latest Scan Results
async function fetchLatestScreener() {
    try {
        const res = await fetch('/api/latest');
        const data = await res.json();
        if (data && Array.isArray(data.setups)) {
            state.setups = data.setups;
            updateStatsBar(data);
            updateFilterCounts();
            renderCards();
        }
    } catch (e) {
        console.error('Error fetching latest screener:', e);
    }
}

// Trigger Manual or Auto Scan
async function triggerScan(isSilent = false) {
    if (state.isScanning) return;
    state.isScanning = true;

    const btn = document.getElementById('scanMainBtn');
    const btnText = document.getElementById('scanBtnText');
    const grid = document.getElementById('cardsGrid');

    if (btn) {
        btn.disabled = true;
        if (btnText) btnText.innerText = 'جاري الفحص السريع... ⏳';
    }

    if (!isSilent && grid) {
        grid.innerHTML = `
            <div class="loading-state">
                <div class="spinner"></div>
                <p>جاري مسح وتحليل 60+ عملة نشطة على Bybit عبر خوارزميات RSI و EMA و Volume Breakout...</p>
                <small style="color: var(--text-dim); margin-top: 8px;">يستغرق الفحص بين 4 إلى 7 ثوانٍ</small>
            </div>
        `;
    }

    try {
        const tf = state.timeframe;
        const res = await fetch(`/api/scan?interval=${tf}&limit=70`);
        const data = await res.json();

        if (data && Array.isArray(data.setups)) {
            state.setups = data.setups;
            updateStatsBar(data);
            updateFilterCounts();
            renderCards();

            // Check for top picks to beep
            const hasGolden = data.setups.some(s => s.score >= 90);
            if (hasGolden) playAlertBeep();

            showToast(`اكتمل الفحص! تم رصد ${data.setups.length} صفقة فنية مؤكدة 🎯`);
        }
    } catch (e) {
        showToast('حدث خطأ أثناء فحص السوق: ' + e.message);
    } finally {
        state.isScanning = false;
        if (btn) {
            btn.disabled = false;
            if (btnText) btnText.innerText = 'فحص السوق اللحظي الآن';
        }
    }
}

function updateStatsBar(data) {
    const sCount = document.getElementById('statScannedCount');
    if (sCount) sCount.innerText = data.scanned_count || '60';

    const rCount = document.getElementById('statRecommendedCount');
    if (rCount) rCount.innerText = (data.setups || []).length;

    const lScan = document.getElementById('statLastScan');
    if (lScan) lScan.innerText = data.scan_time || '--';
}

function updateFilterCounts() {
    const setups = state.setups || [];
    
    document.getElementById('countAll').innerText = setups.length;
    document.getElementById('countBreakout').innerText = setups.filter(s => s.setup_type === 'VOLUME_BREAKOUT').length;
    document.getElementById('countPullback').innerText = setups.filter(s => s.setup_type === 'TREND_PULLBACK').length;
    document.getElementById('countOversold').innerText = setups.filter(s => s.setup_type === 'OVERSOLD_BOUNCE').length;
    document.getElementById('countDivergence').innerText = setups.filter(s => s.setup_type === 'BULLISH_DIVERGENCE').length;
}

function setFilter(filterKey) {
    state.activeFilter = filterKey;
    document.querySelectorAll('.filter-pill').forEach(b => {
        b.classList.toggle('active', b.dataset.filter === filterKey);
    });
    renderCards();
}

function handleLocalSearch() {
    const input = document.getElementById('coinSearchInput');
    if (input) {
        state.searchQuery = input.value.trim().toUpperCase();
        renderCards();
    }
}

function clearSearch() {
    const input = document.getElementById('coinSearchInput');
    if (input) {
        input.value = '';
        state.searchQuery = '';
        renderCards();
    }
}

function onTimeframeChange() {
    const sel = document.getElementById('timeframeSelect');
    if (sel) {
        state.timeframe = sel.value;
        triggerScan();
    }
}

function toggleSoundAlert() {
    state.soundEnabled = !state.soundEnabled;
    const btn = document.getElementById('soundToggleBtn');
    const icon = document.getElementById('soundIcon');
    const label = document.getElementById('soundLabel');

    if (state.soundEnabled) {
        btn.classList.add('active');
        icon.innerText = '🔔';
        label.innerText = 'صوت التنبيه: مفعّل';
        playAlertBeep();
    } else {
        btn.classList.remove('active');
        icon.innerText = '🔕';
        label.innerText = 'صوت التنبيه: صامت';
    }
}

function renderCards() {
    const grid = document.getElementById('cardsGrid');
    if (!grid) return;

    let list = state.setups || [];

    // Filter by type
    if (state.activeFilter !== 'all') {
        list = list.filter(s => s.setup_type === state.activeFilter);
    }

    // Filter by search query
    if (state.searchQuery) {
        list = list.filter(s => s.symbol.includes(state.searchQuery) || s.base_coin.includes(state.searchQuery));
    }

    if (list.length === 0) {
        grid.innerHTML = `
            <div class="empty-state">
                <span style="font-size: 40px; margin-bottom: 12px;">🔍</span>
                <h3>لا توجد صفقات مطابقة للبحث أو الفلتر حالياً</h3>
                <p style="font-size: 13px; color: var(--text-dim); margin-top: 6px;">جرب تغيير التصنيف أو اضغط "فحص السوق اللحظي" للتحديث المباشر.</p>
            </div>
        `;
        return;
    }

    grid.innerHTML = list.map((s, idx) => {
        let tagClass = 'tag-breakout';
        let typeIcon = '🚀';
        if (s.setup_type === 'OVERSOLD_BOUNCE') {
            tagClass = 'tag-oversold';
            typeIcon = '💎';
        } else if (s.setup_type === 'TREND_PULLBACK') {
            tagClass = 'tag-pullback';
            typeIcon = '📈';
        } else if (s.setup_type === 'BULLISH_DIVERGENCE') {
            tagClass = 'tag-divergence';
            typeIcon = '🔄';
        }

        const scoreColor = s.score >= 90 ? 'var(--green-profit)' : (s.score >= 80 ? 'var(--cyan-accent)' : 'var(--gold-accent)');
        const bybitTradeUrl = `https://www.bybit.com/trade/spot/${s.base_coin}/USDT`;

        return `
            <div class="trade-card">
                <!-- Header -->
                <div class="card-header-row">
                    <div class="card-coin-identity">
                        <span class="card-rank">#${idx + 1}</span>
                        <div class="card-titles">
                            <span class="card-symbol mono">${s.symbol}</span>
                        </div>
                    </div>
                    <span class="card-score-pill" style="color: ${scoreColor}; border-color: ${scoreColor};">
                        ⭐ ${s.score}%
                    </span>
                </div>

                <!-- Setup & Live Price -->
                <div class="card-setup-row">
                    <span class="setup-tag ${tagClass}">
                        ${typeIcon} ${s.setup_name}
                    </span>
                    <div class="card-price-box">
                        <span class="card-live-price mono">$${s.price_formatted}</span>
                        <span class="card-price-chg mono ${s.price_chg_24h >= 0 ? 'text-green' : 'text-red'}">
                            ${s.price_chg_24h >= 0 ? '+' : ''}${s.price_chg_24h.toFixed(2)}%
                        </span>
                    </div>
                </div>

                <!-- Technical Matrix -->
                <div class="indicators-matrix-row">
                    <div class="matrix-item">
                        <span class="matrix-label">مؤشر RSI:</span>
                        <span class="matrix-val mono ${s.rsi <= 35 ? 'text-green' : ''}">${s.rsi}</span>
                    </div>
                    <div class="matrix-item">
                        <span class="matrix-label">مضاعف السيولة:</span>
                        <span class="matrix-val mono text-cyan">${s.vol_ratio}x ⚡</span>
                    </div>
                    <div class="matrix-item">
                        <span class="matrix-label">سيولة 24س:</span>
                        <span class="matrix-val mono">$${s.turnover24h_m}M</span>
                    </div>
                </div>

                <!-- Trade Levels Box -->
                <div class="levels-box">
                    <div class="entry-level-row">
                        <span class="entry-label">🎯 نطاق الشراء المقترح:</span>
                        <span class="entry-val mono">${s.entry_display}</span>
                    </div>

                    <!-- Targets Grid -->
                    <div class="targets-grid">
                        <div class="target-pill tp1" title="الهدف الأول">
                            <span class="t-name">TP1 (سريع)</span>
                            <span class="t-price mono">${s.tp1_display}</span>
                            <span class="t-pct">+${s.tp1_pct}%</span>
                        </div>
                        <div class="target-pill tp2" title="الهدف الثاني (الرئيسي)">
                            <span class="t-name">TP2 (رئيسي)</span>
                            <span class="t-price mono">${s.tp2_display}</span>
                            <span class="t-pct">+${s.tp2_pct}%</span>
                        </div>
                        <div class="target-pill tp3" title="الهدف الثالث">
                            <span class="t-name">TP3 (موجة)</span>
                            <span class="t-price mono">${s.tp3_display}</span>
                            <span class="t-pct">+${s.tp3_pct}%</span>
                        </div>
                        <div class="target-pill sl" title="وقف الخسارة الإلزامي">
                            <span class="t-name">SL (الوقف)</span>
                            <span class="t-price mono">${s.sl_display}</span>
                            <span class="t-pct">-${s.sl_pct}%</span>
                        </div>
                    </div>
                </div>

                <!-- Meta & R:R -->
                <div class="card-meta-row">
                    <span class="rr-meter mono">⚖️ نسبة العائد / المخاطرة: <b>1 : ${s.rr_ratio}</b></span>
                    <span class="text-dim" style="font-size: 11px;">فريم: ${state.timeframe}د</span>
                </div>

                <!-- Rationale -->
                <div class="card-rationale">
                    <span class="r-icon">💡</span>
                    <span>${s.rationale_arabic}</span>
                </div>

                <!-- Card Actions -->
                <div class="card-actions-grid">
                    <button type="button" class="btn-card-action primary" onclick="openChartModal('${s.symbol}')">
                        <span>📊 الشارت</span>
                    </button>
                    <button type="button" class="btn-card-action" onclick="copySignal('${s.symbol}')">
                        <span>📋 نسخ</span>
                    </button>
                    <button type="button" class="btn-card-trade-api" onclick="openTradingAssistant('${s.symbol}')" title="تداول فوري ذكي ومباشر عبر Bybit API">
                        <span>⚡ تداول فوري (Bybit API)</span>
                    </button>
                    <a href="${bybitTradeUrl}" target="_blank" rel="noopener noreferrer" class="btn-card-ext" title="فتح العملة على موقع Bybit">
                        <span>↗</span>
                    </a>
                </div>
            </div>
        `;
    }).join('');
}

// Global variable for active chart instance
let activeLightweightChart = null;

// Open Interactive Candlestick Chart Modal with Trade Levels (Entry, TP1, TP2, TP3, SL)
function openChartModal(symbol, customLevels = null) {
    const modal = document.getElementById('chartModal');
    const title = document.getElementById('modalChartTitle');
    const subtitle = document.getElementById('modalChartSubtitle');
    const btnTrade = document.getElementById('btnChartTradeTrigger');

    state.chartActiveSymbol = symbol;

    let levels = customLevels;
    let setupObj = null;

    if (!levels) {
        setupObj = (state.setups || []).find(item => item.symbol === symbol);
        if (setupObj) {
            const entryVal = getSetupPrice(setupObj);
            levels = {
                entryPrice: entryVal,
                tp1: setupObj.tp1 || 0,
                tp2: setupObj.tp2 || 0,
                tp3: setupObj.tp3 || 0,
                sl: setupObj.sl || 0,
                rr: setupObj.rr_ratio || (entryVal && setupObj.tp1 && setupObj.sl && (entryVal - setupObj.sl > 0) ? ((setupObj.tp1 - entryVal) / (entryVal - setupObj.sl)).toFixed(2) : '--'),
                setupName: setupObj.setup_name || ''
            };
        }
    }

    state.chartActiveLevels = levels;

    if (title) {
        title.innerText = `رسم بياني مباشر وتحديد مستويات الصفقة | BYBIT:${symbol}`;
    }
    if (subtitle) {
        const desc = levels?.setupName ? `النموذج: ${levels.setupName} | ` : '';
        subtitle.innerText = `${desc}خط سعر الدخول (Cyan) | أهداف جني الأرباح TP1, TP2, TP3 (أخضر) | وقف الخسارة SL (أحمر)`;
    }

    // Populate HUD Bar
    const hudEntry = document.getElementById('hudEntryVal');
    const hudTp1 = document.getElementById('hudTp1Val');
    const hudTp2 = document.getElementById('hudTp2Val');
    const hudTp3 = document.getElementById('hudTp3Val');
    const hudSl = document.getElementById('hudSlVal');
    const hudRr = document.getElementById('hudRrVal');

    if (hudEntry) hudEntry.innerText = levels?.entryPrice ? `$${formatPrice(levels.entryPrice)}` : '--';
    if (hudTp1) hudTp1.innerText = levels?.tp1 ? `$${formatPrice(levels.tp1)}` : '--';
    if (hudTp2) hudTp2.innerText = levels?.tp2 ? `$${formatPrice(levels.tp2)}` : '--';
    if (hudTp3) hudTp3.innerText = levels?.tp3 ? `$${formatPrice(levels.tp3)}` : '--';
    if (hudSl) hudSl.innerText = levels?.sl ? `$${formatPrice(levels.sl)}` : '--';
    if (hudRr) hudRr.innerText = levels?.rr ? `1 : ${levels.rr}` : '--';

    if (modal) modal.classList.remove('hidden');

    // Default to Levels chart mode
    switchChartMode('levels');
}

// Switch between Custom Levels Chart and Full TradingView Widget
function switchChartMode(mode) {
    const btnLevels = document.getElementById('btnChartModeLevels');
    const btnTv = document.getElementById('btnChartModeTv');
    const levelsBox = document.getElementById('levelsChartContainer');
    const tvBox = document.getElementById('tvChartContainer');
    const hudBar = document.getElementById('chartLevelsHudBar');

    const symbol = state.chartActiveSymbol || 'BTCUSDT';
    const levels = state.chartActiveLevels;

    if (mode === 'levels') {
        if (btnLevels) btnLevels.classList.add('active');
        if (btnTv) btnTv.classList.remove('active');
        if (levelsBox) levelsBox.style.display = 'block';
        if (tvBox) tvBox.style.display = 'none';
        if (hudBar) hudBar.style.display = 'flex';
        renderLevelsChart(symbol, levels);
    } else {
        if (btnLevels) btnLevels.classList.remove('active');
        if (btnTv) btnTv.classList.add('active');
        if (levelsBox) levelsBox.style.display = 'none';
        if (tvBox) tvBox.style.display = 'block';
        if (hudBar) hudBar.style.display = 'none';
        renderTvChart(symbol);
    }
}

// Render Bybit Candlesticks with Horizontal Price Lines (Entry, TP, SL) using LightweightCharts
async function renderLevelsChart(symbol, levels) {
    const container = document.getElementById('levelsChartContainer');
    if (!container) return;

    // Remove previous chart instance safely
    if (activeLightweightChart) {
        try {
            activeLightweightChart.remove();
        } catch (e) {
            console.warn('Error removing old chart:', e);
        }
        activeLightweightChart = null;
    }

    // Keep or inject loading spinner
    let loadingEl = document.getElementById('chartLoadingOverlay');
    if (!loadingEl) {
        container.innerHTML = `
            <div class="chart-loading-overlay" id="chartLoadingOverlay">
                <div class="spinner"></div>
                <span>جاري سحب الشموع ورسم خطوط الصفقة من Bybit...</span>
            </div>
        `;
        loadingEl = document.getElementById('chartLoadingOverlay');
    } else {
        loadingEl.style.display = 'flex';
    }

    try {
        const interval = state.timeframe === '60' ? '60' : (state.timeframe === 'D' ? 'D' : '240');
        const res = await fetch(`/api/kline?symbol=${symbol}&interval=${interval}&limit=120`);
        const json = await res.json();
        const rawList = json?.result?.list || [];

        if (loadingEl) loadingEl.style.display = 'none';

        if (!rawList || rawList.length === 0) {
            container.innerHTML = `
                <div class="chart-error-banner" style="display:flex; flex-direction:column; align-items:center; justify-content:center; height:100%; color:var(--text-dim); gap:12px;">
                    <span style="font-size:32px;">⚠️</span>
                    <p style="font-size:14px; font-weight:700;">تعذر تحميل بيانات الشموع لزوج ${symbol} من منصة Bybit.</p>
                    <button class="btn-mini-action" onclick="renderLevelsChart('${symbol}', state.chartActiveLevels)">إعادة المحاولة</button>
                </div>
            `;
            return;
        }

        // Bybit returns list sorted descending (newest first). Lightweight charts needs ascending (oldest first).
        const candleData = rawList.slice().reverse().map(item => ({
            time: Math.floor(parseInt(item[0]) / 1000),
            open: parseFloat(item[1]),
            high: parseFloat(item[2]),
            low: parseFloat(item[3]),
            close: parseFloat(item[4])
        })).filter(c => !isNaN(c.time) && !isNaN(c.close));

        if (typeof LightweightCharts === 'undefined') {
            console.error('LightweightCharts library not loaded');
            container.innerHTML = `<div class="chart-error-banner" style="padding:20px; color:#F6465D;">تعذر تحميل مكتبة الرسوم البيانية.</div>`;
            return;
        }

        const chart = LightweightCharts.createChart(container, {
            width: container.clientWidth || 900,
            height: 520,
            layout: {
                background: { color: '#07090E' },
                textColor: '#94A3B8',
                fontSize: 12,
                fontFamily: "'Segoe UI', Tahoma, Geneva, Verdana, sans-serif"
            },
            grid: {
                vertLines: { color: 'rgba(42, 53, 71, 0.35)' },
                horzLines: { color: 'rgba(42, 53, 71, 0.35)' },
            },
            crosshair: {
                mode: LightweightCharts.CrosshairMode.Normal,
                vertLine: {
                    color: 'rgba(0, 240, 255, 0.5)',
                    width: 1,
                    style: LightweightCharts.LineStyle.Dashed,
                    labelBackgroundColor: '#00F0FF',
                },
                horzLine: {
                    color: 'rgba(0, 240, 255, 0.5)',
                    width: 1,
                    style: LightweightCharts.LineStyle.Dashed,
                    labelBackgroundColor: '#00F0FF',
                }
            },
            rightPriceScale: {
                borderColor: 'rgba(42, 53, 71, 0.8)',
                autoScale: true,
                scaleMargins: {
                    top: 0.12,
                    bottom: 0.12,
                },
            },
            timeScale: {
                borderColor: 'rgba(42, 53, 71, 0.8)',
                timeVisible: true,
                secondsVisible: false,
            },
        });

        activeLightweightChart = chart;

        const candleSeries = chart.addCandlestickSeries({
            upColor: '#0ECB81',
            downColor: '#F6465D',
            borderVisible: false,
            wickUpColor: '#0ECB81',
            wickDownColor: '#F6465D',
        });

        candleSeries.setData(candleData);

        // Render Trade Levels: Entry, TP1, TP2, TP3, Stop Loss
        const ep = levels?.entryPrice;
        const tp1 = levels?.tp1;
        const tp2 = levels?.tp2;
        const tp3 = levels?.tp3;
        const sl = levels?.sl;

        // 1. Entry Price Line (Cyan, Dashed, Distinct)
        if (ep && ep > 0) {
            candleSeries.createPriceLine({
                price: ep,
                color: '#00F0FF',
                lineWidth: 2,
                lineStyle: LightweightCharts.LineStyle.Dashed,
                axisLabelVisible: true,
                title: `🎯 نقطة الدخول ENTRY ($${formatPrice(ep)})`,
            });
        }

        // 2. Take Profit 1 (Green, Solid)
        if (tp1 && tp1 > 0) {
            candleSeries.createPriceLine({
                price: tp1,
                color: '#0ECB81',
                lineWidth: 2,
                lineStyle: LightweightCharts.LineStyle.Solid,
                axisLabelVisible: true,
                title: `🟢 هدف TP1 ($${formatPrice(tp1)})`,
            });
        }

        // 3. Take Profit 2 (Bright Green, Solid)
        if (tp2 && tp2 > 0) {
            candleSeries.createPriceLine({
                price: tp2,
                color: '#10B981',
                lineWidth: 2,
                lineStyle: LightweightCharts.LineStyle.Solid,
                axisLabelVisible: true,
                title: `🚀 هدف TP2 الرئيسي ($${formatPrice(tp2)})`,
            });
        }

        // 4. Take Profit 3 (Emerald Green, Dotted)
        if (tp3 && tp3 > 0) {
            candleSeries.createPriceLine({
                price: tp3,
                color: '#34D399',
                lineWidth: 1,
                lineStyle: LightweightCharts.LineStyle.Dotted,
                axisLabelVisible: true,
                title: `💎 هدف TP3 أقصى ($${formatPrice(tp3)})`,
            });
        }

        // 5. Stop Loss Line (Red, Dashed, Prominent)
        if (sl && sl > 0) {
            candleSeries.createPriceLine({
                price: sl,
                color: '#F6465D',
                lineWidth: 2,
                lineStyle: LightweightCharts.LineStyle.Dashed,
                axisLabelVisible: true,
                title: `⛔ وقف الخسارة SL ($${formatPrice(sl)})`,
            });
        }

        chart.timeScale().fitContent();

        // Responsive resize
        const resizeObserver = new ResizeObserver(entries => {
            if (!entries || entries.length === 0 || !activeLightweightChart) return;
            const { width, height } = entries[0].contentRect;
            if (width > 0) {
                activeLightweightChart.applyOptions({ width });
            }
        });
        resizeObserver.observe(container);

    } catch (err) {
        console.error('Failed to render Lightweight Chart:', err);
        if (loadingEl) loadingEl.style.display = 'none';
        container.innerHTML = `<div class="chart-error-banner" style="padding:20px; color:#F6465D;">حدث خطأ أثناء رسم شارت المستويات: ${err.message}</div>`;
    }
}

// Render Embedded TradingView Widget as Alternative
function renderTvChart(symbol) {
    const tvBox = document.getElementById('tvChartContainer');
    if (!tvBox) return;
    tvBox.innerHTML = `<div id="tv_chart_${symbol}" style="width:100%; height:520px;"></div>`;
    setTimeout(() => {
        if (typeof TradingView !== 'undefined') {
            new TradingView.widget({
                "autosize": true,
                "symbol": `BYBIT:${symbol}`,
                "interval": state.timeframe === "60" ? "60" : (state.timeframe === "D" ? "D" : "240"),
                "timezone": "Etc/UTC",
                "theme": "dark",
                "style": "1",
                "locale": "ar_AE",
                "toolbar_bg": "#0D121D",
                "enable_publishing": false,
                "hide_side_toolbar": false,
                "allow_symbol_change": true,
                "container_id": `tv_chart_${symbol}`,
                "backgroundColor": "#07090E",
                "gridColor": "rgba(42, 53, 71, 0.25)"
            });
        }
    }, 50);
}

// Close Chart Modal and clean up
function closeChartModal() {
    const modal = document.getElementById('chartModal');
    if (modal) modal.classList.add('hidden');
    if (activeLightweightChart) {
        try {
            activeLightweightChart.remove();
        } catch (e) {}
        activeLightweightChart = null;
    }
    const levelsBox = document.getElementById('levelsChartContainer');
    if (levelsBox) levelsBox.innerHTML = '';
    const tvBox = document.getElementById('tvChartContainer');
    if (tvBox) tvBox.innerHTML = '';
}

// Helper to open Assistant directly from Chart Modal
function openAssistantFromChart() {
    const sym = state.chartActiveSymbol;
    if (!sym) return;
    closeChartModal();
    openTradingAssistant(sym);
}

// Helper to open Chart from Trading Assistant Modal
function openChartFromAssistant() {
    const sym = state.assistantCoin;
    if (!sym) return;
    closeTradingAssistant();
    openChartModal(sym);
}

// Helper to open Chart for a specific Demo Position with its exact entry and targets
function openChartForDemoPosition(positionId) {
    const pos = (state.demo.openPositions || []).find(p => p.id === positionId);
    if (!pos) return;

    const customLevels = {
        entryPrice: pos.entryPrice,
        tp1: pos.tp1 || 0,
        tp2: pos.tp2 || 0,
        tp3: pos.tp3 || 0,
        sl: pos.sl || 0,
        rr: pos.entryPrice && pos.tp1 && pos.sl && (pos.entryPrice - pos.sl > 0) ? ((pos.tp1 - pos.entryPrice) / (pos.entryPrice - pos.sl)).toFixed(2) : '--',
        setupName: pos.setupName || 'صفقة محاكاة'
    };

    openChartModal(pos.symbol, customLevels);
}

// Copy Signal Text for Sharing
function copySignal(symbol) {
    const s = (state.setups || []).find(item => item.symbol === symbol);
    if (!s) return;

    const text = 
`🎯 *إشارة صفقة فوري (SPOT) | منصة Bybit*
━━━━━━━━━━━━━━━━━━
💎 *العملة:* #${s.base_coin} (${s.symbol})
📊 *النموذج:* ${s.setup_name}
⭐ *قوة الإشارة:* ${s.score}%
💵 *السعر الحالي:* $${s.price_formatted} USDT

🟢 *نطاق الدخول المقترح:*
   ${s.entry_display} USDT

🎯 *الأهداف الاستثمارية:*
   ▫️ TP1: $${s.tp1_display} (+${s.tp1_pct}%)
   ▫️ TP2: $${s.tp2_display} (+${s.tp2_pct}%) [الهدف الرئيسي]
   ▫️ TP3: $${s.tp3_display} (+${s.tp3_pct}%)

⛔ *وقف الخسارة (Stop Loss):*
   $${s.sl_display} (-${s.sl_pct}%)

⚖️ *نسبة العائد للمخاطر (R:R):* 1 : ${s.rr_ratio}
💡 *التحليل:* ${s.rationale_arabic}
━━━━━━━━━━━━━━━━━━
_تم الفحص آلياً عبر Bybit Spot AI Screener Pro_`;

    navigator.clipboard.writeText(text).then(() => {
        showToast(`تم نسخ توصية ${s.base_coin} بنجاح إلى الحافظة! 📋`);
    }).catch(() => {
        showToast('تعذر النسخ التلقائي.');
    });
}

function showToast(msg, type = 'info') {
    const toast = document.getElementById('toastMessage');
    if (!toast) return;
    toast.innerText = msg;
    toast.className = `toast-message ${type}`;
    toast.classList.remove('hidden');
    setTimeout(() => {
        toast.classList.add('hidden');
    }, 3500);
}

function formatPrice(val) {
    const num = parseFloat(val);
    if (isNaN(num)) return val;
    if (num >= 100) return num.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    if (num >= 1) return num.toFixed(4);
    return num.toFixed(6);
}

/* ==============================================================================
   🚀 BYBIT SMART TRADING ASSISTANT & WALLET INTEGRATION LOGIC
   ============================================================================== */

// Fetch Bybit Account Status
async function fetchAccountStatus() {
    try {
        const res = await fetch('/api/account/status');
        const data = await res.json();
        const dot = document.getElementById('accountStatusDot');
        const apiDot = document.getElementById('apiDot');
        const text = document.getElementById('apiKeyStatusText');

        if (data.connected) {
            state.account.connected = true;
            state.account.maskedKey = data.maskedKey;
            if (dot) dot.className = 'wallet-status-dot green';
            if (apiDot) apiDot.className = 'status-pulse-dot green';
            if (text) text.innerText = data.maskedKey || 'Bybit V5';
        } else {
            state.account.connected = false;
            if (dot) dot.className = 'wallet-status-dot red';
            if (apiDot) apiDot.className = 'status-pulse-dot red';
            if (text) text.innerText = 'غير متصل';
        }
    } catch (e) {
        console.warn('Account status check failed:', e);
    }
}

// Fetch Live Unified Account Balance
async function fetchAccountBalance() {
    try {
        const res = await fetch('/api/account/balance');
        const data = await res.json();
        if (data.retCode === 0) {
            state.account.totalEquity = data.totalEquity || 0;
            state.account.totalWallet = data.totalWallet || 0;
            state.account.usdtAvailable = data.usdtAvailable || 0;
            state.account.holdings = data.holdings || [];

            const eqEl = document.getElementById('headerTotalEquity');
            const usdtEl = document.getElementById('headerAvailableUsdt');
            if (state.tradingMode === 'live') {
                if (eqEl) eqEl.innerText = `$${data.totalEquity.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
                if (usdtEl) usdtEl.innerText = `USDT متاح: $${data.usdtAvailable.toFixed(2)}`;
            }

            const dot = document.getElementById('accountStatusDot');
            if (dot) dot.className = 'wallet-status-dot green';
        }
    } catch (e) {
        console.warn('Balance fetch error:', e);
    }
}

// Helper to safely extract current market price from setup object
function getSetupPrice(s) {
    if (!s) return 0;
    if (typeof s.price === 'number' && s.price > 0) return s.price;
    if (s.price_formatted) {
        const p = parseFloat(s.price_formatted);
        if (!isNaN(p) && p > 0) return p;
    }
    if (typeof s.last_price === 'number' && s.last_price > 0) return s.last_price;
    if (typeof s.entry_max === 'number' && s.entry_max > 0) return s.entry_max;
    if (typeof s.entry_min === 'number' && s.entry_min > 0) return s.entry_min;
    return 0;
}

// Open Smart Trading Assistant Modal for a Specific Setup
function openTradingAssistant(symbol) {
    const s = (state.setups || []).find(item => item.symbol === symbol);
    if (!s) {
        showToast('لم يتم العثور على بيانات الصفقة المحددة', 'error');
        return;
    }

    state.activeTradeSetup = s;
    state.orderType = 'Market';

    // Populate modal banner & targets
    const currentMarketPrice = getSetupPrice(s);
    document.getElementById('tradeSymbol').innerText = s.symbol;
    document.getElementById('tradeSetupTag').innerText = `${s.setup_name || 'صفقة فورية'}`;
    document.getElementById('tradeCurrentPrice').innerText = `$${s.price_formatted || currentMarketPrice}`;
    document.getElementById('tradeScoreBadge').innerText = `⭐ ${s.score}%`;

    document.getElementById('tradeEntryVal').innerText = s.entry_display || `$${s.price_formatted || currentMarketPrice}`;
    document.getElementById('tradeTp1Val').innerText = `${s.tp1_display} (+${s.tp1_pct}%)`;
    document.getElementById('tradeTp2Val').innerText = `${s.tp2_display} (+${s.tp2_pct}%)`;
    document.getElementById('tradeTp3Val').innerText = `${s.tp3_display} (+${s.tp3_pct}%)`;
    document.getElementById('tradeSlVal').innerText = `${s.sl_display} (-${s.sl_pct}%)`;

    // Limit price prefill
    const limitInput = document.getElementById('tradeLimitPrice');
    if (limitInput) {
        limitInput.value = currentMarketPrice || 0;
    }

    // Set order type tabs
    setOrderType('Market');

    // Default amount: 25 USDT
    setTradeAmount(25);

    // Default Take Profit Strategy: TP1 + TP2
    setTpStrategy('TP1_TP2');

    // Update wallet available display & modal mode banner
    updateAssistantModalWalletDisplay();

    // Reset feedback box
    const fb = document.getElementById('tradeFeedbackBox');
    if (fb) {
        fb.className = 'trade-feedback-box hidden full-width';
        fb.innerHTML = '';
    }

    // Reset button
    const btn = document.getElementById('btnExecuteTrade');
    if (btn) {
        btn.disabled = false;
        if (state.tradingMode === 'demo') {
            document.getElementById('btnExecuteText').innerText = 'تنفيذ الصفقة في المحفظة التجريبية (Demo Trade)';
            const icon = document.getElementById('btnExecuteIcon');
            if (icon) icon.innerText = '🎮';
            btn.className = 'btn-execute-trade btn-demo-mode';
        } else {
            document.getElementById('btnExecuteText').innerText = 'تأكيد وتنفيذ أمر الشراء فوراً على Bybit';
            const icon = document.getElementById('btnExecuteIcon');
            if (icon) icon.innerText = '🚀';
            btn.className = 'btn-execute-trade';
        }
    }

    // Show modal
    const modal = document.getElementById('tradingAssistantModal');
    if (modal) modal.classList.remove('hidden');

    calculateTradeReturns();
}

function closeTradingAssistant() {
    const modal = document.getElementById('tradingAssistantModal');
    if (modal) modal.classList.add('hidden');
    state.activeTradeSetup = null;
}

function setOrderType(type) {
    state.orderType = type;
    const btnMarket = document.getElementById('orderTypeMarket');
    const btnLimit = document.getElementById('orderTypeLimit');
    const limitGrp = document.getElementById('limitPriceGroup');

    if (type === 'Market') {
        btnMarket?.classList.add('active');
        btnLimit?.classList.remove('active');
        if (limitGrp) limitGrp.style.display = 'none';
    } else {
        btnLimit?.classList.add('active');
        btnMarket?.classList.remove('active');
        if (limitGrp) limitGrp.style.display = 'block';
    }
    calculateTradeReturns();
}

function setTpStrategy(strategy) {
    state.tpStrategy = strategy;

    // Update strategy cards
    const cards = ['tpStrat1', 'tpStrat2', 'tpStrat3'];
    cards.forEach(id => {
        const el = document.getElementById(id);
        if (el) el.classList.remove('active');
    });

    const activeMap = {
        'TP1': 'tpStrat1',
        'TP1_TP2': 'tpStrat2',
        'TP1_TP2_TP3': 'tpStrat3'
    };
    const activeEl = document.getElementById(activeMap[strategy]);
    if (activeEl) activeEl.classList.add('active');

    // Update shares in pills
    const pill1 = document.getElementById('pillTp1');
    const pill2 = document.getElementById('pillTp2');
    const pill3 = document.getElementById('pillTp3');

    const share1 = document.getElementById('calcShareTp1');
    const share2 = document.getElementById('calcShareTp2');
    const share3 = document.getElementById('calcShareTp3');

    if (strategy === 'TP1') {
        if (share1) share1.innerText = 'حصة 100%';
        if (share2) share2.innerText = 'غير مشمول';
        if (share3) share3.innerText = 'غير مشمول';
        if (pill1) pill1.style.opacity = '1';
        if (pill2) pill2.style.opacity = '0.35';
        if (pill3) pill3.style.opacity = '0.35';
    } else if (strategy === 'TP1_TP2') {
        if (share1) share1.innerText = 'حصة 50%';
        if (share2) share2.innerText = 'حصة 50%';
        if (share3) share3.innerText = 'غير مشمول';
        if (pill1) pill1.style.opacity = '1';
        if (pill2) pill2.style.opacity = '1';
        if (pill3) pill3.style.opacity = '0.35';
    } else {
        if (share1) share1.innerText = 'حصة 33.3%';
        if (share2) share2.innerText = 'حصة 33.3%';
        if (share3) share3.innerText = 'حصة 33.4%';
        if (pill1) pill1.style.opacity = '1';
        if (pill2) pill2.style.opacity = '1';
        if (pill3) pill3.style.opacity = '1';
    }

    calculateTradeReturns();
}

function setTradeAmount(amount) {
    const inp = document.getElementById('tradeAmountInput');
    if (inp) {
        inp.value = amount;
    }
    const chips = document.querySelectorAll('.amount-chip');
    chips.forEach(c => {
        if (c.innerText === `$${amount}`) {
            c.classList.add('active');
        } else {
            c.classList.remove('active');
        }
    });
    calculateTradeReturns();
}

function setTradeAmountMax() {
    const isDemo = state.tradingMode === 'demo';
    const avail = isDemo ? (state.demo.balanceUsdt || 0) : (state.account.usdtAvailable || 0);
    const inp = document.getElementById('tradeAmountInput');
    if (inp) {
        inp.value = avail > 1 ? Math.floor(avail * 100) / 100 : 10;
    }
    const chips = document.querySelectorAll('.amount-chip');
    chips.forEach(c => c.classList.remove('active'));
    if (chips.length > 0) chips[chips.length - 1].classList.add('active');
    calculateTradeReturns();
}

function calculateTradeReturns() {
    const s = state.activeTradeSetup;
    if (!s) return;

    const amountInput = document.getElementById('tradeAmountInput');
    const amount = parseFloat(amountInput ? amountInput.value : 0) || 0;

    let price = getSetupPrice(s);
    if (state.orderType === 'Limit') {
        const lim = parseFloat(document.getElementById('tradeLimitPrice')?.value);
        if (lim > 0) price = lim;
    }

    const estCoins = price > 0 ? (amount / price) : 0;
    const estEl = document.getElementById('calcEstCoins');
    if (estEl) {
        estEl.innerText = `الكمية المقدرة: ~ ${estCoins.toLocaleString(undefined, {maximumFractionDigits: 4})} ${s.base_coin}`;
    }

    const tp1Pct = s.tp1_pct || 0;
    const tp2Pct = s.tp2_pct || 0;
    const tp3Pct = s.tp3_pct || 0;
    const slPct = s.sl_pct || 0;

    const gainTp1 = amount * (tp1Pct / 100);
    const gainTp2 = amount * (tp2Pct / 100);
    const gainTp3 = amount * (tp3Pct / 100);
    const lossSl = amount * (slPct / 100);

    const setEl = (id, txt) => {
        const el = document.getElementById(id);
        if (el) el.innerText = txt;
    };

    setEl('calcGainTp1', `+$${gainTp1.toFixed(2)}`);
    setEl('calcPctTp1', `(+${tp1Pct.toFixed(1)}%)`);

    setEl('calcGainTp2', `+$${gainTp2.toFixed(2)}`);
    setEl('calcPctTp2', `(+${tp2Pct.toFixed(1)}%)`);

    setEl('calcGainTp3', `+$${gainTp3.toFixed(2)}`);
    setEl('calcPctTp3', `(+${tp3Pct.toFixed(1)}%)`);

    setEl('calcLossSl', `-$${lossSl.toFixed(2)}`);
    setEl('calcPctSl', `(-${slPct.toFixed(1)}%)`);

    // Calculate Blended Net Expected Return based on selected strategy
    let blendedGain = 0;
    let blendedPct = 0;
    let planLabel = '';

    if (state.tpStrategy === 'TP1') {
        blendedGain = gainTp1;
        blendedPct = tp1Pct;
        planLabel = 'خطة تأمين الأرباح: خروج كامل 100% عند الهدف الأول TP1';
    } else if (state.tpStrategy === 'TP1_TP2') {
        blendedGain = (gainTp1 * 0.5) + (gainTp2 * 0.5);
        blendedPct = (tp1Pct * 0.5) + (tp2Pct * 0.5);
        planLabel = 'خطة متوازنة: خروج 50% عند TP1 وإكمال 50% عند TP2';
    } else {
        blendedGain = (gainTp1 * 0.3333) + (gainTp2 * 0.3333) + (gainTp3 * 0.3334);
        blendedPct = (tp1Pct * 0.3333) + (tp2Pct * 0.3333) + (tp3Pct * 0.3334);
        planLabel = 'خطة اقتناص الموجة: تقسيم 33% عند TP1 و 33% عند TP2 و 34% عند TP3';
    }

    setEl('blendedGainVal', `+$${blendedGain.toFixed(2)} USDT`);
    setEl('blendedPctVal', `(+${blendedPct.toFixed(2)}%)`);
    setEl('blendedPlanLabel', planLabel);
}

async function executeBybitTrade() {
    const s = state.activeTradeSetup;
    if (!s) return;

    const amountInput = document.getElementById('tradeAmountInput');
    const amount = parseFloat(amountInput ? amountInput.value : 0);
    if (!amount || amount <= 0) {
        showTradeFeedback('يرجى تحديد مبلغ استثمار صالح (أكبر من 0 USDT)', 'error');
        return;
    }

    const btn = document.getElementById('btnExecuteTrade');
    const btnText = document.getElementById('btnExecuteText');

    btn.disabled = true;
    btnText.innerText = 'جاري التوقيع وإرسال الأمر إلى سيرفرات Bybit...';
    showTradeFeedback('⏳ جاري إرسال الأمر وتوقيعه عبر HMAC-SHA256...', 'loading');

    const entryP = getSetupPrice(s);
    const payload = {
        symbol: s.symbol,
        side: 'Buy',
        orderType: state.orderType,
        qty: state.orderType === 'Market' ? amount.toString() : (entryP > 0 ? (amount / entryP).toFixed(4) : amount.toString()),
        marketUnit: state.orderType === 'Market' ? 'quoteCoin' : undefined,
        tpStrategy: state.tpStrategy
    };

    if (state.orderType === 'Limit') {
        const limitPrice = parseFloat(document.getElementById('tradeLimitPrice')?.value);
        if (!limitPrice || limitPrice <= 0) {
            showTradeFeedback('يرجى كتابة سعر دخول صالح لأمر الـ Limit', 'error');
            btn.disabled = false;
            btnText.innerText = 'تأكيد وتنفيذ أمر الشراء فوراً على Bybit';
            return;
        }
        payload.price = limitPrice.toString();
    }

    const autoTp = document.getElementById('chkAutoTp')?.checked;
    const autoSl = document.getElementById('chkAutoSl')?.checked;

    if (autoTp) {
        if (state.tpStrategy === 'TP1') {
            payload.takeProfit = (s.tp1 || s.tp1_display || '').toString();
        } else if (state.tpStrategy === 'TP1_TP2') {
            payload.takeProfit = (s.tp2 || s.tp2_display || '').toString();
        } else {
            payload.takeProfit = (s.tp3 || s.tp3_display || '').toString();
        }
    }
    payload.tp1 = (s.tp1 || s.tp1_display || '').toString();
    payload.tp2 = (s.tp2 || s.tp2_display || '').toString();
    payload.tp3 = (s.tp3 || s.tp3_display || '').toString();
    payload.setupName = s.setup_name || s.pattern || 'صفقة Bybit';

    if (autoSl && (s.sl || s.sl_display)) {
        payload.stopLoss = (s.sl || s.sl_display).toString();
    }

    try {
        const res = await fetch('/api/order/place', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const result = await res.json();

        if (result.retCode === 0) {
            playAlertBeep();
            const orderId = result.result?.orderId || result.result?.orderLinkId || 'OK';
            const stratLabel = state.tpStrategy === 'TP1' ? 'الهدف 1 فقط (100%)' : (state.tpStrategy === 'TP1_TP2' ? 'الهدف 1 + 2 (50%/50%)' : 'جميع الأهداف 1+2+3');
            showTradeFeedback(`
                <div style="font-size: 15px; font-weight: 800; color: #0ECB81; margin-bottom: 6px;">
                    🎉 تم إرسال وتنفيذ الأمر بنجاح على منصة Bybit!
                </div>
                <div style="font-size: 12px; color: var(--text-secondary);">
                    رقم الأمر في بايبت: <code class="mono" style="color: var(--cyan-accent); font-weight: bold;">${orderId}</code>
                </div>
                <div style="font-size: 12px; color: var(--text-dim); margin-top: 4px;">
                    النوع: ${state.orderType === 'Market' ? 'شراء فوري Market' : 'أمر محدد Limit'} | المبلغ: $${amount} USDT | استراتيجية الأهداف: <b style="color:var(--gold-accent);">${stratLabel}</b>
                </div>
            `, 'success');

            btnText.innerText = '✅ تم تنفيذ العملية بنجاح';
            fetchAccountBalance();
            showToast(`تم تنفيذ أمر الشراء لعملة ${s.base_coin} بنجاح!`, 'success');
        } else {
            const err = result.retMsg || result.error || 'فشل تنفيذ الأمر';
            showTradeFeedback(`❌ خطأ من منصة Bybit: ${err} (كود: ${result.retCode})`, 'error');
            btn.disabled = false;
            btnText.innerText = 'إعادة المحاولة';
        }
    } catch (e) {
        showTradeFeedback(`❌ خطأ في الاتصال بالخادم المحلي: ${e.message}`, 'error');
        btn.disabled = false;
        btnText.innerText = 'إعادة المحاولة';
    }
}

function showTradeFeedback(html, type) {
    const fb = document.getElementById('tradeFeedbackBox');
    if (!fb) return;
    fb.className = `trade-feedback-box full-width ${type}`;
    fb.innerHTML = html;
}

// Holdings & Wallet Modal Management
function openHoldingsModal() {
    const modal = document.getElementById('holdingsModal');
    if (modal) modal.classList.remove('hidden');

    const totalEqEl = document.getElementById('holdingsTotalEquity');
    const usdtAvailEl = document.getElementById('holdingsUsdtAvailable');
    if (totalEqEl) totalEqEl.innerText = `$${(state.account.totalEquity || 0).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
    if (usdtAvailEl) usdtAvailEl.innerText = `$${(state.account.usdtAvailable || 0).toFixed(2)} USDT`;

    renderHoldingsTable();
    fetchAccountBalance().then(renderHoldingsTable);
}

function closeHoldingsModal() {
    const modal = document.getElementById('holdingsModal');
    if (modal) modal.classList.add('hidden');
}

function renderHoldingsTable() {
    const tbody = document.getElementById('holdingsTableBody');
    if (!tbody) return;

    const list = state.account.holdings || [];
    if (list.length === 0) {
        tbody.innerHTML = `<tr><td colspan="4" style="text-align:center; padding: 25px; color: var(--text-dim);">لا توجد عملات في المحفظة حالياً.</td></tr>`;
        return;
    }

    tbody.innerHTML = list.map(c => `
        <tr>
            <td>
                <div style="display:flex; align-items:center; gap:8px;">
                    <span style="font-weight:800; color:var(--text-primary); font-size:14px;">${c.coin}</span>
                </div>
            </td>
            <td class="mono" style="font-weight:700;">${c.balance.toLocaleString(undefined, {maximumFractionDigits: 4})}</td>
            <td class="mono text-green">${c.available.toLocaleString(undefined, {maximumFractionDigits: 4})}</td>
            <td class="mono text-gold" style="font-weight:800;">$${c.usdValue.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}</td>
        </tr>
    `).join('');
}

// Open Orders Modal Management
async function openOrdersModal() {
    const modal = document.getElementById('openOrdersModal');
    if (modal) modal.classList.remove('hidden');
    fetchOpenOrders();
}

function closeOrdersModal() {
    const modal = document.getElementById('openOrdersModal');
    if (modal) modal.classList.add('hidden');
}

async function fetchOpenOrders() {
    const container = document.getElementById('openOrdersListContainer');
    if (!container) return;

    container.innerHTML = `
        <div class="loading-state">
            <div class="spinner"></div>
            <p>جاري جلب الأوامر المفتوحة من منصة Bybit...</p>
        </div>
    `;

    try {
        const res = await fetch('/api/orders/open');
        const data = await res.json();
        const orders = data.result?.list || [];
        state.openOrders = orders;

        const badge = document.getElementById('openOrdersBadge');
        if (badge) {
            badge.innerText = orders.length;
            badge.style.display = orders.length > 0 ? 'inline-flex' : 'none';
        }

        if (orders.length === 0) {
            container.innerHTML = `
                <div class="empty-state" style="padding: 30px;">
                    <span style="font-size: 36px; margin-bottom: 8px;">✨</span>
                    <h4>لا توجد أوامر معلقة حالياً على Bybit Spot</h4>
                    <p style="font-size: 12px; color: var(--text-dim); margin-top: 4px;">كافة الأوامر تم تنفيذها أو إلغاؤها بالكامل.</p>
                </div>
            `;
            return;
        }

        container.innerHTML = orders.map(o => {
            const hasSl = o.triggerPrice && parseFloat(o.triggerPrice) > 0;
            const isTpSl = o.stopOrderType === 'tpslOrder' || hasSl;
            return `
            <div class="order-card-item">
                <div class="oci-header">
                    <span class="oci-sym mono">${o.symbol}</span>
                    <span class="oci-side ${o.side === 'Buy' ? 'text-green' : 'text-red'}">
                        ${o.side === 'Buy' ? 'شراء' : 'بيع'} ${isTpSl ? '🎯 TP/SL مركب OCO' : `(${o.orderType})`}
                    </span>
                    <span class="oci-time mono text-dim">${new Date(parseInt(o.createdTime)).toLocaleTimeString('ar-EG')}</span>
                </div>
                <div class="oci-details-grid">
                    <div><span class="lbl">الهدف (TP):</span> <b class="mono text-green">$${parseFloat(o.price).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 6})}</b></div>
                    ${hasSl ? `<div><span class="lbl">الستوب (SL):</span> <b class="mono text-red">$${parseFloat(o.triggerPrice).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 6})}</b></div>` : ''}
                    <div><span class="lbl">الكمية:</span> <b class="mono">${parseFloat(o.qty).toLocaleString()}</b></div>
                    <div><span class="lbl">المنفذ:</span> <b class="mono text-cyan">${parseFloat(o.cumExecQty || 0).toLocaleString()}</b></div>
                </div>
                <div class="oci-actions" style="display:flex; gap:8px;">
                    <button type="button" class="btn-chart-order" onclick="openChartForOpenOrder('${o.symbol}', '${o.price}', '${o.side}', '${o.triggerPrice || ''}')" title="عرض السعر على الشارت">
                        📊 الشارت
                    </button>
                    <button type="button" class="btn-cancel-order" onclick="cancelBybitOrder('${o.symbol}', '${o.orderId}')">
                        ✖ إلغاء الأمر
                    </button>
                </div>
            </div>
            `;
        }).join('');
    } catch (e) {
        container.innerHTML = `<div class="empty-state"><p style="color:var(--red-loss);">خطأ في جلب الأوامر: ${e.message}</p></div>`;
    }
}

async function cancelBybitOrder(symbol, orderId) {
    if (!confirm(`هل أنت متأكد من رغبتك في إلغاء الأمر لعملة ${symbol}؟`)) return;

    try {
        const res = await fetch('/api/order/cancel', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ symbol, orderId })
        });
        const data = await res.json();
        if (data.retCode === 0) {
            showToast('تم إلغاء الأمر بنجاح!', 'success');
            fetchOpenOrders();
            fetchAccountBalance();
        } else {
            showToast(`فشل إلغاء الأمر: ${data.retMsg}`, 'error');
        }
    } catch (e) {
        showToast(`خطأ في الإلغاء: ${e.message}`, 'error');
    }
}

// API Key Settings Modal
function openApiModal() {
    const modal = document.getElementById('apiModal');
    if (modal) modal.classList.remove('hidden');
    const msg = document.getElementById('apiFeedbackMsg');
    if (msg) msg.className = 'api-feedback-msg hidden';
}

function closeApiModal() {
    const modal = document.getElementById('apiModal');
    if (modal) modal.classList.add('hidden');
}

async function saveApiKeys() {
    const key = document.getElementById('inputApiKey')?.value.trim();
    const secret = document.getElementById('inputApiSecret')?.value.trim();
    const msg = document.getElementById('apiFeedbackMsg');

    if (!key || !secret) {
        if (msg) {
            msg.className = 'api-feedback-msg error';
            msg.innerText = 'يرجى كتابة كل من API Key و API Secret';
        }
        return;
    }

    try {
        const res = await fetch('/api/save-keys', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ apiKey: key, apiSecret: secret })
        });
        const data = await res.json();
        if (data.success) {
            if (msg) {
                msg.className = 'api-feedback-msg success';
                msg.innerText = '✅ تم حفظ المفاتيح وتفعيل الربط بنجاح!';
            }
            fetchAccountStatus();
            fetchAccountBalance();
            setTimeout(closeApiModal, 1200);
        } else {
            if (msg) {
                msg.className = 'api-feedback-msg error';
                msg.innerText = data.error || 'حدث خطأ أثناء الحفظ';
            }
        }
    } catch (e) {
        if (msg) {
            msg.className = 'api-feedback-msg error';
            msg.innerText = e.message;
        }
    }
}

// ==============================================================================
// 🎮 Paper Trading (Demo Simulation) Engine & Mode Management
// ==============================================================================

function initTradingModeUI() {
    const savedMode = localStorage.getItem('bybit_spot_mode') || 'demo';
    state.tradingMode = savedMode;
    updateModeUI();
}

function switchTradingMode(mode) {
    if (mode !== 'demo' && mode !== 'live') return;
    state.tradingMode = mode;
    localStorage.setItem('bybit_spot_mode', mode);
    updateModeUI();

    if (mode === 'demo') {
        showToast('تم التبديل إلى وضع المحاكاة التجريبي الافتراضي (Demo) 🎮', 'info');
        fetchDemoAccountState();
    } else {
        showToast('تم التبديل إلى وضع التداول الحقيقي المباشر على Bybit 🔴', 'warning');
        fetchAccountBalance();
    }
}

function updateModeUI() {
    const isDemo = state.tradingMode === 'demo';
    const btnDemo = document.getElementById('modeSwitchDemo');
    const btnLive = document.getElementById('modeSwitchLive');
    const walletWidget = document.getElementById('walletHeaderWidget');
    const walletIcon = document.getElementById('walletWidgetIcon');
    const walletTitle = document.getElementById('walletWidgetTitle');
    const eqEl = document.getElementById('headerTotalEquity');
    const usdtEl = document.getElementById('headerAvailableUsdt');
    const demoNavBtn = document.getElementById('btnOpenDemoModal');

    if (btnDemo) btnDemo.classList.toggle('active', isDemo);
    if (btnLive) btnLive.classList.toggle('active', !isDemo);

    if (isDemo) {
        if (walletWidget) walletWidget.classList.add('demo-mode-active');
        if (walletIcon) walletIcon.innerText = '🎮';
        if (walletTitle) walletTitle.innerText = 'المحفظة التجريبية (Demo):';
        if (eqEl) eqEl.innerText = `$${(state.demo.totalEquity || 10000).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
        if (usdtEl) usdtEl.innerText = `USDT تجريبي متاح: $${(state.demo.balanceUsdt || 10000).toFixed(2)}`;
        if (demoNavBtn) demoNavBtn.classList.add('active-demo');
    } else {
        if (walletWidget) walletWidget.classList.remove('demo-mode-active');
        if (walletIcon) walletIcon.innerText = '💼';
        if (walletTitle) walletTitle.innerText = 'إجمالي المحفظة (Bybit V5):';
        if (eqEl) eqEl.innerText = `$${(state.account.totalEquity || 0).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
        if (usdtEl) usdtEl.innerText = `USDT متاح: $${(state.account.usdtAvailable || 0).toFixed(2)}`;
        if (demoNavBtn) demoNavBtn.classList.remove('active-demo');
    }

    updateAssistantModalWalletDisplay();
}

function handleWalletWidgetClick() {
    if (state.tradingMode === 'demo') {
        openDemoModal();
    } else {
        openHoldingsModal();
    }
}

function toggleTradingModeFromModal() {
    const newMode = state.tradingMode === 'demo' ? 'live' : 'demo';
    switchTradingMode(newMode);
}

function updateAssistantModalWalletDisplay() {
    const isDemo = state.tradingMode === 'demo';
    const banner = document.getElementById('assistantModeBanner');
    const ambTitle = document.getElementById('ambTitle');
    const ambDesc = document.getElementById('ambDesc');
    const ambIcon = document.getElementById('ambIcon');
    const ambSwitch = document.getElementById('btnAmbSwitch');
    const availLabel = document.getElementById('tradeWalletAvail');
    const execBtn = document.getElementById('btnExecuteTrade');
    const execText = document.getElementById('btnExecuteText');
    const execIcon = document.getElementById('btnExecuteIcon');

    if (isDemo) {
        if (banner) { banner.className = 'assistant-mode-banner demo'; }
        if (ambIcon) ambIcon.innerText = '🎮';
        if (ambTitle) ambTitle.innerText = 'وضع التداول التجريبي الافتراضي (Paper Trading):';
        if (ambDesc) ambDesc.innerText = 'يتم الدخول بالصفقة وفق السعر اللحظي الحقيقي وخصم الرسوم التقديرية (0.10%) دون المخاطرة بأي رصيد حقيقي.';
        if (ambSwitch) ambSwitch.innerText = 'تبديل للحقيقي 🔴';
        if (availLabel) availLabel.innerText = `USDT تجريبي متاح في المحفظة: $${(state.demo.balanceUsdt || 10000).toFixed(2)}`;
        if (execBtn) execBtn.className = 'btn-execute-trade btn-demo-mode';
        if (execIcon) execIcon.innerText = '🎮';
        if (execText) execText.innerText = 'تنفيذ الصفقة في المحفظة التجريبية (Demo Trade)';
    } else {
        if (banner) { banner.className = 'assistant-mode-banner live'; }
        if (ambIcon) ambIcon.innerText = '⚠️';
        if (ambTitle) ambTitle.innerText = 'وضع التداول الحقيقي المباشر (Bybit V5 Live):';
        if (ambDesc) ambDesc.innerText = 'تنبيه: سيتم خصم أموال حقيقية من محفظتك في Bybit وتنفيذ أمر الشراء الفعلي في السوق.';
        if (ambSwitch) ambSwitch.innerText = 'تبديل للمحاكاة 🎮';
        if (availLabel) availLabel.innerText = `USDT متاح في محفظة Bybit: $${(state.account.usdtAvailable || 0).toFixed(2)}`;
        if (execBtn) execBtn.className = 'btn-execute-trade';
        if (execIcon) execIcon.innerText = '🚀';
        if (execText) execText.innerText = 'تأكيد وتنفيذ أمر الشراء فوراً على Bybit';
    }
}

// Router for execute trade button
function handleExecuteTradeClick() {
    if (state.tradingMode === 'demo') {
        executeDemoTrade();
    } else {
        executeBybitTrade();
    }
}

// Execute Demo Trade
async function executeDemoTrade() {
    const s = state.activeTradeSetup;
    if (!s) return;

    const amountInput = document.getElementById('tradeAmountInput');
    const amount = parseFloat(amountInput ? amountInput.value : 0);
    if (!amount || amount <= 0) {
        showTradeFeedback('يرجى تحديد مبلغ استثمار صالح (أكبر من 0 USDT)', 'error');
        return;
    }

    if (amount > (state.demo.balanceUsdt || 0)) {
        showTradeFeedback(`رصيد USDT التجريبي غير كافٍ. المتاح حالياً: $${(state.demo.balanceUsdt || 0).toFixed(2)} USDT.<br>يمكنك إعادة شحن المحفظة بضغطة زر من نافذة صفقات المحاكاة.`, 'error');
        return;
    }

    let entryPrice = getSetupPrice(s);
    if (state.orderType === 'Limit') {
        const lim = parseFloat(document.getElementById('tradeLimitPrice')?.value);
        if (lim > 0) entryPrice = lim;
    }

    if (!entryPrice || entryPrice <= 0) {
        showTradeFeedback('تعذر قراءة السعر اللحظي للعملة، يرجى كتابة السعر يدوياً في خانة Limit أو إعادة فحص السوق.', 'error');
        btn.disabled = false;
        btnText.innerText = 'إعادة المحاولة';
        return;
    }

    const btn = document.getElementById('btnExecuteTrade');
    const btnText = document.getElementById('btnExecuteText');
    btn.disabled = true;
    btnText.innerText = 'جاري محاكاة تنفيذ الصفقة بالأسعار اللحظية...';
    showTradeFeedback('⏳ جاري تسجيل الصفقة وخصم الرسوم التقديرية (0.10%)...', 'loading');

    const autoTp = document.getElementById('chkAutoTp')?.checked;
    const autoSl = document.getElementById('chkAutoSl')?.checked;

    const payload = {
        symbol: s.symbol,
        side: 'Buy',
        orderType: state.orderType,
        amountUsdt: amount,
        price: entryPrice,
        setupName: s.setup_name || 'صفقة محاكاة',
        score: s.score || 0,
        tpStrategy: state.tpStrategy,
        tp1: autoTp ? (s.tp1 || parseFloat(s.tp1_display) || null) : null,
        tp2: autoTp ? (s.tp2 || parseFloat(s.tp2_display) || null) : null,
        tp3: autoTp ? (s.tp3 || parseFloat(s.tp3_display) || null) : null,
        sl: autoSl ? (s.sl || parseFloat(s.sl_display) || null) : null
    };

    try {
        const res = await fetch('/api/demo/order', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json();

        if (data.retCode === 0) {
            playAlertBeep();
            const pos = data.result?.position || {};
            const fee = pos.feePaid || (amount * 0.001);
            showTradeFeedback(`
                <div style="font-size: 15px; font-weight: 800; color: #0ECB81; margin-bottom: 6px;">
                    🎮 تم تنفيذ الصفقة بنجاح في المحفظة التجريبية!
                </div>
                <div style="font-size: 12px; color: var(--text-secondary);">
                    العملة: <b style="color:var(--cyan-accent);">${s.symbol}</b> | سعر الدخول اللحظي: <code class="mono" style="color:#FFF;">$${entryPrice}</code>
                </div>
                <div style="font-size: 12px; color: var(--text-dim); margin-top: 4px;">
                    المبلغ المستثمر: $${amount.toFixed(2)} USDT | الرسوم المقدرة (0.1%): $${fee.toFixed(4)} USDT | الرصيد المتبقي: $${(data.result?.remainingBalance || 0).toFixed(2)}
                </div>
                <div style="margin-top: 8px;">
                    <button type="button" class="btn-mini-action" onclick="openDemoModal(); closeTradingAssistant();">📊 عرض الصفقات في محفظة المحاكاة ⚡</button>
                </div>
            `, 'success');

            btnText.innerText = '✅ تم فتح الصفقة التجريبية';
            await fetchDemoAccountState();
            showToast(`تم فتح صفقة محاكاة لعملة ${s.base_coin} بنجاح! 🎮`, 'success');
        } else {
            showTradeFeedback(`❌ فشل التنفيذ التجريبي: ${data.retMsg}`, 'error');
            btn.disabled = false;
            btnText.innerText = 'إعادة المحاولة';
        }
    } catch (e) {
        showTradeFeedback(`❌ خطأ في الاتصال بالسيرفر: ${e.message}`, 'error');
        btn.disabled = false;
        btnText.innerText = 'إعادة المحاولة';
    }
}

// Fetch Full Demo Account State
async function fetchDemoAccountState() {
    try {
        const res = await fetch('/api/demo/state');
        const data = await res.json();
        if (data && data.retCode === 0) {
            state.demo = data;

            // Update badge in header
            const openCount = (data.openPositions || []).length;
            const badge = document.getElementById('demoTradesCountBadge');
            if (badge) {
                badge.innerText = openCount;
                badge.style.display = openCount > 0 ? 'inline-block' : 'none';
            }

            // Update modal summary KPIs if elements exist
            const eqEl = document.getElementById('demoTotalEquity');
            const availEl = document.getElementById('demoAvailableUsdt');
            const initEl = document.getElementById('demoInitialBalText');
            const winEl = document.getElementById('demoWinRate');
            const countEl = document.getElementById('demoWinTradesCount');
            const pnlEl = document.getElementById('demoRealizedPnl');
            const pctEl = document.getElementById('demoPnlPercent');

            if (eqEl) eqEl.innerText = `$${(data.totalEquity || 10000).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
            if (availEl) availEl.innerText = `$${(data.balanceUsdt || 10000).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
            if (initEl) initEl.innerText = `رأس المال المبدئي: $${(data.initialBalance || 10000).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;

            const stats = data.stats || {};
            if (winEl) winEl.innerText = `${stats.winRate || 0}%`;
            if (countEl) countEl.innerText = `${stats.winningTrades || 0} رابحة / ${stats.totalTrades || 0} إجمالي`;

            const realPnl = stats.totalRealizedPnL || 0;
            if (pnlEl) {
                pnlEl.innerText = `${realPnl >= 0 ? '+' : ''}$${realPnl.toFixed(2)}`;
                pnlEl.className = `dk-val mono ${realPnl >= 0 ? 'text-green' : 'text-red'}`;
            }
            if (pctEl) {
                const initB = data.initialBalance || 10000;
                const pnlPcnt = initB > 0 ? ((realPnl / initB) * 100) : 0;
                pctEl.innerText = `(${pnlPcnt >= 0 ? '+' : ''}${pnlPcnt.toFixed(2)}%)`;
                pctEl.className = `dk-sub mono ${pnlPcnt >= 0 ? 'text-green' : 'text-red'}`;
            }

            // Update tab badge counts
            const bOpen = document.getElementById('badgeOpenPositions');
            const bHist = document.getElementById('badgeHistory');
            if (bOpen) bOpen.innerText = openCount;
            if (bHist) bHist.innerText = (data.history || []).length;

            // If in Demo mode, refresh header widget
            if (state.tradingMode === 'demo') {
                const hEq = document.getElementById('headerTotalEquity');
                const hUsdt = document.getElementById('headerAvailableUsdt');
                if (hEq) hEq.innerText = `$${(data.totalEquity || 10000).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
                if (hUsdt) hUsdt.innerText = `USDT تجريبي متاح: $${(data.balanceUsdt || 10000).toFixed(2)}`;
            }

            renderDemoPositionsTable();
            renderDemoHistoryTable();
        }
    } catch (e) {
        console.warn('fetchDemoAccountState error:', e);
    }
}

// Monitor live PnL of open demo positions every 3s
async function monitorDemoPositionsLive() {
    if (!state.demo || !state.demo.openPositions || state.demo.openPositions.length === 0) return;

    let hasChanges = false;
    for (const pos of state.demo.openPositions) {
        // Find live price from setups or pulse
        const s = (state.setups || []).find(item => item.symbol === pos.symbol);
        let livePrice = s ? getSetupPrice(s) : null;

        if (!livePrice && pos.symbol === 'BTCUSDT' && document.getElementById('btcPrice')) {
            const raw = document.getElementById('btcPrice').innerText.replace('$', '').replace(/,/g, '');
            livePrice = parseFloat(raw);
        }
        if (!livePrice && pos.symbol === 'ETHUSDT' && document.getElementById('ethPrice')) {
            const raw = document.getElementById('ethPrice').innerText.replace('$', '').replace(/,/g, '');
            livePrice = parseFloat(raw);
        }
        if (!livePrice && pos.symbol === 'SOLUSDT' && document.getElementById('solPrice')) {
            const raw = document.getElementById('solPrice').innerText.replace('$', '').replace(/,/g, '');
            livePrice = parseFloat(raw);
        }

        if (livePrice && livePrice > 0) {
            pos.currentPrice = livePrice;
            const gross = pos.qty * livePrice;
            const fee = gross * 0.001;
            pos.currentValue = round(gross - fee, 2);
            pos.unrealizedPnL = round(pos.currentValue - pos.amountUsdt, 2);
            pos.unrealizedPnLPct = round(((livePrice - pos.entryPrice) / pos.entryPrice) * 100, 2);
            hasChanges = true;

            // Auto TP Trigger Check
            const tpTarget = pos.tp2 || pos.tp1;
            if (tpTarget && livePrice >= tpTarget && !pos.isTriggeringClose) {
                pos.isTriggeringClose = true;
                autoCloseDemoPosition(pos.id, livePrice, `تحقيق الهدف المنشود (${tpTarget}) 🎯`);
                return;
            }

            // Auto SL Trigger Check
            if (pos.sl && livePrice <= pos.sl && !pos.isTriggeringClose) {
                pos.isTriggeringClose = true;
                autoCloseDemoPosition(pos.id, livePrice, `تفعيل وقف الخسارة الإلزامي (${pos.sl}) 🛡️`);
                return;
            }
        }
    }

    if (hasChanges) {
        renderDemoPositionsTable();
    }
}

function round(val, dec = 2) {
    return Number(Math.round(val + 'e' + dec) + 'e-' + dec);
}

// Auto close helper
async function autoCloseDemoPosition(positionId, closePrice, reason) {
    try {
        const res = await fetch('/api/demo/close', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ positionId, closePrice, reason })
        });
        const data = await res.json();
        if (data.retCode === 0) {
            playAlertBeep();
            const rec = data.result || {};
            const pnl = rec.realizedPnL || 0;
            const isProfit = pnl >= 0;
            showToast(`${isProfit ? '🎉' : '🛡️'} ${reason}: ${rec.symbol} (${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)})`, isProfit ? 'success' : 'warning');
            await fetchDemoAccountState();
        }
    } catch (e) {
        console.warn('Auto close error:', e);
    }
}

// Manual close position
async function manualCloseDemoPosition(positionId) {
    const pos = (state.demo.openPositions || []).find(p => p.id === positionId);
    if (!pos) return;

    const pnl = pos.unrealizedPnL || 0;
    const confirmMsg = `هل تريد إغلاق وتسييل صفقة ${pos.symbol} الآن بسعر السوق؟\n\nالسعر الحالي: $${pos.currentPrice}\nالربح/الخسارة اللحظية: ${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)} (${pos.unrealizedPnLPct || 0}%)\n\nسيتم إضافة العائد إلى كاش المحفظة التجريبية فوراً.`;
    if (!confirm(confirmMsg)) return;

    try {
        const res = await fetch('/api/demo/close', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                positionId: positionId,
                closePrice: pos.currentPrice || pos.entryPrice,
                reason: 'إغلاق وتسييل يدوي ⚡'
            })
        });
        const data = await res.json();
        if (data.retCode === 0) {
            playAlertBeep();
            showToast(`تم تسييل وإغلاق صفقة ${pos.symbol} بنجاح!`, 'success');
            await fetchDemoAccountState();
        } else {
            showToast(`خطأ في الإغلاق: ${data.retMsg}`, 'error');
        }
    } catch (e) {
        showToast('فشل الاتصال: ' + e.message, 'error');
    }
}

// Render Demo Tables
state.prevDemoPrices = state.prevDemoPrices || {};

function renderDemoPositionsTable() {
    const tbody = document.getElementById('demoPositionsTableBody');
    if (!tbody) return;

    const list = state.demo.openPositions || [];
    if (list.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="8" style="text-align:center; padding: 30px; color: var(--text-dim);">
                    لا توجد صفقات محاكاة مفتوحة حالياً. يمكنك فتح صفقة تجريبية مباشرة من أي كارت عملة في الفاحص! 🚀
                </td>
            </tr>
        `;
        return;
    }

    tbody.innerHTML = list.map(pos => {
        const pnl = pos.unrealizedPnL || 0;
        const pnlPct = pos.unrealizedPnLPct || 0;
        const isUp = pnl >= 0;
        const pnlClass = isUp ? 'text-green' : 'text-red';
        const pnlSign = isUp ? '+' : '-';
        const absPnl = Math.abs(pnl);
        const pnlDisplay = absPnl < 0.01 && absPnl > 0 ? absPnl.toFixed(4) : absPnl.toFixed(2);

        // Price Tick Flash Animation Check
        const prevPrice = state.prevDemoPrices[pos.id];
        let tickClass = '';
        if (prevPrice !== undefined && pos.currentPrice !== undefined) {
            if (pos.currentPrice > prevPrice) {
                tickClass = 'tick-flash-up';
            } else if (pos.currentPrice < prevPrice) {
                tickClass = 'tick-flash-down';
            }
        }
        state.prevDemoPrices[pos.id] = pos.currentPrice;

        const tpText = pos.tp2 ? `TP1: $${pos.tp1} | TP2: $${pos.tp2}` : (pos.tp1 ? `TP1: $${pos.tp1}` : '--');
        const slText = pos.sl ? `SL: $${pos.sl}` : '--';

        return `
            <tr>
                <td>
                    <div style="font-weight: 800; font-size: 15px; color: var(--text-primary); display:flex; align-items:center; gap:6px;">
                        <span>${pos.symbol}</span>
                        <span class="live-dot-mini green"></span>
                    </div>
                    <span class="badge-spot-mini">${pos.setupName || 'صفقة محاكاة'}</span>
                </td>
                <td class="mono font-bold">$${formatPrice(pos.entryPrice)}</td>
                <td class="mono font-bold text-cyan" style="font-size:14px;">
                    <span class="${tickClass}">$${formatPrice(pos.currentPrice || pos.entryPrice)}</span>
                    <span class="pulse-radar-dot"></span>
                </td>
                <td class="mono">$${pos.amountUsdt.toFixed(2)} USDT</td>
                <td class="mono font-bold">${pos.qty.toLocaleString(undefined, {maximumFractionDigits: 4})} ${pos.baseCoin}</td>
                <td>
                    <div style="font-size: 11px; color: var(--green-profit); font-weight: 700;">🎯 ${tpText}</div>
                    <div style="font-size: 11px; color: var(--red-loss); font-weight: 700;">🛡️ ${slText}</div>
                </td>
                <td>
                    <div class="mono font-bold ${pnlClass}" style="font-size: 15px;">
                        ${pnlSign}$${pnlDisplay}
                    </div>
                    <span class="pnl-pill-mini ${isUp ? 'profit' : 'loss'} mono">${isUp ? '+' : ''}${pnlPct.toFixed(2)}%</span>
                </td>
                <td style="white-space: nowrap; display: flex; gap: 6px; align-items: center;">
                    <button type="button" class="btn-demo-chart" onclick="openChartForDemoPosition('${pos.id}')" title="عرض الصفقة وخطوط الدخول والهدف والستوب على الشارت">
                        📊 الشارت
                    </button>
                    <button type="button" class="btn-demo-liquidate" onclick="manualCloseDemoPosition('${pos.id}')" title="إغلاق فوري بسعر السوق">
                        ⚡ إغلاق وتسييل
                    </button>
                </td>
            </tr>
        `;
    }).join('');
}

function renderDemoHistoryTable() {
    const tbody = document.getElementById('demoHistoryTableBody');
    if (!tbody) return;

    const list = state.demo.history || [];
    if (list.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="8" style="text-align:center; padding: 30px; color: var(--text-dim);">
                    سجل الصفقات فارغ حالياً. الصفقات المغلقة ستظهر هنا مع تفاصيل الأرباح ونسب العائد.
                </td>
            </tr>
        `;
        return;
    }

    tbody.innerHTML = list.map(rec => {
        const pnl = rec.realizedPnL || 0;
        const pnlPct = rec.pnlPct || 0;
        const isUp = pnl >= 0;
        const pnlClass = isUp ? 'text-green' : 'text-red';
        const pnlSign = isUp ? '+' : '';

        return `
            <tr>
                <td><b>${rec.symbol}</b></td>
                <td class="mono">$${formatPrice(rec.entryPrice)}</td>
                <td class="mono font-bold">$${formatPrice(rec.closePrice)}</td>
                <td class="mono">$${rec.amountUsdt.toFixed(2)}</td>
                <td class="mono text-gold font-bold">$${(rec.netReturn || 0).toFixed(2)}</td>
                <td>
                    <b class="${pnlClass} mono">${pnlSign}$${pnl.toFixed(2)}</b>
                    <small class="${pnlClass} mono">(${pnlSign}${pnlPct.toFixed(2)}%)</small>
                </td>
                <td>
                    <span class="reason-badge ${isUp ? 'win' : 'loss'}">${rec.exitReason || 'إغلاق'}</span>
                </td>
                <td class="text-dim mono" style="font-size: 11px;">${rec.closedAt || '--'}</td>
            </tr>
        `;
    }).join('');
}

// Demo Modal Navigation & Actions
function openDemoModal() {
    const modal = document.getElementById('demoModal');
    if (modal) modal.classList.remove('hidden');
    switchDemoTab('positions');
    fetchDemoAccountState();
}

function closeDemoModal() {
    const modal = document.getElementById('demoModal');
    if (modal) modal.classList.add('hidden');
}

function switchDemoTab(tab) {
    const tabs = ['positions', 'history', 'reset'];
    tabs.forEach(t => {
        const btn = document.getElementById(`demoTabBtn${capitalize(t)}`);
        const content = document.getElementById(`demoTab${capitalize(t)}`);
        if (btn) btn.classList.toggle('active', t === tab);
        if (content) {
            content.style.display = (t === tab) ? 'block' : 'none';
        }
    });

    if (tab === 'positions') renderDemoPositionsTable();
    if (tab === 'history') renderDemoHistoryTable();
}

function capitalize(s) {
    return s.charAt(0).toUpperCase() + s.slice(1);
}

function setResetAmountInput(val) {
    const input = document.getElementById('inputCustomResetAmount');
    if (input) input.value = val;

    const chips = document.querySelectorAll('.topup-chip');
    chips.forEach(c => {
        c.classList.toggle('active', c.innerText.includes(val.toLocaleString()));
    });
}

async function confirmResetDemoBalance() {
    const input = document.getElementById('inputCustomResetAmount');
    const val = parseFloat(input ? input.value : 0) || 10000;
    const clearHistory = document.getElementById('chkClearHistory')?.checked || false;

    if (val <= 0) {
        showToast('يرجى إدخال مبلغ صحيح لرأس المال التجريبي', 'error');
        return;
    }

    if (!confirm(`تأكيد إعادة تعيين المحفظة التجريبية:\n\nالمبلغ الجديد: $${val.toLocaleString()} USDT\nمسح السجل القديم: ${clearHistory ? 'نعم' : 'لا'}\n\nهل تريد المتابعة الآن؟`)) {
        return;
    }

    try {
        const res = await fetch('/api/demo/reset', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ amount: val, clearHistory: clearHistory })
        });
        const data = await res.json();
        if (data.retCode === 0) {
            playAlertBeep();
            showToast(data.retMsg, 'success');
            await fetchDemoAccountState();
            switchDemoTab('positions');
        } else {
            showToast('فشل إعادة التعيين: ' + data.retMsg, 'error');
        }
    } catch (e) {
        showToast('خطأ في الاتصال: ' + e.message, 'error');
    }
}

// ==============================================================================
// 🔴 Real Positions Tracker & Chart Integration
// ==============================================================================
state.realPositions = { openPositions: [], history: [] };
state.prevRealPrices = {};
state.activeRealTab = 'positions';

function openRealModal() {
    const modal = document.getElementById('realPositionsModal');
    if (modal) modal.classList.remove('hidden');
    fetchRealPositions();
}

function closeRealModal() {
    const modal = document.getElementById('realPositionsModal');
    if (modal) modal.classList.add('hidden');
}

function switchRealTab(tabKey) {
    state.activeRealTab = tabKey;
    const btnPos = document.getElementById('tabBtnRealPositions');
    const btnHist = document.getElementById('tabBtnRealHistory');
    const contentPos = document.getElementById('realTabPositions');
    const contentHist = document.getElementById('realTabHistory');
    const kpiOpen = document.getElementById('realKpiOpenGrid');
    const kpiHist = document.getElementById('realKpiHistoryGrid');

    if (tabKey === 'positions') {
        if (btnPos) btnPos.classList.add('active');
        if (btnHist) btnHist.classList.remove('active');
        if (contentPos) { contentPos.style.display = 'block'; contentPos.classList.add('active'); }
        if (contentHist) { contentHist.style.display = 'none'; contentHist.classList.remove('active'); }
        if (kpiOpen) kpiOpen.style.display = 'grid';
        if (kpiHist) kpiHist.style.display = 'none';
        renderRealPositionsTable();
    } else {
        if (btnPos) btnPos.classList.remove('active');
        if (btnHist) btnHist.classList.add('active');
        if (contentPos) { contentPos.style.display = 'none'; contentPos.classList.remove('active'); }
        if (contentHist) { contentHist.style.display = 'block'; contentHist.classList.add('active'); }
        if (kpiOpen) kpiOpen.style.display = 'none';
        if (kpiHist) kpiHist.style.display = 'grid';
        renderRealHistoryTable();
    }
}

async function fetchRealPositions() {
    try {
        const res = await fetch('/api/real/positions');
        const data = await res.json();
        if (data.retCode === 0) {
            state.realPositions = data;
            const openList = data.openPositions || [];
            const histList = data.history || [];

            // Update Header badge
            const badge = document.getElementById('realTradesCountBadge');
            if (badge) {
                badge.innerText = openList.length;
                badge.style.display = openList.length > 0 ? 'inline-flex' : 'none';
            }

            // Update Tab badges
            const bOpen = document.getElementById('badgeRealOpenPositions');
            const bHist = document.getElementById('badgeRealHistory');
            if (bOpen) bOpen.innerText = openList.length;
            if (bHist) bHist.innerText = histList.length;

            // Open KPIs
            const invEl = document.getElementById('realTotalInvested');
            const valEl = document.getElementById('realTotalValue');
            const pnlEl = document.getElementById('realTotalPnL');
            if (invEl) invEl.innerText = `$${(data.totalInvested || 0).toFixed(2)} USDT`;
            if (valEl) valEl.innerText = `$${(data.totalValue || 0).toFixed(2)} USDT`;
            if (pnlEl) {
                const pnl = data.totalUnrealizedPnL || 0;
                const isUp = pnl >= 0;
                pnlEl.className = `dk-val mono font-bold ${isUp ? 'text-green' : 'text-red'}`;
                pnlEl.innerText = `${isUp ? '+' : ''}$${pnl.toFixed(2)}`;
            }

            // History KPIs
            const histList = data.history || [];
            const sumPnl = histList.reduce((acc, h) => acc + (parseFloat(h.realizedPnL) || 0), 0);
            const stats = data.stats || {};
            const rPnl = (stats.totalRealizedPnL !== undefined && stats.totalRealizedPnL !== null) ? stats.totalRealizedPnL : sumPnl;

            const histPnlEl = document.getElementById('realHistTotalPnl');
            const openHistPnlEl = document.getElementById('realOpenHistTotalPnl');
            const histWinEl = document.getElementById('realHistWinRate');
            const histCountEl = document.getElementById('realHistTradesCount');
            const pnlFormatted = `${rPnl >= 0 ? '+' : ''}$${rPnl.toFixed(2)} USDT`;
            const pnlClass = `dk-val mono font-bold ${rPnl >= 0 ? 'text-green' : 'text-red'}`;

            if (histPnlEl) {
                histPnlEl.innerText = pnlFormatted;
                histPnlEl.className = pnlClass;
            }
            if (openHistPnlEl) {
                openHistPnlEl.innerText = pnlFormatted;
                openHistPnlEl.className = pnlClass;
            }
            if (histWinEl) histWinEl.innerText = `${stats.winRate || 100}%`;
            if (histCountEl) histCountEl.innerText = `${histList.length} صفقات`;

            renderRealPositionsTable();
            renderRealHistoryTable();
        }
    } catch (e) {
        console.warn('Error fetching real positions:', e);
    }
}

function renderRealHistoryTable() {
    const tbody = document.getElementById('realHistoryTableBody');
    if (!tbody) return;

    const list = state.realPositions?.history || [];
    if (list.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="9" style="text-align:center; padding: 30px; color: var(--text-dim);">
                    سجل الصفقات المنتهية فارغ حالياً. الصفقات المحققة ستظهر هنا فور إغلاقها وتوثيق أرباحها.
                </td>
            </tr>
        `;
        return;
    }

    tbody.innerHTML = list.map(h => {
        const pnl = parseFloat(h.realizedPnL || 0);
        const pnlPct = parseFloat(h.pnlPct || 0);
        const isUp = pnl >= 0;
        const pnlClass = isUp ? 'text-green' : 'text-red';
        const sign = isUp ? '+' : '';
        const netCash = parseFloat(h.netReturn || (h.qty * h.closePrice) || 0);

        return `
            <tr>
                <td>
                    <div style="font-weight: 800; font-size: 14px; color: var(--text-primary); display:flex; align-items:center; gap:6px;">
                        <span>${h.symbol}</span>
                        <span class="live-dot-mini ${isUp ? 'green' : 'red'}"></span>
                    </div>
                    <span class="badge-spot-mini" style="border-color: rgba(14,203,129,0.4); color: var(--green-profit);">منفذة على Bybit</span>
                </td>
                <td class="mono font-bold">$${formatPrice(h.entryPrice)}</td>
                <td class="mono font-bold text-cyan">$${formatPrice(h.closePrice)}</td>
                <td class="mono font-bold">${parseFloat(h.qty || 0).toLocaleString()} ${h.baseCoin || ''}</td>
                <td class="mono font-bold text-gold">$${netCash.toFixed(2)} USDT</td>
                <td>
                    <div class="mono font-bold ${pnlClass}" style="font-size: 14px;">
                        ${sign}$${pnl.toFixed(2)}
                    </div>
                    <span class="pnl-pill-mini ${isUp ? 'profit' : 'loss'} mono">${sign}${pnlPct.toFixed(2)}%</span>
                </td>
                <td>
                    <span style="font-size: 12px; font-weight: 700; color: ${isUp ? 'var(--green-profit)' : 'var(--red-loss)'};">
                        ${h.exitReason || 'إغلاق الصفقة'}
                    </span>
                </td>
                <td class="mono text-dim" style="font-size: 11.5px; white-space: nowrap;">
                    ${h.closedAt || '--'}
                </td>
                <td>
                    <button type="button" class="btn-demo-chart" onclick="openChartForHistoryTrade('${h.symbol}', ${h.entryPrice}, ${h.closePrice})" title="عرض الشارت وسعر الخروج">
                        📊 الشارت
                    </button>
                </td>
            </tr>
        `;
    }).join('');
}

function openChartForHistoryTrade(symbol, entry, exit) {
    closeRealModal();
    const customLevels = {
        entryPrice: entry,
        tp1: exit,
        setupName: `📜 صفقة منفذة ومحققة | ${symbol} (سعر البيع: $${exit})`
    };
    openChartModal(symbol, customLevels);
}


function renderRealPositionsTable() {
    const tbody = document.getElementById('realPositionsTableBody');
    if (!tbody) return;

    const list = state.realPositions?.openPositions || [];
    if (list.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="8" style="text-align:center; padding: 30px; color: var(--text-dim);">
                    لا توجد صفقات حقيقية مفتوحة حالياً. يمكنك تنفيذ أي صفقة مباشرة من الفاحص برأس مالك الحقيقي! 🚀
                </td>
            </tr>
        `;
        return;
    }

    tbody.innerHTML = list.map(pos => {
        const pnl = pos.unrealizedPnL || 0;
        const pnlPct = pos.unrealizedPnLPct || 0;
        const isUp = pnl >= 0;
        const pnlClass = isUp ? 'text-green' : 'text-red';
        const pnlSign = isUp ? '+' : '';
        const absPnl = Math.abs(pnl);
        const pnlDisplay = absPnl < 0.01 && absPnl > 0 ? absPnl.toFixed(4) : absPnl.toFixed(2);

        // Price Tick Flash Animation Check
        const prevPrice = state.prevRealPrices[pos.id];
        let tickClass = '';
        if (prevPrice !== undefined && pos.currentPrice !== undefined) {
            if (pos.currentPrice > prevPrice) {
                tickClass = 'tick-flash-up';
            } else if (pos.currentPrice < prevPrice) {
                tickClass = 'tick-flash-down';
            }
        }
        state.prevRealPrices[pos.id] = pos.currentPrice;

        const tpText = pos.tp2 ? `TP1: $${pos.tp1} | TP2: $${pos.tp2}` : (pos.tp1 ? `TP: $${pos.tp1}` : '--');
        const slText = pos.sl ? `SL: $${pos.sl}` : '--';

        return `
            <tr>
                <td>
                    <div style="font-weight: 800; font-size: 15px; color: var(--text-primary); display:flex; align-items:center; gap:6px;">
                        <span>${pos.symbol}</span>
                        <span class="live-dot-mini red"></span>
                    </div>
                    <span class="badge-spot-mini" style="border-color: rgba(246,70,93,0.4); color: #F6465D;">${pos.setupName || 'صفقة Bybit حقيقية'}</span>
                </td>
                <td class="mono font-bold">$${formatPrice(pos.entryPrice)}</td>
                <td class="mono font-bold text-cyan" style="font-size:14px;">
                    <span class="${tickClass}">$${formatPrice(pos.currentPrice || pos.entryPrice)}</span>
                    <span class="pulse-radar-dot"></span>
                </td>
                <td class="mono font-bold">$${pos.amountUsdt.toFixed(2)} USDT</td>
                <td class="mono font-bold">${pos.qty.toLocaleString(undefined, {maximumFractionDigits: 4})} ${pos.baseCoin}</td>
                <td>
                    <div style="font-size: 11px; color: var(--green-profit); font-weight: 700;">🎯 ${tpText}</div>
                    <div style="font-size: 11px; color: var(--red-loss); font-weight: 700;">🛡️ ${slText}</div>
                </td>
                <td>
                    <div class="mono font-bold ${pnlClass}" style="font-size: 15px;">
                        ${pnlSign}$${pnlDisplay}
                    </div>
                    <span class="pnl-pill-mini ${isUp ? 'profit' : 'loss'} mono">${isUp ? '+' : ''}${pnlPct.toFixed(2)}%</span>
                </td>
                <td style="white-space: nowrap; display: flex; gap: 6px; align-items: center;">
                    <button type="button" class="btn-demo-chart" onclick="openChartForRealPosition('${pos.id}')" title="عرض الصفقة وخطوط الدخول والهدف والستوب على الشارت">
                        📊 شارت
                    </button>
                    <button type="button" class="btn-demo-close" onclick="manualCloseRealPosition('${pos.id}')" title="تسييل فوري للعملة بسعر السوق على Bybit">
                        ⚡ إغلاق
                    </button>
                </td>
            </tr>
        `;
    }).join('');
}

function openChartForRealPosition(positionId) {
    const pos = (state.realPositions?.openPositions || []).find(p => p.id === positionId);
    if (!pos) return;

    const customLevels = {
        entryPrice: pos.entryPrice,
        tp1: pos.tp1 || 0,
        tp2: pos.tp2 || 0,
        tp3: pos.tp3 || 0,
        sl: pos.sl || 0,
        rr: pos.entryPrice && pos.tp1 && pos.sl && (pos.entryPrice - pos.sl > 0) ? ((pos.tp1 - pos.entryPrice) / (pos.entryPrice - pos.sl)).toFixed(2) : '--',
        setupName: `🔴 صفقة Bybit حقيقية | ${pos.symbol}`
    };

    closeRealModal();
    openChartModal(pos.symbol, customLevels);
}

function openChartForOpenOrder(symbol, price, side, triggerPrice) {
    closeOrdersModal();
    const p = parseFloat(price) || 0;
    const sl = parseFloat(triggerPrice) || 0;
    const customLevels = {
        entryPrice: p,
        tp1: side === 'Sell' ? p : 0,
        sl: sl > 0 ? sl : 0,
        setupName: `📜 أمر معلق على Bybit (${side === 'Sell' ? (sl > 0 ? 'هدف وستوب TP/SL' : 'بيع TP') : 'شراء'})`
    };
    openChartModal(symbol, customLevels);
}

async function manualCloseRealPosition(positionId) {
    const pos = (state.realPositions?.openPositions || []).find(p => p.id === positionId);
    if (!pos) return;

    const pnl = pos.unrealizedPnL || 0;
    const confirmMsg = `هل تريد بالتأكيد تسييل وإغلاق صفقة ${pos.symbol} الحقيقية الآن بسعر السوق؟\n\nالسعر اللحظي: $${pos.currentPrice}\nالربح/الخسارة اللحظية: ${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)} (${pos.unrealizedPnLPct || 0}%)\n\nسيتم بيع كمية ${pos.qty} ${pos.baseCoin} فورا بسعر السوق على منصة Bybit وإلغاء أي أمر جني أرباح معلق.`;
    if (!confirm(confirmMsg)) return;

    try {
        const res = await fetch('/api/real/close', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ positionId: positionId })
        });
        const data = await res.json();
        if (data.retCode === 0) {
            playAlertBeep();
            showToast(`تم بيع وتسييل صفقة ${pos.symbol} بنجاح على Bybit!`, 'success');
            await fetchRealPositions();
            await fetchAccountBalance();
        } else {
            showToast(`خطأ في الإغلاق من Bybit: ${data.retMsg}`, 'error');
        }
    } catch (e) {
        showToast('فشل الاتصال بالخادم: ' + e.message, 'error');
    }
}
