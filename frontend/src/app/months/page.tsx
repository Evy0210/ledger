"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, cny, gbp, monthLabel } from "@/lib/api";
import { RequireAuth } from "@/components/require-auth";
import { HistoryCalendar } from "@/components/history-calendar";
import type { MonthRow } from "@/lib/types";

export default function MonthsPage() {
  return <RequireAuth><MonthsInner /></RequireAuth>;
}

function MonthsInner() {
  const [months, setMonths] = useState<MonthRow[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api.months().then((r) => setMonths(r.months)).catch((e) => setError((e as Error).message));
  }, []);

  if (error) return <main className="panel"><div className="error-note">{error}</div></main>;
  if (!months) return null;
  if (!months.length) return <div className="empty">还没有任何记录</div>;

  const recent = [...months].slice(0, 12).reverse();
  const max = Math.max(1, ...recent.map((m) => m.total_gbp));
  const total = months.reduce((s, m) => s + m.total_gbp, 0);
  const totalCny = months.reduce((s, m) => s + m.total_cny, 0);

  return (
    <main>
      <section className="panel hero">
        <div className="hero-label">all time</div>
        <div className="hero-total">{gbp(total)}</div>
        <div className="hero-cny">≈ {cny(totalCny)} · {months.length} 个月 · 月均 {gbp(total / months.length)}</div>
      </section>

      <HistoryCalendar />

      <section className="panel months-panel">
        <h3 className="panel-title">最近 {recent.length} 个月</h3>
        <div className="trend">
          {recent.map((m) => (
            <Link href={`/?m=${m.month}`} key={m.month} title={`${monthLabel(m.month)} ${gbp(m.total_gbp)}`}>
              <i style={{ height: `${Math.max(4, m.total_gbp / max * 100)}%` }} />
              <span>{Number(m.month.slice(5))}月</span>
            </Link>
          ))}
        </div>
        <table className="months-table">
          <tbody>
            {months.map((m) => (
              <tr key={m.month}>
                <td><Link href={`/?m=${m.month}`}>{monthLabel(m.month)}</Link> <span className="tag">{m.count} 笔</span></td>
                <td className="money muted">{cny(m.total_cny)}</td>
                <td className="money"><b>{gbp(m.total_gbp)}</b></td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </main>
  );
}
