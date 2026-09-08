import { inr } from '../utils/format';

export default function MoversPanel({ rows, emptyMessage = 'No movers to show yet.' }) {
  if (!rows || !rows.length) {
    return <div className="empty-state">{emptyMessage}</div>;
  }

  return (
    <>
      {rows.map((r) => {
        const up = r.pct_change >= 0;
        return (
          <div className="mover-row" key={`${r.crop}-${r.mandi}`}>
            <div className="mover-name">
              <span className="mover-commodity">{r.crop}</span>
              <span className="mover-mandi">{r.mandi}</span>
            </div>
            <div className="mover-figures">
              <span className="mover-price">₹{inr(r.latest_price)}</span>
              <span className={`pct-pill ${up ? 'up' : 'down'}`}>
                {up ? '↑' : '↓'} {Math.abs(r.pct_change).toFixed(1)}%
              </span>
            </div>
          </div>
        );
      })}
    </>
  );
}
