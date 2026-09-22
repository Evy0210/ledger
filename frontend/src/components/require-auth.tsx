"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { getToken, maybeEnterDemo } from "@/lib/api";

/** 没登录就去 /login；登录了再渲染子页面（避免一闪而过的空数据）。 */
export function RequireAuth({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [ok, setOk] = useState(false);
  useEffect(() => {
    maybeEnterDemo();
    if (getToken()) setOk(true);
    else router.replace("/login");
  }, [router]);
  return ok ? <>{children}</> : null;
}
