"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { api, cny, gbp } from "@/lib/api";
import { AuthImage } from "@/components/auth-image";
import { ExpenseForm } from "@/components/expense-form";
import { RequireAuth } from "@/components/require-auth";
import { SplitSheet, SplitTags } from "@/components/split-sheet";
import type { Expense, ExpenseInput } from "@/lib/types";

export default function ExpensePage() {
  return <RequireAuth><ExpenseInner /></RequireAuth>;
}

const SOURCE_LABEL = { manual: "手动录入", receipt: "小票识别", telegram: "Telegram", email: "邮件账单", sms: "信用卡短信" };

function ExpenseInner() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [expense, setExpense] = useState<Expense | null>(null);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [sheet, setSheet] = useState(false);

  useEffect(() => {
    api.getExpense(id).then(setExpense).catch((e) => setError((e as Error).message));
  }, [id]);

  async function save(input: ExpenseInput) {
    setSaving(true); setError("");
    try {
      await api.updateExpense(id, input);
      router.push(`/?m=${input.date.slice(0, 7)}`);
    } catch (e) {
      setError((e as Error).message);
      setSaving(false);
    }
  }

  async function remove() {
    if (!expense || !confirm(`删掉这笔 ${expense.merchant || ""} ${gbp(expense.amount_gbp)}？`)) return;
    try {
      await api.deleteExpense(id);
      router.push(`/?m=${expense.date.slice(0, 7)}`);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  if (error && !expense) return <main className="panel"><div className="error-note">{error}</div></main>;
  if (!expense) return null;

  return (
    <main>
      <section className="panel">
        <h3 className="panel-title">
          <span>{gbp(expense.amount_gbp)} <small>≈ {cny(expense.amount_cny)} · 汇率 {expense.fx_gbp_cny.toFixed(3)}</small></span>
          <small>{SOURCE_LABEL[expense.source]}</small>
        </h3>
        {error && <div className="error-note">{error}</div>}
        <ExpenseForm
          initial={expense} submitLabel="保存修改" submitting={saving} onSubmit={save}
          extra={<button className="btn danger small" style={{ marginLeft: "auto" }} onClick={remove}>删除</button>}
        />
      </section>
      <section className="panel">
        <h3 className="panel-title">👥 分账 / 代付 <small>{expense.split_kind ? "" : "这笔全算你自己的"}</small></h3>
        <SplitTags e={expense} />
        <div className="row-actions" style={{ marginTop: expense.split_kind ? 10 : 0 }}>
          <button className="btn small" onClick={() => setSheet(true)}>{expense.split_kind ? "修改" : "设置分账 / 代付"}</button>
        </div>
      </section>
      {sheet && (
        <SplitSheet expense={expense} mode="menu" onClose={() => setSheet(false)}
          onChanged={(e) => { setExpense(e); setSheet(false); }} onDelete={() => void remove()} />
      )}
      {expense.image && (
        <section className="panel">
          <h3 className="panel-title">原图</h3>
          <AuthImage name={expense.image} className="receipt-img" />
        </section>
      )}
    </main>
  );
}
