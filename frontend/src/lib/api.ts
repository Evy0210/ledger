"use client";

import type { DayTotal, Expense, ExpenseInput, InboundEmail, MonthRow, MonthSummary, PantryOverview, ParsedReceipt, ReconcileResult, Settings, SheetOverview, SheetRoster, SheetSyncResult, SmsTx, SplitInput, Status } from "./types";

// Same-origin: Next rewrites /api/* to the backend both in dev and in prod.
const TOKEN_KEY = "ledger_token";

export function getToken(): string {
  if (typeof window === "undefined") return "";
  try { return localStorage.getItem(TOKEN_KEY) || ""; } catch { return ""; }
}

export function setToken(token: string) {
  localStorage.setItem(TOKEN_KEY, token);
  window.dispatchEvent(new Event("auth-changed"));
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY);
  window.dispatchEvent(new Event("auth-changed"));
}

// 访客（主页进来的人）用固定 token "guest"：next.config 按这个头把请求转给演示后端，
// 看到的全是假数据。真实后端不认 guest，万一转错也只是 401。
export const GUEST_TOKEN = "guest";

export function isGuest(): boolean {
  return getToken() === GUEST_TOKEN;
}

/** 链接带 ?demo=1 且本机没登录过 → 以访客身份进入。已登录的（主人自己）不受影响。 */
export function maybeEnterDemo(): boolean {
  if (typeof window === "undefined" || getToken()) return false;
  const url = new URL(window.location.href);
  if (url.searchParams.get("demo") !== "1") return false;
  setToken(GUEST_TOKEN);
  url.searchParams.delete("demo");
  window.history.replaceState(null, "", url.pathname + url.search + url.hash);
  return true;
}

function authHeader(): Record<string, string> {
  const token = getToken();
  // HTTP 头只能装 ASCII —— 中文密码必须编码，后端会解码回来
  return token ? { Authorization: `Bearer ${encodeURIComponent(token)}` } : {};
}

async function fail(resp: Response, fallback: string): Promise<never> {
  let detail = `${fallback} (${resp.status})`;
  try {
    const body = await resp.json();
    if (body.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
  } catch { /* keep default */ }
  if (resp.status === 401 && typeof window !== "undefined" && !location.pathname.startsWith("/login")) {
    clearToken();
    location.href = "/login";
  }
  throw new Error(detail);
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const resp = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...authHeader(), ...(init.headers as Record<string, string>) },
  });
  if (!resp.ok) await fail(resp, "请求失败");
  return resp.json();
}

export const api = {
  // 登录不带旧 token：访客换真实密码时，请求得走真实后端
  login: async (password: string) => {
    const resp = await fetch("/api/login", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ password }),
    });
    if (!resp.ok) await fail(resp, "登录失败");
    return resp.json() as Promise<{ token: string }>;
  },
  status: () => request<Status>("/api/status"),
  listExpenses: (month: string) => request<{ expenses: Expense[] }>(`/api/expenses?month=${month}`),
  rangeExpenses: (start: string, end: string) => request<{ expenses: Expense[] }>(`/api/expenses?start=${start}&end=${end}`),
  days: (start: string, end: string) => request<{ days: DayTotal[] }>(`/api/days?start=${start}&end=${end}`),
  getExpense: (id: number | string) => request<Expense>(`/api/expenses/${id}`),
  createExpense: (input: ExpenseInput) =>
    request<Expense>("/api/expenses", { method: "POST", body: JSON.stringify(input) }),
  updateExpense: (id: number | string, input: ExpenseInput) =>
    request<Expense>(`/api/expenses/${id}`, { method: "PUT", body: JSON.stringify(input) }),
  deleteExpense: (id: number | string) => request<{ ok: boolean }>(`/api/expenses/${id}`, { method: "DELETE" }),
  setSplit: (id: number | string, body: SplitInput) =>
    request<Expense>(`/api/expenses/${id}/split`, { method: "PATCH", body: JSON.stringify(body) }),
  listSplit: (month = "") =>
    request<{ expenses: Expense[]; owed_gbp: number; unsettled: number }>(`/api/split${month ? `?month=${month}` : ""}`),
  sheetPeople: () => request<SheetRoster>("/api/sheet/people"),
  sheet: () => request<SheetOverview>("/api/sheet"),
  syncSheet: () => request<SheetSyncResult>("/api/sheet/sync", { method: "POST" }),
  pantry: () => request<PantryOverview>("/api/pantry"),
  setPantry: (id: number, body: { status?: "used" | "tossed" | "in"; expires?: string }) =>
    request<{ ok: boolean }>(`/api/pantry/${id}`, { method: "POST", body: JSON.stringify(body) }),
  enrichItems: (month = "") =>
    request<{ expenses_updated: number; remaining: number }>(`/api/items/enrich${month ? `?month=${month}` : ""}`, { method: "POST" }),
  summary: (month: string) => request<MonthSummary>(`/api/summary?month=${month}`),
  months: () => request<{ months: MonthRow[] }>("/api/months"),
  getSettings: () => request<Settings>("/api/settings"),
  updateSettings: (patch: Partial<Settings>) =>
    request<Settings>("/api/settings", { method: "PUT", body: JSON.stringify(patch) }),
  testTelegram: () => request<{ ok: boolean }>("/api/settings/test-telegram", { method: "POST" }),
  unbindTelegram: () => request<Settings>("/api/settings/unbind-telegram", { method: "POST" }),
  reconcile: (text: string) =>
    request<{ results: ReconcileResult[] }>("/api/reconcile", { method: "POST", body: JSON.stringify({ text }) }),
  reconcileCommit: (transactions: SmsTx[], adjustments: { expense_id: number; tx: SmsTx }[] = []) =>
    request<{ created: Expense[]; adjusted: Expense[] }>("/api/reconcile/commit", { method: "POST", body: JSON.stringify({ transactions, adjustments }) }),
  pollMail: () => request<{ processed: number; skipped: number; errors: number }>("/api/mail/poll", { method: "POST" }),
  mailLog: (limit = 15) => request<{ log: InboundEmail[] }>(`/api/mail/log?limit=${limit}`),
  parseReceipt: async (image: Blob): Promise<ParsedReceipt | { kind: "transactions"; results: ReconcileResult[] }> => {
    const form = new FormData();
    form.append("file", image, "receipt.jpg");
    const resp = await fetch("/api/receipts/parse", { method: "POST", headers: authHeader(), body: form });
    if (!resp.ok) await fail(resp, "识别失败");
    return resp.json();
  },
  reconcileImage: async (image: Blob): Promise<{ results: ReconcileResult[]; single_receipt?: boolean }> => {
    const form = new FormData();
    form.append("file", image, "statement.jpg");
    const resp = await fetch("/api/reconcile/image", { method: "POST", headers: authHeader(), body: form });
    if (!resp.ok) await fail(resp, "识别失败");
    return resp.json();
  },
  /** 小票图需要鉴权头，<img> 带不了 —— 拉成 blob 再给 object URL。 */
  receiptBlobUrl: async (name: string): Promise<string> => {
    const resp = await fetch(`/api/receipts/${name}`, { headers: authHeader() });
    if (!resp.ok) throw new Error("图片加载失败");
    return URL.createObjectURL(await resp.blob());
  },
};

/** 手机原图动辄好几 MB —— 上传前在浏览器里缩到最长边 1800px 的 jpeg。
    小票细字多，比菜谱图留大一点。 */
export function downscaleImage(file: File, maxSide = 1800): Promise<Blob> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    const url = URL.createObjectURL(file);
    img.onload = () => {
      URL.revokeObjectURL(url);
      const scale = Math.min(1, maxSide / Math.max(img.width, img.height));
      const canvas = document.createElement("canvas");
      canvas.width = Math.round(img.width * scale);
      canvas.height = Math.round(img.height * scale);
      canvas.getContext("2d")!.drawImage(img, 0, 0, canvas.width, canvas.height);
      canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error("图片处理失败"))), "image/jpeg", 0.88);
    };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("读不了这张图片")); };
    img.src = url;
  });
}

export const gbp = (n: number) => `£${n.toLocaleString("en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
export const cny = (n: number) => `¥${n.toLocaleString("zh-CN", { maximumFractionDigits: 0 })}`;

export function thisMonth(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

export function todayStr(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export function shiftMonth(month: string, delta: number): string {
  const [y, m] = month.split("-").map(Number);
  const d = new Date(y, m - 1 + delta, 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

export function monthLabel(month: string): string {
  const [y, m] = month.split("-");
  return `${y} 年 ${Number(m)} 月`;
}

/** "YYYY-MM-DD" 加减天数（按本地日历算，不经过 UTC，免得跨夏令时差一天）。 */
export function addDays(day: string, delta: number): string {
  const [y, m, d] = day.split("-").map(Number);
  return dateStr(new Date(y, m - 1, d + delta));
}

export function dateStr(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** 那一周的周一（英国习惯周一开头）。 */
export function weekStart(day: string): string {
  const [y, m, d] = day.split("-").map(Number);
  return addDays(day, -((new Date(y, m - 1, d).getDay() + 6) % 7));
}
