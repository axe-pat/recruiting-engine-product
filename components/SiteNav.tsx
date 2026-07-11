import Link from "next/link";

const links = [
  { href: "/#product", label: "Product" },
  { href: "/story", label: "Story" },
  { href: "/architecture", label: "Architecture" },
] as const;

export function SiteNav() {
  return (
    <header className="site-nav">
      <Link className="brand" href="/" aria-label="Recruiting Engine home">
        <span className="brand-mark" aria-hidden="true">
          RE
        </span>
        <span>
          <strong>Recruiting Engine</strong>
          <small>Built in public. Run in production.</small>
        </span>
      </Link>
      <nav aria-label="Primary navigation">
        {links.map((link) => (
          <Link href={link.href} key={link.href}>
            {link.label}
          </Link>
        ))}
      </nav>
      <a
        className="nav-code-link"
        href="https://github.com/axe-pat"
        target="_blank"
        rel="noreferrer"
      >
        View code <span aria-hidden="true">↗</span>
      </a>
    </header>
  );
}

