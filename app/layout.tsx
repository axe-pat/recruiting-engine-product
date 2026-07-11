import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { headers } from "next/headers";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export async function generateMetadata(): Promise<Metadata> {
  const requestHeaders = await headers();
  const forwardedHost = requestHeaders.get("x-forwarded-host")?.split(",")[0]?.trim();
  const host = forwardedHost || requestHeaders.get("host") || "localhost:3000";
  const forwardedProtocol = requestHeaders.get("x-forwarded-proto")?.split(",")[0]?.trim();
  const protocol = forwardedProtocol === "http" || forwardedProtocol === "https"
    ? forwardedProtocol
    : host.startsWith("localhost")
      ? "http"
      : "https";
  const origin = new URL(`${protocol}://${host}`).origin;
  const title = "Recruiting Engine — The job search, rebuilt as a product";
  const description =
    "A production AI recruiting system built through months of real use: role discovery, decision intelligence, tailored applications, relationship outreach, and outcome learning.";
  const socialImage = `${origin}/og.png`;

  return {
    metadataBase: new URL(origin),
    title: {
      default: title,
      template: "%s · Recruiting Engine",
    },
    description,
    applicationName: "Recruiting Engine",
    authors: [{ name: "Akshat" }],
    keywords: [
      "AI product",
      "product management",
      "recruiting automation",
      "job search",
      "portfolio case study",
    ],
    openGraph: {
      title,
      description:
        "One PM, two production lanes, and 151 commits turning a personal pain point into a real AI product.",
      type: "website",
      url: origin,
      images: [{ url: socialImage, width: 1200, height: 630, alt: title }],
    },
    twitter: {
      card: "summary_large_image",
      title,
      description:
        "A production AI system for discovering roles, making decisions, tailoring applications, and running relationship outreach.",
      images: [socialImage],
    },
  };
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="scroll-smooth">
      <head>
        <meta
          httpEquiv="Content-Security-Policy"
          content="default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self' http://127.0.0.1:* http://localhost:*; object-src 'none'; base-uri 'self'; form-action 'self'; worker-src 'self'"
        />
        <meta name="referrer" content="no-referrer" />
      </head>
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased`}
      >
        {children}
      </body>
    </html>
  );
}
