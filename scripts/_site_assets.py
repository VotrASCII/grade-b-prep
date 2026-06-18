"""CSS and JS strings for the static site, kept apart from build logic."""

CSS = r""":root{
  --bg:#f3f1ea;
  --bg-2:#ece9df;
  --ink:#1b1a17;
  --ink-soft:#46443d;
  --muted:#8a877d;
  --line:#d9d4c8;
  --line-soft:#e4dfd4;
  --accent:#9c4221;       /* muted terracotta */
  --accent-soft:#bf6a44;
  --good:#3f6f4f;
  --bad:#a23b34;
  --card:#f8f6f0;
  --shadow:0 1px 0 rgba(27,26,23,.04);
  --max:1140px;
  --ease:cubic-bezier(.22,.61,.36,1);
}

*{box-sizing:border-box;margin:0;padding:0}
html{scroll-behavior:smooth}
body{
  background:var(--bg);
  color:var(--ink);
  font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
  font-weight:400;
  line-height:1.6;
  -webkit-font-smoothing:antialiased;
  text-rendering:optimizeLegibility;
}
a{color:inherit;text-decoration:none}
em{font-style:italic}
::selection{background:var(--accent);color:#fff}

/* ---- typography helpers ---- */
.display{
  font-family:'Instrument Serif',Georgia,serif;
  font-weight:400;
  line-height:1.02;
  letter-spacing:-.01em;
  font-size:clamp(2.7rem,8vw,6.4rem);
}
.display em{font-style:italic;color:var(--accent)}
.eyebrow{
  font-family:'JetBrains Mono',ui-monospace,monospace;
  text-transform:uppercase;
  letter-spacing:.22em;
  font-size:.7rem;
  color:var(--muted);
}
.lede{
  font-size:clamp(1.05rem,1.6vw,1.3rem);
  color:var(--ink-soft);
  max-width:46ch;
  margin-top:1.4rem;
}

/* ---- nav ---- */
.nav{
  position:sticky;top:0;z-index:50;
  display:flex;align-items:center;justify-content:space-between;
  padding:1.15rem clamp(1.25rem,5vw,3.5rem);
  background:color-mix(in srgb,var(--bg) 86%,transparent);
  backdrop-filter:blur(10px);
  border-bottom:1px solid transparent;
  transition:border-color .3s var(--ease);
}
.nav.scrolled{border-bottom-color:var(--line)}
.brand{
  font-family:'JetBrains Mono',monospace;
  font-weight:500;letter-spacing:.04em;font-size:.95rem;
}
.brand-dot{color:var(--accent);padding:0 .15em}
.nav-links{display:flex;gap:1.8rem}
.nav-links a{
  font-size:.82rem;color:var(--ink-soft);
  position:relative;padding-bottom:2px;
}
.nav-links a::after{
  content:"";position:absolute;left:0;bottom:0;width:0;height:1px;
  background:var(--accent);transition:width .3s var(--ease);
}
.nav-links a:hover{color:var(--ink)}
.nav-links a:hover::after{width:100%}

main{max-width:var(--max);margin:0 auto;padding:0 clamp(1.25rem,5vw,3.5rem)}

/* ---- hero ---- */
.hero{padding:clamp(4rem,12vh,9rem) 0 clamp(3rem,8vh,6rem)}
.hero .display{margin-top:1.4rem}
.hero-meta{
  display:flex;flex-wrap:wrap;gap:1.4rem 2.4rem;align-items:baseline;
  margin-top:2.6rem;padding-top:1.6rem;border-top:1px solid var(--line);
  font-size:.92rem;color:var(--ink-soft);
}
.hero-meta b{
  font-family:'Instrument Serif',serif;font-size:1.5rem;
  color:var(--ink);font-weight:400;margin-right:.3em;
}
.hero-latest{
  font-family:'JetBrains Mono',monospace;font-size:.74rem;
  letter-spacing:.02em;color:var(--muted);margin-left:auto;
}
.scroll-cue{
  display:inline-block;margin-top:3rem;font-size:.8rem;
  font-family:'JetBrains Mono',monospace;letter-spacing:.05em;color:var(--muted);
  transition:color .3s,transform .3s;
}
.scroll-cue:hover{color:var(--accent);transform:translateY(2px)}

/* ---- section heads ---- */
.section-head{
  display:grid;grid-template-columns:auto 1fr;gap:.3rem 1.4rem;align-items:baseline;
  padding:clamp(3rem,7vh,5rem) 0 2rem;border-top:1px solid var(--line);
}
.section-head .sh-num{
  font-family:'JetBrains Mono',monospace;font-size:.78rem;color:var(--accent);
  letter-spacing:.05em;padding-top:.5rem;
}
.section-head h2{
  font-family:'Instrument Serif',serif;font-weight:400;
  font-size:clamp(1.9rem,4vw,3rem);line-height:1;letter-spacing:-.01em;
}
.section-head p{grid-column:2;color:var(--ink-soft);max-width:52ch;margin-top:.5rem}

/* ---- week index list ---- */
.week-list{border-top:1px solid var(--line)}
.week-row{
  display:grid;
  grid-template-columns:7.5rem 1fr auto 1.5rem;
  align-items:center;gap:1.5rem;
  padding:1.5rem .4rem;border-bottom:1px solid var(--line-soft);
  transition:padding .35s var(--ease),background .35s var(--ease);
}
.week-row:hover{background:var(--bg-2);padding-left:1.1rem;padding-right:1.1rem}
.wr-num{
  font-family:'JetBrains Mono',monospace;font-size:.78rem;
  letter-spacing:.04em;color:var(--muted);
}
.week-row:hover .wr-num{color:var(--accent)}
.wr-title{
  font-family:'Instrument Serif',serif;font-size:clamp(1.3rem,2.4vw,1.9rem);
  letter-spacing:-.01em;
}
.wr-meta{
  font-family:'JetBrains Mono',monospace;font-size:.72rem;color:var(--muted);
  text-align:right;white-space:nowrap;
}
.wr-sep{padding:0 .55em;opacity:.5}
.wr-arrow{
  font-size:1.1rem;color:var(--accent);justify-self:end;
  opacity:0;transform:translateX(-6px);transition:.35s var(--ease);
}
.week-row:hover .wr-arrow{opacity:1;transform:translateX(0)}

@media(max-width:680px){
  .week-row{
    grid-template-columns:1fr auto;
    grid-template-areas:"title title" "num meta";
    gap:.5rem .9rem;padding:1.3rem .2rem;
  }
  .week-row:hover{padding-left:.2rem;padding-right:.2rem;background:none}
  .wr-title{grid-area:title;font-size:1.55rem}
  .wr-num{grid-area:num}
  .wr-meta{grid-area:meta;align-self:center}
  .wr-arrow{display:none}
}

/* ---- about ---- */
.about-grid{
  display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));
  gap:1px;background:var(--line-soft);border:1px solid var(--line-soft);
  margin-bottom:2rem;
}
.about-card{background:var(--bg);padding:2rem 1.8rem 2.2rem}
.about-card .ac-num{
  font-family:'JetBrains Mono',monospace;color:var(--accent);font-size:.78rem;
}
.about-card h3{
  font-family:'Instrument Serif',serif;font-weight:400;font-size:1.5rem;
  margin:.9rem 0 .6rem;
}
.about-card p{color:var(--ink-soft);font-size:.95rem}

/* ---- week page ---- */
.week-main{padding-bottom:4rem}
.back-link{
  display:inline-block;margin:2.2rem 0 .5rem;
  font-family:'JetBrains Mono',monospace;font-size:.78rem;color:var(--muted);
  transition:color .3s,transform .3s;
}
.back-link:hover{color:var(--accent);transform:translateX(-3px)}
.week-head{padding:1.5rem 0 clamp(2.5rem,6vh,4rem)}
.week-head .lede{
  font-family:'JetBrains Mono',monospace;font-size:.8rem;letter-spacing:.02em;
  color:var(--muted);max-width:none;
}

.summary{border-top:1px solid var(--line)}
.topic{
  display:grid;grid-template-columns:minmax(0,1fr);
  padding:clamp(2rem,4vh,3rem) 0;border-bottom:1px solid var(--line-soft);
}
@media(min-width:820px){
  .topic{grid-template-columns:15rem 1fr;gap:2.5rem}
  .topic-head{position:sticky;top:5rem;align-self:start}
}
.topic-head{display:flex;align-items:baseline;gap:.9rem;margin-bottom:1rem}
.topic-num{
  font-family:'JetBrains Mono',monospace;font-size:.78rem;color:var(--accent);
}
.topic-head h2{
  font-family:'Instrument Serif',serif;font-weight:400;
  font-size:clamp(1.4rem,2.6vw,2rem);line-height:1.05;letter-spacing:-.01em;
}
.topic-body h3.sub{
  font-family:'Inter',sans-serif;font-size:.78rem;font-weight:600;
  text-transform:uppercase;letter-spacing:.12em;color:var(--muted);
  margin:1.4rem 0 .6rem;
}
ul.facts{list-style:none}
ul.facts li{
  position:relative;padding:.55rem 0 .55rem 1.4rem;
  border-bottom:1px solid var(--line-soft);
  color:var(--ink-soft);font-size:1rem;line-height:1.62;
}
ul.facts li:last-child{border-bottom:none}
ul.facts li::before{
  content:"";position:absolute;left:0;top:1.05rem;width:6px;height:6px;
  border:1px solid var(--accent);border-radius:50%;
}
ul.facts li strong{color:var(--ink);font-weight:600}
.fig{
  font-variant-numeric:tabular-nums;
  color:var(--ink);
  background:linear-gradient(transparent 62%,color-mix(in srgb,var(--accent) 18%,transparent) 0);
  padding:0 .05em;
}
.star{color:var(--accent);font-size:.85em;vertical-align:.05em;margin-right:.1em}
.provenance{
  color:var(--muted);font-size:.85rem;font-style:italic;
  padding:1.6rem 0;max-width:70ch;
}

/* ---- quiz ---- */
.practice{margin-top:1rem}
.quiz{border-top:1px solid var(--line)}
.quiz-bar{
  position:sticky;top:3.6rem;z-index:20;
  display:flex;align-items:center;gap:1rem;
  padding:1rem 0;background:color-mix(in srgb,var(--bg) 90%,transparent);
  backdrop-filter:blur(8px);border-bottom:1px solid var(--line-soft);
}
.quiz-progress{flex:1;height:2px;background:var(--line);overflow:hidden}
.quiz-progress span{
  display:block;height:100%;width:0;background:var(--accent);
  transition:width .4s var(--ease);
}
.quiz-score{
  font-family:'JetBrains Mono',monospace;font-size:.74rem;color:var(--ink-soft);
  white-space:nowrap;
}
.quiz-reset{
  font-family:'JetBrains Mono',monospace;font-size:.72rem;
  background:none;border:1px solid var(--line);color:var(--ink-soft);
  padding:.35rem .8rem;border-radius:2px;cursor:pointer;transition:.25s;
}
.quiz-reset:hover{border-color:var(--accent);color:var(--accent)}

.q{padding:1.8rem 0;border-bottom:1px solid var(--line-soft)}
.q-stem{display:flex;gap:.9rem;margin-bottom:1rem}
.q-no{
  font-family:'JetBrains Mono',monospace;font-size:.78rem;color:var(--accent);
  padding-top:.15rem;flex-shrink:0;
}
.q-text{font-size:1.06rem;line-height:1.55;color:var(--ink)}
.q-options{list-style:none;display:grid;gap:.5rem}
.opt{
  display:flex;gap:.75rem;align-items:flex-start;
  padding:.75rem .9rem;border:1px solid var(--line);border-radius:3px;
  background:var(--card);cursor:pointer;font-size:.97rem;color:var(--ink-soft);
  transition:border-color .2s,background .2s,color .2s;text-align:left;width:100%;
}
.opt:hover:not([disabled]){border-color:var(--accent-soft);color:var(--ink)}
.opt .opt-key{
  font-family:'JetBrains Mono',monospace;font-size:.78rem;color:var(--muted);
  flex-shrink:0;padding-top:.05rem;
}
.opt[disabled]{cursor:default}
.opt.correct{border-color:var(--good);background:color-mix(in srgb,var(--good) 9%,var(--card));color:var(--ink)}
.opt.correct .opt-key{color:var(--good)}
.opt.wrong{border-color:var(--bad);background:color-mix(in srgb,var(--bad) 8%,var(--card));color:var(--ink)}
.opt.wrong .opt-key{color:var(--bad)}
.opt.muted{opacity:.55}
.q-verdict{
  font-family:'JetBrains Mono',monospace;font-size:.74rem;margin-top:.7rem;
  letter-spacing:.02em;
}
.q-verdict.ok{color:var(--good)}
.q-verdict.no{color:var(--bad)}

/* ---- pager ---- */
.week-pager{
  display:flex;justify-content:space-between;gap:1rem;
  margin-top:3rem;padding-top:1.6rem;border-top:1px solid var(--line);
}
.pager{
  display:flex;flex-direction:column;gap:.2rem;
  font-family:'Instrument Serif',serif;font-size:1.3rem;
  transition:color .25s,transform .25s;
}
.pager span{
  font-family:'JetBrains Mono',monospace;font-size:.68rem;
  text-transform:uppercase;letter-spacing:.12em;color:var(--muted);
}
.pager.older{text-align:right}
.pager:not(.disabled):hover{color:var(--accent)}
.pager.newer:not(.disabled):hover{transform:translateX(-4px)}
.pager.older:not(.disabled):hover{transform:translateX(4px)}
.pager.disabled{color:var(--line);cursor:default}

/* ---- footer ---- */
.site-foot{
  max-width:var(--max);margin:5rem auto 0;
  padding:3rem clamp(1.25rem,5vw,3.5rem) 2rem;border-top:1px solid var(--line);
}
.foot-grid{display:flex;flex-wrap:wrap;justify-content:space-between;gap:2rem}
.foot-mark{font-family:'JetBrains Mono',monospace;font-weight:500;letter-spacing:.04em}
.foot-note{color:var(--muted);font-size:.9rem;margin-top:.5rem;max-width:30ch}
.foot-links{display:flex;flex-direction:column;gap:.5rem;font-size:.88rem}
.foot-links a{color:var(--ink-soft);transition:color .25s}
.foot-links a:hover{color:var(--accent)}
.foot-base{
  margin-top:2.5rem;padding-top:1.4rem;border-top:1px solid var(--line-soft);
  font-family:'JetBrains Mono',monospace;font-size:.68rem;color:var(--muted);
  letter-spacing:.02em;
}

/* ---- reveal animation ---- */
.reveal{opacity:0;transform:translateY(18px);transition:opacity .7s var(--ease),transform .7s var(--ease)}
.reveal.in{opacity:1;transform:none}
@media(prefers-reduced-motion:reduce){
  .reveal{opacity:1;transform:none;transition:none}
  html{scroll-behavior:auto}
}
"""

JS = r"""(() => {
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
"""
