"use client";

import { useState } from "react";
import Link from "next/link";
import { api, cny, gbp } from "@/lib/api";
import { SwipeRow } from "@/components/swipe-row";
import { SplitSheet, SplitTags, type SheetMode } from "@/components/split-sheet";
import { catInfo, itemLabel, type Expense } from "@/lib/types";

/** 按天分组的账单列表，左右滑分账 / 代付 / 删除。首页和历史页的日历共用。 */
export function ExpenseList({ expenses, today, onChanged, emptyText = "这个月还没有记录" }: { expenses: Expense[] | null; today: string; onChanged: () => void; emptyText?: string }) {
  const [sheet, setSheet] = useState<{ e: Expense; mode: SheetMode } | null>(null);
  if (!expenses) return null;
  if (!expenses.length) return <div className="empty">{emptyText}</div>;
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

export function weekday(date: string): string {
  return ["周日", "周一", "周二", "周三", "周四", "周五", "周六"][new Date(date + "T12:00:00").getDay()];
}
