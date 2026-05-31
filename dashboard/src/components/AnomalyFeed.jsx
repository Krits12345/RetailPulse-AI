export default function AnomalyFeed({ anomalies }) {
  const list = anomalies?.anomalies ?? [];

  return (
    <div className="card">
      <div className="card-title">
        Active Anomalies
        {list.length > 0 && (
          <span style={{ marginLeft: 8, color: list.some((a) => a.severity === "CRITICAL") ? "#ef4444" : "#f59e0b" }}>
            {list.length} active
          </span>
        )}
      </div>

      {!anomalies ? (
        <div className="loading">Loading…</div>
      ) : list.length === 0 ? (
        <div className="no-anomalies">✓ All clear — no anomalies detected</div>
      ) : (
        list
          .sort((a, b) => {
            const order = { CRITICAL: 0, WARN: 1, INFO: 2 };
            return order[a.severity] - order[b.severity];
          })
          .map((a) => (
            <div key={a.anomaly_id} className="anomaly-item">
              <span className={`anomaly-dot severity-${a.severity}`} />
              <div>
                <div className="anomaly-desc">
                  <strong>{a.anomaly_type.replace(/_/g, " ")}</strong> — {a.description}
                </div>
                <div className="anomaly-action">↳ {a.suggested_action}</div>
              </div>
            </div>
          ))
      )}
    </div>
  );
}
