// Cloudflare Email Worker: 收账单的地址（MAIL_INBOUND_ADDRESS）收到的每封邮件原样推给后端。
// 部署：Cloudflare 控制台 → Workers & Pages → Create → 粘贴这段 → 变量里加
// LEDGER_URL（比如 https://ledger.example.com）和 LEDGER_SECRET（= deploy/secret.yaml 的
// MAIL_INBOUND_SECRET）→ Email Routing 里把收账单地址的动作设为「Send to a Worker」选它。
export default {
  async email(message, env) {
    const raw = await new Response(message.raw).arrayBuffer();
    const resp = await fetch(`${env.LEDGER_URL}/api/mail/inbound`, {
      method: "POST",
      headers: {
        "Content-Type": "message/rfc822",
        "Authorization": `Bearer ${env.LEDGER_SECRET}`,
        "X-Envelope-From": message.from,   // 信封发件人（自动转发时 From 头可能是原商家）
      },
      body: raw,
    });
    if (!resp.ok) {
      // 抛错会让发件方收到退信，方便发现后端挂了
      throw new Error(`ledger backend ${resp.status}: ${await resp.text()}`);
    }
  },
};
