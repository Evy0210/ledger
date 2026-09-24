// 镜像 backend/categories.py；顺序 = 前端下拉框顺序。
export type Category =
  | "groceries" | "dining" | "transport" | "housing" | "study" | "shopping"
  | "entertainment" | "health" | "travel" | "subscription" | "other";

export interface CategoryInfo { key: Category; label: string; emoji: string; hint: string }

export const CATEGORIES: CategoryInfo[] = [
  { key: "groceries", label: "超市买菜", emoji: "🛒", hint: "超市、便利店、菜场、日用品" },
  { key: "dining", label: "外食餐饮", emoji: "🍜", hint: "餐厅、外卖、咖啡、奶茶、酒吧" },
  { key: "transport", label: "交通出行", emoji: "🚌", hint: "公交、地铁、火车、打车" },
  { key: "housing", label: "房租水电", emoji: "🏠", hint: "房租、水电燃气、网费" },
  { key: "study", label: "学习教育", emoji: "📚", hint: "学费、教材、文具、课程" },
  { key: "shopping", label: "购物", emoji: "🛍️", hint: "衣服、鞋包、数码、家居" },
  { key: "entertainment", label: "娱乐社交", emoji: "🎬", hint: "电影、演出、游戏、礼物" },
  { key: "health", label: "健康医疗", emoji: "💊", hint: "药、看病、健身" },
  { key: "travel", label: "旅行", emoji: "✈️", hint: "机票、酒店、旅途花销" },
  { key: "subscription", label: "订阅通讯", emoji: "📱", hint: "话费、Netflix、软件会员" },
  { key: "other", label: "其他", emoji: "📦", hint: "不好归类的" },
];
export const CATEGORY_MAP = Object.fromEntries(CATEGORIES.map((c) => [c.key, c])) as Record<Category, CategoryInfo>;
export const catInfo = (key: string): CategoryInfo => CATEGORY_MAP[key as Category] || CATEGORY_MAP.other;

export const CURRENCIES = ["GBP", "CNY", "EUR", "USD"] as const;
export const CURRENCY_SYMBOL: Record<string, string> = { GBP: "£", CNY: "¥", EUR: "€", USD: "$" };

export type ItemWho = "" | "me" | "shared" | "them";   // 分账时这项归谁：''/shared 公用，me 我的，them 别人的
export interface ExpenseItem { name: string; name_zh?: string; qty: number; amount: number; category: Category; sub?: string; who?: ItemWho; shelf_days?: number }
export const itemLabel = (i: ExpenseItem) => (i.name_zh && i.name_zh !== i.name ? `${i.name_zh} · ${i.name}` : i.name);

export interface PantryItem {
  id: number; expense_id: number; item_idx: number; name: string; name_raw: string; sub: string; qty: number;
  bought: string; expires: string; status: "in" | "used" | "tossed"; days_left: number;
}
export interface PantryOverview { soon: PantryItem[]; fresh: PantryItem[]; dry: PantryItem[]; count: number; recipes_url: string }

// 镜像 backend/categories.py 的 SUBCATEGORIES
export const SUBCATEGORIES: Record<Category, [string, string][]> = {
  groceries: [["produce", "蔬菜水果"], ["meat", "肉禽蛋"], ["seafood", "海鲜"], ["dairy", "奶制品"], ["staples", "主食粮油"],
    ["snacks", "零食饮料"], ["condiments", "调料"], ["household", "日用品"], ["toiletries", "洗护"], ["fees", "购物袋/杂费"]],
  dining: [["meal", "正餐"], ["delivery", "外卖"], ["coffee", "咖啡奶茶"], ["fastfood", "快餐小吃"], ["drinks", "酒吧饮酒"], ["fees", "配送/服务费"]],
  transport: [["public", "公交地铁"], ["rail", "火车"], ["taxi", "打车"], ["bike", "共享单车"], ["other", "其他"]],
  housing: [["rent", "房租"], ["utilities", "水电燃气"], ["internet", "网费"], ["council", "Council Tax"], ["other", "其他"]],
  study: [["tuition", "学费"], ["books", "书籍教材"], ["stationery", "文具打印"], ["courses", "课程考试"], ["other", "其他"]],
  shopping: [["clothes", "衣服鞋包"], ["electronics", "数码"], ["home", "家居"], ["beauty", "美妆护肤"], ["gifts", "礼物"], ["other", "其他"]],
  entertainment: [["shows", "电影演出"], ["games", "游戏"], ["social", "聚会社交"], ["sports", "运动"], ["other", "其他"]],
  health: [["medicine", "药品"], ["medical", "看病诊疗"], ["fitness", "健身"], ["insurance", "保险"], ["other", "其他"]],
  travel: [["flights", "机票"], ["hotel", "住宿"], ["local", "当地交通"], ["attractions", "景点门票"], ["food", "旅途餐饮"], ["other", "其他"]],
  subscription: [["phone", "话费流量"], ["streaming", "流媒体"], ["software", "软件"], ["membership", "会员"], ["other", "其他"]],
  other: [],
};
export const subLabel = (cat: string, sub?: string): string =>
  (SUBCATEGORIES[cat as Category] || []).find(([k]) => k === sub)?.[1] || "";

/** 表单里编辑的字段（发给后端）。 */
export interface ExpenseInput {
  date: string;
  merchant: string;
  category: Category;
  currency: string;
  amount: number;
  items: ExpenseItem[];
  note: string;
  image?: string;
  order_ref?: string;
}

export interface Expense extends ExpenseInput {
  id: number;
  amount_gbp: number;
  amount_cny: number;
  fx_gbp_cny: number;
  source: "manual" | "receipt" | "telegram" | "email" | "sms";
  image: string;
  order_ref: string;
  split_kind: "" | "split" | "paid_for";
  split_n: number;
  split_with: string;
  settled: boolean;
  share_gbp: number;      // 我自己那份（代付 = 0，分账 = 1/n）
  share_cny: number;
  owed_gbp: number;       // 别人还没还我的
  created_at: number;
  updated_at: number;
}

/** /api/receipts/parse 返回的草稿。duplicate = 库里已经有同一笔（比如邮件先到了）。 */
export interface ParsedReceipt extends ExpenseInput {
  kind: "receipt";
  image: string;
  duplicate: { id: number; merchant: string; date: string; amount_gbp: number; has_items: boolean } | null;
}

export interface CategoryTotal {
  key: Category; label: string; emoji: string; gbp: number; cny: number; pct: number;
  subs: { key: string; label: string; gbp: number }[];
}

export interface MonthSummary {
  month: string;
  count: number;
  total_gbp: number;      // 我自己的支出（分账按份额、代付不算）
  total_cny: number;
  gross_gbp: number;      // 实际刷卡总额
  owed_gbp: number;       // 待收
  days_logged: number;
  days_in_month: number;
  prev_month_total_gbp: number;
  budget_gbp: number | null;
  by_category: CategoryTotal[];
  by_day: { date: string; gbp: number }[];
  top_merchants: { name: string; gbp: number }[];
}

export interface MonthRow { month: string; count: number; total_gbp: number; total_cny: number }

export interface Status {
  today: string;
  today_logged: boolean;
  last_entry_date: string;
  days_since_last: number;
  telegram_bound: boolean;
  telegram_enabled: boolean;
  vision_ready: boolean;
  vision_model: string;
  mail_enabled: boolean;
  mail_address: string;
  mail_inbound_address: string;
  mail_last_poll: number;
  mail_last_error: string;
}

export interface InboundEmail {
  id: number;
  subject: string;
  sender: string;
  order_ref: string;
  status: "ok" | "skipped" | "rejected" | "error";
  detail: string;
  expense_id: number;
  created_at: number;
}

export interface Settings {
  reminder_enabled: boolean;
  reminder_hour: number;
  reminder_mode: "daily" | "weekdays" | "weekly";
  monthly_report: boolean;
  budget_gbp: number | null;
  pantry_reminder: boolean;
  pantry_hour: number;
  telegram_enabled: boolean;
  telegram_bound: boolean;
  mail_enabled: boolean;
  mail_address: string;
  mail_inbound_address: string;
  mail_last_poll: number;
  mail_last_error: string;
}

export interface SmsTx { date: string; merchant: string; amount: number; currency: string; kind: "purchase" | "refund" | "other" }
export interface ReconcileResult {
  tx: SmsTx;
  status: "matched" | "maybe" | "adjust" | "unmatched" | "ignored";
  diff_gbp?: number;      // adjust：短信金额 − 账本金额（配送费 / 差价）
  expense: { id: number; date: string; merchant: string; amount_gbp: number; category: string } | null;
}

/** 拍小票时发现是流水截图 → 结果通过 sessionStorage 交给对账页 */
export const RECONCILE_HANDOFF_KEY = "ledger_reconcile_handoff";

export interface SplitInput { split_kind: "" | "split" | "paid_for"; split_n: number; split_with: string; settled: boolean; item_who?: ItemWho[] }

/** 分账下我的份额（和后端 compute_share 一致），用于面板里实时预览 */
export function previewShare(amount: number, items: ExpenseItem[], n: number, who: ItemWho[]): number {
  const priced = items.map((it, i) => ({ amt: it.amount || 0, who: who[i] || "" })).filter((x) => x.amt > 0);
  const sum = priced.reduce((s, x) => s + x.amt, 0);
  if (!priced.length || sum <= 0 || !priced.some((x) => x.who === "me" || x.who === "them")) return amount / n;
  const mine = priced.filter((x) => x.who === "me").reduce((s, x) => s + x.amt, 0);
  const shared = priced.filter((x) => x.who !== "me" && x.who !== "them").reduce((s, x) => s + x.amt, 0);
  return amount * (mine + shared / n) / sum;
}

// ---- 合租分账表（Google Sheet）--------------------------------------------
export type SheetPerson = { name: string; paid_gbp: number; owes_gbp: number; net_gbp: number };
export type SheetTransfer = { from: string; to: string; amount_gbp: number };
export type SheetRow = {
  row: number; date: string; merchant: string; amount_gbp: number; payer: string;
  participants: string[]; each_gbp: number; note: string; settled: boolean;
  ref: string; from_web: boolean;          // from_web=false 就是室友自己在表上记的
};
export type SheetOverview = {
  enabled: boolean; me?: string; url?: string; people: SheetPerson[]; transfers?: SheetTransfer[];
  rows: SheetRow[]; open_count?: number; open_gbp?: number;
};
export type SheetSyncResult = SheetOverview & {
  pushed?: number; appended?: number; pulled?: number; removed?: number;
};
export type SheetRoster = { enabled: boolean; me: string; people: string[]; roommates: string[] };

export interface DayTotal { date: string; count: number; gbp: number; cny: number }
