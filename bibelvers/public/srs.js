// Wiederholungs-Algorithmus (SM-2-Variante mit Lernschritten), reine Funktionen.
(function (root) {
  const MIN = 60 * 1000, DAY = 24 * 60 * MIN;
  const LEARN_STEPS = [1 * MIN, 10 * MIN];     // neue Karten: 1 min, 10 min, dann 1 Tag
  const RELEARN_STEPS = [10 * MIN];            // vergessene Karten
  const MAX_IVL = 365;

  function startOfDay(t) { const d = new Date(t); d.setHours(4, 0, 0, 0); if (d.getTime() > t) d.setDate(d.getDate() - 1); return d.getTime(); }
  function dueInDays(now, days) { return startOfDay(now) + days * DAY; }
  function fuzz(ivl) { if (ivl < 3) return ivl; const f = Math.max(1, Math.round(ivl * 0.05)); return ivl + Math.round((Math.random() * 2 - 1) * f); }

  // rating: 1 Nochmal, 2 Schwer, 3 Gut, 4 Leicht. Gibt eine neue Karte zurück.
  function schedule(card, rating, now, opts) {
    const c = Object.assign({}, card);
    const noFuzz = opts && opts.noFuzz;
    const fz = noFuzz ? (x) => x : fuzz;
    c.reps = (c.reps || 0) + 1;
    c.last = now;
    if (c.ease == null) c.ease = 2.5;
    if (c.state === "new") { c.state = "learning"; c.step = 0; c.relearn = false; }

    if (c.state === "learning") {
      const steps = c.relearn ? RELEARN_STEPS : LEARN_STEPS;
      const graduate = (days) => { c.state = "review"; c.interval = days; c.due = dueInDays(now, days); c.step = 0; c.relearn = false; };
      if (rating === 1) { c.step = 0; c.due = now + steps[0]; }
      else if (rating === 2) { c.due = now + Math.round((steps[c.step] || steps[steps.length - 1]) * 1.5); }
      else if (rating === 3) {
        c.step += 1;
        if (c.step >= steps.length) graduate(c.relearn ? Math.max(1, c.interval || 1) : 1);
        else c.due = now + steps[c.step];
      } else {
        graduate(c.relearn ? Math.max(2, Math.round((c.interval || 1) * 1.5)) : 4);
      }
      return c;
    }

    // review
    const ivl = c.interval || 1;
    const late = Math.max(0, Math.floor((startOfDay(now) - (c.due || now)) / DAY));
    if (rating === 1) {
      c.lapses = (c.lapses || 0) + 1;
      c.ease = Math.max(1.3, c.ease - 0.2);
      c.interval = Math.max(1, Math.round(ivl * 0.3));
      c.state = "learning"; c.relearn = true; c.step = 0; c.due = now + RELEARN_STEPS[0];
      return c;
    }
    let next;
    if (rating === 2) { c.ease = Math.max(1.3, c.ease - 0.15); next = Math.max(ivl + 1, Math.round(ivl * 1.2)); }
    else if (rating === 3) { next = Math.max(ivl + 1, Math.round((ivl + late / 2) * c.ease)); }
    else { c.ease = c.ease + 0.15; next = Math.max(ivl + 2, Math.round((ivl + late) * c.ease * 1.3)); }
    c.interval = Math.min(MAX_IVL, fz(next));
    c.due = dueInDays(now, c.interval);
    return c;
  }

  function fmtIvl(ms) {
    if (ms < 60 * MIN) return Math.max(1, Math.round(ms / MIN)) + " min";
    if (ms < DAY) return Math.round(ms / (60 * MIN)) + " h";
    const d = Math.round(ms / DAY);
    if (d < 31) return d + " T";
    if (d < 365) return (d / 30).toFixed(d < 90 ? 1 : 0).replace(".", ",") + " Mo";
    return (d / 365).toFixed(1).replace(".", ",") + " J";
  }

  // Bewertungs-Vorschlag aus Fehlerquote (Tipp-Modus) und Hinweis-Nutzung.
  function suggest(errors, words, usedHints) {
    if (words === 0) return 3;
    const q = errors / words;
    if (q > 0.25) return 1;
    if (q > 0.08 || usedHints) return 2;
    return 3;
  }

  const api = { schedule, fmtIvl, suggest, startOfDay, DAY, MIN };
  if (typeof module !== "undefined") module.exports = api; else root.SRS = api;
})(this);
