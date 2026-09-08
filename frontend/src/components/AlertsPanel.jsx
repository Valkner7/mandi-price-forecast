export default function AlertsPanel({ rows }) {
  const sorted = [...(rows || [])]
    .sort((a, b) => Math.abs(b.pct_change) - Math.abs(a.pct_change))
    .slice(0, 5);

  if (!sorted.length) {
    return <div className="empty-state">No alerts right now.</div>;
  }

  return (
    <>
      {sorted.map((r) => {
        const severity = Math.abs(r.pct_change) > 4 ? 'high' : 'medium';
        const text = r.pct_change >= 0
          ? `${r.crop} at ${r.mandi} projected up ${r.pct_change.toFixed(1)}% over the next ${r.forecast_horizon_days} days.`
          : `${r.crop} at ${r.mandi} projected down ${Math.abs(r.pct_change).toFixed(1)}% over the next ${r.forecast_horizon_days} days — watch before selling.`;
        return (
          <div className="alert-row" key={`${r.crop}-${r.mandi}`}>
            <span className={`alert-dot ${severity}`}></span>
            <span className="alert-text">{text}</span>
          </div>
        );
      })}
    </>
  );
}
