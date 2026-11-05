import type { Metadata, Viewport } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "Vector · Prediction Intelligence",
    template: "%s · Vector",
  },
  description:
    "Verified-market probabilistic intelligence. Screenshot analysis, calibrated forecasts, and disciplined abstention for prediction markets.",
  applicationName: "Vector",
  keywords: [
    "prediction markets",
    "calibrated forecasting",
    "Polymarket",
    "Bayse",
    "decision intelligence",
  ],
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  themeColor: "#09090b",
  colorScheme: "dark",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html
      lang="en"
      className="h-full antialiased font-sans"
    >
      <body className="min-h-full bg-canvas text-zinc-100">{children}</body>
    </html>
  );
}
