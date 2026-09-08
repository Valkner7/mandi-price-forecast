import { inr } from '../utils/format';

export default function Hero({ predict }) {
  if (!predict) return null;

  const trendArrow = predict.trend === 'rising' ? '↑ ' : predict.trend === 'falling' ? '↓ ' : '→ ';
  const trendClass = predict.trend === 'rising' ? 'up' : predict.trend === 'falling' ? 'down' : 'stable';

  const forecastLast = predict.forecast[predict.forecast.length - 1];
  const forecastUp = forecastLast.price >= predict.latest_price;
  const pct = ((forecastLast.price - predict.latest_price) / predict.latest_price) * 100;

  return (
    <div className="hero">
      <div className="hero-cell">
        <div className="hero-label">Current price · {predict.crop}</div>
        <div className="hero-value">
          ₹{inr(predict.latest_price)}{' '}
          <span className="unit">/{predict.unit.replace('INR per ', '')}</span>
        </div>
        <div className="hero-note">{predict.mandi} mandi, as of {predict.latest_date}</div>
      </div>

      <div className="hero-cell">
        <div className="hero-label">Trend</div>
        <div className={`hero-value ${trendClass}`}>{trendArrow}{predict.trend}</div>
        <div className="hero-note">Model: {predict.model}</div>
      </div>

      <div className="hero-cell">
        <div className="hero-label">7-day forecast</div>
        <div className="hero-value">₹{inr(forecastLast.price)}</div>
        <div className="hero-delta">
          <span className={forecastUp ? 'up' : 'down'}>
            {forecastUp ? '↑' : '↓'} {Math.abs(pct).toFixed(1)}% by {forecastLast.date}
          </span>
        </div>
        <div className="hero-note">{predict.confidence?.note || ''}</div>
      </div>
    </div>
  );
}
