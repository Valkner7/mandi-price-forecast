// "Why tomorrow's move?" — renders the /predict `explanation` block
// (LightGBM Tree SHAP, or the simpler ETS level/trend split on the fallback
// path — both share one schema, see price_model.explain_serving_row and
// routers/predict._explain_ets_fallback).
//
// Values arrive already converted to rupees by the backend (the model's
// target is a *percentage* change, so raw SHAP fractions are never shown).
// Shows nothing at all if the explanation is missing or unavailable, so an
// older backend or a failed explanation never leaves an error box behind.

// Signed rupee string with 2 decimals, e.g. "+₹0.27" / "−₹0.21" / "₹0.00".
// (utils/format's inr() rounds values >= 1 to whole rupees, which would
// erase these small numbers, so this has its own formatter.)
function signedRupees(n) {
  const abs = Math.abs(n);
  if (abs < 0.005) return '₹0.00';
  return `${n > 0 ? '+' : '−'}₹${abs.toFixed(2)}`;
}

export default function ExplanationPanel({ explanation }) {
  if (!explanation || !explanation.available || !explanation.buckets?.length) return null;

  const { buckets, baseline_change_rupees: baseline, predicted_change_rupees: predicted } = explanation;
  const maxAbs = Math.max(...buckets.map((b) => Math.abs(b.contribution_rupees)), 0.01);
  const isShap = explanation.method === 'shap';
  const predictedClass = predicted > 0 ? 'up' : predicted < 0 ? 'down' : 'stable';

  return (
    <div className="panel">
      <div className="panel-header">
        <div>
          <div className="panel-title">Why this move?</div>
          <div className="panel-sub">
            {isShap
              ? "What the model leaned on for tomorrow's predicted change"
              : 'Simpler fallback model: recent level vs. recent trend'}
          </div>
        </div>
      </div>

      <div className="expl-list" role="list" aria-label="Contribution of each factor to tomorrow's predicted price change, in rupees per quintal">
        {buckets.map((b) => {
          const width = Math.max((Math.abs(b.contribution_rupees) / maxAbs) * 50, b.direction === 'neutral' ? 0 : 2);
          const arrow = b.direction === 'up' ? '↑' : b.direction === 'down' ? '↓' : '→';
          return (
            <div className="expl-row" role="listitem" key={b.key}>
              <div className="expl-label">{b.label}</div>
              <div className="expl-track" aria-hidden="true">
                <div className="expl-center" />
                {b.direction !== 'neutral' && (
                  <div
                    className={`expl-bar ${b.direction}`}
                    style={b.direction === 'up' ? { left: '50%', width: `${width}%` } : { right: '50%', width: `${width}%` }}
                  />
                )}
              </div>
              <div className={`expl-value ${b.direction === 'neutral' ? 'stable' : b.direction}`}>
                {arrow} {signedRupees(b.contribution_rupees)}
              </div>
            </div>
          );
        })}
      </div>

      <div className="expl-total">
        {isShap && (
          <span className="expl-total-part">
            Model&apos;s starting point <strong>{signedRupees(baseline)}</strong>
          </span>
        )}
        <span className="expl-total-part">
          Net predicted change tomorrow{' '}
          <strong className={predictedClass}>
            {signedRupees(predicted)}
            {explanation.predicted_change_pct != null && ` (${explanation.predicted_change_pct > 0 ? '+' : ''}${explanation.predicted_change_pct.toFixed(2)}%)`}
          </strong>{' '}
          per quintal
        </span>
      </div>

      <div className="expl-note">
        {explanation.note} Covers tomorrow only — later days of the 7-day forecast build on it.
      </div>
    </div>
  );
}
