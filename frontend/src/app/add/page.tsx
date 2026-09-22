"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { api, downscaleImage, gbp } from "@/lib/api";
import { ExpenseForm } from "@/components/expense-form";
import { RequireAuth } from "@/components/require-auth";
import { RECONCILE_HANDOFF_KEY, type ExpenseInput, type ParsedReceipt } from "@/lib/types";

export default function AddPage() {
  return <RequireAuth><AddInner /></RequireAuth>;
}

function AddInner() {
  const router = useRouter();
  const [mode, setMode] = useState<"receipt" | "manual">("receipt");
  const [preview, setPreview] = useState("");
  const [scanning, setScanning] = useState(false);
  const [parsed, setParsed] = useState<ParsedReceipt | null>(null);
  const [mergeInto, setMergeInto] = useState<number | null>(null);   // 补到已有那笔，而不是新记
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [visionReady, setVisionReady] = useState(true);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api.status().then((s) => { setVisionReady(s.vision_ready); if (!s.vision_ready) setMode("manual"); }).catch(() => {});
    return () => { if (preview) URL.revokeObjectURL(preview); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleFile(file: File | undefined) {
    if (!file) return;
    setError(""); setParsed(null);
    setPreview(URL.createObjectURL(file));
    setScanning(true);
    try {
      const blob = await downscaleImage(file);
      const result = await api.parseReceipt(blob);
      if (result.kind === "transactions") {
        // 是银行流水 / 短信列表的截图 → 交给对账页
        try { sessionStorage.setItem(RECONCILE_HANDOFF_KEY, JSON.stringify(result.results)); } catch { /* ignore */ }
        router.push("/reconcile");
        return;
      }
      setParsed(result);
      setMergeInto(result.duplicate ? result.duplicate.id : null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setScanning(false);
    }
  }

  async function save(input: ExpenseInput) {
    setSaving(true); setError("");
    try {
      if (mergeInto) await api.updateExpense(mergeInto, input);
      else await api.createExpense(input);
      router.push("/");
    } catch (e) {
      setError((e as Error).message);
      setSaving(false);
    }
  }

  // 粘贴截图（电脑上 Cmd+V）
  useEffect(() => {
    const onPaste = (ev: ClipboardEvent) => {
      const f = Array.from(ev.clipboardData?.files || []).find((x) => x.type.startsWith("image/"));
      if (f && mode === "receipt") void handleFile(f);
    };
    window.addEventListener("paste", onPaste);
    return () => window.removeEventListener("paste", onPaste);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode]);

  return (
    <main className="panel">
      <div className="mode-tabs">
        <button className={`mode-tab ${mode === "receipt" ? "active" : ""}`} onClick={() => setMode("receipt")} disabled={!visionReady}>📷 小票 / 截图</button>
        <button className={`mode-tab ${mode === "manual" ? "active" : ""}`} onClick={() => setMode("manual")}>✏️ 手动</button>
      </div>

      {mode === "receipt" && (
        <>
          {!visionReady && <div className="error-note">服务器没配视觉模型，暂时只能手动记。</div>}
          {!preview ? (
            <div
              className="dropzone" onClick={() => fileRef.current?.click()}
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => { e.preventDefault(); void handleFile(e.dataTransfer.files[0]); }}
            >
              <div className="big">🧾</div>
              <div><b>拍一张小票</b>，或者选一张支付截图</div>
              <div className="hint">超市小票、Deliveroo 订单、Monzo / 支付宝截图都行。电脑上也可以直接 Cmd+V 粘贴。</div>
            </div>
          ) : (
            <div className="preview">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={preview} alt="小票预览" />
              {scanning && <div className="scan"><div className="scan-line" /><span>识别中 🔍</span></div>}
            </div>
          )}
          <input
            ref={fileRef} type="file" accept="image/*" capture="environment" hidden
            onChange={(e) => { void handleFile(e.target.files?.[0]); e.target.value = ""; }}
          />
          {preview && !scanning && (
            <div className="row-actions" style={{ marginTop: 10 }}>
              <button className="btn ghost small" onClick={() => { setPreview(""); setParsed(null); setError(""); }}>换一张</button>
              {parsed && <span className="hint" style={{ margin: 0 }}>识别好了，检查一下再保存 👇</span>}
            </div>
          )}
          {error && <div className="error-note">{error}</div>}
          {parsed?.duplicate && (
            <div className="banner" style={{ marginTop: 14 }}>
              <span>🔗</span>
              <span>
                这笔好像已经记过了：{parsed.duplicate.date} {parsed.duplicate.merchant} {gbp(parsed.duplicate.amount_gbp)}
                {parsed.duplicate.has_items ? "" : "（还没有明细价格）"}。
                {mergeInto ? "保存会把明细补到那笔上。" : "保存会另记一笔。"}
              </span>
              <button className="btn ghost small" onClick={() => setMergeInto((v) => (v ? null : parsed.duplicate!.id))}>
                {mergeInto ? "改为另记一笔" : "改为补到那笔"}
              </button>
            </div>
          )}
          {parsed && (
            <div style={{ marginTop: 18 }}>
              <ExpenseForm key={parsed.image} initial={parsed} submitLabel={mergeInto ? "补充到已有记录" : "保存这笔"} submitting={saving} onSubmit={save} />
            </div>
          )}
        </>
      )}

      {mode === "manual" && (
        <>
          {error && <div className="error-note">{error}</div>}
          <ExpenseForm submitLabel="保存这笔" submitting={saving} onSubmit={save} />
        </>
      )}
    </main>
  );
}
