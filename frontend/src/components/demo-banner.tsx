"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { isGuest } from "@/lib/api";

/** 访客模式的提示条：告诉来看的人这是假数据，并给主人一个换成真账本的入口。 */
export function DemoBanner() {
  const [guest, setGuest] = useState(false);
  const pathname = usePathname();

  useEffect(() => { setGuest(isGuest()); }, [pathname]);
  useEffect(() => {
    const sync = () => setGuest(isGuest());
    window.addEventListener("auth-changed", sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener("auth-changed", sync);
      window.removeEventListener("storage", sync);
    };
  }, []);

  if (!guest || pathname === "/login") return null;
  return (
    <div className="demo-banner">
      <span>演示模式 · 以下为虚构数据</span>
      <Link href="/login" className="demo-banner-btn">输入密码</Link>
    </div>
  );
}
