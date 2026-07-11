import type { Metadata } from "next";

import { SiteFooter } from "@/components/SiteFooter";
import { SiteNav } from "@/components/SiteNav";

export const metadata: Metadata = {
  title: "Install",
  description: "Install the private local companion and Chrome side panel for Recruiting Engine.",
};

const companionCommands = `git clone https://github.com/axe-pat/recruiting-engine-product.git
cd recruiting-engine-product
export PYTHONPATH="$PWD/companion"
python3 -m recruiting_companion serve`;

export default function InstallPage() {
  return (
    <main className="inner-page">
      <SiteNav />
      <header className="install-hero">
        <span className="eyebrow">Install · Local-first release</span>
        <h1>Use the product without giving the product your private life.</h1>
        <p>
          The interface is hosted. The system of record runs on your device. Pair them once, then
          add the Chrome companion if you want explicit browser intake and review.
        </p>
        <div>
          <a className="button-primary" href="/app">Explore the preview →</a>
          <a className="button-secondary" href="https://github.com/axe-pat/recruiting-engine-product" target="_blank" rel="noreferrer">Open source ↗</a>
        </div>
      </header>

      <section className="install-steps">
        <article>
          <span>01</span>
          <div>
            <small>Private system of record</small>
            <h2>Start the local companion.</h2>
            <p>Python 3.11+ is the only runtime requirement. No hosted account or cloud database is created.</p>
            <pre><code>{companionCommands}</code></pre>
            <p className="install-note">The process binds only to <code>127.0.0.1:8765</code> and prints the path to a one-time pairing token.</p>
          </div>
        </article>
        <article>
          <span>02</span>
          <div>
            <small>Private onboarding</small>
            <h2>Create the workspace.</h2>
            <p>Open onboarding, add one baseline resume and a compact target frame, then paste the one-time local pairing token.</p>
            <a className="install-action" href="/app/onboarding">Start onboarding →</a>
          </div>
        </article>
        <article>
          <span>03</span>
          <div>
            <small>Optional Chrome side panel</small>
            <h2>Add browser intake and review.</h2>
            <p>Until the store listing completes external review, load <code>extension/</code> unpacked from <code>chrome://extensions</code>. Grant only the chosen loopback origin.</p>
            <ol>
              <li>Enable Developer mode.</li>
              <li>Choose Load unpacked and select the extension directory.</li>
              <li>Open the side panel and use the same local bearer token.</li>
            </ol>
            <a className="install-action" href="https://github.com/axe-pat/recruiting-engine-product/tree/main/extension" target="_blank" rel="noreferrer">Extension guide ↗</a>
          </div>
        </article>
      </section>

      <section className="install-boundary section-shell">
        <div><span className="section-index">What this release does</span><h2>A real portable core with an honest boundary.</h2></div>
        <div className="install-boundary-grid">
          <article><strong>Portable users</strong><p>Uploads, reviewed job imports, durable state, deterministic queues, run reports, browser intake, and explicit outreach approval.</p></article>
          <article><strong>Existing operators</strong><p>Read-only binding to exact production evidence while the installed signed scheduler retains live-run authority.</p></article>
          <article><strong>Not claimed</strong><p>No hosted multi-tenant database, silent LinkedIn automation, application auto-submit, or message auto-send.</p></article>
        </div>
      </section>
      <SiteFooter />
    </main>
  );
}
