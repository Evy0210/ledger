"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

/** 小票图接口要鉴权头，普通 <img> 带不了 —— 先 fetch 成 blob。 */
export function AuthImage({ name, className, alt = "小票" }: { name: string; className?: string; alt?: string }) {
  const [src, setSrc] = useState("");
  useEffect(() => {
    let url = "";
    api.receiptBlobUrl(name).then((u) => { url = u; setSrc(u); }).catch(() => setSrc(""));
    return () => { if (url) URL.revokeObjectURL(url); };
  }, [name]);
  if (!src) return <div className="preview" style={{ height: 120 }} />;
  // eslint-disable-next-line @next/next/no-img-element
  return <img src={src} className={className} alt={alt} />;
}
