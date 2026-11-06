import type { Metadata, Viewport } from "next";
import { DM_Sans, Instrument_Serif } from "next/font/google";

import "./globals.css";

const dmSans = DM_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-dm-sans",
  display: "swap",
});

const instrumentSerif = Instrument_Serif({
  subsets: ["latin"],
  weight: "400",
  style: ["normal", "italic"],
  variable: "--font-instrument-serif",
  display: "swap",
});

export const metadata: Metadata = {
  title: {
    default: "Klocka · Prediction Intelligence",
    template: "%s · Klocka",
  },
  description:
    "Verified-market probabilistic intelligence. Screenshot analysis, calibrated forecasts, and disciplined abstention for prediction markets.",
  applicationName: "Klocka",
  keywords: [
    "prediction markets",
    "calibrated forecasting",
    "decision intelligence",
  ],
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  themeColor: "#f7f7f5",
  colorScheme: "light",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html
      lang="en"
      className={`${dmSans.variable} ${instrumentSerif.variable} h-full antialiased font-sans`}
    >
      <body className="min-h-full bg-canvas text-zinc-900">{children}</body>
    </html>
  );
}
