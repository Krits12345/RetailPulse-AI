export default function MetricsPanel({ metrics }) {
  if (!metrics) {
    return (
      <>
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="card">
            <div className="loading">Loading…</div>
          </div>
        ))}
      </>
    );
  }

  const convPct = (metrics.conversion_rate * 100).toFixed(1);
  const abPct = (metrics.abandonment_rate * 100).toFixed(1);

  return (
    <>
      <div className="card">
        <div className="card-title">Unique Visitors Today</div>
        <div className="metric-value">{metrics.unique_visitors}</div>
        <div className="metric-sub">customer sessions</div>
      </div>

      <div className="card">
        <div className="card-title">Conversion Rate</div>
        <div className="metric-value">{convPct}%</div>
        <span className={`metric-badge ${convPct >= 30 ? "badge-green" : convPct >= 15 ? "badge-amber" : "badge-red"}`}>
          {convPct >= 30 ? "On Target" : convPct >= 15 ? "Below Avg" : "Critical"}
        </span>
      </div>

      <div className="card">
        <div className="card-title">Queue Depth</div>
        <div className="metric-value">{metrics.current_queue_depth}</div>
        <span className={`metric-badge ${metrics.current_queue_depth < 5 ? "badge-green" : metrics.current_queue_depth < 10 ? "badge-amber" : "badge-red"}`}>
          {metrics.current_queue_depth < 5 ? "Normal" : metrics.current_queue_depth < 10 ? "Building" : "Spike"}
        </span>
      </div>

      <div className="card">
        <div className="card-title">Abandonment Rate</div>
        <div className="metric-value">{abPct}%</div>
        <div className="metric-sub">left billing queue</div>
      </div>
    </>
  );
}
