import type { Metadata, Viewport } from "next";
import Link from "next/link";
import { DemoBanner } from "@/components/demo-banner";
import { HeaderNav } from "@/components/header-nav";
import "./globals.css";

export const metadata: Metadata = {
  title: "一角账本",
  description: "在英国的每一笔花销",
  manifest: "/manifest.json",
  appleWebApp: { capable: true, title: "账本", statusBarStyle: "default" },
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: "#f4f1ea",
};

function Receipt() {
  return (
    <svg viewBox="0 0 64 64" width="34" height="34" aria-hidden="true">
      <path d="M18 10h28v44l-4-3-4 3-4-3-4 3-4-3-4 3-4-3z" fill="#fffdf9" stroke="#23272a" strokeWidth="2.5" strokeLinejoin="round" />
      <path d="M24 22h16M24 30h16M24 38h9" stroke="#23272a" strokeWidth="2.5" strokeLinecap="round" />
      <circle cx="40" cy="38" r="3" fill="#c8553d" />
    </svg>
  );
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>
        <div className="container">
          <DemoBanner />
          <header className="site-header">
            <Link href="/" className="site-brand">
              <Receipt />
              <div>
                <div className="site-title">一角账本</div>
                <div className="site-sub">LEDGER · UK</div>
              </div>
            </Link>
            <HeaderNav />
          </header>
          {children}
          <footer className="site-footer">every penny, noted.</footer>
        </div>
      </body>
    </html>
  );
}
