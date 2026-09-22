"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { getToken } from "@/lib/api";

export function HeaderNav() {
  const [authed, setAuthed] = useState(false);
  const pathname = usePathname();

  // 站内跳转不会重挂载导航 —— 靠 auth-changed 事件 + 路由变化同步登录状态。
  useEffect(() => { setAuthed(!!getToken()); }, [pathname]);
  useEffect(() => {
    const sync = () => setAuthed(!!getToken());
    window.addEventListener("auth-changed", sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener("auth-changed", sync);
      window.removeEventListener("storage", sync);
    };
  }, []);

  const [spinning, setSpinning] = useState(false);
  if (!authed) return null;
  const is = (p: string) => (pathname === p ? "nav-link active" : "nav-link");
  return (
    <nav className="site-nav">
      <Link href="/" className={is("/")}>本月</Link>
      <Link href="/months" className={is("/months")}>历史</Link>
      <Link href="/reconcile" className={is("/reconcile")}>对账</Link>
      <Link href="/split" className={is("/split")}>分账</Link>
      <Link href="/pantry" className={is("/pantry")}>食材</Link>
      <Link href="/login" className={is("/login")}>设置</Link>
      {/* 装到主屏幕后没有浏览器的刷新键，这里补一个 */}
      <button className={`nav-refresh ${spinning ? "spin" : ""}`} aria-label="刷新" title="刷新"
        onClick={() => { setSpinning(true); window.location.reload(); }}>↻</button>
    </nav>
  );
}
