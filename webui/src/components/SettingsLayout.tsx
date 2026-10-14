import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';

export type SettingsSection = { id: string; label: string };

export function SettingsLayout({ header, sections, children }: {
  header: ReactNode;
  sections: SettingsSection[];
  children: ReactNode;
}) {
  const pageRef = useRef<HTMLDivElement>(null);
  const headerRef = useRef<HTMLDivElement>(null);
  const navRef = useRef<HTMLElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const [activeSection, setActiveSection] = useState(sections[0]?.id || '');
  const location = useLocation();
  const navigate = useNavigate();

  useLayoutEffect(() => {
    let frame = 0;
    const update = () => {
      const headerHeight = headerRef.current?.getBoundingClientRect().height || 0;
      const navHeight = window.matchMedia('(max-width: 760px)').matches
        ? navRef.current?.getBoundingClientRect().height || 0 : 0;
      pageRef.current?.style.setProperty('--settings-header-height', `${headerHeight}px`);
      pageRef.current?.style.setProperty('--settings-nav-height', `${navHeight}px`);

      let current = sections[0]?.id || '';
      for (const section of sections) {
        const element = document.getElementById(section.id);
        if (element && element.getBoundingClientRect().top <= headerHeight + navHeight + 24) {
          current = section.id;
        }
      }
      // The final, shorter panel cannot always reach the top of the viewport.
      if (window.scrollY + window.innerHeight >= document.documentElement.scrollHeight - 2) {
        current = sections[sections.length - 1]?.id || current;
      }
      setActiveSection(current);
    };
    const schedule = () => {
      window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(update);
    };
    const observer = new ResizeObserver(schedule);
    for (const element of [headerRef.current, navRef.current, contentRef.current]) {
      if (element) observer.observe(element);
    }
    window.addEventListener('scroll', schedule, { passive: true });
    window.addEventListener('resize', schedule);
    update();
    return () => {
      observer.disconnect();
      window.cancelAnimationFrame(frame);
      window.removeEventListener('scroll', schedule);
      window.removeEventListener('resize', schedule);
    };
  }, [sections]);

  function focusSection(id: string) {
    const section = document.getElementById(id);
    if (!section) return;
    // The save banner can resize before the observer's next animation frame.
    const headerHeight = headerRef.current?.getBoundingClientRect().height || 0;
    const navHeight = window.matchMedia('(max-width: 760px)').matches
      ? navRef.current?.getBoundingClientRect().height || 0 : 0;
    pageRef.current?.style.setProperty('--settings-header-height', `${headerHeight}px`);
    pageRef.current?.style.setProperty('--settings-nav-height', `${navHeight}px`);
    section.scrollIntoView({ block: 'start' });
    section.focus({ preventScroll: true });
    setActiveSection(id);
  }

  useEffect(() => {
    const id = location.hash.slice(1);
    if (!sections.some((section) => section.id === id)) return;
    const frame = window.requestAnimationFrame(() => focusSection(id));
    return () => window.cancelAnimationFrame(frame);
  }, [location.hash, sections]);

  return (
    <div className="settingsPage" ref={pageRef}>
      <div className="settingsHeader" ref={headerRef}>{header}</div>
      <div className="settingsLayout">
        <nav className="settingsNav" aria-label="Settings sections" ref={navRef}>
          <div className="settingsSectionPicker">
            <label htmlFor="settings-section-picker">Settings section</label>
            <select id="settings-section-picker" value={activeSection} onChange={(event) => {
              const id = event.target.value;
              if (location.hash === `#${id}`) focusSection(id);
              else navigate({ search: location.search, hash: `#${id}` });
            }}>
              {sections.map((section) => <option key={section.id} value={section.id}>{section.label}</option>)}
            </select>
          </div>
          <ul className="settingsSectionLinks">
            {sections.map((section) => (
              <li key={section.id}>
                <Link to={{ search: location.search, hash: `#${section.id}` }} aria-current={activeSection === section.id ? 'location' : undefined}
                  onClick={() => { if (location.hash === `#${section.id}`) focusSection(section.id); }}>
                  {section.label}
                </Link>
              </li>
            ))}
          </ul>
        </nav>
        <div className="settingsGrid" ref={contentRef}>{children}</div>
      </div>
    </div>
  );
}
