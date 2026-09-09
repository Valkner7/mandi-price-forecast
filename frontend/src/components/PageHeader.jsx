function orderedOptions(values, prioritized) {
  const p = prioritized || [];
  return [
    ...p.filter((v) => values.includes(v)),
    ...values.filter((v) => !p.includes(v)),
  ];
}

export default function PageHeader({
  crops,
  mandis,
  reliableCrops,
  crop,
  mandi,
  onCropChange,
  onMandiChange,
  disabled,
}) {
  const todayLabel = new Date().toLocaleDateString('en-IN', {
    weekday: 'long',
    day: 'numeric',
    month: 'long',
  });

  const cropOptions = orderedOptions(crops || [], reliableCrops || []);
  const mandiOptions = orderedOptions(mandis || [], []);

  return (
    <div className="page-header">
      <div>
        <h1 className="page-title">Price forecast dashboard</h1>
        <div className="page-meta">{todayLabel}</div>
      </div>
      <div className="selector-row">
                <select
          id="crop-select"
          name="crop"
          className="selector"
          value={crop || ''}
          disabled={disabled}
          onChange={(e) => onCropChange(e.target.value)}
        >
          {cropOptions.map((c) => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>
        <select
          id="mandi-select"
          name="mandi"
          className="selector"
          value={mandi || ''}
          disabled={disabled}
          onChange={(e) => onMandiChange(e.target.value)}
        >
          {mandiOptions.map((m) => (
            <option key={m} value={m}>{m}</option>
          ))}
        </select>
      </div>
    </div>
  );
}

