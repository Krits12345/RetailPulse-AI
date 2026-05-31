import {
  BarChart, Bar, XAxis, YAxis, Tooltip, Cell, ResponsiveContainer,
} from "recharts";

const COLORS = ["#7c3aed", "#6d28d9", "#5b21b6", "#4c1d95"];

export default function FunnelChart({ funnel }) {
  return (
    <div className="card">
      <div className="card-title">Conversion Funnel</div>
      {!funnel ? (
        <div className="loading">Loading…</div>
      ) : funnel.total_sessions === 0 ? (
        <div className="loading">No sessions today yet</div>
      ) : (
        <>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={funnel.stages} layout="vertical" margin={{ left: 20, right: 20 }}>
              <XAxis type="number" hide />
              <YAxis type="category" dataKey="stage" width={90} tick={{ fill: "#94a3b8", fontSize: 12 }} />
              <Tooltip
                contentStyle={{ background: "#1a1d27", border: "1px solid #2a2d3e", borderRadius: 6 }}
                labelStyle={{ color: "#e2e8f0" }}
                formatter={(val, name) => [val, "Visitors"]}
              />
              <Bar dataKey="count" radius={[0, 4, 4, 0]}>
                {funnel.stages.map((_, i) => (
                  <Cell key={i} fill={COLORS[i % COLORS.length]} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>

          <div style={{ marginTop: 8 }}>
            {funnel.stages.slice(1).map((stage) => (
              <div key={stage.stage} style={{ display: "flex", justifyContent: "space-between", padding: "3px 0", fontSize: 12, color: "#64748b" }}>
                <span>{stage.stage}</span>
                <span style={{ color: stage.dropoff_pct > 50 ? "#ef4444" : "#f59e0b" }}>
                  −{stage.dropoff_pct.toFixed(1)}% drop-off
                </span>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
