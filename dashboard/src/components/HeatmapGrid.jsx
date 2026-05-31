export default function HeatmapGrid({ heatmap }) {
  return (
    <div className="card">
      <div className="card-title">
        Zone Heatmap
        {heatmap && !heatmap.data_confidence && (
          <span style={{ color: "#f59e0b", marginLeft: 8, fontStyle: "italic" }}>
            low data
          </span>
        )}
      </div>

      {!heatmap ? (
        <div className="loading">Loading…</div>
      ) : heatmap.zones.length === 0 ? (
        <div className="loading">No zone data today</div>
      ) : (
        <div className="heatmap-grid">
          {heatmap.zones.map((zone) => (
            <div key={zone.zone_id} className="heatmap-row">
              <span className="heatmap-label">{zone.zone_id}</span>
              <div className="heatmap-bar-container">
                <div
                  className="heatmap-bar"
                  style={{ width: `${zone.normalised_score}%` }}
                />
              </div>
              <span className="heatmap-score">{zone.normalised_score.toFixed(0)}</span>
            </div>
          ))}
        </div>
      )}

      {heatmap && heatmap.zones.length > 0 && (
        <div style={{ marginTop: 12, fontSize: 11, color: "#475569" }}>
          Score = visit frequency, normalised 0–100
        </div>
      )}
    </div>
  );
}
