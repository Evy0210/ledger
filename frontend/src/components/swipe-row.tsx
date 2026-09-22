"use client";

import { useRef, useState } from "react";

/** 手机上左右滑一行：右滑（露出绿色）→ onRight，左滑（露出红色）→ onLeft。
    横向位移超过阈值才算，纵向滚动不受影响；滑过之后的那次点击不触发跳转。 */
export function SwipeRow({ children, onLeft, onRight, leftLabel, rightLabel }: {
  children: React.ReactNode; onLeft: () => void; onRight: () => void; leftLabel: string; rightLabel: string;
}) {
  const [dx, setDx] = useState(0);
  const [dragging, setDragging] = useState(false);
  const start = useRef<{ x: number; y: number } | null>(null);
  const axis = useRef<"x" | "y" | null>(null);
  const swiped = useRef(false);
  const THRESHOLD = 72;

  function onTouchStart(e: React.TouchEvent) {
    const t = e.touches[0];
    start.current = { x: t.clientX, y: t.clientY };
    axis.current = null;
    swiped.current = false;
  }
  function onTouchMove(e: React.TouchEvent) {
    if (!start.current) return;
    const t = e.touches[0];
    const mx = t.clientX - start.current.x, my = t.clientY - start.current.y;
    if (!axis.current) {
      if (Math.abs(mx) < 6 && Math.abs(my) < 6) return;
      axis.current = Math.abs(mx) > Math.abs(my) ? "x" : "y";
    }
    if (axis.current !== "x") return;
    setDragging(true);
    // 越过阈值后阻尼一下，别拖飞
    const clamped = Math.sign(mx) * (Math.abs(mx) > THRESHOLD ? THRESHOLD + (Math.abs(mx) - THRESHOLD) * 0.3 : Math.abs(mx));
    setDx(clamped);
  }
  function onTouchEnd() {
    if (axis.current === "x") {
      if (dx >= THRESHOLD) { swiped.current = true; onRight(); }
      else if (dx <= -THRESHOLD) { swiped.current = true; onLeft(); }
    }
    setDx(0); setDragging(false); start.current = null;
  }

  return (
    <div className={`swipe ${dragging ? "dragging" : ""}`} onTouchStart={onTouchStart} onTouchMove={onTouchMove} onTouchEnd={onTouchEnd} onTouchCancel={onTouchEnd}>
      {dx > 0 && <div className="swipe-under right"><span className="l">{rightLabel}</span></div>}
      {dx < 0 && <div className="swipe-under left"><span className="r">{leftLabel}</span></div>}
      <div
        style={{ transform: `translateX(${dx}px)` }}
        onClickCapture={(e) => { if (swiped.current) { e.preventDefault(); e.stopPropagation(); swiped.current = false; } }}
      >
        {children}
      </div>
    </div>
  );
}
