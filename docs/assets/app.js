(() => {
  // Scroll reveal
  // threshold 0 so elements taller than the viewport (e.g. the long quiz) still reveal
  const obs = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (e.isIntersecting) { e.target.classList.add('in'); obs.unobserve(e.target); }
    }
  }, { threshold: 0, rootMargin: '0px 0px -6% 0px' });
  const reveals = document.querySelectorAll('.reveal');
  reveals.forEach((el, i) => {
    el.style.transitionDelay = Math.min(i * 35, 240) + 'ms';
    obs.observe(el);
  });
  // Safety net: never leave content permanently hidden.
  window.addEventListener('load', () => setTimeout(() => {
    reveals.forEach((el) => el.classList.add('in'));
  }, 2500));

  // Sticky nav border on scroll
  const nav = document.querySelector('.nav');
  if (nav) {
    const onScroll = () => nav.classList.toggle('scrolled', window.scrollY > 8);
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
  }

  // Quiz
  const dataEl = document.getElementById('quiz-data');
  const wrap = document.getElementById('quiz-questions');
  if (!dataEl || !wrap) return;

  let questions = [];
  try { questions = JSON.parse(dataEl.textContent || '[]'); } catch (_) { return; }
  if (!questions.length) return;

  const fill = document.getElementById('quiz-progress-fill');
  const scoreEl = document.getElementById('quiz-score');
  const resetBtn = document.getElementById('quiz-reset');
  const total = questions.length;
  let answered = 0, correct = 0;

  const keyOf = (opt) => {
    const m = String(opt).match(/^\s*([A-E])[.)]/);
    return m ? m[1] : '';
  };
  const textOf = (opt) => String(opt).replace(/^\s*[A-E][.)]\s*/, '');

  function updateBar() {
    if (fill) fill.style.width = (answered / total * 100) + '%';
    if (scoreEl) {
      scoreEl.textContent = answered === total
        ? `Done · ${correct} / ${total} correct`
        : `${answered} / ${total} answered`;
    }
  }

  function render() {
    answered = 0; correct = 0;
    wrap.innerHTML = '';
    questions.forEach((q, i) => {
      const card = document.createElement('div');
      card.className = 'q';
      const stem = document.createElement('div');
      stem.className = 'q-stem';
      stem.innerHTML = `<span class="q-no">Q${String(i + 1).padStart(2, '0')}</span>` +
        `<span class="q-text"></span>`;
      stem.querySelector('.q-text').textContent = q.question || '';
      card.appendChild(stem);

      const ul = document.createElement('ul');
      ul.className = 'q-options';
      const ans = (q.answer || '').toString().trim().toUpperCase();
      (q.options || []).forEach((opt) => {
        const li = document.createElement('li');
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'opt';
        const k = keyOf(opt);
        btn.innerHTML = `<span class="opt-key">${k || '•'}</span><span class="opt-text"></span>`;
        btn.querySelector('.opt-text').textContent = textOf(opt);
        btn.addEventListener('click', () => choose(card, btn, k, ans));
        li.appendChild(btn);
        ul.appendChild(li);
      });
      card.appendChild(ul);

      const verdict = document.createElement('div');
      verdict.className = 'q-verdict';
      card.appendChild(verdict);
      wrap.appendChild(card);
    });
    updateBar();
  }

  function choose(card, btn, picked, ans) {
    if (card.dataset.done) return;
    card.dataset.done = '1';
    answered++;
    const isCorrect = picked && picked === ans;
    if (isCorrect) correct++;

    card.querySelectorAll('.opt').forEach((o) => {
      o.setAttribute('disabled', '');
      const k = o.querySelector('.opt-key').textContent.trim();
      if (k === ans) o.classList.add('correct');
      else if (o === btn) o.classList.add('wrong');
      else o.classList.add('muted');
    });

    const verdict = card.querySelector('.q-verdict');
    if (isCorrect) { verdict.textContent = 'Correct'; verdict.classList.add('ok'); }
    else { verdict.textContent = ans ? `Answer: ${ans}` : 'Recorded'; verdict.classList.add('no'); }
    updateBar();
  }

  if (resetBtn) resetBtn.addEventListener('click', render);
  render();
})();
