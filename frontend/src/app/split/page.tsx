"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { api, gbp, monthLabel, shiftMonth, thisMonth } from "@/lib/api";
import { RequireAuth } from "@/components/require-auth";
import { catInfo, type Expense, type SheetOverview } from "@/lib/types";

export default function SplitPage() {
  return <RequireAuth><SplitInner /></RequireAuth>;
}

function SplitInner() {
  const [month, setMonth] = useState("");          // "" = 全部
  const [rows, setRows] = useState<Expense[] | null>(null);
  const [owed, setOwed] = useState(0);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [onlyOpen, setOnlyOpen] = useState(true);
  const [sheet, setSheet] = useState<SheetOverview | null>(null);
  const [syncing, setSyncing] = useState(false);

  async function load(m: string) {
    setError("");
    try {
      const r = await api.listSplit(m);
      setRows(r.expenses);
      setOwed(r.owed_gbp);
    } catch (e) {
      setError((e as Error).message);
    }
  }
  useEffect(() => { void load(month); }, [month]);
  useEffect(() => { api.sheet().then(setSheet).catch(() => setSheet(null)); }, []);

  async function syncSheet() {
    setSyncing(true); setError(""); setNotice("");
    try {
      const r = await api.syncSheet();
      setSheet(r);
      const moved = (r.appended || 0) + (r.pushed || 0) + (r.pulled || 0) + (r.removed || 0);
      setNotice(moved ? `同步好了：推上去 ${r.pushed || 0} 笔、拉回 ${r.pulled || 0} 笔、删掉 ${r.removed || 0} 笔 ✓`
                      : "已经是最新的了 ✓");
      await load(month);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSyncing(false);
    }
  }

  const shown = useMemo(() => (rows || []).filter((r) => !onlyOpen || !r.settled), [rows, onlyOpen]);

  // 按人汇总：split_with 里逗号分开的名字各欠 amount/n；代付全额算给写的那个人
  const perPerson = useMemo(() => {
    const m = new Map<string, number>();
    for (const r of (rows || []).filter((x) => !x.settled)) {
      const names = r.split_with.split(/[,，、/ ]+/).map((s) => s.trim()).filter(Boolean);
      if (!names.length) { m.set("（没写名字）", (m.get("（没写名字）") || 0) + r.owed_gbp); continue; }
      // 按「别人一共欠我多少」摊，不能用 amount/n —— 商品级标了「我的 / TA 的」时 amount/n 是错的
      const each = r.split_kind === "split" ? r.owed_gbp / Math.max(r.split_n - 1, 1) : r.owed_gbp / names.length;
      for (const n of names) m.set(n, (m.get(n) || 0) + each);
    }
    return [...m.entries()].sort((a, b) => b[1] - a[1]);
  }, [rows]);

  async function toggleSettled(e: Expense) {
    try {
      const updated = await api.setSplit(e.id, { split_kind: e.split_kind, split_n: e.split_n, split_with: e.split_with, settled: !e.settled });
      setRows((old) => old && old.map((x) => (x.id === e.id ? updated : x)));
      setOwed((v) => v + (updated.owed_gbp - e.owed_gbp));
    } catch (err) {
      setError((err as Error).message);
    }
  }

  /** 分账里公用 / 别人的商品（商品级标了才有；没标 = 整单公用） */
  function sharedInfo(r: Expense) {
    const flagged = r.items.some((i) => i.who === "me" || i.who === "them");
    if (r.split_kind !== "split" || !flagged) return { shared: [] as Expense["items"], theirs: [] as Expense["items"], whole: true };
    return {
      shared: r.items.filter((i) => i.who !== "me" && i.who !== "them" && i.amount > 0),
      theirs: r.items.filter((i) => i.who === "them" && i.amount > 0),
      whole: false,
    };
  }
  const itemsText = (items: Expense["items"]) => items.map((i) => `${i.name_zh || i.name}${i.qty && i.qty !== 1 ? ` ×${i.qty}` : ""} ${i.amount.toFixed(2)}`).join("; ");

  function table(sep: string): string {
    const head = ["日期", "商家", "总额 £", "类型", "人数", "公用商品", "公用金额 £", "TA 的商品", "每人应付 £", "我付 £", "别人欠我 £", "和谁分", "已收款"];
    const lines = shown.map((r) => {
      const si = sharedInfo(r);
      const sharedAmt = si.whole ? r.amount_gbp : si.shared.reduce((s, i) => s + i.amount, 0);
      return [
        r.date, r.merchant, r.amount_gbp.toFixed(2), r.split_kind === "split" ? "分账" : "代付",
        r.split_kind === "split" ? String(r.split_n) : "",
        r.split_kind === "split" ? (si.whole ? "整单" : itemsText(si.shared)) : itemsText(r.items),
        r.split_kind === "split" ? sharedAmt.toFixed(2) : r.amount_gbp.toFixed(2),
        itemsText(si.theirs),
        r.split_kind === "split" ? (sharedAmt / r.split_n).toFixed(2) : "",
        r.share_gbp.toFixed(2), (r.amount_gbp - r.share_gbp).toFixed(2), r.split_with, r.settled ? "是" : "否",
      ].map((c) => (sep === "," ? `"${String(c).replace(/"/g, '""')}"` : String(c).replace(/\t/g, " ")));
    });
    return [head, ...lines].map((l) => l.join(sep)).join("\n");
  }

  async function copyTable() {
    try {
      await navigator.clipboard.writeText(table("\t"));
      setNotice("已复制，去 Google Sheet 里 Cmd+V / 长按粘贴就是表格 ✓");
    } catch {
      setNotice("复制失败，用「下载 CSV」吧");
    }
  }

  function downloadCsv() {
    const blob = new Blob(["﻿" + table(",")], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `分账-${month || "全部"}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  }

  return (
    <main>
      <section className="panel hero">
        <div className="hero-label">owed to me</div>
        <div className="hero-total">{gbp(owed)}</div>
        <div className="hero-cny">{(rows || []).filter((r) => !r.settled).length} 笔还没收 · 首页支出已按你自己那份计算</div>
        {perPerson.length > 0 && (
          <div className="hero-chips">
            {perPerson.map(([name, amt]) => <span key={name} className="tag owed">{name} 欠 {gbp(amt)}</span>)}
          </div>
        )}
      </section>


      {sheet?.enabled && (
        <section className="panel">
          <div className="panel-title">
            <span>🏠 合租账本</span>
            <button className="btn ghost small" onClick={syncSheet} disabled={syncing}>
              {syncing ? "同步中…" : "🔄 同步"}
            </button>
          </div>
          <div className="hero-chips">
            {sheet.people.map((p) => (
              <span key={p.name} className={`tag ${p.net_gbp > 0.005 ? "owed" : ""}`}>
                {p.name}{" "}
                {Math.abs(p.net_gbp) < 0.005 ? "已结清" : p.net_gbp > 0 ? `应收 ${gbp(p.net_gbp)}` : `应付 ${gbp(-p.net_gbp)}`}
              </span>
            ))}
          </div>
          {!!sheet.transfers?.length && (
            <div className="s-sub" style={{ marginTop: 10 }}>
              结清方案：{sheet.transfers.map((t) => `${t.from} → ${t.to} ${gbp(t.amount_gbp)}`).join("；")}
            </div>
          )}
          {(() => {
            const theirs = sheet.rows.filter((r) => !r.from_web && !r.settled);
            if (!theirs.length) return null;
            return (
              <>
                <div className="panel-title" style={{ marginTop: 14 }}><span>室友记的（本站没有）</span></div>
                {theirs.map((r) => (
                  <div className="split-row" key={r.row}>
                    <span aria-hidden>🏠</span>
                    <div style={{ minWidth: 0 }}>
                      <div>{r.merchant} <span className="money">{gbp(r.amount_gbp)}</span></div>
                      <div className="s-sub">
                        {r.date} · {r.payer} 垫的 · {r.participants.join("、")} 各 {gbp(r.each_gbp)}
                        {r.note ? ` · ${r.note}` : ""}
                      </div>
                    </div>
                  </div>
                ))}
              </>
            );
          })()}
        </section>
      )}

      <section className="panel">
        <div className="panel-title">
          <span>
            <button className="btn ghost small" onClick={() => setMonth(month ? shiftMonth(month, -1) : thisMonth())}>‹</button>
            {" "}{month ? monthLabel(month) : "全部"}{" "}
            <button className="btn ghost small" disabled={!month || month === thisMonth()} onClick={() => setMonth(shiftMonth(month, 1))}>›</button>
            {month && <button className="btn ghost small" onClick={() => setMonth("")}>全部</button>}
          </span>
          <label style={{ fontWeight: 400, fontSize: 13 }}>
            <input type="checkbox" checked={onlyOpen} onChange={(e) => setOnlyOpen(e.target.checked)} /> 只看没收的
          </label>
        </div>
        <div className="row-actions" style={{ marginTop: 0, marginBottom: 12 }}>
          {sheet?.enabled
            ? <button className="btn small" onClick={syncSheet} disabled={syncing}>{syncing ? "同步中…" : "🔄 同步到合租表"}</button>
            : <button className="btn small" onClick={copyTable} disabled={!shown.length}>📋 复制成表格</button>}
          <button className="btn small" onClick={downloadCsv} disabled={!shown.length}>⬇️ 下载 CSV</button>
        </div>
        {notice && <div className="ok-note">{notice}</div>}
        {error && <div className="error-note">{error}</div>}
        {rows && shown.length === 0 && <div className="empty">{onlyOpen ? "都收完了 🎉" : "还没有分账 / 代付的记录。在首页右滑一笔试试。"}</div>}
        {shown.map((r) => (
          <div className={`split-row ${r.settled ? "done" : ""}`} key={r.id}>
            <input type="checkbox" checked={r.settled} onChange={() => toggleSettled(r)} title="已收款" />
            <div style={{ minWidth: 0 }}>
              <div><Link href={`/expense/${r.id}`}>{catInfo(r.category).emoji} {r.merchant || catInfo(r.category).label}</Link> <span className="money">{gbp(r.amount_gbp)}</span></div>
              <div className="s-sub">
                {r.date} · {r.split_kind === "split" ? `${r.split_n} 人分` : "代付"}{r.split_with ? ` · ${r.split_with}` : ""}
                {r.split_kind === "split" && (() => {
                  const si = sharedInfo(r);
                  if (si.whole) return <> · 整单平分，每人 {gbp(r.amount_gbp / r.split_n)}</>;
                  const amt = si.shared.reduce((s, i) => s + i.amount, 0);
                  return <> · 公用 {gbp(amt)}（{si.shared.map((i) => i.name_zh || i.name).join("、")}），每人 {gbp(amt / r.split_n)}{si.theirs.length ? `；TA 的 ${si.theirs.map((i) => i.name_zh || i.name).join("、")}` : ""}</>;
                })()}
              </div>
            </div>
            <span className={`tag ${r.settled ? "" : "owed"}`}>{r.settled ? "已收" : `待收 ${gbp(r.owed_gbp)}`}</span>
          </div>
        ))}
      </section>
    </main>
  );
}
