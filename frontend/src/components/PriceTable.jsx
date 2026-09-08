import { inr } from '../utils/format';

export default function PriceTable({ rows, emptyMessage }) {
  if (!rows || !rows.length) {
    return (
      <div className="empty-state">
        {emptyMessage || 'No mandis with enough history for this crop.'}
      </div>
    );
  }

  return (
    <div>
      <table className="price-table">
        <thead>
          <tr>
            <th>Mandi</th>
            <th>Price</th>
            <th>7-day forecast</th>
            <th>Change</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const up = r.pct_change >= 0;
            return (
              <tr key={r.mandi}>
                <td>{r.mandi}</td>
                <td className="num">₹{inr(r.latest_price)}</td>
                <td className="num">₹{inr(r.forecast_price)}</td>
                <td className={`num ${up ? 'up' : 'down'}`}>
                  {up ? '↑' : '↓'} {Math.abs(r.pct_change).toFixed(1)}%
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
