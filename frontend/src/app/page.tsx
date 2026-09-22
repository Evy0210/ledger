"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { api, cny, gbp, monthLabel, shiftMonth, thisMonth, todayStr } from "@/lib/api";
import { RequireAuth } from "@/components/require-auth";
import { SwipeRow } from "@/components/swipe-row";
import { SplitSheet, SplitTags, type SheetMode } from "@/components/split-sheet";
import { catInfo, itemLabel, type CategoryTotal, type Expense, type MonthSummary, type Status } from "@/lib/types";

export default function HomePage() {
  return <RequireAuth><Dashboard /></RequireAuth>;
}

function Dashboard() {
  const [month, setMonth] = useState("");
  const [summary, setSummary] = useState<MonthSummary | null>(null);
  const [expenses, setExpenses] = useState<Expense[] | null>(null);
  const [status, setStatus] = useState<Status | null>(null);
  const [error, setError] = useState("");

  // ?m=2026-08 优先（Telegram 月报里的链接 / 浏览器后退），否则本月
  useEffect(() => {
    const m = new URLSearchParams(window.location.search).get("m") || "";
    setMonth(/^\d{4}-\d{2}$/.test(m) ? m : thisMonth());
    api.status().then(setStatus).catch(() => {});
  }, []);

  const load = useCallback(async (m: string) => {
    setError("");
    try {
      const [s, e] = await Promise.all([api.summary(m), api.listExpenses(m)]);
      setSummary(s);
      setExpenses(e.expenses);
    } catch (err) {
      setError((err as Error).message);
    }
  }, []);

  useEffect(() => {
    if (!month) return;
    void load(month);
    const url = month === thisMonth() ? "/" : `/?m=${month}`;
    window.history.replaceState(null, "", url);
  }, [month, load]);

  // 从 Telegram / 邮件切回来时数据可能已经变了 —— 重新拉一次
  useEffect(() => {
    if (!month) return;
    const onVisible = () => { if (document.visibilityState === "visible") { void load(month); api.status().then(setStatus).catch(() => {}); } };
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("focus", onVisible);
    return () => { document.removeEventListener("visibilitychange", onVisible); window.removeEventListener("focus", onVisible); };
  }, [month, load]);

  if (!month) return null;
  const isCurrent = month === thisMonth();
  const today = todayStr();

  return (
    <main>
      {status && isCurrent && <NudgeBanner status={status} />}

      <div className="month-nav">
        <button onClick={() => setMonth(shiftMonth(month, -1))} aria-label="上个月">‹</button>
        <h1>{monthLabel(month)}</h1>
        <button onClick={() => setMonth(shiftMonth(month, 1))} disabled={isCurrent} aria-label="下个月">›</button>
      </div>

      {error && <div className="error-note">{error}</div>}

      {summary && <Hero s={summary} isCurrent={isCurrent} />}

      {summary && summary.by_category.length > 0 && (
        <section className="panel">
          <h3 className="panel-title">分类 <small>有明细的按明细摊</small></h3>
          <div className="cat-list">
            {summary.by_category.map((c) => <CategoryRow key={c.key} c={c} />)}
          </div>
        </section>
      )}

      {summary && summary.count > 0 && <DaySpark s={summary} today={today} />}

      <ExpenseList expenses={expenses} today={today} onChanged={() => void load(month)} />

      <Link href="/add" className="fab" aria-label="记一笔">＋</Link>
    </main>
  );
}

function CategoryRow({ c }: { c: CategoryTotal }) {
  const [open, setOpen] = useState(false);
  const subs = c.subs.filter((sub) => sub.key || c.subs.length > 1);   // 只有「未细分」一项就不展示
  return (
    <div className={`cat-row ${subs.length ? "expandable" : ""}`} onClick={() => subs.length && setOpen((v) => !v)}>
      <div className="cat-emoji">{c.emoji}</div>
      <div>
        <div className="cat-name"><span>{c.label}{subs.length > 0 && <span className="faint"> {open ? "▾" : "▸"}</span>}</span><span className="cat-pct">{c.pct.toFixed(0)}%</span></div>
        <div className="cat-bar"><i style={{ width: `${Math.max(2, c.pct)}%` }} /></div>
      </div>
      <div className="cat-amt">{gbp(c.gbp)}<small>{cny(c.cny)}</small></div>
      {open && subs.length > 0 && (
        <div className="cat-subs">
          {subs.map((sub) => <span key={sub.key || "_"}>{sub.label} <b>{gbp(sub.gbp)}</b></span>)}
        </div>
      )}
    </div>
  );
}

function NudgeBanner({ status }: { status: Status }) {
  if (status.today_logged) return null;
  const gap = status.days_since_last;
  const text = gap < 0 ? "还没有任何记录，从第一笔开始吧"
    : gap <= 1 ? "今天还没记账"
    : `已经 ${gap} 天没记账了`;
  return (
    <div className="banner">
      <span>🔔</span><span>{text}</span>
      <Link href="/add">去记一笔 →</Link>
    </div>
  );
}

function Hero({ s, isCurrent }: { s: MonthSummary; isCurrent: boolean }) {
  const dayOfMonth = isCurrent ? new Date().getDate() : s.days_in_month;
  const perDay = dayOfMonth ? s.total_gbp / dayOfMonth : 0;
  const projected = isCurrent && dayOfMonth >= 3 ? perDay * s.days_in_month : 0;
  const delta = s.prev_month_total_gbp ? (s.total_gbp - s.prev_month_total_gbp) / s.prev_month_total_gbp * 100 : null;
  const budgetPct = s.budget_gbp ? Math.min(100, s.total_gbp / s.budget_gbp * 100) : 0;
  return (
    <section className="panel hero">
      <div className="hero-label">{isCurrent ? "this month so far" : "total"}</div>
      <div className="hero-total">{gbp(s.total_gbp)}</div>
      <div className="hero-cny">≈ {cny(s.total_cny)}</div>
      <div className="hero-meta">
        <span><b>{s.count}</b> 笔</span>
        <span><b>{s.days_logged}</b>/{s.days_in_month} 天有记录</span>
        <span>日均 <b>{gbp(perDay)}</b></span>
        {projected > 0 && <span>照这样月底 <b>{gbp(projected)}</b></span>}
        {delta !== null && (
          <span className={delta >= 0 ? "delta-up" : "delta-down"}>
            比上月 {delta >= 0 ? "+" : ""}{delta.toFixed(0)}%
          </span>
        )}
      </div>
      {(s.gross_gbp > s.total_gbp + 0.005 || s.owed_gbp > 0) && (
        <div className="hero-chips">
          <Link href="/split">实际刷卡 {gbp(s.gross_gbp)}</Link>
          {s.owed_gbp > 0 && <Link href="/split" className="owed">待收 {gbp(s.owed_gbp)} →</Link>}
        </div>
      )}
      {s.budget_gbp && (
        <div className="budget">
          <div className={`budget-bar ${s.total_gbp > s.budget_gbp ? "over" : ""}`}><i style={{ width: `${budgetPct}%` }} /></div>
          <div className="budget-text">
            <span>预算 {gbp(s.budget_gbp)}</span>
            <span>{s.total_gbp > s.budget_gbp ? `超了 ${gbp(s.total_gbp - s.budget_gbp)}` : `还剩 ${gbp(s.budget_gbp - s.total_gbp)}`}</span>
          </div>
        </div>
      )}
    </section>
  );
}

function DaySpark({ s, today }: { s: MonthSummary; today: string }) {
  const byDay = Object.fromEntries(s.by_day.map((d) => [d.date, d.gbp]));
  const max = Math.max(1, ...s.by_day.map((d) => d.gbp));
  const days = Array.from({ length: s.days_in_month }, (_, i) => `${s.month}-${String(i + 1).padStart(2, "0")}`);
  return (
    <section className="panel">
      <h3 className="panel-title">每天 <small>最高一天 {gbp(max)}</small></h3>
      <div className="spark">
        {days.map((d) => {
          const v = byDay[d] || 0;
          return <i key={d} className={`${v ? "has" : ""} ${d === today ? "today" : ""}`} style={{ height: `${Math.max(3, v / max * 100)}%` }} title={`${d}  ${gbp(v)}`} />;
        })}
      </div>
      <div className="spark-axis"><span>1</span><span>{Math.round(s.days_in_month / 2)}</span><span>{s.days_in_month}</span></div>
    </section>
  );
}

function ExpenseList({ expenses, today, onChanged }: { expenses: Expense[] | null; today: string; onChanged: () => void }) {
  const [sheet, setSheet] = useState<{ e: Expense; mode: SheetMode } | null>(null);
  if (!expenses) return null;
  if (!expenses.length) return <div className="empty">这个月还没有记录</div>;
  const groups: { date: string; rows: Expense[]; total: number }[] = [];
  for (const e of expenses) {
    const g = groups[groups.length - 1];
    if (g && g.date === e.date) { g.rows.push(e); g.total += e.share_gbp; }
    else groups.push({ date: e.date, rows: [e], total: e.share_gbp });
  }
  async function remove(e: Expense) {
    try { await api.deleteExpense(e.id); setSheet(null); onChanged(); } catch (err) { alert((err as Error).message); }
  }
  return (
    <>
      {groups.map((g) => (
        <div className="day-group" key={g.date}>
          <div className="day-head">
            <span>{g.date === today ? "今天" : g.date.slice(5).replace("-", "/")} · {weekday(g.date)}</span>
            <span>{gbp(g.total)}</span>
          </div>
          {g.rows.map((e) => {
            const c = catInfo(e.category);
            // 列表里：中文 + 原文并排（学单词用），只放前两项
            // 每一对「中文 · 原文」不在中间断行
            const sub = e.items.length
              ? <>{e.items.slice(0, 2).map((i, k) => <span key={k} style={{ whiteSpace: "nowrap" }}>{k ? "、" : ""}{itemLabel(i)}</span>)}{e.items.length > 2 ? ` 等 ${e.items.length} 项` : ""}</>
              : e.note || c.label;
            return (
              <SwipeRow key={e.id} rightLabel="👥 分账" leftLabel="代付 / 删除 🤝"
                onRight={() => setSheet({ e, mode: "split" })} onLeft={() => setSheet({ e, mode: "left" })}>
                <Link href={`/expense/${e.id}`} className="expense-row">
                  <div className="e-emoji">{c.emoji}</div>
                  <div className="e-main">
                    <div className="e-merchant">{e.merchant || c.label}</div>
                    <div className="e-sub">{sub}</div>
                    <SplitTags e={e} />
                  </div>
                  <div className="e-amt">
                    <div className="money" style={e.split_kind ? { textDecoration: "line-through", color: "var(--ink-faint)", fontWeight: 400, fontSize: 13 } : undefined}>{gbp(e.amount_gbp)}</div>
                    {e.split_kind ? <div className="money">{gbp(e.share_gbp)}</div>
                      : <small>{e.currency !== "GBP" ? `${e.currency} ${e.amount.toFixed(2)}` : cny(e.amount_cny)}</small>}
                    <button className="row-more" aria-label="更多" onClick={(ev) => { ev.preventDefault(); ev.stopPropagation(); setSheet({ e, mode: "menu" }); }}>···</button>
                  </div>
                </Link>
              </SwipeRow>
            );
          })}
        </div>
      ))}
      {sheet && (
        <SplitSheet
          expense={sheet.e} mode={sheet.mode} onClose={() => setSheet(null)}
          onChanged={() => { setSheet(null); onChanged(); }} onDelete={() => void remove(sheet.e)}
        />
      )}
    </>
  );
}

function weekday(date: string): string {
  return ["周日", "周一", "周二", "周三", "周四", "周五", "周六"][new Date(date + "T12:00:00").getDay()];
}
