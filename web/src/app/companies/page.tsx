"use client";

import { useState, useEffect } from "react";

interface Company {
  id: number;
  canonical_key: string;
  company_name: string;
  confidence: number;
  signal_types: string[];
  status: string | null;
  last_signal_at: string | null;
}

interface CompanyResponse {
  items: Company[];
  total: number;
  page: number;
  page_size: number;
  has_more: boolean;
}

function ConfidenceBadge({ confidence }: { confidence: number }) {
  const color = confidence >= 0.7 
    ? "text-green-400" 
    : confidence >= 0.4 
      ? "text-yellow-400" 
      : "text-red-400";
  
  return (
    <span className={`${color} font-medium`}>
      {(confidence * 100).toFixed(0)}%
    </span>
  );
}

function StatusBadge({ status }: { status: string | null }) {
  if (!status) return <span className="text-neutral-500">-</span>;
  
  const colors: Record<string, string> = {
    Source: "bg-amber-500/20 text-amber-400",
    "Initial Meeting / Call": "bg-blue-500/20 text-blue-400",
    Dilligence: "bg-purple-500/20 text-purple-400",
    Tracking: "bg-neutral-500/20 text-neutral-400",
    Committed: "bg-emerald-500/20 text-emerald-400",
    Funded: "bg-green-500/20 text-green-400",
    Passed: "bg-red-500/20 text-red-400",
    Lost: "bg-red-800/20 text-red-600",
  };
  
  return (
    <span className={`px-2 py-1 rounded-full text-xs ${colors[status] || "bg-neutral-500/20 text-neutral-400"}`}>
      {status}
    </span>
  );
}

export default function CompaniesPage() {
  const [companies, setCompanies] = useState<Company[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [hasMore, setHasMore] = useState(false);

  useEffect(() => {
    async function fetchCompanies() {
      setLoading(true);
      try {
        const params = new URLSearchParams({
          page: page.toString(),
          page_size: "20",
        });
        if (search) params.set("q", search);
        
        const res = await fetch(`/api/v1/companies?${params}`);
        if (res.ok) {
          const data: CompanyResponse = await res.json();
          setCompanies(data.items);
          setTotal(data.total);
          setHasMore(data.has_more);
        }
      } catch (err) {
        console.error("Failed to fetch companies:", err);
      } finally {
        setLoading(false);
      }
    }
    
    fetchCompanies();
  }, [page, search]);

  return (
    <div className="p-8">
      <div className="mb-8 flex justify-between items-center">
        <div>
          <h1 className="text-3xl font-semibold mb-2">Companies</h1>
          <p className="text-neutral-500">
            {total} companies in your pipeline
          </p>
        </div>
        
        <div className="flex gap-4">
          <input
            type="text"
            placeholder="Search companies..."
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setPage(1);
            }}
            className="px-4 py-2 bg-neutral-900 border border-neutral-800 rounded-lg text-sm focus:outline-none focus:border-neutral-700"
          />
        </div>
      </div>
      
      <div className="bg-neutral-900 border border-neutral-800 rounded-xl overflow-hidden">
        <table className="w-full">
          <thead className="border-b border-neutral-800">
            <tr className="text-left text-sm text-neutral-500">
              <th className="px-6 py-4 font-medium">Company</th>
              <th className="px-6 py-4 font-medium">Canonical Key</th>
              <th className="px-6 py-4 font-medium">Confidence</th>
              <th className="px-6 py-4 font-medium">Status</th>
              <th className="px-6 py-4 font-medium">Signals</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-neutral-800">
            {loading ? (
              [...Array(5)].map((_, i) => (
                <tr key={i} className="animate-pulse">
                  <td className="px-6 py-4"><div className="h-4 bg-neutral-800 rounded w-32"></div></td>
                  <td className="px-6 py-4"><div className="h-4 bg-neutral-800 rounded w-40"></div></td>
                  <td className="px-6 py-4"><div className="h-4 bg-neutral-800 rounded w-12"></div></td>
                  <td className="px-6 py-4"><div className="h-4 bg-neutral-800 rounded w-20"></div></td>
                  <td className="px-6 py-4"><div className="h-4 bg-neutral-800 rounded w-24"></div></td>
                </tr>
              ))
            ) : companies.length === 0 ? (
              <tr>
                <td colSpan={5} className="px-6 py-12 text-center text-neutral-500">
                  No companies found. Run the pipeline to collect signals.
                </td>
              </tr>
            ) : (
              companies.map((company) => (
                <tr key={company.id} className="hover:bg-neutral-800/50 cursor-pointer">
                  <td className="px-6 py-4 font-medium">{company.company_name}</td>
                  <td className="px-6 py-4 text-sm text-neutral-400 font-mono">
                    {company.canonical_key || "-"}
                  </td>
                  <td className="px-6 py-4">
                    <ConfidenceBadge confidence={company.confidence} />
                  </td>
                  <td className="px-6 py-4">
                    <StatusBadge status={company.status} />
                  </td>
                  <td className="px-6 py-4 text-sm text-neutral-400">
                    {company.signal_types.join(", ") || "-"}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      
      {(page > 1 || hasMore) && (
        <div className="mt-4 flex justify-center gap-4">
          <button
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page === 1}
            className="px-4 py-2 bg-neutral-800 rounded-lg disabled:opacity-50"
          >
            Previous
          </button>
          <span className="px-4 py-2 text-neutral-400">Page {page}</span>
          <button
            onClick={() => setPage((p) => p + 1)}
            disabled={!hasMore}
            className="px-4 py-2 bg-neutral-800 rounded-lg disabled:opacity-50"
          >
            Next
          </button>
        </div>
      )}
    </div>
  );
}
