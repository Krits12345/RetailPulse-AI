import { useState, useEffect, useCallback } from "react";
import MetricsPanel from "./components/MetricsPanel";
import FunnelChart from "./components/FunnelChart";
import HeatmapGrid from "./components/HeatmapGrid";
import AnomalyFeed from "./components/AnomalyFeed";
import HealthStatus from "./components/HealthStatus";

const API_BASE = import.meta.env.VITE_API_URL || "/api";
const STORES = ["STORE_BLR_002", "STORE_MUM_005"];
const REFRESH_MS = 5000;

async function fetchJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

export default function App() {
  const [store, setStore] = useState(STORES[0]);
  const [metrics, setMetrics] = useState(null);
  const [funnel, setFunnel] = useState(null);
  const [heatmap, setHeatmap] = useState(null);
  const [anomalies, setAnomalies] = useState(null);
  const [health, setHealth] = useState(null);
  const [lastRefresh, setLastRefresh] = useState(null);
  const [error, setError] = useState(null);

  const refresh = useCallback(async () => {
    try {
      const [m, f, h, a, sys] = await Promise.all([
        fetchJSON(`${API_BASE}/stores/${store}/metrics`),
        fetchJSON(`${API_BASE}/stores/${store}/funnel`),
        fetchJSON(`${API_BASE}/stores/${store}/heatmap`),
        fetchJSON(`${API_BASE}/stores/${store}/anomalies`),
        fetchJSON(`${API_BASE}/health`),
      ]);
      setMetrics(m);
      setFunnel(f);
      setHeatmap(h);
      setAnomalies(a);
      setHealth(sys);
      setLastRefresh(new Date());
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  }, [store]);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, REFRESH_MS);
    return () => clearInterval(id);
  }, [refresh]);

  const storeHealth = health?.stores?.find((s) => s.store_id === store);

  return (
    <div className="app">
      <header className="header">
        <div className="header-left">
          <span className="logo">🏪 Store Intelligence</span>
          <select
            className="store-selector"
            value={store}
            onChange={(e) => setStore(e.target.value)}
          >
            {STORES.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </div>
        <div className="live-indicator">
          <span className="live-dot" />
          LIVE · {lastRefresh ? lastRefresh.toLocaleTimeString() : "connecting…"}
        </div>
      </header>

      <main className="main">
        {error && <div className="error">⚠ API error: {error}</div>}

        {/* Row 1: KPI cards */}
        <div className="grid-4">
          <MetricsPanel metrics={metrics} />
        </div>

        {/* Row 2: Funnel + Heatmap */}
        <div className="grid-2">
          <FunnelChart funnel={funnel} />
          <HeatmapGrid heatmap={heatmap} />
        </div>

        {/* Row 3: Anomalies + Health */}
        <div className="grid-3">
          <AnomalyFeed anomalies={anomalies} />
          <HealthStatus health={health} />
        </div>
      </main>
    </div>
  );
}
