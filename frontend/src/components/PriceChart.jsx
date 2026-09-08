import { useMemo } from 'react';
import { Line } from 'react-chartjs-2';
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Tooltip,
  Legend,
} from 'chart.js';
import { inr, shortDate } from '../utils/format';

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Tooltip, Legend);

export default function PriceChart({ history, predict }) {
  const { data, options } = useMemo(() => {
    const actualDates = history.points.map((p) => p.date);
    const forecastDates = predict.forecast.map((f) => f.date);
    const labels = [...actualDates, ...forecastDates];

    const actualData = history.points.map((p) => p.price);
    // Pad the forecast series with nulls under the actual-only range, then
    // stitch in the last actual value so the two lines join with no gap.
    const forecastData = [
      ...new Array(actualDates.length - 1).fill(null),
      actualData[actualData.length - 1],
      ...predict.forecast.map((f) => f.price),
    ];

    const data = {
      labels,
      datasets: [
        {
          label: 'Actual',
          data: actualData,
          borderColor: '#a6a290',
          backgroundColor: 'transparent',
          borderWidth: 2,
          pointRadius: 0,
          tension: 0.25,
          spanGaps: true,
        },
        {
          label: 'Forecast',
          data: forecastData,
          borderColor: '#d4a017',
          backgroundColor: 'transparent',
          borderWidth: 2,
          borderDash: [5, 4],
          pointRadius: 0,
          tension: 0.25,
          spanGaps: true,
        },
      ],
    };

    const options = {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 600 },
      interaction: { mode: 'index', intersect: false },
      scales: {
        x: {
          grid: { color: '#292b20' },
          ticks: {
            color: '#6f6c5e',
            font: { family: 'IBM Plex Mono', size: 11 },
            callback: (value) => shortDate(labels[value]),
            maxTicksLimit: 8,
            autoSkip: true,
          },
        },
        y: {
          grid: { color: '#292b20' },
          ticks: {
            color: '#6f6c5e',
            font: { family: 'IBM Plex Mono', size: 11 },
            callback: (v) => `₹${Math.round(v / 100) / 10}k`,
          },
        },
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#21231a',
          borderColor: '#34362a',
          borderWidth: 1,
          titleFont: { family: 'IBM Plex Sans', size: 11.5 },
          bodyFont: { family: 'IBM Plex Mono', size: 12.5 },
          callbacks: {
            title: (items) => shortDate(labels[items[0].dataIndex]),
            label: (item) => `${item.dataset.label} ₹${inr(item.parsed.y)}`,
          },
        },
      },
    };

    return { data, options };
  }, [history, predict]);

  return (
    <div className="chart-wrap">
      <Line data={data} options={options} />
    </div>
  );
}
