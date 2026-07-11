export function SiteFooter() {
  return (
    <footer className="site-footer">
      <div>
        <p className="footer-kicker">A real product, built from a real search.</p>
        <h2>One person played the user and the PM. AI played the engineering team.</h2>
      </div>
      <div className="footer-links">
        <a href="/story">Read the product story</a>
        <a href="/architecture">Inspect the system</a>
        <a href="https://github.com/axe-pat/Outreach" target="_blank" rel="noreferrer">
          Outreach repo ↗
        </a>
        <a
          href="https://github.com/axe-pat/Resume-generator"
          target="_blank"
          rel="noreferrer"
        >
          ResumeGenerator repo ↗
        </a>
      </div>
      <p className="footer-note">
        Public product surface uses sanitized aggregates and fictionalized demo rows. Personal
        application materials, messages, and relationship data stay private.
      </p>
    </footer>
  );
}
