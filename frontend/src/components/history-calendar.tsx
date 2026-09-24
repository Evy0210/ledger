"use client";

import { useCallback, useEffect, useState } from "react";
import { addDays, api, cny, gbp, todayStr, weekStart } from "@/lib/api";
import { ExpenseList, weekday } from "@/components/expense-list";
import type { DayTotal, Expense } from "@/lib/types";

type View = "day" | "week" | "month" | "year";
const VIEWS: [View, string][] = [["day", "日"], ["week", "周"], ["month", "月"], ["year", "年"]];
const VIEW_KEY = "ledger.calendar-view";
const WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"];

function monthEnd(month: string): string {
  const [y, m] = month.split("-").map(Number);
  return `${month}-${String(new Date(y, m, 0).getDate()).padStart(2, "0")}`;
}

function rangeOf(view: View, anchor: string): [string, string] {
  if (view === "day") return [anchor, anchor];
  if (view === "week") { const s = weekStart(anchor); return [s, addDays(s, 6)]; }
  if (view === "month") return [`${anchor.slice(0, 7)}-01`, monthEnd(anchor.slice(0, 7))];
  return [`${anchor.slice(0, 4)}-01-01`, `${anchor.slice(0, 4)}-12-31`];
}

function shift(view: View, anchor: string, delta: number): string {
  if (view === "day") return addDays(anchor, delta);
  if (view === "week") return addDays(weekStart(anchor), delta * 7);
  const [y, m] = anchor.split("-").map(Number);
  if (view === "month") return `${new Date(y, m - 1 + delta, 1).getFullYear()}-${String(new Date(y, m - 1 + delta, 1).getMonth() + 1).padStart(2, "0")}-01`;
  return `${y + delta}-01-01`;
}

function label(view: View, anchor: string, today: string): string {
  const [y, m, d] = anchor.split("-").map(Number);
  if (view === "day") return anchor === today ? `今天 · ${m}月${d}日` : `${y === Number(today.slice(0, 4)) ? "" : `${y}年`}${m}月${d}日 ${weekday(anchor)}`;
  if (view === "week") {
    const [s, e] = rangeOf("week", anchor);
    return `${Number(s.slice(5, 7))}/${Number(s.slice(8))} – ${Number(e.slice(5, 7))}/${Number(e.slice(8))}`;
  }
  if (view === "month") return `${y} 年 ${m} 月`;
  return `${y} 年`;
}

/** 房租这种大额会把别的天都压成一个色 —— 用 85 分位当满格，超过的封顶。 */
function heatScale(values: number[]): number {
  const sorted = values.filter((v) => v > 0).sort((a, b) => a - b);
  return Math.max(1, sorted[Math.floor((sorted.length - 1) * 0.85)] || 0);
}
function heatBg(v: number, scale: number) {
  if (!v) return undefined;
  return { background: `color-mix(in srgb, var(--brick) ${Math.round(6 + Math.min(1, v / scale) * 54)}%, var(--card))` };
}
const shortGbp = (v: number) => `£${v < 10 ? v.toFixed(1).replace(/\.0$/, "") : Math.round(v)}`;

export function HistoryCalendar() {
  const today = todayStr();
  const [view, setView] = useState<View | null>(null);
  const [anchor, setAnchor] = useState(today);
  const [picked, setPicked] = useState<string | null>(null);
  const [expenses, setExpenses] = useState<Expense[] | null>(null);
  const [days, setDays] = useState<DayTotal[] | null>(null);
  const [error, setError] = useState("");

  // ?v=week&d=2026-09-24 优先（从明细页后退回来能回到原处），否则上次用的视图 + 今天
  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    const v = q.get("v") || (() => { try { return localStorage.getItem(VIEW_KEY); } catch { return null; } })();
    const d = q.get("d") || "";
    setView(VIEWS.some(([k]) => k === v) ? v as View : "month");
    if (/^\d{4}-\d{2}-\d{2}$/.test(d)) setAnchor(d);
  }, []);

  const load = useCallback(async (v: View, a: string) => {
    setError("");
    const [s, e] = rangeOf(v, a);
    try {
      if (v === "year") { setDays((await api.days(s, e)).days); setExpenses(null); }
      else { setExpenses((await api.rangeExpenses(s, e)).expenses); setDays(null); }
    } catch (err) {
      setError((err as Error).message);
    }
  }, []);

  useEffect(() => {
    if (!view) return;
    setExpenses(null); setDays(null); setPicked(null);
    void load(view, anchor);
    window.history.replaceState(null, "", `/months?v=${view}&d=${anchor}`);
  }, [view, anchor, load]);

  if (!view) return null;

  function go(v: View, a: string) {
    setView(v); setAnchor(a);
    try { localStorage.setItem(VIEW_KEY, v); } catch {}
  }

  const [start, end] = rangeOf(view, anchor);
  const nextStart = rangeOf(view, shift(view, anchor, 1))[0];
  const perDay: Record<string, number> = {};
  const perDayCount: Record<string, number> = {};
  for (const e of expenses || []) {
    perDay[e.date] = (perDay[e.date] || 0) + e.share_gbp;
    perDayCount[e.date] = (perDayCount[e.date] || 0) + 1;
  }
  for (const d of days || []) { perDay[d.date] = d.gbp; perDayCount[d.date] = d.count; }
  const total = Object.values(perDay).reduce((a, b) => a + b, 0);
  const totalCny = expenses ? expenses.reduce((a, e) => a + (e.share_cny ?? e.amount_cny), 0) : (days || []).reduce((a, d) => a + d.cny, 0);
  const count = Object.values(perDayCount).reduce((a, b) => a + b, 0);
  // 日均只算到今天为止；年视图从第一笔记录算起（年中才开始记账的年份不被前几个月的空白拉低）
  const firstDay = view === "year" ? Object.keys(perDay).sort()[0] || start : start;
  const last = end < today ? end : today;
  const elapsed = firstDay > last ? 0 : Math.round((Date.parse(last) - Date.parse(firstDay)) / 864e5) + 1;
  const loaded = expenses !== null || days !== null;
  const listed = expenses && picked ? expenses.filter((e) => e.date === picked) : expenses;

  return (
    <>
      <section className="panel cal-panel">
        <div className="mode-tabs cal-tabs">
          {VIEWS.map(([k, t]) => (
            <button key={k} className={`mode-tab ${view === k ? "active" : ""}`} onClick={() => go(k, anchor)}>{t}</button>
          ))}
        </div>
        <div className="month-nav cal-nav">
          <button onClick={() => go(view, shift(view, anchor, -1))} aria-label="上一段">‹</button>
          <div className="cal-title">
            <h2>{label(view, anchor, today)}</h2>
            {start > today || end < today
              ? <button className="cal-today" onClick={() => go(view, today)}>回到今天</button> : null}
          </div>
          <button onClick={() => go(view, shift(view, anchor, 1))} disabled={nextStart > today} aria-label="下一段">›</button>
        </div>

        {error && <div className="error-note">{error}</div>}

        {loaded && (
          <div className="cal-sum">
            <span className="money cal-total">{gbp(total)}</span>
            <span className="muted">≈ {cny(totalCny)}</span>
            <span className="muted"><b>{count}</b> 笔</span>
            {view !== "day" && elapsed > 0 && <span className="muted">日均 <b>{gbp(total / elapsed)}</b></span>}
          </div>
        )}

        {loaded && view === "week" && <WeekStrip start={start} perDay={perDay} today={today} picked={picked} onPick={setPicked} />}
        {loaded && view === "month" && (
          <MonthGrid month={anchor.slice(0, 7)} perDay={perDay} perDayCount={perDayCount} today={today} picked={picked} onPick={setPicked} />
        )}
        {(view === "week" || view === "month") && loaded && (
          <div className="cal-picked">
            {picked
              ? <button className="btn small" onClick={() => setPicked(null)}>看整{view === "week" ? "周" : "月"}明细</button>
              : <span className="faint">点某一天只看那天</span>}
          </div>
        )}
        {loaded && view === "year" && <YearGrid year={anchor.slice(0, 4)} perDay={perDay} today={today} onMonth={(m) => go("month", `${m}-01`)} />}
      </section>

      {view !== "year" && listed && (
        <ExpenseList expenses={listed} today={today} onChanged={() => void load(view, anchor)}
          emptyText={picked || view === "day" ? "这天没有记录" : `这${view === "week" ? "周" : "个月"}没有记录`} />
      )}
    </>
  );
}

function WeekStrip({ start, perDay, today, picked, onPick }: {
  start: string; perDay: Record<string, number>; today: string; picked: string | null; onPick: (d: string | null) => void;
}) {
  const dates = Array.from({ length: 7 }, (_, i) => addDays(start, i));
  const max = heatScale(dates.map((d) => perDay[d] || 0));
  return (
    <div className="week-strip">
      {dates.map((d, i) => {
        const v = perDay[d] || 0;
        return (
          <button key={d} disabled={d > today} className={`week-day ${d === today ? "today" : ""} ${d === picked ? "picked" : ""}`}
            onClick={() => onPick(d === picked ? null : d)}>
            <span className="week-amt">{v ? shortGbp(v) : ""}</span>
            <span className="week-bar"><i className={v > max ? "over" : ""} style={{ height: `${v ? Math.max(6, Math.min(1, v / max) * 100) : 0}%` }} /></span>
            <span className="week-wd">{WEEKDAYS[i]}</span>
            <span className="week-num">{Number(d.slice(8))}</span>
          </button>
        );
      })}
    </div>
  );
}

function MonthGrid({ month, perDay, perDayCount, today, picked, onPick }: {
  month: string; perDay: Record<string, number>; perDayCount: Record<string, number>; today: string;
  picked: string | null; onPick: (d: string | null) => void;
}) {
  const [y, m] = month.split("-").map(Number);
  const lead = (new Date(y, m - 1, 1).getDay() + 6) % 7;
  const n = new Date(y, m, 0).getDate();
  const cells = [...Array<null>(lead).fill(null), ...Array.from({ length: n }, (_, i) => `${month}-${String(i + 1).padStart(2, "0")}`)];
  const scale = heatScale(Object.values(perDay));
  return (
    <div className="cal-grid">
      {WEEKDAYS.map((w) => <div key={w} className="cal-wd">{w}</div>)}
      {cells.map((d, i) => {
        if (!d) return <div key={`_${i}`} />;
        const v = perDay[d] || 0;
        return (
          <button key={d} disabled={d > today} style={heatBg(v, scale)}
            className={`cal-day ${d === today ? "today" : ""} ${d === picked ? "picked" : ""}`}
            onClick={() => onPick(d === picked ? null : d)}>
            <span className="cal-num">{Number(d.slice(8))}</span>
            {v > 0 && <span className="cal-amt">{shortGbp(v)}</span>}
            {perDayCount[d] > 1 && <span className="cal-count">{perDayCount[d]}笔</span>}
          </button>
        );
      })}
    </div>
  );
}

/** 一年 12 个小月历，只看颜色深浅；点哪个月进那个月。 */
function YearGrid({ year, perDay, today, onMonth }: {
  year: string; perDay: Record<string, number>; today: string; onMonth: (month: string) => void;
}) {
  const scale = heatScale(Object.values(perDay));
  return (
    <div className="year-grid">
      {Array.from({ length: 12 }, (_, k) => {
        const month = `${year}-${String(k + 1).padStart(2, "0")}`;
        const [y, m] = [Number(year), k + 1];
        const lead = (new Date(y, m - 1, 1).getDay() + 6) % 7;
        const n = new Date(y, m, 0).getDate();
        const dates = Array.from({ length: n }, (_, i) => `${month}-${String(i + 1).padStart(2, "0")}`);
        const sum = dates.reduce((a, d) => a + (perDay[d] || 0), 0);
        const future = `${month}-01` > today;
        return (
          <button key={month} className="year-month" disabled={future} onClick={() => onMonth(month)}>
            <div className="year-head"><span>{m}月</span><span className="money">{sum ? `£${Math.round(sum)}` : ""}</span></div>
            <div className="year-cells">
              {Array.from({ length: lead }, (_, i) => <i key={`_${i}`} className="blank" />)}
              {dates.map((d) => <i key={d} className={`${d === today ? "today" : ""} ${d > today ? "future" : ""}`} style={heatBg(perDay[d] || 0, scale)} />)}
            </div>
          </button>
        );
      })}
    </div>
  );
}
