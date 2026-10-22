(() => {
  const searchInput = document.getElementById('terms-search');
  const clearButton = document.getElementById('terms-search-clear');
  const searchCount = document.getElementById('terms-search-count');
  const noResults = document.getElementById('terms-no-results');
  const sections = [...document.querySelectorAll('[data-terms-section]')];
  const links = [...document.querySelectorAll('[data-terms-link]')];
  const searchableNodes = sections.flatMap(section =>
    [...section.querySelectorAll('h2, h3, p')]
  );
  let activeObserver;
  let searchTimer;

  if (!searchInput || !sections.length) return;

  searchableNodes.forEach(node => {
    node.dataset.originalText = node.textContent;
  });

  const escapeHtml = text => text.replace(/[&<>"']/g, character => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  })[character]);

  const escapeRegex = text => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

  function updateHighlights(tokens) {
    if (!tokens.length) {
      searchableNodes.forEach(node => {
        node.textContent = node.dataset.originalText || '';
      });
      return;
    }

    const terms = [...tokens].sort((left, right) => right.length - left.length);
    const matcher = new RegExp(`(${terms.map(escapeRegex).join('|')})`, 'gi');
    const tokenSet = new Set(tokens);

    searchableNodes.forEach(node => {
      const original = node.dataset.originalText || '';
      node.innerHTML = original.split(matcher)
        .map(part => tokenSet.has(part.toLocaleLowerCase())
          ? `<mark>${escapeHtml(part)}</mark>`
          : escapeHtml(part))
        .join('');
    });
  }

  function setActiveSection(sectionId) {
    links.forEach(link => {
      const active = link.dataset.termsLink === sectionId;
      link.classList.toggle('is-active', active);
      if (active) link.setAttribute('aria-current', 'location');
      else link.removeAttribute('aria-current');
    });
  }

  function observeVisibleSections() {
    activeObserver?.disconnect();
    if (!('IntersectionObserver' in window)) return;

    activeObserver = new IntersectionObserver(() => {
      const visibleSections = sections.filter(section => !section.hidden);
      const documentTop = document.querySelector('.terms-document')?.getBoundingClientRect().top || 0;
      const anchorOffset = parseFloat(getComputedStyle(visibleSections[0]).scrollMarginTop) || 0;
      const readingLine = documentTop + anchorOffset + 8;
      const passedReadingLine = visibleSections.filter(section =>
        section.getBoundingClientRect().top <= readingLine
      );
      const current = passedReadingLine.at(-1) || visibleSections[0];
      if (current) setActiveSection(current.id);
    }, {
      rootMargin: '-90px 0px -72% 0px',
      threshold: 0,
    });

    sections.filter(section => !section.hidden).forEach(section => activeObserver.observe(section));
  }

  function filterSections() {
    const query = searchInput.value.trim().toLocaleLowerCase();
    const tokens = query ? query.split(/\s+/).filter(Boolean) : [];
    let visibleCount = 0;

    sections.forEach(section => {
      const text = (section.dataset.searchText || section.textContent).toLocaleLowerCase();
      const matches = tokens.every(token => text.includes(token));
      section.hidden = !matches;
      if (matches) visibleCount += 1;

      const link = links.find(item => item.dataset.termsLink === section.id);
      if (link) link.hidden = !matches;
    });

    updateHighlights(tokens);
    searchCount.textContent = query
      ? `${visibleCount} of ${sections.length} sections`
      : `${sections.length} sections`;
    clearButton.hidden = !query;
    noResults.hidden = visibleCount > 0;

    const activeLink = links.find(link => link.classList.contains('is-active') && !link.hidden);
    if (!activeLink) {
      const firstVisible = sections.find(section => !section.hidden);
      if (firstVisible) setActiveSection(firstVisible.id);
    }
    observeVisibleSections();
  }

  sections.forEach(section => {
    section.dataset.searchText = section.textContent.toLocaleLowerCase();
  });

  searchInput.addEventListener('input', () => {
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(filterSections, 160);
  });

  clearButton.addEventListener('click', () => {
    searchInput.value = '';
    filterSections();
    searchInput.focus();
  });

  links.forEach(link => {
    link.addEventListener('click', event => {
      const sectionId = link.dataset.termsLink;
      const target = document.getElementById(sectionId);
      if (!target || target.hidden) return;

      event.preventDefault();
      history.pushState(null, '', `#${sectionId}`);
      target.scrollIntoView({
        behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
        block: 'start',
      });
      setActiveSection(sectionId);
    });
  });

  window.addEventListener('popstate', () => {
    const target = document.getElementById(decodeURIComponent(location.hash.slice(1)));
    if (target && !target.hidden) {
      target.scrollIntoView({behavior: 'auto', block: 'start'});
      setActiveSection(target.id);
    }
  });

  document.getElementById('terms-print')?.addEventListener('click', () => window.print());

  const initialTarget = document.getElementById(decodeURIComponent(location.hash.slice(1)));
  if (initialTarget) setActiveSection(initialTarget.id);
  filterSections();
})();