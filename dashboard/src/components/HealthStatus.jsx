export default function HealthStatus({ health }) {
  return (
    <div className="card">
      <div className="card-title">System Health</div>

      {!health ? (
        <div className="loading">Loading…</div>
      ) : (
        <>
          <div style={{ marginBottom: 12, display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{
              width: 10, height: 10, borderRadius: "50%",
              background: health.status === "healthy" ? "#10b981" : "#ef4444",
              display: "inline-block",
            }} />
            <span style={{ fontWeight: 600, fontSize: 14 }}>
              {health.status === "healthy" ? "Healthy" : "Degraded"}
            </span>
            <span style={{ color: "#475569", fontSize: 12 }}>v{health.version}</span>
          </div>

          {health.stores.map((s) => (
            <div key={s.store_id} className="health-row">
              <span style={{ fontSize: 12, color: "#94a3b8" }}>{s.store_id}</span>
              <span className={`status-${s.status === "OK" ? "ok" : s.status === "STALE_FEED" ? "stale" : "nodata"}`}>
                {s.status === "OK"
                  ? `✓ ${s.lag_seconds?.toFixed(0)}s ago`
                  : s.status === "STALE_FEED"
                  ? `⚠ STALE ${(s.lag_seconds / 60).toFixed(0)}m`
                  : "NO DATA"}
              </span>
            </div>
          ))}

          <div style={{ marginTop: 12, fontSize: 11, color: "#475569" }}>
            DB: {health.db_status} · refreshed every 5s
          </div>
        </>
      )}
    </div>
  );
}
