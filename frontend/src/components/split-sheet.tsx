"use client";

import { useEffect, useState } from "react";
import { api, gbp } from "@/lib/api";
import { previewShare, type Expense, type ItemWho, type SheetRoster, type SplitInput } from "@/lib/types";

const SEP = /[,，、/\s]+/;
let rosterCache: SheetRoster | null = null;

/** 合租花名册。整个会话只拉一次。 */
function useRoster() {
  const [roster, setRoster] = useState<SheetRoster | null>(rosterCache);
  useEffect(() => {
    if (rosterCache) return;
    api.sheetPeople().then((r) => { rosterCache = r; setRoster(r); }).catch(() => { /* 没配合租表就算了 */ });
  }, []);
  return roster;
}

/** 和谁分：室友点标签（名字一定对得上，才能同步到合租表），其他人手打。 */
function WhoPicker({ roommates, picked, toggle, otherText, setOtherText, toSheet }: {
  roommates: string[]; picked: string[]; toggle: (name: string) => void;
  otherText: string; setOtherText: (v: string) => void; toSheet: boolean;
}) {
  return (
    <div className="field">
      <label>和谁分</label>
      {roommates.length > 0 && (
        <div className="chips">
          {roommates.map((r) => (
            <button key={r} className={`cat-chip ${picked.includes(r) ? "active" : ""}`} onClick={() => toggle(r)}>
              {picked.includes(r) ? "✓ " : ""}{r}
            </button>
          ))}
        </div>
      )}
      <input className="input" value={otherText} onChange={(e) => setOtherText(e.target.value)}
             placeholder={roommates.length ? "其他人（不是室友的，比如 Amy）" : "Amy, Ben"} maxLength={100} />
      <div className="hint" style={{ marginBottom: 0 }}>
        {toSheet ? `✅ 会同步到合租表（${picked.join("、")}）` : "🔒 只记在本站，不进合租表"}
      </div>
    </div>
  );
}

export type SheetMode = "split" | "left" | "menu";

/** 底部弹层：
    - split：分账设置（人数 / 和谁 / 已收款）
    - left：左滑菜单（代付已收回 / 删除）
    - menu：电脑上的 ··· 菜单（分账 / 代付 / 删除） */
export function SplitSheet({ expense, mode, onClose, onChanged, onDelete }: {
  expense: Expense; mode: SheetMode; onClose: () => void; onChanged: (e: Expense) => void; onDelete: () => void;
}) {
  const [view, setView] = useState<SheetMode>(mode);
  const [n, setN] = useState(expense.split_kind === "split" ? expense.split_n : 2);
  const [settled, setSettled] = useState(expense.settled);
  const roster = useRoster();
  const roommates = roster?.enabled ? roster.roommates : [];
  const [picked, setPicked] = useState<string[]>([]);
  const [otherText, setOtherText] = useState("");
  // 花名册到了之后，把已存的「和谁分」拆成「室友标签 + 其他人」
  useEffect(() => {
    const names = (expense.split_with || "").split(SEP).filter(Boolean);
    const low = roommates.map((r) => r.toLowerCase());
    setPicked(roommates.filter((r) => names.some((x) => x.toLowerCase() === r.toLowerCase())));
    setOtherText(names.filter((x) => !low.includes(x.toLowerCase())).join(", "));
  }, [roster, expense.split_with]);   // eslint-disable-line react-hooks/exhaustive-deps
  const toggle = (name: string) =>
    setPicked((old) => (old.includes(name) ? old.filter((x) => x !== name) : [...old, name]));
  const otherNames = otherText.split(SEP).filter(Boolean);
  const named = picked.length + otherNames.length;
  const headcount = named > 0 ? named + 1 : n;          // 点了名就按点的人数算，没点才用手选的
  const whoText = [...picked, ...otherNames].join(", ");
  const toSheet = !!roster?.enabled && picked.length > 0;
  const [who, setWhoList] = useState<ItemWho[]>(expense.items.map((i) => i.who || ""));
  const hasItems = expense.items.some((i) => (i.amount || 0) > 0);
  const flagged = who.some((w) => w === "me" || w === "them");
  const myShare = previewShare(expense.amount_gbp, expense.items, headcount, who);
  const setAll = (w: ItemWho) => setWhoList(expense.items.map(() => w));
  const cycle = (i: number) => setWhoList((old) => old.map((w, k) => (k === i ? (w === "me" ? "them" : w === "them" ? "shared" : "me") : w)));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function apply(body: SplitInput) {
    setBusy(true); setError("");
    try {
      onChanged(await api.setSplit(expense.id, body));
      onClose();
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  const title = `${expense.merchant || "这笔"} · ${gbp(expense.amount_gbp)}`;

  return (
    <>
      <button className="sheet-backdrop" aria-label="关闭" onClick={onClose} />
      <div className="sheet" role="dialog">
        <div className="sheet-handle" />
        {view === "split" && (
          <>
            <h3>👥 分账</h3>
            <div className="sub">{title}，你只算自己那份{hasItems ? `，${expense.items.length} 项明细` : "（这笔没有明细，整单平分）"}</div>
            <div className="field">
              <label>几个人分（含你）</label>
              {named > 0 ? (
                <div className="chips">
                  <span className="tag split">{headcount} 人：你{[...picked, ...otherNames].map((x) => ` + ${x}`).join("")}</span>
                </div>
              ) : (
                <div className="chips">
                  {[2, 3, 4, 5, 6].map((k) => (
                    <button key={k} className={`cat-chip ${n === k ? "active" : ""}`} onClick={() => setN(k)}>{k} 人</button>
                  ))}
                  <input className="input" style={{ width: 80 }} type="number" min={2} max={30} value={n} onChange={(e) => setN(Math.max(2, Number(e.target.value) || 2))} />
                </div>
              )}
              <div className="hint" style={{ marginBottom: 0 }}>
                {flagged ? <>我的 {gbp(myShare)}，别人合计欠你 {gbp(expense.amount_gbp - myShare)}</>
                  : <>整单平分：每人 {gbp(expense.amount_gbp / headcount)}，别人合计欠你 {gbp(expense.amount_gbp - expense.amount_gbp / headcount)}</>}
              </div>
            </div>
            {hasItems && (
              <div className="field">
                <label style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <span>哪些是公用的？点一项切换：公用 → 我的 → TA 的</span>
                  <span className="chips">
                    <button className="cat-chip" onClick={() => setAll("shared")}>全公用</button>
                    <button className="cat-chip" onClick={() => setAll("me")}>全我的</button>
                  </span>
                </label>
                <div className="who-list">
                  {expense.items.map((it, i) => (
                    <button key={i} className={`who-row ${who[i] || "shared"}`} onClick={() => cycle(i)} disabled={!(it.amount > 0)}>
                      <span className="who-tag">{who[i] === "me" ? "我的" : who[i] === "them" ? "TA 的" : "公用"}</span>
                      <span className="who-name">
                        {it.name_zh && it.name_zh !== it.name ? <>{it.name_zh} <span className="faint">{it.name}</span></> : it.name}
                        {it.qty && it.qty !== 1 ? ` ×${it.qty}` : ""}
                      </span>
                      <span className="money">{it.amount > 0 ? gbp(it.amount) : "—"}</span>
                    </button>
                  ))}
                </div>
              </div>
            )}
            <WhoPicker roommates={roommates} picked={picked} toggle={toggle}
                       otherText={otherText} setOtherText={setOtherText} toSheet={toSheet} />
            <div className="setting-row" style={{ borderBottom: "none", paddingTop: 0 }}>
              <div className="label">他们已经把钱给我了</div>
              <button className={`switch ${settled ? "on" : ""}`} aria-label="已收款" onClick={() => setSettled((v) => !v)} />
            </div>
            {error && <div className="error-note">{error}</div>}
            <div className="row-actions" style={{ marginTop: 6 }}>
              <button className="btn primary" disabled={busy} onClick={() => apply({ split_kind: "split", split_n: headcount, split_with: whoText, settled, item_who: who })}>保存</button>
              {expense.split_kind && (
                <button className="btn ghost" disabled={busy} onClick={() => apply({ split_kind: "", split_n: 1, split_with: "", settled: false })}>取消分账</button>
              )}
              <button className="btn ghost" style={{ marginLeft: "auto" }} onClick={onClose}>关闭</button>
            </div>
          </>
        )}

        {view === "left" && (
          <>
            <h3>{title}</h3>
            <div className="sub">这笔不是你自己的花销？</div>
            <WhoPicker roommates={roommates} picked={picked} toggle={toggle}
                       otherText={otherText} setOtherText={setOtherText} toSheet={toSheet} />
            {error && <div className="error-note">{error}</div>}
            <div className="sheet-actions">
              <button className="btn" disabled={busy} onClick={() => apply({ split_kind: "paid_for", split_n: 1, split_with: whoText, settled: true })}>
                🤝 帮别人付的，已经收回 —— 不算我的支出，记录留着
              </button>
              <button className="btn" disabled={busy} onClick={() => apply({ split_kind: "paid_for", split_n: 1, split_with: whoText, settled: false })}>
                ⏳ 帮别人付的，还没收回 —— 记进「待收」
              </button>
              <button className="btn danger" disabled={busy} onClick={() => { if (confirm("真的删掉？删了就没法和银行短信对账了。")) onDelete(); }}>
                🗑 删除这笔
              </button>
              <button className="btn ghost" onClick={onClose}>取消</button>
            </div>
          </>
        )}

        {view === "menu" && (
          <>
            <h3>{title}</h3>
            <div className="sub">{expense.date}{expense.split_kind === "split" ? ` · ${expense.split_n} 人分` : expense.split_kind === "paid_for" ? " · 代付" : ""}</div>
            <div className="sheet-actions">
              <button className="btn" onClick={() => setView("split")}>👥 和室友分账</button>
              <button className="btn" onClick={() => setView("left")}>🤝 代付 / 删除</button>
              {expense.split_kind && !expense.settled && (
                <button className="btn" disabled={busy} onClick={() => apply({ split_kind: expense.split_kind, split_n: expense.split_n, split_with: expense.split_with, settled: true })}>✅ 标记为已收款</button>
              )}
              {expense.split_kind && (
                <button className="btn ghost" disabled={busy} onClick={() => apply({ split_kind: "", split_n: 1, split_with: "", settled: false })}>恢复为自己的支出</button>
              )}
              <button className="btn ghost" onClick={onClose}>取消</button>
            </div>
          </>
        )}
      </div>
    </>
  );
}

export function SplitTags({ e }: { e: Expense }) {
  if (!e.split_kind) return null;
  return (
    <div className="e-tags">
      {e.split_kind === "split"
        ? <span className="tag split">👥 {e.split_n} 人分{e.items.some((i) => i.who === "me" || i.who === "them") ? ` · 公用 ${e.items.filter((i) => i.who !== "me" && i.who !== "them" && i.amount > 0).length} 项` : ""} · 我 {gbp(e.share_gbp)}</span>
        : <span className="tag paid">🤝 代付{e.split_with ? ` ${e.split_with}` : ""}</span>}
      {e.settled ? <span className="tag">已收</span> : <span className="tag owed">待收 {gbp(e.owed_gbp)}</span>}
    </div>
  );
}
