import { Suspense } from "react";

async function getStats() {
  try {
    const res = await fetch("http://localhost:8000/api/v1/system/ingestion-status", {
      cache: "no-store",
    });
    if (!res.ok) return null;
    return res.json();
  } catch {
    return null;
  }
}

function StatCard({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="bg-neutral-900 border border-neutral-800 rounded-xl p-6">
      <p className="text-sm text-neutral-500 uppercase tracking-wide mb-2">{label}</p>
      <p className="text-3xl font-semibold">{value}</p>
    </div>
  );
}

async function DashboardStats() {
  const stats = await getStats();
  
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
      <StatCard label="Total Signals" value={stats?.total_signals ?? 0} />
      <StatCard label="Status" value={stats?.status ?? "Unknown"} />
      <StatCard label="Last Signal" value={stats?.last_signal_at ? "Recent" : "N/A"} />
      <StatCard label="Health" value="Healthy" />
    </div>
  );
}

export default function DashboardPage() {
  return (
    <div className="p-8">
      <div className="mb-8">
        <h1 className="text-3xl font-semibold mb-2">Dashboard</h1>
        <p className="text-neutral-500">Overview of your deal pipeline and signals</p>
      </div>
      
      <Suspense fallback={
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="bg-neutral-900 border border-neutral-800 rounded-xl p-6 animate-pulse">
              <div className="h-4 bg-neutral-800 rounded w-20 mb-4"></div>
              <div className="h-8 bg-neutral-800 rounded w-16"></div>
            </div>
          ))}
        </div>
      }>
        <DashboardStats />
      </Suspense>
      
      <div className="mt-8 bg-neutral-900 border border-neutral-800 rounded-xl p-6">
        <h2 className="text-xl font-semibold mb-4">Getting Started</h2>
        <p className="text-neutral-400 mb-4">
          Welcome to Discovery Engine. This is your new web-based dashboard for managing 
          deal flow and signals.
        </p>
        <ul className="space-y-2 text-neutral-400">
          <li className="flex items-center gap-2">
            <span className="text-green-500">●</span>
            Next.js frontend running on port 5000
          </li>
          <li className="flex items-center gap-2">
            <span className="text-blue-500">●</span>
            FastAPI backend on port 8000
          </li>
          <li className="flex items-center gap-2">
            <span className="text-yellow-500">●</span>
            Authentication coming soon (NextAuth + Google)
          </li>
        </ul>
      </div>
    </div>
  );
}
