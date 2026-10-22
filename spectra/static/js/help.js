(() => {
  const searchInput = document.getElementById('faq-search');
  const clearSearchButton = document.getElementById('faq-search-clear');
  const emptyState = document.getElementById('faq-empty');
  const resultCount = document.getElementById('faq-result-count');
  const resultsTitle = document.getElementById('faq-results-title');
  const tabs = [...document.querySelectorAll('.faq-category-tab')];
  const sections = [...document.querySelectorAll('[data-faq-section]')];
  const cards = [...document.querySelectorAll('[data-faq-card]')];
  const searchableCopy = [...document.querySelectorAll('.faq-question-copy, .faq-answer-copy')];
  let activeCategory = 'all';
  let debounceTimer;

  if (!searchInput || !cards.length) return;

  searchableCopy.forEach((element) => {
    element.dataset.originalText = element.textContent;
  });

  const escapeHtml = (text) => text.replace(/[&<>"']/g, (character) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  })[character]);

  const updateHighlight = (element, query) => {
    const original = element.dataset.originalText || '';
    if (!query) {
      element.textContent = original;
      return;
    }
    const escapedQuery = query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const matcher = new RegExp(`(${escapedQuery})`, 'gi');
    element.innerHTML = original.split(matcher)
      .map((part) => part.toLowerCase() === query.toLowerCase()
        ? `<mark>${escapeHtml(part)}</mark>`
        : escapeHtml(part))
      .join('');
  };

  const setActiveCategory = (category) => {
    activeCategory = category;
    tabs.forEach((tab) => {
      const isActive = tab.dataset.category === category;
      tab.classList.toggle('is-active', isActive);
      tab.setAttribute('aria-pressed', String(isActive));
    });
    const activeTab = tabs.find((tab) => tab.dataset.category === category);
    resultsTitle.textContent = category === 'all'
      ? 'All topics'
      : activeTab?.childNodes[0]?.textContent.trim() || activeTab?.textContent.trim() || 'Topics';
    filterCards();
  };

  const filterCards = () => {
    const query = searchInput.value.trim().toLocaleLowerCase();
    let visibleCount = 0;

    cards.forEach((card) => {
      const question = card.querySelector('.faq-question-copy');
      const answer = card.querySelector('.faq-answer-copy');
      const matchesText = `${question?.dataset.originalText || ''} ${answer?.dataset.originalText || ''}`
        .toLocaleLowerCase()
        .includes(query);
      const matchesCategory = activeCategory === 'all' || card.dataset.category === activeCategory;
      const visible = matchesText && matchesCategory;
      card.hidden = !visible;
      if (visible) visibleCount += 1;
      if (question) updateHighlight(question, query);
      if (answer) updateHighlight(answer, query);
    });

    sections.forEach((section) => {
      section.hidden = !section.querySelector('[data-faq-card]:not([hidden])');
    });

    resultCount.textContent = `${visibleCount} ${visibleCount === 1 ? 'answer' : 'answers'}`;
    emptyState.hidden = visibleCount !== 0;
    clearSearchButton.hidden = query.length === 0;
  };

  tabs.forEach((tab) => {
    tab.addEventListener('click', () => setActiveCategory(tab.dataset.category));
  });

  searchInput.addEventListener('input', () => {
    window.clearTimeout(debounceTimer);
    debounceTimer = window.setTimeout(filterCards, 130);
  });

  const clearSearch = () => {
    searchInput.value = '';
    filterCards();
    searchInput.focus();
  };

  clearSearchButton.addEventListener('click', clearSearch);
  document.getElementById('faq-empty-clear')?.addEventListener('click', clearSearch);

  document.addEventListener('keydown', (event) => {
    const target = event.target;
    const isTyping = target instanceof HTMLElement
      && (target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName));
    if (event.key === '/' && !isTyping && !event.ctrlKey && !event.metaKey && !event.altKey) {
      event.preventDefault();
      searchInput.focus();
    }
    if (event.key === 'Escape' && target === searchInput && searchInput.value) clearSearch();
  });

  document.querySelectorAll('.faq-question').forEach((button) => {
    button.addEventListener('click', () => {
      const expanded = button.getAttribute('aria-expanded') === 'true';
      const answer = document.getElementById(button.getAttribute('aria-controls'));
      button.setAttribute('aria-expanded', String(!expanded));
      answer.setAttribute('aria-hidden', String(expanded));
      answer.style.maxHeight = expanded ? '0px' : `${answer.scrollHeight}px`;
    });
  });

  filterCards();
})();