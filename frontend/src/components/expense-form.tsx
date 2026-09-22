"use client";

import { useMemo, useState } from "react";
import { todayStr } from "@/lib/api";
import { CATEGORIES, CURRENCIES, CURRENCY_SYMBOL, SUBCATEGORIES, catInfo, type Category, type ExpenseInput, type ExpenseItem } from "@/lib/types";

interface Props {
  initial?: Partial<ExpenseInput>;
  submitLabel: string;
  submitting?: boolean;
  onSubmit: (input: ExpenseInput) => void;
  extra?: React.ReactNode;   // 放在按钮行右边（比如删除）
}

const blankItem = (category: Category): ExpenseItem => ({ name: "", qty: 1, amount: 0, category, sub: "" });

export function ExpenseForm({ initial, submitLabel, submitting, onSubmit, extra }: Props) {
  const [date, setDate] = useState(initial?.date || todayStr());
  const [merchant, setMerchant] = useState(initial?.merchant || "");
  const [category, setCategory] = useState<Category>(initial?.category || "groceries");
  const [currency, setCurrency] = useState(initial?.currency || "GBP");
  const [amount, setAmount] = useState(initial?.amount ? String(initial.amount) : "");
  const [items, setItems] = useState<ExpenseItem[]>(initial?.items || []);
  const [note, setNote] = useState(initial?.note || "");
  const [showItems, setShowItems] = useState((initial?.items?.length || 0) > 0);
  const [error, setError] = useState("");

  const itemSum = useMemo(() => items.reduce((s, i) => s + (Number(i.amount) || 0), 0), [items]);
  const total = Number(amount) || 0;
  const mismatch = items.length > 0 && itemSum > 0 && Math.abs(itemSum - total) > 0.011;

  function updateItem(idx: number, patch: Partial<ExpenseItem>) {
    setItems((old) => old.map((it, i) => (i === idx ? { ...it, ...patch } : it)));
  }

  function submit() {
    setError("");
    if (!total || total <= 0) { setError("金额得大于 0"); return; }
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) { setError("日期格式不对"); return; }
    onSubmit({
      date, merchant: merchant.trim(), category, currency, amount: total,
      items: items.filter((i) => i.name.trim()).map((i) => ({ ...i, name: i.name.trim(), qty: Number(i.qty) || 1, amount: Number(i.amount) || 0 })),
      note: note.trim(), image: initial?.image, order_ref: initial?.order_ref,
    });
  }

  return (
    <div>
      <div className="field">
        <label>实付金额</label>
        <div className="amount-input">
          <select className="select" value={currency} onChange={(e) => setCurrency(e.target.value)}>
            {CURRENCIES.map((c) => <option key={c} value={c}>{CURRENCY_SYMBOL[c]} {c}</option>)}
          </select>
          <input
            className="input mono" type="number" inputMode="decimal" step="0.01" min="0" placeholder="0.00"
            value={amount} onChange={(e) => setAmount(e.target.value)} autoFocus={!initial?.amount}
          />
        </div>
      </div>

      <div className="grid-2">
        <div className="field">
          <label>商家 / 用途</label>
          <input className="input" value={merchant} onChange={(e) => setMerchant(e.target.value)} placeholder="Tesco / 午饭 / Uber" maxLength={60} />
        </div>
        <div className="field">
          <label>日期</label>
          <input className="input mono" type="date" value={date} onChange={(e) => setDate(e.target.value)} max={todayStr()} />
        </div>
      </div>

      <div className="field">
        <label>分类</label>
        <div className="cat-pick">
          {CATEGORIES.map((c) => (
            <button
              key={c.key} type="button"
              className={`cat-chip ${category === c.key ? "active" : ""}`}
              onClick={() => setCategory(c.key)}
              title={c.hint}
            >
              {c.emoji} {c.label}
            </button>
          ))}
        </div>
      </div>

      <div className="field">
        <label style={{ display: "flex", justifyContent: "space-between" }}>
          <span>明细（可选，每项可以单独分类）</span>
          {!showItems && (
            <button type="button" className="btn ghost small" onClick={() => { setShowItems(true); if (!items.length) setItems([blankItem(category)]); }}>
              ＋ 添加明细
            </button>
          )}
        </label>
        {showItems && (
          <div className="items-editor">
            {items.map((it, idx) => (
              <div className="item-row" key={idx}>
                {/* 手机上三行：原文 / 中文 / [数量 金额 分类 ×]；电脑上原文和中文并排（布局由 .item-* 的 grid-column 决定） */}
                <input className="input item-name" placeholder="商品（小票原文）" value={it.name} onChange={(e) => updateItem(idx, { name: e.target.value })} />
                <input className="input item-zh" placeholder="中文名（可选，对照学）" value={it.name_zh || ""} onChange={(e) => updateItem(idx, { name_zh: e.target.value })} />
                <input className="input mono item-qty" type="number" inputMode="decimal" step="0.5" min="0" placeholder="数量" title="数量"
                  value={it.qty} onChange={(e) => updateItem(idx, { qty: Number(e.target.value) })} />
                <input className="input mono item-amt" type="number" inputMode="decimal" step="0.01" min="0" placeholder="金额" title="该行金额"
                  value={it.amount || ""} onChange={(e) => updateItem(idx, { amount: Number(e.target.value) })} />
                <button type="button" className="x" aria-label="删除" onClick={() => setItems((old) => old.filter((_, i) => i !== idx))}>×</button>
                {/* 大类 › 子类 合成一个下拉，值是 "groceries/dairy" */}
                <select
                  className="select item-cat" value={`${it.category}/${it.sub || ""}`}
                  onChange={(e) => { const [category, sub] = e.target.value.split("/"); updateItem(idx, { category: category as Category, sub }); }}
                >
                  {CATEGORIES.map((c) => (
                    <optgroup key={c.key} label={`${c.emoji} ${c.label}`}>
                      <option value={`${c.key}/`}>{c.emoji} {c.label}（不细分）</option>
                      {SUBCATEGORIES[c.key].map(([k, label]) => <option key={k} value={`${c.key}/${k}`}>{c.emoji} {c.label} › {label}</option>)}
                    </optgroup>
                  ))}
                </select>
              </div>
            ))}
            <div className={`items-foot ${mismatch ? "mismatch" : ""}`}>
              <span>
                明细合计 <b className="mono">{CURRENCY_SYMBOL[currency] || ""}{itemSum.toFixed(2)}</b>
                {mismatch && <>，和实付差 <b className="mono">{(total - itemSum).toFixed(2)}</b>（汇总时按比例摊）</>}
              </span>
              <button type="button" className="btn ghost small" onClick={() => setItems((old) => [...old, blankItem(category)])}>＋ 一行</button>
            </div>
          </div>
        )}
      </div>

      <div className="field">
        <label>备注</label>
        <input className="input" value={note} onChange={(e) => setNote(e.target.value)} placeholder="Clubcard 省了 £2 / 和朋友 AA" maxLength={300} />
      </div>

      {error && <div className="error-note">{error}</div>}
      <div className="row-actions">
        <button className="btn brick" onClick={submit} disabled={submitting}>
          {submitting ? "保存中…" : submitLabel}
        </button>
        <span className="hint" style={{ margin: 0 }}>{catInfo(category).emoji} {catInfo(category).hint}</span>
        {extra}
      </div>
    </div>
  );
}
