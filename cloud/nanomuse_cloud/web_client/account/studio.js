/* The contents behave like ordinary links: section URLs survive reload and Back. */
(() => {
  const panels = [...document.querySelectorAll('#private-content > section')];
  const links = [...document.querySelectorAll('.account-nav a[href^="#"]')];
  const title = document.querySelector('.page-heading h1');
  const originalTitle = title.textContent;
  function show() {
    const id = panels.some(p => '#' + p.id === location.hash) ? location.hash.slice(1) : 'overview';
    for (const panel of panels) panel.hidden = panel.id !== id;
    for (const link of links) {
      const active = link.hash === '#' + id;
      if (active) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current');
    }
    const link = links.find(a => a.hash === '#' + id);
    title.textContent = id === 'overview' ? originalTitle : link?.textContent || originalTitle;
  }
  addEventListener('hashchange', () => {show();title.tabIndex=-1;title.focus({preventScroll:true});scrollTo(0,0);});
  show();
})();
