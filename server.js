const express = require('express');
const app = express();
const PORT = process.env.PORT || 3000;

app.use(express.json());

// API Endpoint for Mandi Forecast Data
app.get('/api/forecast', (req, res) => {
    const { state = 'Punjab', mandi = 'Khanna', commodity = 'Wheat' } = req.query;

    res.json({
        state,
        mandi,
        commodity,
        currentPrice: 2480,
        predictedPrice: 2580,
        confidenceInterval: "₹2,510 - ₹2,650",
        marketSignal: "BUY / HOLD",
        chartData: {
            labels: ['Aug 25', 'Aug 26', 'Aug 27', 'Aug 28', 'Aug 29 (Today)', 'Aug 30', 'Aug 31', 'Sep 01', 'Sep 02'],
            historical: [2400, 2420, 2390, 2450, 2480, null, null, null, null],
            forecast: [null, null, null, null, 2480, 2510, 2540, 2520, 2580]
        }
    });
});

// Serve UI Dashboard directly on Root
app.get('/', (req, res) => {
    res.send(`
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Mandi Price Forecast System</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
</head>
<body class="bg-slate-50 text-slate-900 p-6 font-sans">
    <div class="max-w-6xl mx-auto">
        <header class="mb-8 border-b border-slate-200 pb-4">
            <h1 class="text-3xl font-bold text-emerald-700">🌾 Mandi Price Forecasting System</h1>
            <p class="text-slate-500 mt-1">Real-time market analytics & ML-powered modal price predictions</p>
        </header>

        <!-- Control Filters -->
        <div class="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6 bg-white p-4 rounded-xl shadow-sm border border-slate-200">
            <div>
                <label class="block text-xs font-semibold text-slate-500 mb-1">STATE</label>
                <select id="stateSelect" class="w-full px-3 py-2 bg-slate-50 border border-slate-300 rounded-lg text-sm focus:ring-2 focus:ring-emerald-500">
                    <option value="Punjab">Punjab</option>
                    <option value="Haryana">Haryana</option>
                    <option value="Maharashtra">Maharashtra</option>
                </select>
            </div>
            <div>
                <label class="block text-xs font-semibold text-slate-500 mb-1">MANDI</label>
                <select id="mandiSelect" class="w-full px-3 py-2 bg-slate-50 border border-slate-300 rounded-lg text-sm focus:ring-2 focus:ring-emerald-500">
                    <option value="Khanna">Khanna APMC</option>
                    <option value="Ludhiana">Ludhiana Market</option>
                </select>
            </div>
            <div>
                <label class="block text-xs font-semibold text-slate-500 mb-1">COMMODITY</label>
                <select id="commoditySelect" class="w-full px-3 py-2 bg-slate-50 border border-slate-300 rounded-lg text-sm focus:ring-2 focus:ring-emerald-500">
                    <option value="Wheat">Wheat</option>
                    <option value="Paddy">Paddy</option>
                    <option value="Mustard">Mustard</option>
                </select>
            </div>
        </div>

        <!-- Metric KPI Cards -->
        <div class="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
            <div class="bg-white p-5 rounded-xl shadow-sm border border-slate-200">
                <p class="text-sm text-slate-500 font-medium">Current Modal Price</p>
                <p class="text-3xl font-bold text-slate-800 mt-2" id="currentPrice">₹--</p>
            </div>
            <div class="bg-white p-5 rounded-xl shadow-sm border border-emerald-200">
                <p class="text-sm text-slate-500 font-medium">Predicted Price (Sep 02)</p>
                <p class="text-3xl font-bold text-emerald-600 mt-2" id="predictedPrice">₹--</p>
            </div>
            <div class="bg-white p-5 rounded-xl shadow-sm border border-slate-200">
                <p class="text-sm text-slate-500 font-medium">Model Confidence Range</p>
                <p class="text-2xl font-bold text-slate-700 mt-2" id="confidenceInterval">--</p>
            </div>
        </div>

        <!-- Chart -->
        <div class="bg-white p-6 rounded-xl shadow-sm border border-slate-200">
            <h2 class="text-lg font-semibold text-slate-800 mb-4">Price Trend & ML Projections</h2>
            <div class="relative h-80 w-full">
                <canvas id="forecastChart"></canvas>
            </div>
        </div>
    </div>

    <script>
        let chartInstance;

        async function loadForecast() {
            const state = document.getElementById('stateSelect').value;
            const mandi = document.getElementById('mandiSelect').value;
            const commodity = document.getElementById('commoditySelect').value;

            const res = await fetch(`/api/forecast?state=${state}&mandi=${mandi}&commodity=${commodity}`);
            const data = await res.json();

            document.getElementById('currentPrice').innerText = '₹' + data.currentPrice;
            document.getElementById('predictedPrice').innerText = '₹' + data.predictedPrice;
            document.getElementById('confidenceInterval').innerText = data.confidenceInterval;

            const ctx = document.getElementById('forecastChart').getContext('2d');
            if (chartInstance) chartInstance.destroy();

            chartInstance = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: data.chartData.labels,
                    datasets: [
                        {
                            label: 'Historical Price (₹)',
                            data: data.chartData.historical,
                            borderColor: '#334155',
                            backgroundColor: '#334155',
                            borderWidth: 2,
                            spanGaps: true
                        },
                        {
                            label: 'Predicted Price (₹)',
                            data: data.chartData.forecast,
                            borderColor: '#10b981',
                            backgroundColor: '#10b981',
                            borderDash: [5, 5],
                            borderWidth: 2,
                            spanGaps: true
                        }
                    ]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false
                }
            });
        }

        document.getElementById('stateSelect').addEventListener('change', loadForecast);
        document.getElementById('mandiSelect').addEventListener('change', loadForecast);
        document.getElementById('commoditySelect').addEventListener('change', loadForecast);

        window.onload = loadForecast;
    </script>
</body>
</html>
    `);
});

app.listen(PORT, () => {
    console.log(`Mandi Forecast server running on port ${PORT}`);
});
