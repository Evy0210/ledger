"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { api, downscaleImage, gbp } from "@/lib/api";
import { RequireAuth } from "@/components/require-auth";
import { RECONCILE_HANDOFF_KEY, catInfo, type ReconcileResult, type SmsTx } from "@/lib/types";

export default function ReconcilePage() {
  return <RequireAuth><ReconcileInner /></RequireAuth>;
}

function ReconcileInner() {
  const [text, setText] = useState("");
  const [results, setResults] = useState<ReconcileResult[] | null>(null);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  // 从「记一笔」页拍的图被识别为流水截图 → 结果直接接过来
  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(RECONCILE_HANDOFF_KEY);
      if (raw) {
        sessionStorage.removeItem(RECONCILE_HANDOFF_KEY);
        show(JSON.parse(raw) as ReconcileResult[]);
        setDone("");
      }
    } catch { /* ignore */ }
  }, []);

  function show(list: ReconcileResult[]) {
    setResults(list);
    setPicked(new Set(list.map((x, i) => (x.status === "unmatched" || x.status === "adjust" ? i : -1)).filter((i) => i >= 0)));
  }

  async function runImage(file: File | undefined) {
    if (!file) return;
    setBusy(true); setError(""); setDone(""); setResults(null);
    try {
      const r = await api.reconcileImage(await downscaleImage(file));
      show(r.results);
      if (r.single_receipt) setDone("这张看起来是单张小票，只按一笔比对了；要入账请去「记一笔」页。");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function run() {
    setBusy(true); setError(""); setDone(""); setResults(null);
    try {
      const r = await api.reconcile(text);
      show(r.results);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function commit() {
    if (!results) return;
    const chosen = results.map((r, i) => ({ r, i })).filter(({ i }) => picked.has(i));
    const txs: SmsTx[] = chosen.filter(({ r }) => r.status === "unmatched").map(({ r }) => r.tx);
    const adjustments = chosen.filter(({ r }) => r.status === "adjust" && r.expense).map(({ r }) => ({ expense_id: r.expense!.id, tx: r.tx }));
    if (!txs.length && !adjustments.length) return;
    setBusy(true); setError("");
    try {
      const r = await api.reconcileCommit(txs, adjustments);
      setDone(`补记 ${r.created.length} 笔，金额更新 ${r.adjusted.length} 笔。有小票的话再拍一下，会自动并进去。`);
      setResults((old) => old && old.map((x, i) => {
        if (!picked.has(i)) return x;
        if (x.status === "adjust") return { ...x, status: "matched", expense: x.expense && { ...x.expense, amount_gbp: x.tx.amount } };
        return { ...x, status: "matched", expense: { id: r.created.find((c) => Math.abs(c.amount - x.tx.amount) < 0.011)?.id || 0, date: x.tx.date, merchant: x.tx.merchant, amount_gbp: x.tx.amount, category: "other" } };
      }));
      setPicked(new Set());
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const unmatched = results?.filter((r) => r.status === "unmatched" || r.status === "adjust").length || 0;

  return (
    <main>
      <section className="panel">
        <h3 className="panel-title">💳 信用卡短信对账</h3>
        <p className="hint">
          把银行发的消费短信粘进来（一条或一次粘一堆都行，英文中文都认）。每条会和账本比对：记过的打 ✓，
          没记的可以一键补上——补上的先按短信记，之后小票或截图来了会自动并进去。
          也可以直接传<b>银行 App 流水或短信列表的截图</b>，或者把短信文字 / 截图发给 Telegram bot。
        </p>
        <textarea
          className="textarea" rows={6} value={text} onChange={(e) => setText(e.target.value)}
          placeholder={"Monzo: You spent £12.30 at UBER *TRIP\nBarclays: £4.50 at PRET A MANGER on 17 Sep\n招商银行：您尾号1234的信用卡消费人民币68.00元"}
        />
        <div className="row-actions" style={{ marginTop: 10 }}>
          <button className="btn primary" onClick={run} disabled={busy || !text.trim()}>{busy ? "比对中…" : "对账"}</button>
          <button className="btn" onClick={() => fileRef.current?.click()} disabled={busy}>📷 用截图对账</button>
          <input ref={fileRef} type="file" accept="image/*" hidden onChange={(e) => { void runImage(e.target.files?.[0]); e.target.value = ""; }} />
          {results && unmatched > 0 && (
            <button className="btn brick" onClick={commit} disabled={busy || picked.size === 0}>应用勾选的 {picked.size} 项</button>
          )}
        </div>
        {error && <div className="error-note">{error}</div>}
        {done && <div className="ok-note">{done} <Link href="/">回本月 →</Link></div>}
      </section>

      {results && (
        <section className="panel">
          <h3 className="panel-title">结果 <small>{results.length} 条</small></h3>
          {results.length === 0 && <div className="empty">没从这段文字里找到消费记录</div>}
          {results.map((r, i) => (
            <div className="recon-row" key={i}>
              <div>
                {r.status === "unmatched" || r.status === "adjust" ? (
                  <input type="checkbox" checked={picked.has(i)} onChange={(e) => {
                    const next = new Set(picked); if (e.target.checked) next.add(i); else next.delete(i); setPicked(next);
                  }} />
                ) : r.status === "matched" ? "✅" : r.status === "maybe" ? "❓" : "➖"}
              </div>
              <div className="r-main">
                <div><span className="mono">{r.tx.date.slice(5)}</span> {r.tx.merchant || "—"} <b className="money">{r.tx.currency === "GBP" ? gbp(r.tx.amount) : `${r.tx.currency} ${r.tx.amount.toFixed(2)}`}</b></div>
                <div className="r-sub">
                  {r.status === "matched" && r.expense && <>已记：<Link href={`/expense/${r.expense.id}`}>{r.expense.date.slice(5)} {catInfo(r.expense.category).emoji} {r.expense.merchant}</Link></>}
                  {r.status === "maybe" && r.expense && <>可能是 <Link href={`/expense/${r.expense.id}`}>{r.expense.date.slice(5)} {r.expense.merchant} {gbp(r.expense.amount_gbp)}</Link>，商家名对不上，没另记</>}
                  {r.status === "adjust" && r.expense && <>是 <Link href={`/expense/${r.expense.id}`}>{r.expense.date.slice(5)} {r.expense.merchant} {gbp(r.expense.amount_gbp)}</Link> 那笔，差 <b>{gbp(r.diff_gbp || 0)}</b>（配送费 / 差价）→ 勾选后把金额改成短信的</>}
                  {r.status === "unmatched" && "账本里没有这笔"}
                  {r.status === "ignored" && `${r.tx.kind === "refund" ? "退款" : "非消费"}，跳过`}
                </div>
              </div>
              <span className={`tag ${r.status === "matched" ? "ok" : r.status === "maybe" || r.status === "adjust" ? "warn" : r.status === "unmatched" ? "new" : ""}`}>
                {r.status === "matched" ? "已对上" : r.status === "maybe" ? "疑似" : r.status === "adjust" ? "金额有差" : r.status === "unmatched" ? "未记录" : "跳过"}
              </span>
            </div>
          ))}
        </section>
      )}
    </main>
  );
}
