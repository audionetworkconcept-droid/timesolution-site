/* ============================================================
   FeedZero – main.js
   ============================================================ */

(function () {
  'use strict';

  /* ── Mobile hamburger menu ── */
  function initHamburger() {
    const btn = document.querySelector('.nav-hamburger');
    const menu = document.querySelector('.nav-mobile');
    if (!btn || !menu) return;

    btn.addEventListener('click', function () {
      const open = btn.classList.toggle('open');
      menu.classList.toggle('open', open);
      btn.setAttribute('aria-expanded', String(open));
    });

    // Close on outside click
    document.addEventListener('click', function (e) {
      if (!btn.contains(e.target) && !menu.contains(e.target)) {
        btn.classList.remove('open');
        menu.classList.remove('open');
        btn.setAttribute('aria-expanded', 'false');
      }
    });

    // Close on nav link click (mobile)
    menu.querySelectorAll('a').forEach(function (link) {
      link.addEventListener('click', function () {
        btn.classList.remove('open');
        menu.classList.remove('open');
        btn.setAttribute('aria-expanded', 'false');
      });
    });
  }

  /* ── Active nav link detection ── */
  function initActiveNav() {
    const currentPath = window.location.pathname.replace(/\/$/, '');

    // Main nav links
    document.querySelectorAll('.nav-links a, .nav-mobile a').forEach(function (link) {
      const href = link.getAttribute('href');
      if (!href) return;
      const linkPath = href.replace(/\/$/, '').replace(/^\.\.\//, '/').replace(/^\.\//, '/');
      if (currentPath.endsWith(linkPath) || currentPath === linkPath) {
        link.classList.add('active');
      }
    });

    // Doc sidebar links
    document.querySelectorAll('.doc-sidebar-nav a').forEach(function (link) {
      const href = link.getAttribute('href');
      if (!href) return;
      const filename = href.split('/').pop().replace(/\/$/, '');
      if (currentPath.endsWith(filename) || currentPath.endsWith(filename + '.html')) {
        link.classList.add('active');
      }
    });

    // Language switcher
    const lang = currentPath.includes('/fr/') ? 'fr' : 'en';
    document.querySelectorAll('.nav-lang a, .footer-lang a').forEach(function (link) {
      if (link.dataset.lang === lang) {
        link.classList.add('lang-active');
      } else {
        link.classList.remove('lang-active');
      }
    });
  }

  /* ── FAQ accordion ── */
  function initFaq() {
    document.querySelectorAll('.faq-item').forEach(function (item) {
      const question = item.querySelector('.faq-question');
      if (!question) return;

      question.addEventListener('click', function () {
        const isOpen = item.classList.contains('open');

        // Close all others
        document.querySelectorAll('.faq-item.open').forEach(function (openItem) {
          if (openItem !== item) openItem.classList.remove('open');
        });

        item.classList.toggle('open', !isOpen);
        question.setAttribute('aria-expanded', String(!isOpen));
      });
    });
  }

  /* ── Smooth scroll for anchor links ── */
  function initSmoothScroll() {
    document.querySelectorAll('a[href^="#"]').forEach(function (link) {
      link.addEventListener('click', function (e) {
        const id = link.getAttribute('href').slice(1);
        if (!id) return;
        const target = document.getElementById(id);
        if (!target) return;
        e.preventDefault();
        const offset = 80; // nav height
        const top = target.getBoundingClientRect().top + window.pageYOffset - offset;
        window.scrollTo({ top: top, behavior: 'smooth' });
      });
    });
  }

  /* ── Init all ── */
  document.addEventListener('DOMContentLoaded', function () {
    initHamburger();
    initActiveNav();
    initFaq();
    initSmoothScroll();
  });
})();
