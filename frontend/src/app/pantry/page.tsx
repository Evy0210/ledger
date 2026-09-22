"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { RequireAuth } from "@/components/require-auth";
import { subLabel, type PantryItem, type PantryOverview } from "@/lib/types";

export default function PantryPage() {
  return <RequireAuth><PantryInner /></RequireAuth>;
}

function PantryInner() {
  const [ov, setOv] = useState<PantryOverview | null>(null);
  const [error, setError] = useState("");

  const load = () => api.pantry().then(setOv).catch((e) => setError((e as Error).message));
  useEffect(() => { void load(); }, []);

  async function mark(item: PantryItem, status: "used" | "tossed") {
    try {
      await api.setPantry(item.id, { status });
      setOv((old) => old && {
        ...old, count: old.count - 1,
        soon: old.soon.filter((x) => x.id !== item.id), fresh: old.fresh.filter((x) => x.id !== item.id), dry: old.dry.filter((x) => x.id !== item.id),
      });
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function extend(item: PantryItem, days: number) {
    const d = new Date(item.expires + "T12:00:00"); d.setDate(d.getDate() + days);
    const expires = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    try { await api.setPantry(item.id, { expires }); void load(); } catch (e) { setError((e as Error).message); }
  }

  if (error) return <main className="panel"><div className="error-note">{error}</div></main>;
  if (!ov) return null;

  const Group = ({ title, rows, hint }: { title: string; rows: PantryItem[]; hint?: string }) => rows.length === 0 ? null : (
    <section className="panel">
      <h3 className="panel-title">{title} <small>{hint || `${rows.length} 项`}</small></h3>
      {rows.map((r) => (
        <div className="pantry-row" key={r.id}>
          <div style={{ minWidth: 0 }}>
            <div className="p-name">
              {r.name}{r.qty !== 1 ? ` ×${r.qty}` : ""}{" "}
              <span className={`days ${r.days_left < 0 ? "bad" : r.days_left <= 2 ? "warn" : "ok"}`}>
                {r.days_left < 0 ? `过期 ${-r.days_left} 天` : r.days_left === 0 ? "今天到期" : `剩 ${r.days_left} 天`}
              </span>
            </div>
            {r.name_raw && r.name_raw !== r.name && <div className="p-raw">{r.name_raw}</div>}
            <div className="p-sub">
              {r.bought.slice(5)} 买{r.sub ? ` · ${subLabel("groceries", r.sub)}` : ""} · <Link href={`/expense/${r.expense_id}`}>小票</Link>
              {ov?.recipes_url && <>{" · "}<a href={`${ov.recipes_url}/?q=${encodeURIComponent(r.name.slice(0, 6))}`} target="_blank" rel="noreferrer">找菜谱 ↗</a></>}
            </div>
          </div>
          <div className="p-actions">
            <button className="btn small" title="还能放几天，延后 3 天" onClick={() => extend(r, 3)}>+3天</button>
            <button className="btn small" onClick={() => mark(r, "used")}>用完</button>
            <button className="btn ghost small" onClick={() => mark(r, "tossed")}>扔了</button>
          </div>
        </div>
      ))}
    </section>
  );

  return (
    <main>
      <section className="panel hero">
        <div className="hero-label">in the fridge</div>
        <div className="hero-total">{ov.count} <span style={{ fontSize: 18, fontWeight: 400 }}>项</span></div>
        <div className="hero-cny">
          {ov.soon.length ? `${ov.soon.length} 项快过期了，今晚做菜？` : "没有快过期的，放心 🙂"} · 买菜小票 / Ocado 邮件入账后自动进来
        </div>
      </section>
      {ov.count === 0 && (
        <div className="empty">库存是空的。老记录去「设置 → 补全明细信息」跑一次就会出现。</div>
      )}
      <Group title="⚠️ 快过期" rows={ov.soon} hint="2 天内" />
      <Group title="🥬 新鲜" rows={ov.fresh} />
      <Group title="🍚 干货 / 调料" rows={ov.dry} hint="放得住" />
    </main>
  );
}
