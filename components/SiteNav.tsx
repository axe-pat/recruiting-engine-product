/* eslint-disable @next/next/no-html-link-for-pages -- static export needs full-page navigation */

const links = [
  { href: "/#product", label: "Product" },
  { href: "/app", label: "Open app" },
  { href: "/story", label: "Story" },
  { href: "/architecture", label: "Architecture" },
] as const;

export function SiteNav() {
  return (
    <header className="site-nav">
      <a className="brand" href="/" aria-label="Recruiting Engine home">
        <span className="brand-mark" aria-hidden="true">
          RE
        </span>
        <span>
          <strong>Recruiting Engine</strong>
          <small>Built in public. Run in production.</small>
        </span>
      </a>
      <nav aria-label="Primary navigation">
        {links.map((link) => (
          <a href={link.href} key={link.href}>
            {link.label}
          </a>
        ))}
      </nav>
      <a className="nav-code-link" href="/app/onboarding">
        Create workspace <span aria-hidden="true">→</span>
      </a>
    </header>
  );
}
