"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, clearToken, getToken, GUEST_TOKEN, isGuest, maybeEnterDemo, setToken } from "@/lib/api";
import type { InboundEmail, Settings } from "@/lib/types";

export default function LoginPage() {
  const router = useRouter();
  const [authed, setAuthed] = useState<boolean | null>(null);
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [settings, setSettings] = useState<Settings | null>(null);
  const [budget, setBudget] = useState("");
  const [mailLog, setMailLog] = useState<InboundEmail[]>([]);
  const [polling, setPolling] = useState(false);
  const [enriching, setEnriching] = useState(false);

  const [guest, setGuest] = useState(false);

  useEffect(() => {
    if (maybeEnterDemo()) { router.replace("/"); return; }
    const has = !!getToken();
    setAuthed(has);
    setGuest(isGuest());
    if (has && !isGuest()) loadSettings();
  }, [router]);

  function loadSettings() {
    api.getSettings().then((s) => {
      setSettings(s);
      setBudget(s.budget_gbp ? String(s.budget_gbp) : "");
      api.mailLog().then((r) => setMailLog(r.log)).catch(() => {});
    }).catch((e) => setError((e as Error).message));
  }

  async function pollMail() {
    setPolling(true); setError(""); setNotice("");
    try {
      const r = await api.pollMail();
      setNotice(`拉取完成：入账 ${r.processed}，跳过 ${r.skipped}，失败 ${r.errors}`);
      loadSettings();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPolling(false);
    }
  }

  async function login() {
    setError("");
    try {
      const { token } = await api.login(password);
      setToken(token);
      router.push("/");
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function patch(p: Partial<Settings>) {
    setError(""); setNotice("");
    try {
      setSettings(await api.updateSettings(p));
      setNotice("已保存 ✓");
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function testTelegram() {
    setError(""); setNotice("");
    try { await api.testTelegram(); setNotice("发出去了，看看 Telegram ✓"); } catch (e) { setError((e as Error).message); }
  }

  async function unbind() {
    if (!confirm("解除 Telegram 绑定？之后需要重新把密码发给 bot。")) return;
    try { setSettings(await api.unbindTelegram()); } catch (e) { setError((e as Error).message); }
  }

  if (authed === null) return null;

  if (!authed) {
    return (
      <main className="panel" style={{ maxWidth: 420, margin: "40px auto" }}>
        <h2 style={{ marginTop: 0 }}>🔑 登录</h2>
        <p className="hint">私人账本，输入密码进入。</p>
        <div className="field">
          <input
            className="input" type="password" placeholder="密码" autoFocus
            value={password} onChange={(e) => setPassword(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && login()}
          />
        </div>
        {error && <div className="error-note">{error}</div>}
        <button className="btn primary block" onClick={login}>进入</button>
        <button className="btn ghost block" style={{ marginTop: 10 }}
          onClick={() => { setToken(GUEST_TOKEN); router.push("/"); }}>以访客身份浏览（演示数据）</button>
      </main>
    );
  }

  // 访客点「输入密码」来到这里：不给看设置，只给一个换成真实账本的入口
  if (guest) {
    return (
      <main className="panel" style={{ maxWidth: 420, margin: "40px auto" }}>
        <h2 style={{ marginTop: 0 }}>👀 正在看演示账本</h2>
        <p className="hint">现在看到的记录都是虚构的，只用来展示功能。输入密码进入真实账本。</p>
        <div className="field">
          <input
            className="input" type="password" placeholder="密码" autoFocus
            value={password} onChange={(e) => setPassword(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && login()}
          />
        </div>
        {error && <div className="error-note">{error}</div>}
        <button className="btn primary block" onClick={login}>进入真实账本</button>
        <button className="btn ghost block" style={{ marginTop: 10 }} onClick={() => router.push("/")}>继续看演示</button>
      </main>
    );
  }

  return (
    <main>
      <section className="panel">
        <h3 className="panel-title">📣 Telegram 提醒</h3>
        {!settings ? <p className="hint">加载中…</p> : !settings.telegram_enabled ? (
          <p className="hint">服务器还没配 <span className="code">TELEGRAM_BOT_TOKEN</span>。到 @BotFather 建一个 bot，把 token 填进 deploy/secret.yaml 再部署。</p>
        ) : !settings.telegram_bound ? (
          <p className="hint">
            还没绑定。打开你的 bot，把<b>网站密码</b>作为一条消息发给它，就绑定好了。之后小票也可以直接发给它记账。
          </p>
        ) : (
          <>
            <div className="ok-note">已绑定 ✓ 提醒和月报会发到你的 Telegram。</div>
            <div className="setting-row">
              <div>
                <div className="label">没记账时提醒我</div>
                <div className="desc">当天没有任何记录才会发</div>
              </div>
              <button className={`switch ${settings.reminder_enabled ? "on" : ""}`} aria-label="开关"
                onClick={() => patch({ reminder_enabled: !settings.reminder_enabled })} />
            </div>
            <div className="setting-row">
              <div className="label">提醒时间（伦敦时间）</div>
              <select className="select" style={{ width: 110 }} value={settings.reminder_hour}
                onChange={(e) => patch({ reminder_hour: Number(e.target.value) })}>
                {Array.from({ length: 24 }, (_, h) => <option key={h} value={h}>{String(h).padStart(2, "0")}:00</option>)}
              </select>
            </div>
            <div className="setting-row">
              <div className="label">频率</div>
              <select className="select" style={{ width: 130 }} value={settings.reminder_mode}
                onChange={(e) => patch({ reminder_mode: e.target.value as Settings["reminder_mode"] })}>
                <option value="daily">每天</option>
                <option value="weekdays">工作日</option>
                <option value="weekly">每周日</option>
              </select>
            </div>
            <div className="setting-row">
              <div>
                <div className="label">食材快过期提醒</div>
                <div className="desc">库存里 2 天内到期的，提醒做菜</div>
              </div>
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <select className="select" style={{ width: 100 }} value={settings.pantry_hour} onChange={(e) => patch({ pantry_hour: Number(e.target.value) })}>
                  {Array.from({ length: 24 }, (_, h) => <option key={h} value={h}>{String(h).padStart(2, "0")}:00</option>)}
                </select>
                <button className={`switch ${settings.pantry_reminder ? "on" : ""}`} aria-label="开关"
                  onClick={() => patch({ pantry_reminder: !settings.pantry_reminder })} />
              </div>
            </div>
            <div className="setting-row">
              <div>
                <div className="label">每月 1 号推上月汇总</div>
                <div className="desc">早上 9 点，总额、分类占比、和上月对比</div>
              </div>
              <button className={`switch ${settings.monthly_report ? "on" : ""}`} aria-label="开关"
                onClick={() => patch({ monthly_report: !settings.monthly_report })} />
            </div>
            <div className="row-actions">
              <button className="btn small" onClick={testTelegram}>发条测试消息</button>
              <button className="btn ghost small" onClick={unbind}>解除绑定</button>
            </div>
          </>
        )}
      </section>

      <section className="panel">
        <h3 className="panel-title">📧 邮件账单</h3>
        {!settings ? null : (
          <>
            <p className="hint">
              把 Deliveroo / Uber Eats / Just Eat / Amazon / Trainline 的账单邮件<b>转发到 <span className="code">{settings.mail_inbound_address || "（服务器上配置的 MAIL_INBOUND_ADDRESS）"}</span></b>，
              到了就自动识别入账，同一订单不会记两次，入账后 Telegram 会通知。
              在 Outlook 里设一条规则「发件人包含 deliveroo → 转发到这个地址」就全自动了。只收你自己几个邮箱转来的，其他发件人一律拒收。
            </p>
            {settings.mail_enabled && (
              <p className="hint">
                另外也在轮询 <span className="code">{settings.mail_address}</span> 的未读邮件。
                {settings.mail_last_poll > 0 && <> 上次拉取 {new Date(settings.mail_last_poll * 1000).toLocaleString("zh-CN")}。</>}
              </p>
            )}
            {settings.mail_last_error && <div className="error-note">上次拉取出错：{settings.mail_last_error}</div>}
            {settings.mail_enabled && (
              <div className="row-actions" style={{ marginTop: 6 }}>
                <button className="btn small" onClick={pollMail} disabled={polling}>{polling ? "拉取中…" : "立即拉取"}</button>
              </div>
            )}
            {mailLog.length > 0 && (
              <div style={{ marginTop: 14 }}>
                {mailLog.map((m) => (
                  <div className="setting-row" key={m.id} style={{ padding: "8px 0" }}>
                    <div style={{ minWidth: 0 }}>
                      <div className="label" style={{ fontSize: 14, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{m.subject || "（无主题）"}</div>
                      <div className="desc">{m.sender}{m.detail ? ` · ${m.detail}` : ""}</div>
                    </div>
                    {m.status === "ok" ? <a className="tag" href={`/expense/${m.expense_id}`}>✓ 已入账</a>
                      : m.status === "skipped" ? <span className="tag">跳过</span>
                      : m.status === "rejected" ? <span className="tag">拒收</span>
                      : <span className="tag" style={{ background: "var(--brick-soft)", color: "var(--brick-deep)" }}>失败</span>}
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </section>

      <section className="panel">
        <h3 className="panel-title">🈶 补全明细信息</h3>
        <p className="hint">给已有记录的明细补上中文名（WR CHICKEN BRSTS → 鸡胸肉）、子类和保质期，能坏的食材会进「食材」库存。新识别的记录会自动带这些信息，这个按钮只用于老记录。</p>
        <button className="btn" disabled={enriching} onClick={async () => {
          setEnriching(true); setError(""); setNotice("");
          try {
            let total = 0;
            for (let round = 0; round < 40; round++) {       // 每次 12 笔，循环到没有剩余
              const r = await api.enrichItems();
              total += r.expenses_updated;
              setNotice(`已补全 ${total} 笔${r.remaining ? `，还剩 ${r.remaining} 笔…` : " ✓"}`);
              if (!r.remaining) break;
            }
          }
          catch (e) { setError((e as Error).message); } finally { setEnriching(false); }
        }}>{enriching ? "补全中（每笔约 3 秒）…" : "补全所有老记录"}</button>
      </section>

      <section className="panel">
        <h3 className="panel-title">🎯 月预算</h3>
        <p className="hint">设了以后首页会显示进度条；留空就不管。</p>
        <div className="amount-input" style={{ gridTemplateColumns: "1fr auto" }}>
          <input className="input mono" type="number" inputMode="decimal" placeholder="£ 每月" value={budget} onChange={(e) => setBudget(e.target.value)} />
          <button className="btn" onClick={() => patch({ budget_gbp: Number(budget) || 0 })}>保存</button>
        </div>
      </section>

      <section className="panel">
        <h3 className="panel-title">📱 装到手机桌面</h3>
        <p className="hint">
          iPhone：Safari 打开 → 分享 → 「添加到主屏幕」。Android：Chrome 菜单 → 「安装应用」。
          之后点开就是全屏，拍小票更顺手。
        </p>
      </section>

      {error && <div className="error-note">{error}</div>}
      {notice && <div className="ok-note">{notice}</div>}
      <div className="row-actions">
        <button className="btn ghost" onClick={() => { clearToken(); setAuthed(false); setPassword(""); }}>退出登录</button>
      </div>
    </main>
  );
}
